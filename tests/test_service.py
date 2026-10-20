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
from tests.helpers import fresh_db

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


if __name__ == "__main__":
    unittest.main()
