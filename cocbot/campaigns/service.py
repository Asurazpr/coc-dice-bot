"""Campaign service: the single place that enforces campaign roles.

Every public method takes the *acting* Discord user ID and checks that user's
current role in the campaign before reading or writing anything, so a
Keeper Console button checks permissions when it is clicked, not when the
panel was drawn. Non-members get the same "not found" answer whether or not
the campaign exists.

Campaign-side current values (HP/MP/SAN/Luck) live in
``campaign_investigators`` and never write back to the player's permanent
investigator. Maxima are always derived from the permanent characteristics;
``campaign_view`` substitutes the campaign values into the investigator so
every reader (``/check``, sheets, the Keeper Console) sees the same numbers.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from cocbot.campaigns.rules import RAW_PROFILE, CampaignRules, RulesError, from_json, validate
from cocbot.characters.models import STATUS_READY, Investigator
from cocbot.characters.service import (
    CharacterError,
    InvalidState,
    InvestigatorService,
    NotFound,
    ValidationError,
)
from cocbot.db import repo_investigators as repo
from cocbot.i18n import Msg

ROLE_KEEPER, ROLE_ASSISTANT, ROLE_PLAYER = "keeper", "assistant", "player"
KEEPER_ROLES = (ROLE_KEEPER, ROLE_ASSISTANT)
RESOURCES = ("hp", "mp", "san", "luck")
LUCK_MAX = 99
MAX_CONDITIONS = 10
CONDITION_MAX_LEN = 40
REASON_MAX_LEN = 200
NAME_MAX_LEN = 80


class Forbidden(CharacterError):
    """The user is in the campaign but their role does not allow this action."""


# --- records ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Campaign:
    id: int
    name: str
    status: str
    created_by: str
    created_at: str


@dataclass(frozen=True)
class Member:
    campaign_id: int
    user_id: str
    role: str
    status: str
    invited_by: Optional[str]
    pending_investigator_id: Optional[int]


@dataclass(frozen=True)
class Participant:
    campaign_id: int
    investigator_id: int
    user_id: str
    status: str
    hp: Optional[int]
    mp: Optional[int]
    san: Optional[int]
    luck: Optional[int]


@dataclass(frozen=True)
class Condition:
    id: int
    campaign_id: int
    investigator_id: int
    label: str
    added_by: str
    added_at: str


@dataclass(frozen=True)
class Event:
    id: int
    campaign_id: int
    investigator_id: Optional[int]
    actor_user_id: str
    kind: str
    field: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    reason: Optional[str]
    ref_id: Optional[int]
    reverts_event_id: Optional[int]
    reverted_by_event_id: Optional[int]
    created_at: str

    @property
    def revertible(self) -> bool:
        return (self.kind in ("resource", "condition_add", "condition_remove")
                and self.reverted_by_event_id is None and self.reverts_event_id is None)


@dataclass(frozen=True)
class RosterEntry:
    participant: Participant
    investigator: Investigator          # campaign view (campaign values substituted)
    conditions: Tuple[Condition, ...]


@dataclass(frozen=True)
class CampaignContext:
    """A request made in a campaign channel by a participating player."""
    campaign: Campaign
    investigator: Investigator          # campaign view
    participant: Participant
    rules: CampaignRules
    rules_version: int


@dataclass(frozen=True)
class ResourceChange:
    field: str
    old: Optional[int]
    new: int
    maximum: Optional[int]

    @property
    def consequential(self) -> bool:
        """Decreases and reaching 0 need confirmation."""
        return self.new == 0 or (self.old is not None and self.new < self.old)


def campaign_view(inv: Investigator, p: Participant) -> Investigator:
    """The investigator as seen inside a campaign: same sheet, campaign current values."""
    return dataclasses.replace(inv, hp_current=p.hp, mp_current=p.mp, san_current=p.san, luck=p.luck)


def resource_value(inv: Investigator, field: str) -> Optional[int]:
    return {"hp": inv.hp, "mp": inv.mp, "san": inv.san, "luck": inv.luck}[field]


def resource_max(inv: Investigator, field: str) -> Optional[int]:
    d = inv.derived()
    return {"hp": d.hp_max, "mp": d.mp_max, "san": d.san_max, "luck": LUCK_MAX}[field]


class CampaignService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.investigators = InvestigatorService(conn)

    # --- lookups -----------------------------------------------------------------------
    def _campaign(self, campaign_id: int) -> Optional[Campaign]:
        r = self.conn.execute("SELECT campaign_id, name, status, created_by, created_at FROM campaigns "
                              "WHERE campaign_id=?", (int(campaign_id),)).fetchone()
        return Campaign(int(r[0]), r[1], r[2], r[3], r[4]) if r else None

    def _member(self, campaign_id: int, user_id: object) -> Optional[Member]:
        r = self.conn.execute(
            "SELECT campaign_id, user_id, role, status, invited_by, pending_investigator_id FROM campaign_members "
            "WHERE campaign_id=? AND user_id=?", (int(campaign_id), str(user_id))).fetchone()
        return Member(int(r[0]), r[1], r[2], r[3], r[4], r[5]) if r else None

    def role_of(self, campaign_id: int, user_id: object) -> Optional[str]:
        m = self._member(campaign_id, user_id)
        return m.role if m and m.status == "active" else None

    def _require(self, campaign_id: int, actor_id: object, roles: Sequence[str]) -> Tuple[Campaign, Member]:
        camp = self._campaign(campaign_id)
        m = self._member(campaign_id, actor_id) if camp else None
        if camp is None or m is None or m.status != "active":
            raise NotFound(Msg("campaign_not_found"))
        if m.role not in roles:
            raise Forbidden(Msg("keeper_only" if ROLE_ASSISTANT in roles else "primary_keeper_only"))
        return camp, m

    def _participant(self, campaign_id: int, investigator_id: int) -> Optional[Participant]:
        r = self.conn.execute(
            "SELECT campaign_id, investigator_id, user_id, status, hp, mp, san, luck FROM campaign_investigators "
            "WHERE campaign_id=? AND investigator_id=?", (int(campaign_id), int(investigator_id))).fetchone()
        return Participant(int(r[0]), int(r[1]), r[2], r[3], r[4], r[5], r[6], r[7]) if r else None

    def _active_participant_of_user(self, campaign_id: int, user_id: object) -> Optional[Participant]:
        r = self.conn.execute(
            "SELECT investigator_id FROM campaign_investigators WHERE campaign_id=? AND user_id=? "
            "AND status='active'", (int(campaign_id), str(user_id))).fetchone()
        return self._participant(campaign_id, int(r[0])) if r else None

    def _log(self, campaign_id: int, actor_id: object, kind: str, investigator_id: Optional[int] = None,
             field: Optional[str] = None, old: object = None, new: object = None, reason: Optional[str] = None,
             ref_id: Optional[int] = None, reverts: Optional[int] = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO campaign_events (campaign_id, investigator_id, actor_user_id, kind, field, old_value,
                                         new_value, reason, ref_id, reverts_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(campaign_id), investigator_id, str(actor_id), kind, field,
             None if old is None else str(old), None if new is None else str(new), reason, ref_id, reverts),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _clean_reason(reason: Optional[object]) -> Optional[str]:
        if reason is None:
            return None
        text = " ".join(str(reason).split())
        if len(text) > REASON_MAX_LEN:
            raise ValidationError(Msg("reason_too_long", max=REASON_MAX_LEN))
        return text or None

    def _tx(self):
        return _Transaction(self.conn)

    # --- campaigns ---------------------------------------------------------------------
    def create(self, actor_id: object, name: object, channel_id: Optional[object] = None,
               guild_id: Optional[object] = None) -> Campaign:
        clean = " ".join(str(name or "").split())
        if not clean or len(clean) > NAME_MAX_LEN:
            raise ValidationError(Msg("campaign_name_invalid", max=NAME_MAX_LEN))
        if channel_id is not None:
            self._check_channel_free(channel_id)
        with self._tx():
            cur = self.conn.execute("INSERT INTO campaigns (name, created_by) VALUES (?, ?)", (clean, str(actor_id)))
            cid = int(cur.lastrowid)
            self.conn.execute("INSERT INTO campaign_members (campaign_id, user_id, role, status) "
                              "VALUES (?, ?, 'keeper', 'active')", (cid, str(actor_id)))
            self.conn.execute("INSERT INTO campaign_rules (campaign_id, version, rules_json, created_by) "
                              "VALUES (?, 1, ?, ?)", (cid, RAW_PROFILE.to_json(), str(actor_id)))
            self._log(cid, actor_id, "created", new=clean)
            if channel_id is not None:
                self._link(cid, actor_id, channel_id, guild_id)
        return self._campaign(cid)  # type: ignore[return-value]

    def get(self, actor_id: object, campaign_id: int) -> Tuple[Campaign, Member]:
        """Campaign + the actor's membership (any active role)."""
        return self._require(campaign_id, actor_id, (ROLE_KEEPER, ROLE_ASSISTANT, ROLE_PLAYER))

    def list_for(self, actor_id: object, roles: Sequence[str] = (ROLE_KEEPER, ROLE_ASSISTANT, ROLE_PLAYER)
                 ) -> List[Tuple[Campaign, str]]:
        marks = ",".join("?" for _ in roles)
        rows = self.conn.execute(
            f"SELECT c.campaign_id, m.role FROM campaigns c JOIN campaign_members m ON m.campaign_id=c.campaign_id "
            f"WHERE m.user_id=? AND m.status='active' AND c.status='active' AND m.role IN ({marks}) "
            f"ORDER BY lower(c.name)", (str(actor_id), *roles)).fetchall()
        return [(self._campaign(int(r[0])), r[1]) for r in rows]  # type: ignore[misc]

    def campaign_for_channel(self, channel_id: Optional[object]) -> Optional[Campaign]:
        if channel_id is None:
            return None
        r = self.conn.execute("SELECT c.campaign_id FROM campaign_channels ch JOIN campaigns c "
                              "ON c.campaign_id=ch.campaign_id WHERE ch.channel_id=? AND c.status='active'",
                              (str(channel_id),)).fetchone()
        return self._campaign(int(r[0])) if r else None

    def channels(self, actor_id: object, campaign_id: int) -> List[str]:
        self.get(actor_id, campaign_id)
        return [r[0] for r in self.conn.execute(
            "SELECT channel_id FROM campaign_channels WHERE campaign_id=? ORDER BY added_at", (int(campaign_id),))]

    def _check_channel_free(self, channel_id: object, campaign_id: Optional[int] = None) -> None:
        r = self.conn.execute("SELECT campaign_id FROM campaign_channels WHERE channel_id=?",
                              (str(channel_id),)).fetchone()
        if r and int(r[0]) != campaign_id:
            raise InvalidState(Msg("channel_taken"))

    def _link(self, campaign_id: int, actor_id: object, channel_id: object, guild_id: Optional[object]) -> None:
        self.conn.execute("INSERT INTO campaign_channels (channel_id, campaign_id, guild_id, added_by) "
                          "VALUES (?, ?, ?, ?)", (str(channel_id), int(campaign_id),
                                                   None if guild_id is None else str(guild_id), str(actor_id)))
        self._log(campaign_id, actor_id, "channel_link", new=str(channel_id))

    def link_channel(self, actor_id: object, campaign_id: int, channel_id: object,
                     guild_id: Optional[object] = None) -> None:
        self._require(campaign_id, actor_id, (ROLE_KEEPER,))
        self._check_channel_free(channel_id, int(campaign_id))
        if str(channel_id) in self.channels(actor_id, campaign_id):
            return
        with self._tx():
            self._link(campaign_id, actor_id, channel_id, guild_id)

    def unlink_channel(self, actor_id: object, campaign_id: int, channel_id: object) -> None:
        self._require(campaign_id, actor_id, (ROLE_KEEPER,))
        with self._tx():
            n = self.conn.execute("DELETE FROM campaign_channels WHERE channel_id=? AND campaign_id=?",
                                  (str(channel_id), int(campaign_id))).rowcount
            if not n:
                raise NotFound(Msg("channel_not_linked"))
            self._log(campaign_id, actor_id, "channel_unlink", old=str(channel_id))

    # --- membership ----------------------------------------------------------------------
    def _set_member(self, campaign_id: int, user_id: object, role: str, status: str,
                    invited_by: Optional[object] = None, pending: Optional[int] = None) -> None:
        self.conn.execute(
            """
            INSERT INTO campaign_members (campaign_id, user_id, role, status, invited_by, pending_investigator_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, user_id) DO UPDATE SET role=excluded.role, status=excluded.status,
              invited_by=COALESCE(excluded.invited_by, campaign_members.invited_by),
              pending_investigator_id=excluded.pending_investigator_id,
              updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (int(campaign_id), str(user_id), role, status, None if invited_by is None else str(invited_by), pending),
        )

    def invite(self, actor_id: object, campaign_id: int, user_id: object) -> str:
        """Invite a player, or approve them if they already asked to join. Returns the new status."""
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        if str(user_id) == str(actor_id):
            raise InvalidState(Msg("cannot_invite_self"))
        m = self._member(campaign_id, user_id)
        if m and m.status == "active":
            raise InvalidState(Msg("already_member"))
        if m and m.status == "requested":
            return self.approve(actor_id, campaign_id, user_id)
        with self._tx():
            self._set_member(campaign_id, user_id, ROLE_PLAYER, "invited", invited_by=actor_id)
            self._log(campaign_id, actor_id, "invite", new=str(user_id))
        return "invited"

    def join(self, actor_id: object, campaign_id: int, investigator_id: int) -> str:
        """A player joins with one of their own investigators.

        Invited players become active immediately; anyone else creates a
        request that a Keeper must approve. Returns 'active' or 'requested'.
        """
        camp = self._campaign(campaign_id)
        if camp is None or camp.status != "active":
            raise NotFound(Msg("campaign_not_found"))
        inv = self.investigators.get(actor_id, investigator_id, include_archived=False)   # ownership
        if inv.status != STATUS_READY:
            raise InvalidState(Msg("join_needs_ready"))
        m = self._member(campaign_id, actor_id)
        if m and m.status == "active" and m.role in KEEPER_ROLES:
            raise InvalidState(Msg("keepers_do_not_join"))
        current = self._active_participant_of_user(campaign_id, actor_id)
        if current is not None:
            raise InvalidState(Msg("already_participating"))
        if m and m.status in ("invited", "active"):
            with self._tx():
                self._set_member(campaign_id, actor_id, ROLE_PLAYER, "active")
                self._activate(campaign_id, actor_id, inv, actor_id)
            return "active"
        with self._tx():
            self._set_member(campaign_id, actor_id, ROLE_PLAYER, "requested", pending=inv.id)
            self._log(campaign_id, actor_id, "request", investigator_id=inv.id)
        return "requested"

    def _activate(self, campaign_id: int, user_id: object, inv: Investigator, actor_id: object) -> None:
        """Create or reactivate the participation; snapshot personal values only on first join."""
        p = self._participant(campaign_id, inv.id)
        if p is None:
            self.conn.execute(
                "INSERT INTO campaign_investigators (campaign_id, investigator_id, user_id, hp, mp, san, luck) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(campaign_id), inv.id, str(user_id), inv.hp, inv.mp, inv.san, inv.luck))
        else:
            self.conn.execute("UPDATE campaign_investigators SET status='active', "
                              "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                              "WHERE campaign_id=? AND investigator_id=?", (int(campaign_id), inv.id))
        self._log(campaign_id, actor_id, "join", investigator_id=inv.id, new=str(user_id))

    def pending(self, actor_id: object, campaign_id: int) -> List[Tuple[Member, Optional[Investigator]]]:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        rows = self.conn.execute("SELECT user_id FROM campaign_members WHERE campaign_id=? AND status='requested' "
                                 "ORDER BY updated_at", (int(campaign_id),)).fetchall()
        out = []
        for (uid,) in rows:
            m = self._member(campaign_id, uid)
            inv = repo.get(self.conn, m.pending_investigator_id) if m and m.pending_investigator_id else None
            out.append((m, inv))
        return out

    def approve(self, actor_id: object, campaign_id: int, user_id: object) -> str:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        m = self._member(campaign_id, user_id)
        if m is None or m.status != "requested":
            raise NotFound(Msg("no_request"))
        inv = repo.get(self.conn, m.pending_investigator_id) if m.pending_investigator_id else None
        if inv is None or inv.owner_user_id != str(user_id) or inv.is_archived or inv.status != STATUS_READY:
            raise InvalidState(Msg("request_investigator_unavailable"))
        if self._active_participant_of_user(campaign_id, user_id) is not None:
            raise InvalidState(Msg("already_participating"))
        with self._tx():
            self._set_member(campaign_id, user_id, ROLE_PLAYER, "active", invited_by=actor_id)
            self._activate(campaign_id, user_id, inv, actor_id)
        return "active"

    def deny(self, actor_id: object, campaign_id: int, user_id: object) -> None:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        m = self._member(campaign_id, user_id)
        if m is None or m.status != "requested":
            raise NotFound(Msg("no_request"))
        with self._tx():
            self._set_member(campaign_id, user_id, ROLE_PLAYER, "removed")
            self._log(campaign_id, actor_id, "deny", new=str(user_id))

    def remove_player(self, actor_id: object, campaign_id: int, user_id: object,
                      reason: Optional[str] = None) -> None:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        m = self._member(campaign_id, user_id)
        if m is None or m.status == "removed":
            raise NotFound(Msg("not_a_member"))
        if m.role in KEEPER_ROLES:
            raise InvalidState(Msg("remove_keeper_via_assistant"))
        with self._tx():
            self._leave(campaign_id, user_id, actor_id, "remove", self._clean_reason(reason))

    def leave(self, actor_id: object, campaign_id: int) -> None:
        camp, m = self.get(actor_id, campaign_id)
        if m.role == ROLE_KEEPER:
            raise InvalidState(Msg("primary_cannot_leave"))
        with self._tx():
            self._leave(campaign_id, actor_id, actor_id, "leave", None)

    def _leave(self, campaign_id: int, user_id: object, actor_id: object, kind: str, reason: Optional[str]) -> None:
        p = self._active_participant_of_user(campaign_id, user_id)
        if p is not None:
            self.conn.execute("UPDATE campaign_investigators SET status='left', "
                              "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                              "WHERE campaign_id=? AND investigator_id=?", (int(campaign_id), p.investigator_id))
        m = self._member(campaign_id, user_id)
        self._set_member(campaign_id, user_id, m.role if m else ROLE_PLAYER, "removed")
        self._log(campaign_id, actor_id, kind, investigator_id=p.investigator_id if p else None,
                  old=str(user_id), reason=reason)

    def set_assistant(self, actor_id: object, campaign_id: int, user_id: object, enabled: bool) -> None:
        self._require(campaign_id, actor_id, (ROLE_KEEPER,))
        if str(user_id) == str(actor_id):
            raise InvalidState(Msg("cannot_change_own_role"))
        m = self._member(campaign_id, user_id)
        with self._tx():
            if enabled:
                if self._active_participant_of_user(campaign_id, user_id) is not None:
                    raise InvalidState(Msg("assistant_is_player"))
                self._set_member(campaign_id, user_id, ROLE_ASSISTANT, "active", invited_by=actor_id)
                self._log(campaign_id, actor_id, "assistant_add", new=str(user_id))
            else:
                if m is None or m.role != ROLE_ASSISTANT or m.status != "active":
                    raise NotFound(Msg("not_an_assistant"))
                self._set_member(campaign_id, user_id, ROLE_ASSISTANT, "removed")
                self._log(campaign_id, actor_id, "assistant_remove", old=str(user_id))

    def members(self, actor_id: object, campaign_id: int) -> List[Member]:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        rows = self.conn.execute("SELECT user_id FROM campaign_members WHERE campaign_id=? AND status='active' "
                                 "ORDER BY role, updated_at", (int(campaign_id),)).fetchall()
        return [self._member(campaign_id, r[0]) for r in rows]  # type: ignore[misc]

    # --- roster and campaign state -------------------------------------------------------
    def _view(self, p: Participant) -> Investigator:
        inv = repo.get(self.conn, p.investigator_id)
        if inv is None:   # pragma: no cover - FK cascade removes participants of deleted investigators
            raise NotFound(Msg("campaign_not_found"))
        return campaign_view(inv, p)

    def conditions(self, campaign_id: int, investigator_id: int) -> Tuple[Condition, ...]:
        """Internal read: callers have already authorized (Keeper ``entry`` or the owner's own ``context``)."""
        rows = self.conn.execute(
            "SELECT condition_id, campaign_id, investigator_id, label, added_by, added_at FROM campaign_conditions "
            "WHERE campaign_id=? AND investigator_id=? AND removed_at IS NULL ORDER BY condition_id",
            (int(campaign_id), int(investigator_id))).fetchall()
        return tuple(Condition(int(r[0]), int(r[1]), int(r[2]), r[3], r[4], r[5]) for r in rows)

    def roster(self, actor_id: object, campaign_id: int) -> List[RosterEntry]:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        rows = self.conn.execute(
            "SELECT investigator_id FROM campaign_investigators WHERE campaign_id=? AND status='active' "
            "ORDER BY joined_at", (int(campaign_id),)).fetchall()
        out = []
        for (iid,) in rows:
            p = self._participant(campaign_id, iid)
            out.append(RosterEntry(p, self._view(p), self.conditions(campaign_id, iid)))  # type: ignore[arg-type]
        return out

    def entry(self, actor_id: object, campaign_id: int, investigator_id: int) -> RosterEntry:
        """One participating investigator (Keepers only, active participants only)."""
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        p = self._participant(campaign_id, investigator_id)
        if p is None or p.status != "active":
            raise NotFound(Msg("not_in_campaign"))
        return RosterEntry(p, self._view(p), self.conditions(campaign_id, investigator_id))

    def preview_adjust(self, actor_id: object, campaign_id: int, investigator_id: int,
                       values: Dict[str, int]) -> List[ResourceChange]:
        """Validate new absolute values; nothing is saved."""
        entry = self.entry(actor_id, campaign_id, investigator_id)
        inv = entry.investigator
        changes = []
        for field, new in values.items():
            if field not in RESOURCES:
                raise ValidationError(Msg("unknown_resource", field=field))
            maximum = resource_max(inv, field)
            if maximum is None:
                raise InvalidState(Msg("resource_unknown_max", field=field.upper()))
            if not isinstance(new, int) or not 0 <= new <= maximum:
                raise ValidationError(Msg("resource_out_of_range", field=field.upper(), max=maximum,
                                          value=new))
            old = resource_value(inv, field)
            if old != new:
                changes.append(ResourceChange(field, old, new, maximum))
        return changes

    def adjust(self, actor_id: object, campaign_id: int, investigator_id: int, values: Dict[str, int],
               reason: Optional[str] = None, confirmed: bool = False,
               expected: Optional[Dict[str, Optional[int]]] = None) -> List[ResourceChange]:
        """Set campaign values (absolute). Decreases and reaching 0 require ``confirmed``.

        ``expected`` holds the values the Keeper saw when previewing; if any has
        changed since (another Keeper, a revert), nothing is saved.
        """
        reason = self._clean_reason(reason)
        if expected:
            inv = self.entry(actor_id, campaign_id, investigator_id).investigator
            for field, seen in expected.items():
                current = resource_value(inv, field)
                if current != seen:
                    raise InvalidState(Msg("changed_while_confirming", field=field.upper(), current=current,
                                           expected=seen))
        changes = self.preview_adjust(actor_id, campaign_id, investigator_id, values)
        if any(c.consequential for c in changes) and not confirmed:
            raise InvalidState(Msg("confirm_needed"))
        if not changes:
            return []
        with self._tx():
            for c in changes:
                self.conn.execute(f"UPDATE campaign_investigators SET {c.field}=?, "
                                  f"updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                                  f"WHERE campaign_id=? AND investigator_id=?",
                                  (c.new, int(campaign_id), int(investigator_id)))
                self._log(campaign_id, actor_id, "resource", investigator_id, c.field, c.old, c.new, reason)
        return changes

    def add_condition(self, actor_id: object, campaign_id: int, investigator_id: int, label: object,
                      reason: Optional[str] = None) -> Condition:
        self.entry(actor_id, campaign_id, investigator_id)
        text = " ".join(str(label or "").split())
        if not text or len(text) > CONDITION_MAX_LEN:
            raise ValidationError(Msg("condition_invalid", max=CONDITION_MAX_LEN))
        active = self.conditions(campaign_id, investigator_id)
        if any(c.label.lower() == text.lower() for c in active):
            raise InvalidState(Msg("condition_duplicate"))
        if len(active) >= MAX_CONDITIONS:
            raise InvalidState(Msg("condition_limit", max=MAX_CONDITIONS))
        with self._tx():
            cur = self.conn.execute("INSERT INTO campaign_conditions (campaign_id, investigator_id, label, added_by) "
                                    "VALUES (?, ?, ?, ?)", (int(campaign_id), int(investigator_id), text,
                                                            str(actor_id)))
            cond_id = int(cur.lastrowid)
            self._log(campaign_id, actor_id, "condition_add", investigator_id, "condition", None, text,
                      self._clean_reason(reason), ref_id=cond_id)
        return next(c for c in self.conditions(campaign_id, investigator_id) if c.id == cond_id)

    def remove_condition(self, actor_id: object, campaign_id: int, condition_id: int,
                         reason: Optional[str] = None) -> None:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        r = self.conn.execute("SELECT investigator_id, label FROM campaign_conditions WHERE condition_id=? "
                              "AND campaign_id=? AND removed_at IS NULL", (int(condition_id), int(campaign_id))
                              ).fetchone()
        if r is None:
            raise NotFound(Msg("condition_not_found"))
        self.entry(actor_id, campaign_id, int(r[0]))
        with self._tx():
            self.conn.execute("UPDATE campaign_conditions SET removed_by=?, "
                              "removed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE condition_id=?",
                              (str(actor_id), int(condition_id)))
            self._log(campaign_id, actor_id, "condition_remove", int(r[0]), "condition", r[1], None,
                      self._clean_reason(reason), ref_id=int(condition_id))

    # --- history and reverts -----------------------------------------------------------------
    def _event(self, campaign_id: int, event_id: int) -> Optional[Event]:
        r = self.conn.execute(
            "SELECT event_id, campaign_id, investigator_id, actor_user_id, kind, field, old_value, new_value, reason, "
            "ref_id, reverts_event_id, reverted_by_event_id, created_at FROM campaign_events "
            "WHERE campaign_id=? AND event_id=?", (int(campaign_id), int(event_id))).fetchone()
        return Event(*r) if r else None

    def history(self, actor_id: object, campaign_id: int, investigator_id: Optional[int] = None,
                limit: int = 15) -> List[Event]:
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        sql = ("SELECT event_id FROM campaign_events WHERE campaign_id=?"
               + (" AND investigator_id=?" if investigator_id is not None else "")
               + " ORDER BY event_id DESC LIMIT ?")
        args = (int(campaign_id),) + ((int(investigator_id),) if investigator_id is not None else ()) + (int(limit),)
        return [self._event(campaign_id, r[0]) for r in self.conn.execute(sql, args)]  # type: ignore[misc]

    def revert(self, actor_id: object, campaign_id: int, event_id: int, reason: Optional[str] = None) -> Event:
        """Undo one resource/condition event by recording a new, opposite event.

        Refused if the value changed again since (so a later change is never
        silently overwritten), if the event was already reverted, or if it is
        itself a revert.
        """
        self._require(campaign_id, actor_id, KEEPER_ROLES)
        reason = self._clean_reason(reason)
        ev = self._event(campaign_id, event_id)
        if ev is None:
            raise NotFound(Msg("event_not_found"))
        if ev.reverted_by_event_id is not None:
            raise InvalidState(Msg("already_reverted"))
        if not ev.revertible:
            raise InvalidState(Msg("not_revertible"))
        self.entry(actor_id, campaign_id, ev.investigator_id)  # participant must still be active
        with self._tx():
            if ev.kind == "resource":
                p = self._participant(campaign_id, ev.investigator_id)
                current = getattr(p, ev.field)
                if str(current) != ev.new_value:
                    raise InvalidState(Msg("changed_since", field=ev.field.upper(), current=current,
                                           expected=ev.new_value))
                old = None if ev.old_value in (None, "None") else int(ev.old_value)
                self.conn.execute(f"UPDATE campaign_investigators SET {ev.field}=? WHERE campaign_id=? "
                                  f"AND investigator_id=?", (old, int(campaign_id), ev.investigator_id))
                new_id = self._log(campaign_id, actor_id, "revert", ev.investigator_id, ev.field, current, old,
                                   reason, reverts=ev.id)
            elif ev.kind == "condition_add":
                n = self.conn.execute("UPDATE campaign_conditions SET removed_by=?, "
                                      "removed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                                      "WHERE condition_id=? AND removed_at IS NULL", (str(actor_id), ev.ref_id)).rowcount
                if not n:
                    raise InvalidState(Msg("changed_since_condition"))
                new_id = self._log(campaign_id, actor_id, "revert", ev.investigator_id, "condition", ev.new_value,
                                   None, reason, ref_id=ev.ref_id, reverts=ev.id)
            else:  # condition_remove
                active = self.conditions(campaign_id, ev.investigator_id)
                if any(c.label.lower() == (ev.old_value or "").lower() for c in active):
                    raise InvalidState(Msg("changed_since_condition"))
                self.conn.execute("UPDATE campaign_conditions SET removed_by=NULL, removed_at=NULL "
                                  "WHERE condition_id=?", (ev.ref_id,))
                new_id = self._log(campaign_id, actor_id, "revert", ev.investigator_id, "condition", None,
                                   ev.old_value, reason, ref_id=ev.ref_id, reverts=ev.id)
            self.conn.execute("UPDATE campaign_events SET reverted_by_event_id=? WHERE event_id=?",
                              (new_id, ev.id))
        return self._event(campaign_id, new_id)  # type: ignore[return-value]

    # --- rules ---------------------------------------------------------------------------------
    def rules(self, actor_id: object, campaign_id: int) -> Tuple[int, CampaignRules]:
        self.get(actor_id, campaign_id)
        return self._rules(campaign_id)

    def _rules(self, campaign_id: int) -> Tuple[int, CampaignRules]:
        r = self.conn.execute("SELECT version, rules_json FROM campaign_rules WHERE campaign_id=? "
                              "ORDER BY version DESC LIMIT 1", (int(campaign_id),)).fetchone()
        if r is None:  # pragma: no cover - created with every campaign
            return 0, RAW_PROFILE
        return int(r[0]), from_json(r[1])

    def channel_rules(self, campaign_id: int) -> Tuple[int, CampaignRules]:
        """Rules applied to every roll made in the campaign's channels (no member data exposed)."""
        return self._rules(campaign_id)

    def set_rules(self, actor_id: object, campaign_id: int, **changes: object) -> Tuple[int, CampaignRules]:
        self._require(campaign_id, actor_id, (ROLE_KEEPER,))
        version, current = self._rules(campaign_id)
        try:
            new = validate({**dataclasses.asdict(current), **{k: v for k, v in changes.items() if v is not None}})
        except RulesError as e:
            raise ValidationError(e.args[0]) from None
        if new == current:
            return version, current
        with self._tx():
            self.conn.execute("INSERT INTO campaign_rules (campaign_id, version, rules_json, created_by) "
                              "VALUES (?, ?, ?, ?)", (int(campaign_id), version + 1, new.to_json(), str(actor_id)))
            self._log(campaign_id, actor_id, "rules", field="rules", old=current.to_json(), new=new.to_json())
        return version + 1, new

    # --- campaign context for players ------------------------------------------------------------
    def context(self, user_id: object, channel_id: Optional[object]) -> Optional[CampaignContext]:
        """Campaign context when ``user_id`` plays in the campaign linked to ``channel_id``."""
        camp = self.campaign_for_channel(channel_id)
        if camp is None or self.role_of(camp.id, user_id) != ROLE_PLAYER:
            return None
        p = self._active_participant_of_user(camp.id, user_id)
        if p is None:
            return None
        inv = repo.get(self.conn, p.investigator_id)
        if inv is None or inv.is_archived or inv.owner_user_id != str(user_id):
            return None
        version, rules = self._rules(camp.id)
        return CampaignContext(camp, campaign_view(inv, p), p, rules, version)

    def participations(self, investigator_id: int) -> List[Campaign]:
        rows = self.conn.execute("SELECT campaign_id FROM campaign_investigators WHERE investigator_id=? "
                                 "AND status='active'", (int(investigator_id),)).fetchall()
        return [self._campaign(int(r[0])) for r in rows]  # type: ignore[misc]

    def record_roll(self, ctx: CampaignContext, summary: str, roll_id: Optional[int] = None,
                    commit: bool = True) -> int:
        """Private rolls are logged (with their roll ID) so the Keepers can see them in the history."""
        if not commit:
            return self._log(ctx.campaign.id, ctx.participant.user_id, "roll", ctx.participant.investigator_id,
                             "roll", None, summary[:200], ref_id=roll_id)
        with self._tx():
            return self._log(ctx.campaign.id, ctx.participant.user_id, "roll", ctx.participant.investigator_id,
                             "roll", None, summary[:200], ref_id=roll_id)

    def participant_luck(self, campaign_id: int, investigator_id: int, user_id: object) -> Optional[int]:
        """Campaign Luck of ``user_id``'s own active participation (None if not participating)."""
        r = self.conn.execute("SELECT luck FROM campaign_investigators WHERE campaign_id=? AND investigator_id=? "
                              "AND user_id=? AND status='active'",
                              (int(campaign_id), int(investigator_id), str(user_id))).fetchone()
        return None if r is None else r[0]

    def spend_luck_in_tx(self, user_id: object, campaign_id: int, investigator_id: int, amount: int,
                         expected_luck: int, roll_id: int) -> int:
        """Deduct campaign Luck for a Luck spend on a roll. Runs inside the caller's transaction.

        The update only applies if Luck still equals ``expected_luck``; otherwise
        nothing changes and the caller rolls back. Logged as ``luck_spend``,
        which is not Keeper-revertible because the roll result depends on it.
        """
        cur = self.conn.execute(
            "UPDATE campaign_investigators SET luck=luck-?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE campaign_id=? AND investigator_id=? AND user_id=? AND status='active' AND luck=? AND luck>=?",
            (int(amount), int(campaign_id), int(investigator_id), str(user_id), int(expected_luck), int(amount)))
        if cur.rowcount != 1:
            raise InvalidState(Msg("luck_changed"))
        new = int(expected_luck) - int(amount)
        self._log(campaign_id, user_id, "luck_spend", investigator_id, "luck", expected_luck, new, None,
                  ref_id=int(roll_id))
        return new


class _Transaction:
    """Commit on success, roll back on any exception (the service's unit of work)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False


__all__ = ["Campaign", "CampaignContext", "CampaignService", "Condition", "Event", "Forbidden", "KEEPER_ROLES",
           "Member", "Participant", "RESOURCES", "ResourceChange", "RosterEntry", "campaign_view",
           "resource_max", "resource_value"]
