"""Milestone 3B1: persistent roll records, required difficulty and Luck spending."""
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from cocbot.campaigns.checks import plan_check
from cocbot.campaigns.rules import CampaignRules, from_json
from cocbot.campaigns.service import CampaignService
from cocbot.characters.service import InvalidState, InvestigatorService, NotFound, ValidationError
from cocbot.mechanics import dice
from cocbot.mechanics.checks import SuccessLevel
from cocbot.rolls.rules import (
    LUCK_WINDOW_SECONDS,
    RollFacts,
    luck_adjusted_level,
    luck_eligibility,
    meets,
    threshold,
)
from cocbot.rolls.service import RollService
from tests.helpers import SeqRandom, fresh_db

KEEPER, ALICE, BOB = "100", "1001", "1002"
GUILD, TABLE, LOUNGE, OTHER_TABLE = 42, 5001, 5999, 5002
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


def d100(*values):
    """Queue d100 results for the next rolls (units die first, then tens)."""
    seq = []
    for v in values:
        v = 0 if v == 100 else v
        seq += [v % 10, v // 10]
    dice.set_rng(SeqRandom(seq))


def ready(inv_svc, user, name):
    inv = inv_svc.create(user, name)
    inv_svc.set_characteristics(user, inv.id, FULL)
    inv = inv_svc.finalize(user, inv.id)
    inv_svc.select(user, inv.id, GUILD)
    return inv


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.inv = InvestigatorService(self.conn)
        self.camps = CampaignService(self.conn)
        self.rolls = RollService(self.conn)
        self.ada = ready(self.inv, ALICE, "Ada")

    def tearDown(self):
        dice.set_rng(__import__("random").Random())
        self.conn.close()

    def check(self, user, text, natural, channel=LOUNGE, difficulty="regular", bp=0, svc=None):
        d100(natural)
        plan = plan_check(self.conn, user, GUILD, channel, text)
        return (svc or self.rolls).roll_check(plan, user, guild_id=GUILD, channel_id=channel,
                                              bonus_penalty=bp, difficulty=difficulty)

    def personal_luck(self, inv_id=None):
        return self.conn.execute("SELECT luck FROM investigators WHERE investigator_id=?",
                                 (inv_id or self.ada.id,)).fetchone()[0]

    def campaign(self, luck_spending=True, channel=TABLE, keeper=KEEPER, name="Masks"):
        camp = self.camps.create(keeper, name, channel_id=channel, guild_id=GUILD)
        self.camps.invite(keeper, camp.id, ALICE)
        self.camps.join(ALICE, camp.id, self.ada.id)
        if luck_spending:
            self.camps.set_rules(keeper, camp.id, luck_spending=True)
        return camp

    def campaign_luck(self, camp):
        return self.camps.participant_luck(camp.id, self.ada.id, ALICE)


class DifficultyTests(unittest.TestCase):
    def test_meets(self):
        L = SuccessLevel
        self.assertTrue(meets(L.CRITICAL, "extreme"))
        self.assertTrue(meets(L.EXTREME, "hard"))
        self.assertFalse(meets(L.HARD, "extreme"))
        self.assertTrue(meets(L.SUCCESS, "regular"))
        self.assertFalse(meets(L.SUCCESS, "hard"))
        self.assertFalse(meets(L.FAIL, "regular"))
        self.assertFalse(meets(L.FUMBLE, "regular"))

    def test_thresholds(self):
        self.assertEqual([threshold(60, d) for d in ("regular", "hard", "extreme")], [60, 30, 12])
        self.assertEqual(threshold(4, "extreme"), 0)

    def test_luck_never_makes_a_critical(self):
        from cocbot.mechanics.checks import CheckRules
        self.assertEqual(luck_adjusted_level(1, 60, CheckRules()), SuccessLevel.EXTREME)
        self.assertEqual(luck_adjusted_level(4, 60, CheckRules(critical_max=5)), SuccessLevel.EXTREME)


class RecordTests(Base):
    def test_record_captures_everything(self):
        roll = self.check(ALICE, "STR", 62, difficulty="hard", bp=0)
        self.assertEqual((roll.user_id, roll.investigator_id, roll.investigator_name), (ALICE, self.ada.id, "Ada"))
        self.assertEqual((roll.subject_kind, roll.subject_key, roll.target, roll.difficulty),
                         ("characteristic", "STR", 60, "hard"))
        self.assertEqual((roll.natural, roll.natural_level, roll.effective, roll.luck_spent),
                         (62, SuccessLevel.FAIL, 62, 0))
        self.assertEqual((roll.campaign_id, roll.rules_version, roll.visibility, roll.luck_scope),
                         (None, None, "public", "personal"))
        self.assertTrue(roll.rules.luck_spending)                 # standalone profile
        self.assertEqual(roll.dice, {"candidates": [62], "tens": [6], "ones": 2})
        self.assertTrue(roll.created_at)

    def test_bonus_dice_recorded(self):
        dice.set_rng(SeqRandom([5, 7, 2, 9]))                     # units 5; tens 7, 2, 9
        plan = plan_check(self.conn, ALICE, GUILD, LOUNGE, "STR")
        roll = self.rolls.roll_check(plan, ALICE, channel_id=LOUNGE, bonus_penalty=2)
        self.assertEqual((roll.natural, roll.bonus_penalty), (25, 2))
        self.assertEqual(roll.dice["candidates"], [75, 25, 95])

    def test_numeric_check_without_investigator(self):
        roll = self.check(BOB, "45", 10)
        self.assertEqual((roll.investigator_id, roll.subject_kind, roll.luck_scope), (None, "numeric", "none"))

    def test_invalid_difficulty(self):
        with self.assertRaises(ValidationError):
            self.check(ALICE, "STR", 10, difficulty="heroic")

    def test_original_roll_is_permanent(self):
        roll = self.check(ALICE, "STR", 62)
        for sql in ("UPDATE rolls SET natural=10 WHERE roll_id=?", "UPDATE rolls SET target=99 WHERE roll_id=?",
                    "UPDATE rolls SET difficulty='hard' WHERE roll_id=?",
                    "UPDATE rolls SET dice_json='{}' WHERE roll_id=?",
                    "UPDATE rolls SET effective=10 WHERE roll_id=?",          # breaks effective = natural - luck
                    "UPDATE rolls SET luck_spent=2, effective=60 WHERE roll_id=?",   # without a version bump
                    "DELETE FROM rolls WHERE roll_id=?"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql, (roll.id,))
            self.conn.rollback()

    def test_pushed_attempt_must_reference_an_original(self):
        roll = self.check(ALICE, "STR", 62)
        cols = ("user_id, luck_scope, subject_kind, subject_label, target, dice_json, natural, natural_level, "
                "rules_json, visibility, attempt, parent_roll_id, effective, effective_level")
        row = (ALICE, "personal", "characteristic", "STR", 60, "{}", 40, "SUCCESS", CampaignRules().to_json(),
               "public", "pushed")
        with self.assertRaises(sqlite3.IntegrityError):                # no parent
            self.conn.execute(f"INSERT INTO rolls ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              row + (None, 40, "SUCCESS"))
        self.conn.execute(f"INSERT INTO rolls ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          row + (roll.id, 40, "SUCCESS"))
        pushed_id = self.conn.execute("SELECT max(roll_id) FROM rolls").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):                # cannot push a pushed attempt
            self.conn.execute(f"INSERT INTO rolls ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              row + (pushed_id, 40, "SUCCESS"))
        self.conn.rollback()

    def test_only_the_roller_can_read(self):
        roll = self.check(ALICE, "STR", 62)
        self.assertEqual(self.rolls.get(ALICE, roll.id).id, roll.id)
        with self.assertRaises(NotFound):
            self.rolls.get(BOB, roll.id)
        with self.assertRaises(NotFound):
            self.rolls.events(BOB, roll.id)
        with self.assertRaises(NotFound):
            self.rolls.get(ALICE, 9999)
        self.assertEqual(self.rolls.recent(BOB), [])
        self.assertEqual([r.id for r in self.rolls.recent(ALICE)], [roll.id])

    def test_persistence_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            ada = ready(InvestigatorService(conn), ALICE, "Ada")
            d100(62)
            roll = RollService(conn).roll_check(plan_check(conn, ALICE, GUILD, LOUNGE, "STR"), ALICE)
            RollService(conn).spend_luck(ALICE, roll.id, "regular", request_id="i-1")
            conn.close()
            conn2 = fresh_db(path)
            again = RollService(conn2).get(ALICE, roll.id)
            self.assertEqual((again.natural, again.effective, again.luck_spent, again.effective_level),
                             (62, 60, 2, SuccessLevel.SUCCESS))
            self.assertEqual([e.kind for e in RollService(conn2).events(ALICE, roll.id)], ["luck_spent"])
            self.assertEqual(InvestigatorService(conn2).get(ALICE, ada.id).luck, 48)
            conn2.close()


class TargetBoundaryTests(Base):
    """Targets of 0 and above 100 are valid character values and must record without crashing."""

    def test_zero_target(self):
        self.inv.set_skill(ALICE, self.ada.id, "Cthulhu Mythos", 0)
        crit = self.check(ALICE, "Cthulhu Mythos", 1)
        self.assertEqual((crit.target, crit.natural_level), (0, SuccessLevel.CRITICAL))      # 01 stays a Critical
        fail = self.check(ALICE, "Cthulhu Mythos", 50)
        self.assertEqual(fail.natural_level, SuccessLevel.FAIL)
        self.assertEqual(self.check(ALICE, "Cthulhu Mythos", 97).natural_level, SuccessLevel.FUMBLE)
        latest_fail = self.check(ALICE, "Cthulhu Mythos", 50)
        self.assertEqual(self.rolls.luck_status(ALICE, latest_fail.id).reason, "luck_unreachable")   # no level ≥ 1
        self.inv.set_resources(ALICE, self.ada.id, luck=0)
        zero_luck = self.check(ALICE, "luck", 40)
        self.assertEqual((zero_luck.target, zero_luck.natural_level), (0, SuccessLevel.FAIL))

    def test_target_above_100(self):
        self.inv.set_skill(ALICE, self.ada.id, "Spot Hidden", 150)
        r = self.check(ALICE, "Spot Hidden", 99)
        self.assertEqual((r.target, r.natural_level), (150, SuccessLevel.SUCCESS))
        self.assertEqual(self.check(ALICE, "Spot Hidden", 100).natural_level, SuccessLevel.FUMBLE)   # 100 stays
        self.assertEqual(self.check(ALICE, "Spot Hidden", 30).natural_level, SuccessLevel.EXTREME)
        up = self.check(ALICE, "Spot Hidden", 80)                         # Regular: Hard at 75 costs 5
        self.assertEqual([(o.difficulty, o.cost) for o in self.rolls.luck_status(ALICE, up.id).options],
                         [("hard", 5), ("extreme", 50)])
        self.assertEqual(self.rolls.spend_luck(ALICE, up.id, "hard").roll.effective, 75)


class EligibilityTests(Base):
    def reason(self, roll):
        return self.rolls.luck_status(ALICE, roll.id).reason

    def test_eligible_failed_skill_check_offers_reachable_levels(self):
        roll = self.check(ALICE, "STR", 62)
        status = self.rolls.luck_status(ALICE, roll.id)
        self.assertIsNone(status.reason)
        self.assertEqual([(o.difficulty, o.cost, o.effective, o.level) for o in status.options],
                         [("regular", 2, 60, SuccessLevel.SUCCESS), ("hard", 32, 30, SuccessLevel.HARD),
                          ("extreme", 50, 12, SuccessLevel.EXTREME)])

    def test_required_difficulty_limits_the_options(self):
        roll = self.check(ALICE, "STR", 45, difficulty="hard")             # regular success, hard required
        self.assertEqual([o.difficulty for o in self.rolls.luck_status(ALICE, roll.id).options], ["hard", "extreme"])
        self.assertFalse(roll.met)

    def test_ineligible_checks(self):
        cases = [("luck", 70, "luck_not_on_luck"), ("SAN", 70, "luck_not_on_san"),
                 ("STR", 100, "luck_not_on_fumble"), ("STR", 12, "luck_no_upgrade"),
                 ("STR", 1, "luck_no_upgrade")]
        for text, natural, reason in cases:
            with self.subTest(text=text, natural=natural):
                self.assertEqual(self.reason(self.check(ALICE, text, natural)), reason)
        self.assertEqual(self.rolls.luck_status(BOB, self.check(BOB, "45", 70).id).reason, "luck_needs_investigator")

    def test_successes_can_be_upgraded(self):
        # Regular success (45 vs 60): Hard costs 15, Extreme 33 — no "Regular" option, it is already met
        status = self.rolls.luck_status(ALICE, self.check(ALICE, "STR", 45).id)
        self.assertIsNone(status.reason)
        self.assertEqual([(o.difficulty, o.cost, o.level) for o in status.options],
                         [("hard", 15, SuccessLevel.HARD), ("extreme", 33, SuccessLevel.EXTREME)])
        # Hard success (25 vs 60): only Extreme
        status = self.rolls.luck_status(ALICE, self.check(ALICE, "STR", 25).id)
        self.assertEqual([(o.difficulty, o.cost) for o in status.options], [("extreme", 13)])
        # Required Extreme, rolled Regular: only Extreme counts
        status = self.rolls.luck_status(ALICE, self.check(ALICE, "STR", 45, difficulty="extreme").id)
        self.assertEqual([o.difficulty for o in status.options], ["extreme"])

    def test_upgrade_spend_keeps_natural_success_and_improvement(self):
        self.inv.set_skill(ALICE, self.ada.id, "Spot Hidden", 60)
        roll = self.check(ALICE, "Spot Hidden", 45)
        self.assertTrue(roll.improvement_eligible)
        r = self.rolls.spend_luck(ALICE, roll.id, "hard", request_id="u-1").roll
        self.assertEqual((r.natural, r.natural_level, r.effective, r.effective_level, r.luck_spent),
                         (45, SuccessLevel.SUCCESS, 30, SuccessLevel.HARD, 15))
        self.assertTrue(r.improvement_eligible)              # natural success, upgraded: check kept
        self.assertEqual(self.personal_luck(), 35)
        with self.assertRaises(InvalidState):                 # still one spend per roll
            self.rolls.spend_luck(ALICE, roll.id, "extreme", request_id="u-2")
        bought = self.check(ALICE, "Spot Hidden", 62)
        self.assertFalse(bought.improvement_eligible)
        r = self.rolls.spend_luck(ALICE, bought.id, "regular").roll
        self.assertTrue(r.met)
        self.assertFalse(r.improvement_eligible)              # failure turned into a success with Luck

    def test_improvement_checks_follow_the_natural_result_at_the_required_difficulty(self):
        self.inv.set_skill(ALICE, self.ada.id, "Spot Hidden", 60)
        hard_needed = self.check(ALICE, "Spot Hidden", 45, difficulty="hard")     # Regular success, Hard needed
        self.assertFalse(hard_needed.improvement_eligible)
        r = self.rolls.spend_luck(ALICE, hard_needed.id, "hard").roll
        self.assertTrue(r.met)
        self.assertFalse(r.improvement_eligible)              # the required level was bought with Luck
        self.assertTrue(self.check(ALICE, "Spot Hidden", 1).improvement_eligible)          # Critical
        self.assertFalse(self.check(ALICE, "Spot Hidden", 70).improvement_eligible)        # failure
        self.assertFalse(self.check(ALICE, "Spot Hidden", 100).improvement_eligible)       # fumble

    def test_only_skills_earn_improvement_checks(self):
        for text in ("STR", "DEX", "luck", "SAN"):
            with self.subTest(text=text):
                self.assertFalse(self.check(ALICE, text, 5).improvement_eligible)
        self.assertFalse(self.check(ALICE, "45", 5).improvement_eligible)
        self.assertTrue(self.check(ALICE, "Spot Hidden", 5).improvement_eligible)          # base value 25

    def test_low_skill_fumble_range(self):
        # Target below 50: 96-99 are fumbles too (RAW) and cannot be bought off
        self.inv.set_characteristics(ALICE, self.ada.id, {"STR": 40})
        self.assertEqual(self.reason(self.check(ALICE, "STR", 97)), "luck_not_on_fumble")

    def test_not_enough_luck(self):
        self.inv.set_resources(ALICE, self.ada.id, luck=1)
        self.assertEqual(self.reason(self.check(ALICE, "STR", 62)), "luck_not_enough")

    def test_unreachable_extreme(self):
        self.inv.set_characteristics(ALICE, self.ada.id, {"STR": 15})
        self.assertIsNone(self.reason(self.check(ALICE, "STR", 50, difficulty="extreme")))   # 47 Luck to reach 03
        self.inv.set_characteristics(ALICE, self.ada.id, {"STR": 4})                         # fifth of 4 is 0
        self.assertEqual(self.reason(self.check(ALICE, "STR", 50, difficulty="extreme")), "luck_unreachable")

    def test_only_latest_roll_and_time_window(self):
        first = self.check(ALICE, "STR", 62)
        self.check(ALICE, "DEX", 80)
        self.assertEqual(self.reason(first), "luck_not_latest")
        late = RollService(self.conn, clock=lambda: datetime.now(timezone.utc)
                           + timedelta(seconds=LUCK_WINDOW_SECONDS + 5))
        latest = self.rolls.recent(ALICE, 1)[0]
        self.assertEqual(late.luck_status(ALICE, latest.id).reason, "luck_expired")

    def test_pushed_attempts_are_ineligible(self):
        facts = RollFacts("skill", "climb", True, "pushed", 70, SuccessLevel.FAIL, 60, "regular", 0,
                          CampaignRules().to_check_rules(), True, "personal")
        self.assertEqual(luck_eligibility(facts, current_luck=50, spending_allowed_now=True, is_latest=True,
                                          age_seconds=1)[0], "luck_not_on_pushed")


class SpendTests(Base):
    def test_spend_personal_luck(self):
        roll = self.check(ALICE, "STR", 62)
        result = self.rolls.spend_luck(ALICE, roll.id, "regular", request_id="i-1")
        self.assertEqual((result.luck_before, result.luck_after), (50, 48))
        r = result.roll
        self.assertEqual((r.natural, r.natural_level, r.effective, r.effective_level, r.luck_spent),
                         (62, SuccessLevel.FAIL, 60, SuccessLevel.SUCCESS, 2))
        self.assertTrue(r.met)
        self.assertFalse(r.improvement_eligible)
        self.assertEqual(self.personal_luck(), 48)
        ev = self.rolls.events(ALICE, roll.id)[0]
        self.assertEqual((ev.old_effective, ev.new_effective, ev.luck_before, ev.luck_after), (62, 60, 50, 48))

    def test_spend_to_extreme_is_extreme_not_critical(self):
        self.inv.set_characteristics(ALICE, self.ada.id, {"STR": 5})
        roll = self.check(ALICE, "STR", 20, difficulty="extreme")
        r = self.rolls.spend_luck(ALICE, roll.id, "extreme").roll
        self.assertEqual((r.effective, r.effective_level), (1, SuccessLevel.EXTREME))

    def test_only_the_owner_can_spend(self):
        roll = self.check(ALICE, "STR", 62)
        with self.assertRaises(NotFound):
            self.rolls.spend_luck(BOB, roll.id, "regular")
        self.assertEqual(self.personal_luck(), 50)

    def test_single_spend_and_replay_is_idempotent(self):
        roll = self.check(ALICE, "STR", 62)
        first = self.rolls.spend_luck(ALICE, roll.id, "regular", request_id="i-1", expected_version=0)
        again = self.rolls.spend_luck(ALICE, roll.id, "regular", request_id="i-1", expected_version=0)
        self.assertTrue(again.replayed)
        self.assertEqual((again.luck_before, again.luck_after), (first.luck_before, first.luck_after))
        with self.assertRaises(InvalidState):                     # a second, different request
            self.rolls.spend_luck(ALICE, roll.id, "hard", request_id="i-2")
        with self.assertRaises(InvalidState):                     # a stale prompt
            self.rolls.spend_luck(ALICE, roll.id, "regular", request_id="i-3", expected_version=0)
        self.assertEqual(self.personal_luck(), 48)
        self.assertEqual(len(self.rolls.events(ALICE, roll.id)), 1)

    def test_request_id_cannot_be_reused_for_another_roll(self):
        roll = self.check(ALICE, "STR", 62)
        self.rolls.spend_luck(ALICE, roll.id, "regular", request_id="i-1")
        other = self.check(ALICE, "DEX", 75)
        with self.assertRaises(InvalidState):
            self.rolls.spend_luck(ALICE, other.id, "regular", request_id="i-1")
        self.assertEqual(self.personal_luck(), 48)

    def test_luck_changed_between_read_and_write_rolls_everything_back(self):
        roll = self.check(ALICE, "STR", 62)
        real = self.rolls._status

        def stale_status(r):
            status = real(r)
            self.conn.execute("UPDATE investigators SET luck=10 WHERE investigator_id=?", (self.ada.id,))
            return status
        self.rolls._status = stale_status
        with self.assertRaises(InvalidState):
            self.rolls.spend_luck(ALICE, roll.id, "regular", request_id="i-1")
        self.rolls._status = real
        after = self.rolls.get(ALICE, roll.id)
        self.assertEqual((after.luck_spent, after.effective, after.state_version), (0, 62, 0))
        self.assertEqual(self.personal_luck(), 50)                # the in-transaction change was rolled back too
        self.assertEqual(self.conn.execute("SELECT count(*) FROM roll_events").fetchone()[0], 0)

    def test_concurrent_spends_charge_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            ready(InvestigatorService(conn), ALICE, "Ada")
            d100(62)
            roll = RollService(conn).roll_check(plan_check(conn, ALICE, GUILD, LOUNGE, "STR"), ALICE)
            conn.close()
            outcomes = []
            barrier = threading.Barrier(4)

            def worker(n):
                c = sqlite3.connect(path, timeout=10)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA foreign_keys = ON")
                barrier.wait()
                try:
                    RollService(c).spend_luck(ALICE, roll.id, "regular", request_id=f"i-{n}")
                    outcomes.append("ok")
                except InvalidState:
                    outcomes.append("refused")
                finally:
                    c.close()
            threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sorted(outcomes), ["ok", "refused", "refused", "refused"])
            conn = fresh_db(path)
            self.assertEqual(conn.execute("SELECT luck FROM investigators").fetchone()[0], 48)
            self.assertEqual(conn.execute("SELECT count(*) FROM roll_events").fetchone()[0], 1)
            conn.close()

    def test_archived_investigator_cannot_spend(self):
        roll = self.check(ALICE, "STR", 62)
        self.inv.archive(ALICE, self.ada.id)
        with self.assertRaises(InvalidState):
            self.rolls.spend_luck(ALICE, roll.id, "regular")


class CampaignLuckTests(Base):
    def test_rule_off_by_default_in_campaigns(self):
        self.campaign(luck_spending=False)
        roll = self.check(ALICE, "STR", 62, channel=TABLE)
        self.assertEqual((roll.luck_scope, roll.rules_version), ("campaign", 1))
        self.assertEqual(self.rolls.luck_status(ALICE, roll.id).reason, "luck_rule_off")

    def test_campaign_luck_is_spent_not_personal(self):
        camp = self.campaign()
        self.camps.adjust(KEEPER, camp.id, self.ada.id, {"luck": 40}, confirmed=True)
        roll = self.check(ALICE, "STR", 62, channel=TABLE)
        self.assertEqual((roll.campaign_id, roll.rules_version), (camp.id, 2))
        result = self.rolls.spend_luck(ALICE, roll.id, "regular", request_id="i-1")
        self.assertEqual((result.luck_before, result.luck_after), (40, 38))
        self.assertEqual(self.campaign_luck(camp), 38)
        self.assertEqual(self.personal_luck(), 50)
        # The Keeper Console sees the same number and an audited, non-revertible event
        self.assertEqual(self.camps.entry(KEEPER, camp.id, self.ada.id).investigator.luck, 38)
        ev = next(e for e in self.camps.history(KEEPER, camp.id) if e.kind == "luck_spend")
        self.assertEqual((ev.field, ev.old_value, ev.new_value, ev.ref_id, ev.actor_user_id),
                         ("luck", "40", "38", roll.id, ALICE))
        self.assertFalse(ev.revertible)

    def test_other_campaign_untouched(self):
        camp = self.campaign()
        other = self.campaign(channel=OTHER_TABLE, keeper=BOB, name="Other")
        roll = self.check(ALICE, "STR", 62, channel=TABLE)
        self.rolls.spend_luck(ALICE, roll.id, "regular")
        self.assertEqual((self.campaign_luck(camp), self.campaign_luck(other)), (48, 50))

    def test_keeper_turning_the_rule_off_blocks_open_prompts(self):
        camp = self.campaign()
        roll = self.check(ALICE, "STR", 62, channel=TABLE)
        self.camps.set_rules(KEEPER, camp.id, luck_spending=False)
        with self.assertRaises(InvalidState):
            self.rolls.spend_luck(ALICE, roll.id, "regular")
        self.assertEqual(self.campaign_luck(camp), 50)

    def test_leaving_the_campaign_blocks_spending(self):
        camp = self.campaign()
        roll = self.check(ALICE, "STR", 62, channel=TABLE)
        self.camps.leave(ALICE, camp.id)
        self.assertEqual(self.rolls.luck_status(ALICE, roll.id).reason, "luck_unavailable")

    def test_keeper_adjustment_after_roll_uses_current_luck(self):
        camp = self.campaign()
        roll = self.check(ALICE, "STR", 62, channel=TABLE)
        self.camps.adjust(KEEPER, camp.id, self.ada.id, {"luck": 35}, confirmed=True)
        status = self.rolls.luck_status(ALICE, roll.id)
        self.assertEqual([o.difficulty for o in status.options], ["regular", "hard"])     # extreme costs 50
        self.assertEqual(self.rolls.spend_luck(ALICE, roll.id, "hard").luck_after, 3)

    def test_non_participant_in_campaign_channel_cannot_spend(self):
        self.campaign()
        bram = ready(self.inv, BOB, "Bram")
        roll = self.check(BOB, "STR", 62, channel=TABLE)
        self.assertEqual((roll.luck_scope, roll.investigator_id), ("none", bram.id))
        self.assertEqual(self.rolls.luck_status(BOB, roll.id).reason, "luck_needs_investigator")

    def test_private_rolls_are_logged_once_with_roll_id(self):
        camp = self.campaign()
        self.camps.set_rules(KEEPER, camp.id, roll_visibility="private")
        roll = self.check(ALICE, "STR", 62, channel=TABLE)
        self.assertEqual(roll.visibility, "private")
        events = [e for e in self.camps.history(KEEPER, camp.id) if e.kind == "roll"]
        self.assertEqual([e.ref_id for e in events], [roll.id])

    def test_rules_json_round_trip_and_old_profiles(self):
        self.assertFalse(from_json('{"schema": 1, "critical_max": 1, "fumble": "raw", '
                                   '"roll_visibility": "public"}').luck_spending)
        with self.assertRaises(ValidationError):
            camp = self.campaign(luck_spending=False)
            self.camps.set_rules(KEEPER, camp.id, luck_spending="yes")


if __name__ == "__main__":
    unittest.main()
