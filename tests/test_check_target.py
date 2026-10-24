"""/check target resolution against the invoking player's investigator (criteria 6-7)."""
import unittest

from cocbot.characters.check_target import CheckTargetError, resolve_check_target
from cocbot.characters.service import InvestigatorService
from tests.helpers import fresh_db

ALICE, BOB, CAROL = "1001", "1002", "1003"
GUILD = 42
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


class CheckTargetTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        svc = InvestigatorService(self.conn)
        a = svc.create(ALICE, "Ada")
        svc.set_characteristics(ALICE, a.id, FULL)
        svc.set_skill(ALICE, a.id, "spot hidden", 75)
        svc.select(ALICE, a.id, GUILD)
        b = svc.create(BOB, "Bram")
        svc.set_characteristics(BOB, b.id, {**FULL, "DEX": 40})
        svc.set_skill(BOB, b.id, "spot hidden", 30)
        svc.select(BOB, b.id, GUILD)

    def tearDown(self):
        self.conn.close()

    def r(self, user, text, guild=GUILD):
        return resolve_check_target(self.conn, user, guild, text)

    def test_numeric_independent_of_character(self):
        for user in (ALICE, CAROL):
            t = self.r(user, "60")
            self.assertEqual((t.value, t.kind, t.investigator), (60, "numeric", None))
        for bad in ("0", "101"):
            with self.assertRaises(CheckTargetError):
                self.r(CAROL, bad)

    def test_trained_skill_per_player(self):
        self.assertEqual(self.r(ALICE, "Spot Hidden").value, 75)
        self.assertEqual(self.r(BOB, "spot hidden").value, 30)
        t = self.r(ALICE, "侦查")
        self.assertEqual((t.value, t.label, t.detail), (75, "侦查", "trained"))

    def test_untrained_uses_base(self):
        t = self.r(ALICE, "listen")
        self.assertEqual((t.value, t.detail, t.base_value), (20, "base", 20))

    def test_derived_skill_uses_investigator(self):
        self.assertEqual(self.r(ALICE, "dodge").value, 35)
        self.assertEqual(self.r(BOB, "Dodge").value, 20)
        self.assertEqual(self.r(ALICE, "language_own").value, 75)

    def test_characteristics_and_resources(self):
        self.assertEqual(self.r(ALICE, "STR").value, 60)
        self.assertEqual(self.r(ALICE, "力量").label, "力量")
        self.assertEqual(self.r(ALICE, "luck").value, 50)
        self.assertEqual(self.r(ALICE, "SAN").value, 45)
        self.assertEqual(self.r(BOB, "dex").value, 40)

    def test_no_active_investigator(self):
        t = self.r(CAROL, "spot hidden")
        self.assertEqual(t.value, 25)
        self.assertIn("no active investigator", t.detail)
        with self.assertRaises(CheckTargetError):
            self.r(CAROL, "dodge")
        with self.assertRaises(CheckTargetError):
            self.r(CAROL, "STR")

    def test_other_guild_without_selection(self):
        # Alice selected only for GUILD; elsewhere she has no active investigator
        self.assertIn("no active investigator", self.r(ALICE, "spot hidden", guild=7).detail)

    def test_unknown(self):
        with self.assertRaises(CheckTargetError):
            self.r(ALICE, "basket weaving")
        with self.assertRaises(CheckTargetError):
            self.r(ALICE, "   ")


if __name__ == "__main__":
    unittest.main()
