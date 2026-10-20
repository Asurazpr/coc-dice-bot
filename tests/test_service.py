"""Ownership, collection and persistence tests (acceptance criteria 1-5)."""
import os
import random
import sqlite3
import tempfile
import unittest

from cocbot.characters.service import (
    MAX_LIVE_INVESTIGATORS,
    Conflict,
    InvalidState,
    InvestigatorService,
    NotFound,
    ValidationError,
)
from cocbot.ui.sheet_text import build_sheet_text
from tests.helpers import SeqRandom, fresh_db

ALICE, BOB = "111111111111111111", "222222222222222222"
GUILD_A, GUILD_B = 900000000000000001, 900000000000000002

FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.svc = InvestigatorService(self.conn)

    def tearDown(self):
        self.conn.close()


class CollectionTests(ServiceTestCase):
    def test_two_users_multiple_characters(self):
        a1 = self.svc.create(ALICE, "Ada Byrne", occupation="Librarian", age="31")
        a2 = self.svc.create(ALICE, "Harvey Walters")
        b1 = self.svc.create(BOB, "Ada Byrne")  # same name, different owner: fine
        self.assertEqual([i.name for i in self.svc.list(ALICE)], ["Ada Byrne", "Harvey Walters"])
        self.assertEqual([i.id for i in self.svc.list(BOB)], [b1.id])
        self.assertEqual(a1.age, 31)
        self.assertEqual(a1.status, "draft")
        self.assertNotEqual(a1.id, a2.id)

    def test_duplicate_name_same_owner_rejected_case_insensitive(self):
        self.svc.create(ALICE, "Ada")
        with self.assertRaises(Conflict):
            self.svc.create(ALICE, "  ada ")

    def test_validation(self):
        for bad in ("", "   ", "12345", "x" * 65):
            with self.subTest(name=bad), self.assertRaises(ValidationError):
                self.svc.create(ALICE, bad)
        with self.assertRaises(ValidationError):
            self.svc.create(ALICE, "Ok", age="old")
        with self.assertRaises(ValidationError):
            self.svc.create(ALICE, "Ok", age=0)

    def test_limit(self):
        for n in range(MAX_LIVE_INVESTIGATORS):
            self.svc.create(ALICE, f"Inv {chr(65 + n)}")
        with self.assertRaises(Conflict):
            self.svc.create(ALICE, "One too many")
        # archiving frees a slot
        first = self.svc.list(ALICE)[0]
        self.svc.archive(ALICE, first.id)
        self.svc.create(ALICE, "Now fits")


class OwnershipTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.alice_inv = self.svc.create(ALICE, "Ada")

    def test_other_user_cannot_read_or_modify(self):
        i = self.alice_inv.id
        calls = [
            lambda: self.svc.get(BOB, i),
            lambda: self.svc.find(BOB, str(i)),
            lambda: self.svc.find(BOB, "Ada"),
            lambda: self.svc.update_bio(BOB, i, name="Stolen"),
            lambda: self.svc.set_characteristics(BOB, i, {"STR": 90}),
            lambda: self.svc.roll_characteristics(BOB, i),
            lambda: self.svc.set_skill(BOB, i, "spot hidden", 80),
            lambda: self.svc.set_resources(BOB, i, luck=99),
            lambda: self.svc.finalize(BOB, i),
            lambda: self.svc.archive(BOB, i),
            lambda: self.svc.restore(BOB, i),
            lambda: self.svc.delete(BOB, i),
            lambda: self.svc.select(BOB, i, GUILD_A),
        ]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(NotFound):
                call()
        # nothing changed
        self.assertEqual(self.svc.get(ALICE, i).name, "Ada")
        self.assertIsNone(self.svc.active(BOB, GUILD_A))

    def test_database_trigger_blocks_foreign_selection(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO active_investigators(user_id, scope, investigator_id) VALUES (?, 'global', ?)",
                (BOB, self.alice_inv.id),
            )


class ActiveSelectionTests(ServiceTestCase):
    def test_selection_is_per_user(self):
        a1 = self.svc.create(ALICE, "Ada")
        a2 = self.svc.create(ALICE, "Harvey")
        b1 = self.svc.create(BOB, "Bram")
        self.svc.select(ALICE, a1.id, GUILD_A)
        self.svc.select(BOB, b1.id, GUILD_A)
        self.assertEqual(self.svc.active(ALICE, GUILD_A).id, a1.id)
        self.assertEqual(self.svc.active(BOB, GUILD_A).id, b1.id)
        # Alice switching does not affect Bob
        self.svc.select(ALICE, a2.id, GUILD_A)
        self.assertEqual(self.svc.active(ALICE, GUILD_A).id, a2.id)
        self.assertEqual(self.svc.active(BOB, GUILD_A).id, b1.id)

    def test_scope_resolution(self):
        a1 = self.svc.create(ALICE, "Ada")
        a2 = self.svc.create(ALICE, "Harvey")
        self.svc.select(ALICE, a1.id, everywhere=True)
        self.svc.select(ALICE, a2.id, GUILD_B)
        self.assertEqual(self.svc.active(ALICE, GUILD_A).id, a1.id)  # falls back to global
        self.assertEqual(self.svc.active(ALICE, GUILD_B).id, a2.id)  # server override
        self.assertEqual(self.svc.active(ALICE, None).id, a1.id)     # DMs use global
        self.svc.clear_selection(ALICE, GUILD_B)
        self.assertEqual(self.svc.active(ALICE, GUILD_B).id, a1.id)

    def test_select_in_dm_is_global(self):
        a1 = self.svc.create(ALICE, "Ada")
        self.svc.select(ALICE, a1.id, None)
        self.assertEqual(self.svc.active(ALICE, GUILD_A).id, a1.id)

    def test_archive_clears_selection_and_blocks_select(self):
        a1 = self.svc.create(ALICE, "Ada")
        self.svc.select(ALICE, a1.id, GUILD_A)
        self.svc.select(ALICE, a1.id, everywhere=True)
        self.svc.archive(ALICE, a1.id)
        self.assertIsNone(self.svc.active(ALICE, GUILD_A))
        with self.assertRaises(NotFound):
            self.svc.select(ALICE, a1.id, GUILD_A)


class EditingTests(ServiceTestCase):
    def test_characteristics_derived_and_finalize(self):
        inv = self.svc.create(ALICE, "Ada")
        with self.assertRaises(InvalidState):
            self.svc.finalize(ALICE, inv.id)
        inv = self.svc.set_characteristics(ALICE, inv.id, FULL)
        self.assertEqual(inv.characteristics["STR"], 60)
        self.assertEqual(inv.luck, 50)
        self.assertEqual((inv.hp, inv.mp, inv.san), (12, 9, 45))
        inv = self.svc.finalize(ALICE, inv.id)
        self.assertEqual(inv.status, "ready")
        with self.assertRaises(ValidationError):
            self.svc.set_characteristics(ALICE, inv.id, {"STR": ""})
        with self.assertRaises(ValidationError):
            self.svc.set_characteristics(ALICE, inv.id, {"STR": 120})
        with self.assertRaises(ValidationError):
            self.svc.set_characteristics(ALICE, inv.id, {"XYZ": 1})

    def test_roll_only_for_drafts(self):
        inv = self.svc.create(ALICE, "Ada")
        inv = self.svc.roll_characteristics(ALICE, inv.id, random.Random(7))
        self.assertTrue(inv.is_complete)
        self.svc.finalize(ALICE, inv.id)
        with self.assertRaises(InvalidState):
            self.svc.roll_characteristics(ALICE, inv.id)

    def test_skills(self):
        inv = self.svc.create(ALICE, "Ada")
        inv = self.svc.set_skill(ALICE, inv.id, "Spot Hidden", 65)
        inv = self.svc.set_skill(ALICE, inv.id, "图书馆使用", "70")
        self.assertEqual(inv.skills, {"spot_hidden": 65, "library_use": 70})
        inv = self.svc.set_skill(ALICE, inv.id, "spot hidden", "")
        self.assertEqual(inv.skills, {"library_use": 70})
        with self.assertRaises(ValidationError):
            self.svc.set_skill(ALICE, inv.id, "basket weaving", 50)
        with self.assertRaises(ValidationError):
            self.svc.set_skill(ALICE, inv.id, "listen", 500)

    def test_cthulhu_mythos_lowers_max_san(self):
        inv = self.svc.create(ALICE, "Ada")
        self.svc.set_characteristics(ALICE, inv.id, FULL)
        inv = self.svc.set_skill(ALICE, inv.id, "cthulhu_mythos", 10)
        self.assertEqual(inv.derived().san_max, 89)

    def test_resources(self):
        inv = self.svc.create(ALICE, "Ada")
        with self.assertRaises(InvalidState):
            self.svc.set_resources(ALICE, inv.id, hp=5)
        self.svc.set_characteristics(ALICE, inv.id, FULL)
        inv = self.svc.set_resources(ALICE, inv.id, hp="7", san="40", luck="35")
        self.assertEqual((inv.hp, inv.san, inv.luck, inv.mp), (7, 40, 35, 9))
        with self.assertRaises(ValidationError):
            self.svc.set_resources(ALICE, inv.id, hp=13)  # above max 12
        inv = self.svc.set_resources(ALICE, inv.id, hp="")  # reset to max
        self.assertEqual(inv.hp, 12)

    def test_rename(self):
        a = self.svc.create(ALICE, "Ada")
        self.svc.create(ALICE, "Harvey")
        with self.assertRaises(Conflict):
            self.svc.update_bio(ALICE, a.id, name="harvey")
        self.assertEqual(self.svc.update_bio(ALICE, a.id, name="ADA").name, "ADA")
        self.assertEqual(self.svc.find(ALICE, "ada").id, a.id)

    def test_archive_restore_delete(self):
        a = self.svc.create(ALICE, "Ada")
        with self.assertRaises(InvalidState):
            self.svc.delete(ALICE, a.id)
        self.svc.archive(ALICE, a.id)
        self.assertEqual(self.svc.list(ALICE), [])
        self.assertEqual(len(self.svc.list(ALICE, include_archived=True)), 1)
        # name can be reused while archived; restore then conflicts
        b = self.svc.create(ALICE, "Ada")
        with self.assertRaises(Conflict):
            self.svc.restore(ALICE, a.id)
        self.svc.update_bio(ALICE, b.id, name="Ada II")
        self.assertEqual(self.svc.restore(ALICE, a.id).status, "draft")
        self.svc.archive(ALICE, a.id)
        self.svc.delete(ALICE, a.id)
        with self.assertRaises(NotFound):
            self.svc.get(ALICE, a.id)


class PersistenceTests(unittest.TestCase):
    def test_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            svc = InvestigatorService(conn)
            a = svc.create(ALICE, "Ada")
            svc.set_characteristics(ALICE, a.id, FULL)
            svc.set_skill(ALICE, a.id, "listen", 55)
            svc.select(ALICE, a.id, GUILD_A)
            b = svc.create(BOB, "Bram")
            svc.select(BOB, b.id, everywhere=True)
            conn.close()

            conn2 = fresh_db(path)  # "restart": reconnect + re-run migrations
            svc2 = InvestigatorService(conn2)
            self.assertEqual(svc2.active(ALICE, GUILD_A).skills, {"listen": 55})
            self.assertEqual(svc2.active(ALICE, GUILD_A).characteristics["POW"], 45)
            self.assertEqual(svc2.active(BOB, GUILD_A).name, "Bram")
            self.assertIsNone(svc2.active(ALICE, GUILD_B))
            conn2.close()


class ArchiveStatusRegressionTests(ServiceTestCase):
    """Review item 1: restore must return the exact pre-archive status."""

    def test_complete_but_unfinished_draft_stays_draft(self):
        inv = self.svc.create(ALICE, "Ada")
        inv = self.svc.set_characteristics(ALICE, inv.id, FULL)
        self.assertTrue(inv.is_complete)
        self.assertEqual(inv.status, "draft")  # never finalized
        self.svc.archive(ALICE, inv.id)
        self.assertEqual(self.svc.restore(ALICE, inv.id).status, "draft")

    def test_ready_stays_ready(self):
        inv = self.svc.create(ALICE, "Ada")
        self.svc.set_characteristics(ALICE, inv.id, FULL)
        self.svc.finalize(ALICE, inv.id)
        self.svc.archive(ALICE, inv.id)
        restored = self.svc.restore(ALICE, inv.id)
        self.assertEqual(restored.status, "ready")
        self.assertIsNone(restored.status_before_archive)

    def test_archived_before_migration_restores_as_draft(self):
        inv = self.svc.create(ALICE, "Ada")
        self.svc.set_characteristics(ALICE, inv.id, FULL)
        self.svc.archive(ALICE, inv.id)
        # simulate a row archived before migration 008 recorded the status
        self.conn.execute("UPDATE investigators SET status_before_archive=NULL WHERE investigator_id=?", (inv.id,))
        self.conn.commit()
        self.assertEqual(self.svc.restore(ALICE, inv.id).status, "draft")


class SanityCapRegressionTests(ServiceTestCase):
    """Review item 2: current SAN never exceeds Maximum SAN (99 - Cthulhu Mythos)."""

    def setUp(self):
        super().setUp()
        self.inv = self.svc.create(ALICE, "Ada")
        self.svc.set_characteristics(ALICE, self.inv.id, {**FULL, "POW": 90})

    def test_implicit_san_capped_by_mythos(self):
        inv = self.svc.set_skill(ALICE, self.inv.id, "cthulhu_mythos", 20)
        self.assertIsNone(inv.san_current)            # still "starting value"
        self.assertEqual(inv.derived().san_max, 79)
        self.assertEqual(inv.san, 79)                 # POW 90 capped to 79
        self.assertEqual(inv.stat("SAN"), 79)         # what /check SAN uses

    def test_stored_san_lowered_when_mythos_rises(self):
        self.svc.set_resources(ALICE, self.inv.id, san=85)
        inv = self.svc.set_skill(ALICE, self.inv.id, "cthulhu_mythos", 30)
        self.assertEqual(inv.san_current, 69)         # persisted, not just displayed
        self.assertEqual(inv.san, 69)

    def test_stored_san_below_max_untouched(self):
        self.svc.set_resources(ALICE, self.inv.id, san=40)
        inv = self.svc.set_skill(ALICE, self.inv.id, "cthulhu_mythos", 30)
        self.assertEqual(inv.san_current, 40)

    def test_cannot_set_san_above_max(self):
        self.svc.set_skill(ALICE, self.inv.id, "cthulhu_mythos", 30)
        with self.assertRaises(ValidationError):
            self.svc.set_resources(ALICE, self.inv.id, san=70)

    def test_stored_value_above_max_is_still_capped_on_read(self):
        # e.g. legacy-imported data or a direct database edit
        self.svc.set_skill(ALICE, self.inv.id, "cthulhu_mythos", 30)
        self.conn.execute("UPDATE investigators SET san_current=95 WHERE investigator_id=?", (self.inv.id,))
        self.conn.commit()
        self.assertEqual(self.svc.get(ALICE, self.inv.id).san, 69)

    def test_mythos_limit(self):
        with self.assertRaises(ValidationError):
            self.svc.set_skill(ALICE, self.inv.id, "cthulhu_mythos", 100)


class SelectionScopeRegressionTests(ServiceTestCase):
    """Review item 4: a server choice overrides the default; unselect restores the default."""

    def test_default_does_not_override_server_choice(self):
        a = self.svc.create(ALICE, "Ada")
        b = self.svc.create(ALICE, "Bram")
        self.svc.select(ALICE, a.id, GUILD_A)                 # server-specific
        self.svc.select(ALICE, b.id, GUILD_A, everywhere=True)  # default, issued from the same server
        inv, scope = self.svc.active_with_scope(ALICE, GUILD_A)
        self.assertEqual((inv.id, scope), (a.id, f"guild:{GUILD_A}"))
        inv, scope = self.svc.active_with_scope(ALICE, GUILD_B)
        self.assertEqual((inv.id, scope), (b.id, "global"))
        sel = self.svc.selections(ALICE, GUILD_A)
        self.assertEqual((sel["server"].id, sel["default"].id), (a.id, b.id))

    def test_unselect_server_falls_back_to_default(self):
        a = self.svc.create(ALICE, "Ada")
        b = self.svc.create(ALICE, "Bram")
        self.svc.select(ALICE, a.id, GUILD_A)
        self.svc.select(ALICE, b.id, everywhere=True)
        self.assertTrue(self.svc.clear_selection(ALICE, GUILD_A))
        self.assertEqual(self.svc.active_with_scope(ALICE, GUILD_A), (self.svc.get(ALICE, b.id), "global"))
        self.assertFalse(self.svc.clear_selection(ALICE, GUILD_A))   # nothing left to clear
        self.assertTrue(self.svc.clear_selection(ALICE, GUILD_A, everywhere=True))
        self.assertEqual(self.svc.active_with_scope(ALICE, GUILD_A), (None, None))

    def test_unselect_only_affects_caller(self):
        a = self.svc.create(ALICE, "Ada")
        b = self.svc.create(BOB, "Bram")
        self.svc.select(ALICE, a.id, GUILD_A)
        self.svc.select(BOB, b.id, GUILD_A)
        self.svc.clear_selection(ALICE, GUILD_A)
        self.assertEqual(self.svc.active(BOB, GUILD_A).id, b.id)


class HpMpConsistencyRegressionTests(ServiceTestCase):
    """Final M1 fix: current HP/MP never exceed the maximum after characteristic changes.

    FULL gives HP max (55+65)/10 = 12 and MP max 45/5 = 9.
    """

    def setUp(self):
        super().setUp()
        self.inv = self.svc.create(ALICE, "Ada")
        self.svc.set_characteristics(ALICE, self.inv.id, FULL)

    def stored(self, investigator_id=None):
        row = self.conn.execute(
            "SELECT hp_current, mp_current FROM investigators WHERE investigator_id=?",
            (investigator_id or self.inv.id,),
        ).fetchone()
        return row["hp_current"], row["mp_current"]

    def test_reduction_clamps_and_persists(self):
        self.svc.set_resources(ALICE, self.inv.id, hp=11, mp=8)
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": 25, "POW": 20})  # HP 9, MP 4
        self.assertEqual((inv.hp, inv.mp), (9, 4))
        self.assertEqual(self.stored(), (9, 4))

    def test_values_below_new_max_unchanged(self):
        self.svc.set_resources(ALICE, self.inv.id, hp=5, mp=2)
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": 45, "POW": 30})  # HP 11, MP 6
        self.assertEqual((inv.hp, inv.mp), (5, 2))
        self.assertEqual(self.stored(), (5, 2))

    def test_increase_does_not_heal(self):
        self.svc.set_resources(ALICE, self.inv.id, hp=5, mp=2)
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": 90, "POW": 80})  # HP 15, MP 16
        self.assertEqual((inv.hp, inv.mp), (5, 2))
        self.assertEqual(self.stored(), (5, 2))
        self.assertEqual((inv.derived().hp_max, inv.derived().mp_max), (15, 16))

    def test_null_follows_maximum_both_ways(self):
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": 25, "POW": 20})
        self.assertEqual((inv.hp, inv.mp), (9, 4))
        self.assertEqual(self.stored(), (None, None))
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": 90, "POW": 80})
        self.assertEqual((inv.hp, inv.mp), (15, 16))
        self.assertEqual(self.stored(), (None, None))

    def test_zero_stays_zero(self):
        inv = self.svc.set_resources(ALICE, self.inv.id, hp=0, mp=0)
        self.assertEqual((inv.hp, inv.mp), (0, 0))
        for change in ({"CON": 25, "POW": 20}, {"CON": 90, "POW": 80}):
            inv = self.svc.set_characteristics(ALICE, self.inv.id, change)
            self.assertEqual((inv.hp, inv.mp), (0, 0))
            self.assertEqual(self.stored(), (0, 0))
        fields = dict((n, v) for n, v, _ in build_sheet_text(inv, {}).fields)
        self.assertIn("**HP** 0/15", fields["Status"])
        self.assertIn("**MP** 0/16", fields["Status"])

    def test_draft_reroll_clamps(self):
        self.svc.set_resources(ALICE, self.inv.id, hp=12, mp=9)
        # all dice 1: CON 15, SIZ 40, POW 15 -> HP (15+40)/10 = 5, MP 3
        inv = self.svc.roll_characteristics(ALICE, self.inv.id, SeqRandom([1] * 24))
        self.assertEqual((inv.characteristics["CON"], inv.characteristics["SIZ"], inv.characteristics["POW"]),
                         (15, 40, 15))
        self.assertEqual((inv.hp, inv.mp), (5, 3))
        self.assertEqual(self.stored(), (5, 3))

    def test_clearing_a_draft_characteristic_keeps_value(self):
        self.svc.set_resources(ALICE, self.inv.id, hp=7)
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": ""})  # max unknown
        self.assertEqual(inv.hp, 7)
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": 10})  # HP (10+65)/10 = 7
        self.assertEqual(inv.hp, 7)
        inv = self.svc.set_characteristics(ALICE, self.inv.id, {"CON": 5})   # HP 7 -> (5+65)/10 = 7
        self.assertEqual(inv.hp, 7)

    def test_out_of_range_stored_value_capped_on_read(self):
        # e.g. legacy-imported data or a direct database edit
        self.conn.execute("UPDATE investigators SET hp_current=50, mp_current=50 WHERE investigator_id=?",
                          (self.inv.id,))
        self.conn.commit()
        inv = self.svc.get(ALICE, self.inv.id)
        self.assertEqual((inv.hp, inv.mp), (12, 9))


class HpMpPersistenceRegressionTests(unittest.TestCase):
    def test_clamped_values_survive_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            svc = InvestigatorService(conn)
            a = svc.create(ALICE, "Ada")
            svc.set_characteristics(ALICE, a.id, FULL)
            svc.set_resources(ALICE, a.id, hp=11, mp=0)
            svc.set_characteristics(ALICE, a.id, {"CON": 25})          # HP max 9
            b = svc.create(ALICE, "Bram")
            svc.set_characteristics(ALICE, b.id, FULL)                 # NULL current values
            svc.set_characteristics(ALICE, b.id, {"SIZ": 35, "POW": 25})
            conn.close()

            conn2 = fresh_db(path)
            svc2 = InvestigatorService(conn2)
            a2, b2 = svc2.get(ALICE, a.id), svc2.get(ALICE, b.id)
            self.assertEqual((a2.hp_current, a2.mp_current, a2.hp, a2.mp), (9, 0, 9, 0))
            self.assertEqual((b2.hp_current, b2.mp_current, b2.hp, b2.mp), (None, None, 9, 5))
            sheet = dict((n, v) for n, v, _ in build_sheet_text(a2, {}).fields)
            self.assertIn("**HP** 9/9", sheet["Status"])
            self.assertIn("**MP** 0/9", sheet["Status"])
            conn2.close()


if __name__ == "__main__":
    unittest.main()
