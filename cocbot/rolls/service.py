"""RollService: record checks permanently and apply Luck spending atomically.

* A check is rolled and stored in one step (``roll_check``). The natural
  result, dice, target, required difficulty and rule profile never change
  afterwards (database triggers enforce it).
* Only the roller can read a roll here or act on it; anyone else gets "not
  found", the same as for a roll that does not exist.
* ``spend_luck`` runs in a single ``BEGIN IMMEDIATE`` transaction: the roll
  update, the Luck deduction and the event rows all commit together or not
  at all. A replayed interaction (same ``request_id``) returns the already
  applied result instead of charging again; a stale panel (``expected_version``)
  or a concurrent change is refused.
* Campaign Luck is deducted through ``CampaignService.spend_luck_in_tx`` (the
  same table and audit log as the Keeper Console); personal Luck through a
  guarded update of the player's own investigator.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from cocbot.campaigns.checks import CheckPlan
from cocbot.campaigns.rules import CampaignRules, from_json
from cocbot.campaigns.service import CampaignService
from cocbot.characters.service import InvalidState, NotFound, ValidationError
from cocbot.i18n import Msg
from cocbot.mechanics.checks import SuccessLevel, success_level
from cocbot.mechanics.dice import roll_d100_bonus_penalty_candidates
from cocbot.rolls.rules import (
    DIFFICULTIES,
    REGULAR,
    LuckOption,
    RollFacts,
    level_from_name,
    luck_adjusted_level,
    luck_eligibility,
    meets,
    threshold,
)

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Roll:
    id: int
    user_id: str
    investigator_id: Optional[int]
    investigator_name: Optional[str]
    campaign_id: Optional[int]
    channel_id: Optional[str]
    guild_id: Optional[str]
    luck_scope: str
    subject_kind: str
    subject_key: Optional[str]
    subject_label: str
    target: int
    base_value: Optional[int]
    difficulty: str
    bonus_penalty: int
    dice: dict
    natural: int
    natural_level: SuccessLevel
    rules: CampaignRules
    rules_version: Optional[int]
    visibility: str
    attempt: str
    parent_roll_id: Optional[int]
    luck_spent: int
    effective: int
    effective_level: SuccessLevel
    state_version: int
    created_at: str
    updated_at: str

    @property
    def met(self) -> bool:
        return meets(self.effective_level, self.difficulty)

    @property
    def natural_met(self) -> bool:
        return meets(self.natural_level, self.difficulty)

    @property
    def private(self) -> bool:
        return self.visibility == "private"

    @property
    def improvement_eligible(self) -> bool:
        """Only a success the dice gave earns an improvement check; one bought with Luck does not.

        Upgrading an existing success (Regular → Hard) keeps the natural success,
        so it stays eligible (docs/ROLLS.md §2).
        """
        return self.attempt == "original" and self.natural_met

    def facts(self) -> RollFacts:
        return RollFacts(self.subject_kind, self.subject_key, self.investigator_id is not None, self.attempt,
                         self.natural, self.natural_level, self.target, self.difficulty, self.luck_spent,
                         self.rules.to_check_rules(), self.rules.luck_spending, self.luck_scope)


@dataclass(frozen=True)
class RollEvent:
    id: int
    roll_id: int
    actor_user_id: str
    kind: str
    old_effective: Optional[int]
    new_effective: Optional[int]
    old_level: Optional[str]
    new_level: Optional[str]
    luck_before: Optional[int]
    luck_after: Optional[int]
    created_at: str


@dataclass(frozen=True)
class LuckStatus:
    roll: Roll
    reason: Optional[str]            # i18n code when Luck cannot be spent
    options: Tuple[LuckOption, ...]
    current_luck: Optional[int]

    @property
    def eligible(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class SpendResult:
    roll: Roll
    option: LuckOption
    luck_before: int
    luck_after: int
    replayed: bool = False           # the same request had already been applied


_COLUMNS = ("roll_id, user_id, investigator_id, investigator_name, campaign_id, channel_id, guild_id, luck_scope, "
            "subject_kind, subject_key, subject_label, target, base_value, difficulty, bonus_penalty, dice_json, "
            "natural, natural_level, rules_json, rules_version, visibility, attempt, parent_roll_id, luck_spent, "
            "effective, effective_level, state_version, created_at, updated_at")


def _roll(r: sqlite3.Row) -> Roll:
    return Roll(int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], int(r[11]), r[12], r[13],
                int(r[14]), json.loads(r[15]), int(r[16]), level_from_name(r[17]), from_json(r[18]), r[19], r[20],
                r[21], r[22], int(r[23]), int(r[24]), level_from_name(r[25]), int(r[26]), r[27], r[28])


class RollService:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = _utcnow) -> None:
        self.conn = conn
        self.clock = clock
        self.campaigns = CampaignService(conn)

    # --- recording -------------------------------------------------------------------------
    def roll_check(self, plan: CheckPlan, user_id: object, *, guild_id: Optional[object] = None,
                   channel_id: Optional[object] = None, bonus_penalty: int = 0,
                   difficulty: str = REGULAR) -> Roll:
        """Roll the dice for ``plan`` and store the result permanently."""
        if difficulty not in DIFFICULTIES:
            raise ValidationError(Msg("difficulty_invalid", modes=", ".join(DIFFICULTIES)))
        dice = roll_d100_bonus_penalty_candidates(bp=bonus_penalty)
        target = plan.target
        check_rules = plan.rules.to_check_rules()
        level = success_level(dice.chosen, target.value, check_rules)
        inv = target.investigator
        if plan.context is not None:
            scope = "campaign"
        elif inv is not None and plan.campaign is None:
            scope = "personal"
        else:
            scope = "none"     # numbers, or a non-participant rolling in someone's campaign channel
        dice_json = json.dumps({"candidates": dice.candidates, "tens": dice.tens_candidates, "ones": dice.ones},
                               sort_keys=True)
        values = (str(user_id), inv.id if inv else None, inv.name if inv else None,
                  plan.campaign.id if plan.campaign else None,
                  None if channel_id is None else str(channel_id), None if guild_id is None else str(guild_id),
                  scope, target.kind, target.key,
                  target.label if not target.detail else f"{target.label} ({target.detail})", int(target.value),
                  target.base_value if target.detail == "trained" else None, difficulty,
                  int(bonus_penalty), dice_json, dice.chosen, level.name, plan.rules.to_json(),
                  plan.rules_version, "private" if plan.private else "public", dice.chosen, level.name)
        try:
            cur = self.conn.execute(
                "INSERT INTO rolls (user_id, investigator_id, investigator_name, campaign_id, channel_id, guild_id, "
                "luck_scope, subject_kind, subject_key, subject_label, target, base_value, difficulty, "
                "bonus_penalty, dice_json, natural, natural_level, rules_json, rules_version, visibility, "
                "effective, effective_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            roll_id = int(cur.lastrowid)
            if plan.private and plan.context is not None:
                self.campaigns.record_roll(
                    plan.context, f"#{roll_id} {target.label} {target.value} ({difficulty}): "
                                  f"{dice.chosen:02d} {level.name.lower()}", roll_id=roll_id, commit=False)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self._get(roll_id)  # type: ignore[return-value]

    # --- reading (roller only) -------------------------------------------------------------
    def _get(self, roll_id: int) -> Optional[Roll]:
        r = self.conn.execute(f"SELECT {_COLUMNS} FROM rolls WHERE roll_id=?", (int(roll_id),)).fetchone()
        return _roll(r) if r else None

    def get(self, user_id: object, roll_id: int) -> Roll:
        roll = self._get(roll_id)
        if roll is None or roll.user_id != str(user_id):
            raise NotFound(Msg("roll_not_found"))
        return roll

    def recent(self, user_id: object, limit: int = 10) -> List[Roll]:
        rows = self.conn.execute(f"SELECT {_COLUMNS} FROM rolls WHERE user_id=? ORDER BY roll_id DESC LIMIT ?",
                                 (str(user_id), int(limit))).fetchall()
        return [_roll(r) for r in rows]

    def events(self, user_id: object, roll_id: int) -> List[RollEvent]:
        self.get(user_id, roll_id)
        rows = self.conn.execute(
            "SELECT event_id, roll_id, actor_user_id, kind, old_effective, new_effective, old_level, new_level, "
            "luck_before, luck_after, created_at FROM roll_events WHERE roll_id=? ORDER BY event_id",
            (int(roll_id),)).fetchall()
        return [RollEvent(*tuple(r)) for r in rows]

    # --- Luck --------------------------------------------------------------------------------
    def _current_luck(self, roll: Roll) -> Optional[int]:
        if roll.investigator_id is None:
            return None
        if roll.luck_scope == "campaign":
            if roll.campaign_id is None:
                return None
            return self.campaigns.participant_luck(roll.campaign_id, roll.investigator_id, roll.user_id)
        if roll.luck_scope == "personal":
            r = self.conn.execute("SELECT luck FROM investigators WHERE investigator_id=? AND owner_user_id=? "
                                  "AND status <> 'archived'", (roll.investigator_id, roll.user_id)).fetchone()
            return None if r is None else r[0]
        return None

    def _spending_allowed_now(self, roll: Roll) -> bool:
        if roll.luck_scope != "campaign":
            return roll.rules.luck_spending
        camp = self.campaigns._campaign(roll.campaign_id) if roll.campaign_id else None
        if camp is None or camp.status != "active":
            return False
        return self.campaigns.channel_rules(roll.campaign_id)[1].luck_spending

    def _is_latest(self, roll: Roll) -> bool:
        r = self.conn.execute("SELECT 1 FROM rolls WHERE user_id=? AND investigator_id IS ? AND roll_id > ? LIMIT 1",
                              (roll.user_id, roll.investigator_id, roll.id)).fetchone()
        return r is None

    def _status(self, roll: Roll) -> LuckStatus:
        luck = self._current_luck(roll)
        age = (self.clock() - _parse_ts(roll.created_at)).total_seconds()
        reason, options = luck_eligibility(roll.facts(), current_luck=luck,
                                           spending_allowed_now=self._spending_allowed_now(roll),
                                           is_latest=self._is_latest(roll), age_seconds=age)
        return LuckStatus(roll, reason, tuple(options), luck)

    def luck_status(self, user_id: object, roll_id: int) -> LuckStatus:
        return self._status(self.get(user_id, roll_id))

    def spend_luck(self, user_id: object, roll_id: int, difficulty: str, *, request_id: Optional[object] = None,
                   expected_version: Optional[int] = None) -> SpendResult:
        """Spend the Luck needed to reach ``difficulty`` on the caller's own roll. All or nothing."""
        if self.conn.in_transaction:
            self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            result = self._spend(user_id, roll_id, difficulty, request_id, expected_version)
        except Exception:
            self.conn.rollback()
            raise
        self.conn.commit()
        return result

    def _spend(self, user_id, roll_id, difficulty, request_id, expected_version) -> SpendResult:
        roll = self.get(user_id, roll_id)
        if request_id is not None:
            prior = self.conn.execute("SELECT roll_id, luck_before, luck_after FROM roll_events WHERE request_id=?",
                                      (str(request_id),)).fetchone()
            if prior is not None:
                if int(prior[0]) != roll.id:
                    raise InvalidState(Msg("roll_changed"))
                option = LuckOption(roll.difficulty, roll.luck_spent, roll.effective, roll.effective_level)
                return SpendResult(roll, option, int(prior[1]), int(prior[2]), replayed=True)
        if expected_version is not None and roll.state_version != int(expected_version):
            raise InvalidState(Msg("roll_changed"))
        status = self._status(roll)
        if status.reason is not None:
            raise InvalidState(Msg(status.reason))
        option = next((o for o in status.options if o.difficulty == difficulty), None)
        if option is None:
            raise InvalidState(Msg("luck_not_enough"))
        before = int(status.current_luck)  # type: ignore[arg-type]
        cur = self.conn.execute(
            "UPDATE rolls SET luck_spent=?, effective=?, effective_level=?, state_version=state_version+1, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE roll_id=? AND state_version=? AND luck_spent=0",
            (option.cost, option.effective, option.level.name, roll.id, roll.state_version))
        if cur.rowcount != 1:
            raise InvalidState(Msg("roll_changed"))
        if roll.luck_scope == "campaign":
            after = self.campaigns.spend_luck_in_tx(user_id, roll.campaign_id, roll.investigator_id, option.cost,
                                                    before, roll.id)
        else:
            cur = self.conn.execute(
                "UPDATE investigators SET luck=luck-?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE investigator_id=? AND owner_user_id=? AND luck=? AND luck>=? AND status <> 'archived'",
                (option.cost, roll.investigator_id, str(user_id), before, option.cost))
            if cur.rowcount != 1:
                raise InvalidState(Msg("luck_changed"))
            after = before - option.cost
        self.conn.execute(
            "INSERT INTO roll_events (roll_id, actor_user_id, kind, old_effective, new_effective, old_level, "
            "new_level, luck_before, luck_after, request_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (roll.id, str(user_id), "luck_spent", roll.effective, option.effective, roll.effective_level.name,
             option.level.name, before, after, None if request_id is None else str(request_id)))
        return SpendResult(self._get(roll.id), option, before, after)  # type: ignore[arg-type]


__all__ = ["LuckStatus", "Roll", "RollEvent", "RollService", "SpendResult", "luck_adjusted_level", "threshold"]
