"""Milestone 3A: campaigns, Keeper permissions, campaign state, audit and rules."""
import os
import sqlite3
import tempfile
import unittest

from cocbot.campaigns.rules import CampaignRules, from_json
from cocbot.campaigns.service import CampaignService, Forbidden
from cocbot.characters.service import InvalidState, InvestigatorService, NotFound, ValidationError
from cocbot.mechanics.checks import SuccessLevel, success_level
from tests.helpers import fresh_db

KEEPER, ASSIST, ALICE, BOB, EVE = "100", "200", "1001", "1002", "6666"
GUILD, CH_A, CH_B, CH_OTHER = 42, 5001, 5002, 5999
# HP (55+65)/10 = 12, MP 45/5 = 9, SAN 45, Luck 50
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


def ready_investigator(inv_svc, user, name, **resources):
    inv = inv_svc.create(user, name)
    inv_svc.set_characteristics(user, inv.id, FULL)
    if resources:
        inv_svc.set_resources(user, inv.id, **resources)
    return inv_svc.finalize(user, inv.id)


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.inv = InvestigatorService(self.conn)
        self.svc = CampaignService(self.conn)
        self.ada = ready_investigator(self.inv, ALICE, "Ada")
        self.bram = ready_investigator(self.inv, BOB, "Bram")
        self.camp = self.svc.create(KEEPER, "Masks", channel_id=CH_A, guild_id=GUILD)

    def tearDown(self):
        self.conn.close()

    def seat(self, user, inv, camp=None):
        camp = camp or self.camp
        self.svc.invite(KEEPER if camp.id == self.camp.id else self.k2, camp.id, user)
        self.assertEqual(self.svc.join(user, camp.id, inv.id), "active")


class CampaignSetupTests(Base):
    def test_create_defaults(self):
        camp, member = self.svc.get(KEEPER, self.camp.id)
        self.assertEqual((camp.name, member.role, member.status), ("Masks", "keeper", "active"))
        self.assertEqual(self.svc.rules(KEEPER, self.camp.id), (1, CampaignRules()))
        self.assertEqual(self.svc.channels(KEEPER, self.camp.id), [str(CH_A)])
        self.assertEqual(self.svc.campaign_for_channel(CH_A).id, self.camp.id)

    def test_several_campaigns_per_server_one_campaign_per_channel(self):
        other = self.svc.create(BOB, "Horror on the Orient Express", channel_id=CH_B, guild_id=GUILD)
        self.assertNotEqual(other.id, self.camp.id)
        self.assertEqual(self.svc.campaign_for_channel(CH_B).id, other.id)
        with self.assertRaises(InvalidState):
            self.svc.create(BOB, "Third", channel_id=CH_A)
        with self.assertRaises(InvalidState):
            self.svc.link_channel(BOB, other.id, CH_A)

    def test_validation(self):
        for bad in ("", "   ", "x" * 81):
            with self.subTest(name=bad), self.assertRaises(ValidationError):
                self.svc.create(KEEPER, bad)


class MembershipTests(Base):
    def test_invite_then_join(self):
        self.assertEqual(self.svc.invite(KEEPER, self.camp.id, ALICE), "invited")
        self.assertEqual(self.svc.join(ALICE, self.camp.id, self.ada.id), "active")
        self.assertEqual([e.investigator.name for e in self.svc.roster(KEEPER, self.camp.id)], ["Ada"])

    def test_request_then_approve_or_deny(self):
        self.assertEqual(self.svc.join(ALICE, self.camp.id, self.ada.id), "requested")
        self.assertEqual(self.svc.roster(KEEPER, self.camp.id), [])
        pending = self.svc.pending(KEEPER, self.camp.id)
        self.assertEqual([(m.user_id, inv.name) for m, inv in pending], [(ALICE, "Ada")])
        self.assertEqual(self.svc.approve(KEEPER, self.camp.id, ALICE), "active")
        self.assertEqual(len(self.svc.roster(KEEPER, self.camp.id)), 1)
        self.svc.join(BOB, self.camp.id, self.bram.id)
        self.svc.deny(KEEPER, self.camp.id, BOB)
        self.assertEqual(self.svc.pending(KEEPER, self.camp.id), [])
        with self.assertRaises(NotFound):
            self.svc.approve(KEEPER, self.camp.id, BOB)

    def test_invite_of_requester_approves(self):
        self.svc.join(ALICE, self.camp.id, self.ada.id)
        self.assertEqual(self.svc.invite(KEEPER, self.camp.id, ALICE), "active")

    def test_join_rules(self):
        draft = self.inv.create(ALICE, "Drafty")
        with self.assertRaises(InvalidState):
            self.svc.join(ALICE, self.camp.id, draft.id)            # drafts cannot join
        with self.assertRaises(NotFound):
            self.svc.join(BOB, self.camp.id, self.ada.id)            # not Bob's investigator
        with self.assertRaises(InvalidState):
            self.svc.join(KEEPER, self.camp.id, ready_investigator(self.inv, KEEPER, "K").id)
        self.seat(ALICE, self.ada)
        second = ready_investigator(self.inv, ALICE, "Ada II")
        with self.assertRaises(InvalidState):
            self.svc.join(ALICE, self.camp.id, second.id)            # one active investigator per player
        with self.assertRaises(NotFound):
            self.svc.join(ALICE, 9999, self.ada.id)

    def test_database_rejects_foreign_participant(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO campaign_investigators (campaign_id, investigator_id, user_id) "
                              "VALUES (?, ?, ?)", (self.camp.id, self.ada.id, BOB))

    def test_leave_and_rejoin_keeps_campaign_state(self):
        self.seat(ALICE, self.ada)
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 5}, confirmed=True)
        self.svc.leave(ALICE, self.camp.id)
        self.assertEqual(self.svc.roster(KEEPER, self.camp.id), [])
        self.assertIsNone(self.svc.context(ALICE, CH_A))
        self.seat(ALICE, self.ada)
        self.assertEqual(self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator.hp, 5)

    def test_remove_player(self):
        self.seat(ALICE, self.ada)
        self.svc.remove_player(KEEPER, self.camp.id, ALICE, reason="moved away")
        with self.assertRaises(NotFound):
            self.svc.entry(KEEPER, self.camp.id, self.ada.id)
        with self.assertRaises(NotFound):
            self.svc.get(ALICE, self.camp.id)
        with self.assertRaises(InvalidState):
            self.svc.leave(KEEPER, self.camp.id)                     # primary Keeper cannot leave


class PermissionTests(Base):
    def setUp(self):
        super().setUp()
        self.seat(ALICE, self.ada)
        self.svc.set_assistant(KEEPER, self.camp.id, ASSIST, True)

    def keeper_actions(self, actor):
        c, i = self.camp.id, self.ada.id
        return {
            "roster": lambda: self.svc.roster(actor, c),
            "entry": lambda: self.svc.entry(actor, c, i),
            "pending": lambda: self.svc.pending(actor, c),
            "members": lambda: self.svc.members(actor, c),
            "adjust": lambda: self.svc.adjust(actor, c, i, {"hp": 11}, confirmed=True),
            "condition": lambda: self.svc.add_condition(actor, c, i, "Shaken"),
            "history": lambda: self.svc.history(actor, c),
            "invite": lambda: self.svc.invite(actor, c, EVE),
        }

    def test_outsiders_get_not_found_for_everything(self):
        for name, call in self.keeper_actions(EVE).items():
            with self.subTest(name), self.assertRaises(NotFound):
                call()
        with self.assertRaises(NotFound):
            self.svc.rules(EVE, self.camp.id)
        with self.assertRaises(NotFound):
            self.svc.set_rules(EVE, self.camp.id, critical_max=3)
        with self.assertRaises(NotFound):
            self.svc.roster(EVE, 9999)                               # same answer for unknown campaigns

    def test_players_cannot_use_keeper_actions(self):
        for name, call in self.keeper_actions(ALICE).items():
            with self.subTest(name), self.assertRaises(Forbidden):
                call()
        self.assertEqual(self.svc.rules(ALICE, self.camp.id)[0], 1)   # players may read the rules

    def test_assistant_powers(self):
        for name, call in self.keeper_actions(ASSIST).items():
            with self.subTest(name):
                call()
        for call in (lambda: self.svc.set_rules(ASSIST, self.camp.id, critical_max=3),
                     lambda: self.svc.link_channel(ASSIST, self.camp.id, CH_OTHER),
                     lambda: self.svc.set_assistant(ASSIST, self.camp.id, EVE, True)):
            with self.assertRaises(Forbidden):
                call()

    def test_revoked_assistant_loses_access_immediately(self):
        self.svc.roster(ASSIST, self.camp.id)
        self.svc.set_assistant(KEEPER, self.camp.id, ASSIST, False)
        with self.assertRaises(NotFound):
            self.svc.adjust(ASSIST, self.camp.id, self.ada.id, {"hp": 1}, confirmed=True)

    def test_keeper_cannot_reach_non_participants(self):
        # Bram is Bob's, not in the campaign: invisible to the Keeper
        with self.assertRaises(NotFound):
            self.svc.entry(KEEPER, self.camp.id, self.bram.id)
        with self.assertRaises(NotFound):
            self.svc.adjust(KEEPER, self.camp.id, self.bram.id, {"hp": 1}, confirmed=True)
        # and InvestigatorService still refuses the Keeper direct access
        with self.assertRaises(NotFound):
            self.inv.get(KEEPER, self.ada.id)

    def test_keeper_of_one_campaign_has_no_access_to_another(self):
        other = self.svc.create(BOB, "Other", channel_id=CH_B)
        self.svc.invite(BOB, other.id, ALICE)
        self.svc.join(ALICE, other.id, self.ada.id)
        with self.assertRaises(NotFound):
            self.svc.roster(KEEPER, other.id)
        with self.assertRaises(NotFound):
            self.svc.history(BOB, self.camp.id)


class StateIsolationTests(Base):
    def setUp(self):
        super().setUp()
        self.seat(ALICE, self.ada)
        self.k2 = BOB
        self.other = self.svc.create(BOB, "Other", channel_id=CH_B)
        self.seat(ALICE, self.ada, self.other)                       # same investigator, second campaign

    def test_adjustment_stays_in_its_campaign(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 3, "san": 30, "luck": 40}, confirmed=True)
        self.assertEqual(self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator.hp, 3)
        self.assertEqual(self.svc.entry(BOB, self.other.id, self.ada.id).investigator.hp, 12)
        personal = self.inv.get(ALICE, self.ada.id)
        self.assertEqual((personal.hp, personal.san, personal.luck), (12, 45, 50))

    def test_personal_changes_do_not_leak_into_campaign(self):
        self.inv.set_resources(ALICE, self.ada.id, hp=4, luck=20)
        e = self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator
        self.assertEqual((e.hp, e.luck), (12, 50))

    def test_maxima_come_from_permanent_sheet(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 12})
        self.inv.set_characteristics(ALICE, self.ada.id, {"CON": 25})     # max HP 12 -> 9
        e = self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator
        self.assertEqual((e.hp, e.derived().hp_max), (9, 9))

    def test_context_resolution(self):
        ctx = self.svc.context(ALICE, CH_A)
        self.assertEqual((ctx.campaign.id, ctx.investigator.id), (self.camp.id, self.ada.id))
        self.assertEqual(self.svc.context(ALICE, CH_B).campaign.id, self.other.id)
        self.assertIsNone(self.svc.context(ALICE, CH_OTHER))       # unlinked channel: personal behaviour
        self.assertIsNone(self.svc.context(ALICE, None))           # DMs
        self.assertIsNone(self.svc.context(KEEPER, CH_A))          # Keepers have no campaign investigator
        self.assertIsNone(self.svc.context(EVE, CH_A))             # non-members
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"san": 33}, confirmed=True)
        self.assertEqual(self.svc.context(ALICE, CH_A).investigator.san, 33)
        self.assertEqual(self.svc.context(ALICE, CH_B).investigator.san, 45)

    def test_archive_and_delete_blocked_while_playing(self):
        with self.assertRaises(InvalidState) as cm:
            self.inv.archive(ALICE, self.ada.id)
        self.assertIn("Masks", str(cm.exception))
        self.svc.leave(ALICE, self.camp.id)
        self.svc.leave(ALICE, self.other.id)
        self.inv.archive(ALICE, self.ada.id)

    def test_deleting_after_leaving_keeps_the_audit_trail(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 3}, confirmed=True)
        self.svc.leave(ALICE, self.camp.id)
        self.svc.leave(ALICE, self.other.id)
        self.inv.archive(ALICE, self.ada.id)
        self.inv.delete(ALICE, self.ada.id)
        events = self.svc.history(KEEPER, self.camp.id, limit=50)
        self.assertIn(("resource", "12", "3"), [(e.kind, e.old_value, e.new_value) for e in events])
        self.assertTrue(all(e.investigator_id is None for e in events))


class AdjustmentAndConditionTests(Base):
    def setUp(self):
        super().setUp()
        self.seat(ALICE, self.ada)

    def test_preview_and_confirmation(self):
        changes = self.svc.preview_adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 7, "mp": 9, "luck": 55})
        self.assertEqual([(c.field, c.old, c.new) for c in changes], [("hp", 12, 7), ("luck", 50, 55)])
        self.assertTrue(changes[0].consequential)
        self.assertFalse(changes[1].consequential)
        with self.assertRaises(InvalidState):
            self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 7})            # decrease, unconfirmed
        self.assertEqual(self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator.hp, 12)
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"luck": 55})             # increase: direct
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 7}, reason="Fell down the stairs",
                        confirmed=True)
        e = self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator
        self.assertEqual((e.hp, e.luck), (7, 55))

    def test_ranges(self):
        for values in ({"hp": 13}, {"mp": -1}, {"san": 100}, {"luck": 100}, {"str": 5}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.svc.adjust(KEEPER, self.camp.id, self.ada.id, values, confirmed=True)

    def test_events_record_actor_old_new_reason(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 7, "san": 40}, reason="Ghoul", confirmed=True)
        events = [e for e in self.svc.history(KEEPER, self.camp.id, self.ada.id) if e.kind == "resource"]
        self.assertEqual({(e.field, e.old_value, e.new_value, e.reason, e.actor_user_id) for e in events},
                         {("hp", "12", "7", "Ghoul", KEEPER), ("san", "45", "40", "Ghoul", KEEPER)})
        self.assertTrue(all(e.created_at for e in events))

    def test_conditions(self):
        c = self.svc.add_condition(KEEPER, self.camp.id, self.ada.id, "  Shaken ", reason="saw it")
        self.assertEqual(c.label, "Shaken")
        with self.assertRaises(InvalidState):
            self.svc.add_condition(KEEPER, self.camp.id, self.ada.id, "shaken")
        with self.assertRaises(ValidationError):
            self.svc.add_condition(KEEPER, self.camp.id, self.ada.id, "x" * 41)
        for n in range(9):
            self.svc.add_condition(KEEPER, self.camp.id, self.ada.id, f"C{n}")
        with self.assertRaises(InvalidState):
            self.svc.add_condition(KEEPER, self.camp.id, self.ada.id, "one too many")
        self.svc.remove_condition(KEEPER, self.camp.id, c.id)
        self.assertNotIn("Shaken", [x.label for x in self.svc.conditions(self.camp.id, self.ada.id)])
        with self.assertRaises(NotFound):
            self.svc.remove_condition(KEEPER, self.camp.id, c.id)


class HistoryAndRevertTests(Base):
    def setUp(self):
        super().setUp()
        self.seat(ALICE, self.ada)

    def last(self, kind):
        return next(e for e in self.svc.history(KEEPER, self.camp.id, self.ada.id) if e.kind == kind)

    def hp(self):
        return self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator.hp

    def test_revert_resource(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 4}, confirmed=True)
        ev = self.last("resource")
        rev = self.svc.revert(KEEPER, self.camp.id, ev.id, reason="wrong target")
        self.assertEqual(self.hp(), 12)
        self.assertEqual((rev.kind, rev.reverts_event_id, rev.old_value, rev.new_value), ("revert", ev.id, "4", "12"))
        with self.assertRaises(InvalidState):
            self.svc.revert(KEEPER, self.camp.id, ev.id)                 # only once
        with self.assertRaises(InvalidState):
            self.svc.revert(KEEPER, self.camp.id, rev.id)                # reverts are not revertible

    def test_revert_refused_after_later_change(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 8}, confirmed=True)
        first = self.last("resource")
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 6}, confirmed=True)
        with self.assertRaises(InvalidState) as cm:
            self.svc.revert(KEEPER, self.camp.id, first.id)
        self.assertIn("changed again", str(cm.exception))
        self.assertEqual(self.hp(), 6)                                   # nothing overwritten

    def test_revert_conditions(self):
        c = self.svc.add_condition(KEEPER, self.camp.id, self.ada.id, "Bleeding")
        self.svc.revert(KEEPER, self.camp.id, self.last("condition_add").id)
        self.assertEqual(self.svc.conditions(self.camp.id, self.ada.id), ())
        c = self.svc.add_condition(KEEPER, self.camp.id, self.ada.id, "Unconscious")
        self.svc.remove_condition(KEEPER, self.camp.id, c.id)
        self.svc.revert(KEEPER, self.camp.id, self.last("condition_remove").id)
        self.assertEqual([x.label for x in self.svc.conditions(self.camp.id, self.ada.id)], ["Unconscious"])

    def test_events_are_append_only(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 9}, confirmed=True)
        ev = self.last("resource")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM campaign_events WHERE event_id=?", (ev.id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE campaign_events SET new_value='99' WHERE event_id=?", (ev.id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE campaign_events SET actor_user_id='x' WHERE event_id=?", (ev.id,))

    def test_players_and_outsiders_cannot_revert(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 9}, confirmed=True)
        ev = self.last("resource")
        with self.assertRaises(Forbidden):
            self.svc.revert(ALICE, self.camp.id, ev.id)
        with self.assertRaises(NotFound):
            self.svc.revert(EVE, self.camp.id, ev.id)


class RulesTests(Base):
    def test_versioned_rules(self):
        v, rules = self.svc.set_rules(KEEPER, self.camp.id, critical_max=5, roll_visibility="private")
        self.assertEqual((v, rules.critical_max, rules.fumble, rules.roll_visibility), (2, 5, "raw", "private"))
        v, _ = self.svc.set_rules(KEEPER, self.camp.id, fumble="only_100")
        self.assertEqual(v, 3)
        self.assertEqual(self.svc.set_rules(KEEPER, self.camp.id, fumble="only_100")[0], 3)   # no-op
        versions = self.conn.execute("SELECT version FROM campaign_rules WHERE campaign_id=? ORDER BY version",
                                     (self.camp.id,)).fetchall()
        self.assertEqual([r[0] for r in versions], [1, 2, 3])
        rule_events = [e for e in self.svc.history(KEEPER, self.camp.id) if e.kind == "rules"]
        self.assertEqual(len(rule_events), 2)

    def test_only_supported_settings(self):
        for bad in ({"critical_max": 0}, {"critical_max": 6}, {"fumble": "sometimes"},
                    {"roll_visibility": "keeper"}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.svc.set_rules(KEEPER, self.camp.id, **bad)
        with self.assertRaises(ValueError):
            from_json('{"schema": 1, "pushed_rolls": true}')

    def test_mapping_to_engine(self):
        house = CampaignRules(critical_max=5, fumble="only_100").to_check_rules()
        self.assertEqual(success_level(5, 60, house), SuccessLevel.CRITICAL)
        self.assertEqual(success_level(5, 60), SuccessLevel.EXTREME)
        self.assertEqual(success_level(97, 30, house), SuccessLevel.FAIL)
        self.assertEqual(success_level(97, 30), SuccessLevel.FUMBLE)
        self.assertEqual(success_level(100, 30, house), SuccessLevel.FUMBLE)


class CampaignCheckTests(Base):
    def setUp(self):
        super().setUp()
        self.seat(ALICE, self.ada)
        self.inv.select(ALICE, self.ada.id, GUILD)

    def test_plan_outside_and_inside_campaign(self):
        from cocbot.campaigns.checks import plan_check
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"luck": 20}, confirmed=True)
        outside = plan_check(self.conn, ALICE, GUILD, CH_OTHER, "luck")
        self.assertEqual((outside.target.value, outside.campaign, outside.note, outside.private), (50, None, None, False))
        inside = plan_check(self.conn, ALICE, GUILD, CH_A, "luck")
        self.assertEqual((inside.target.value, inside.campaign.id, inside.rules_version), (20, self.camp.id, 1))
        self.assertEqual(inside.note, "Masks · rules v1")
        self.assertEqual(plan_check(self.conn, ALICE, GUILD, CH_A, "45").target.value, 45)

    def test_rules_apply_to_everyone_rolling_in_the_channel(self):
        from cocbot.campaigns.checks import plan_check
        from cocbot.rolls.service import RollService
        self.svc.set_rules(KEEPER, self.camp.id, critical_max=4, roll_visibility="private")
        keeper = plan_check(self.conn, KEEPER, GUILD, CH_A, "60")
        self.assertEqual((keeper.rules.critical_max, keeper.private, keeper.context), (4, True, None))
        self.assertIn("critical 01-04", keeper.note)
        player = plan_check(self.conn, ALICE, GUILD, CH_A, "spot hidden")
        rolls_svc = RollService(self.conn)
        mine = rolls_svc.roll_check(player, ALICE, channel_id=CH_A)
        rolls_svc.roll_check(keeper, KEEPER, channel_id=CH_A)      # private, but Keepers are not participants
        rolls = [e for e in self.svc.history(KEEPER, self.camp.id) if e.kind == "roll"]
        self.assertEqual([(e.ref_id, e.actor_user_id) for e in rolls], [(mine.id, ALICE)])

    def test_stale_confirmation_is_refused(self):
        self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 8}, confirmed=True)
        with self.assertRaises(InvalidState):
            self.svc.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 5}, confirmed=True, expected={"hp": 12})
        self.assertEqual(self.svc.entry(KEEPER, self.camp.id, self.ada.id).investigator.hp, 8)

    def test_parse_adjust(self):
        from cocbot.ui.keeper_text import parse_adjust, parse_user_id
        cur = {"hp": 12, "mp": 9, "san": 45, "luck": None}
        self.assertEqual(parse_adjust({"hp": "-3", "mp": "+1", "san": " 40 ", "luck": ""}, cur),
                         {"hp": 9, "mp": 10, "san": 40})
        for bad in ({"hp": "abc"}, {"hp": "--2"}, {"hp": "1234"}, {"luck": "+5"}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_adjust(bad, cur)
        self.assertEqual(parse_user_id("<@!123>"), "123")
        with self.assertRaises(ValidationError):
            parse_user_id("@someone")


class PersistenceTests(unittest.TestCase):
    def test_everything_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            inv, svc = InvestigatorService(conn), CampaignService(conn)
            ada = ready_investigator(inv, ALICE, "Ada")
            camp = svc.create(KEEPER, "Masks", channel_id=CH_A, guild_id=GUILD)
            svc.set_assistant(KEEPER, camp.id, ASSIST, True)
            svc.invite(KEEPER, camp.id, ALICE)
            svc.join(ALICE, camp.id, ada.id)
            svc.adjust(KEEPER, camp.id, ada.id, {"hp": 5, "san": 41}, reason="ghoul", confirmed=True)
            svc.add_condition(ASSIST, camp.id, ada.id, "Shaken")
            svc.set_rules(KEEPER, camp.id, critical_max=3)
            conn.close()

            conn2 = fresh_db(path)
            svc2 = CampaignService(conn2)
            ctx = svc2.context(ALICE, CH_A)
            self.assertEqual((ctx.investigator.hp, ctx.investigator.san, ctx.rules.critical_max, ctx.rules_version),
                             (5, 41, 3, 2))
            self.assertEqual([c.label for c in svc2.conditions(camp.id, ada.id)], ["Shaken"])
            self.assertEqual(svc2.role_of(camp.id, ASSIST), "assistant")
            kinds = [e.kind for e in svc2.history(ASSIST, camp.id, limit=50)]
            for kind in ("created", "channel_link", "assistant_add", "invite", "join", "resource",
                         "condition_add", "rules"):
                self.assertIn(kind, kinds)
            self.assertEqual(InvestigatorService(conn2).get(ALICE, ada.id).hp, 12)   # permanent untouched
            conn2.close()


if __name__ == "__main__":
    unittest.main()
