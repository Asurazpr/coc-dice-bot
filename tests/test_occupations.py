"""Milestone 2: occupation catalogue, skill choices and skill-point allocation."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cocbot.characters.check_target import resolve_check_target
from cocbot.characters.service import (
    InvalidState,
    InvestigatorService,
    NotFound,
    UnspentPoints,
    ValidationError,
)
from cocbot.db.bootstrap import bootstrap
from cocbot.db.connection import connect
from cocbot.db.migrate import MIGRATIONS_DIR
from cocbot.occupations import catalogue
from cocbot.occupations.service import CreationService, load_picks, load_points
from tests.helpers import fresh_db

ALICE, BOB = "1001", "1002"
GUILD = 42
# EDU 75 -> Accountant EDU×4 = 300; INT 80 -> interest 160; DEX 70 -> Dodge base 35
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.inv_svc = InvestigatorService(self.conn)
        self.svc = CreationService(self.conn)
        self.inv = self.inv_svc.create(ALICE, "Ada")
        self.inv_svc.set_characteristics(ALICE, self.inv.id, FULL)

    def tearDown(self):
        self.conn.close()

    def occ(self, key):
        return catalogue.find(self.conn, ALICE, key)

    def accountant(self, inv_id=None):
        """Accountant: Accounting, Law, Library Use, Listen, Persuade, Spot Hidden + any two; CR 30-70."""
        i = inv_id or self.inv.id
        self.svc.set_occupation(ALICE, i, "accountant")
        self.svc.set_pick(ALICE, i, 6, 0, "psychology")
        return self.svc.set_pick(ALICE, i, 6, 1, "charm")


class CatalogueTests(Base):
    def test_sync_and_search(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM occupations").fetchone()[0], 113)
        self.assertEqual(catalogue.search(self.conn, ALICE, "account")[0].key, "accountant")
        self.assertEqual(catalogue.search(self.conn, ALICE, "会计师")[0].key, "accountant")
        self.assertEqual(catalogue.find(self.conn, ALICE, "Accountant").credit_min, 30)
        self.assertEqual(len(catalogue.search(self.conn, ALICE, "", limit=25)), 25)
        self.assertEqual(catalogue.count_visible(self.conn, ALICE), 113)

    def test_resync_is_idempotent_and_retires_removed(self):
        before = self.conn.execute("SELECT occupation_id FROM occupations WHERE key='accountant'").fetchone()[0]
        self.assertEqual(catalogue.sync_builtin(self.conn)["retired"], 0)
        self.assertEqual(self.conn.execute("SELECT occupation_id FROM occupations WHERE key='accountant'")
                         .fetchone()[0], before)  # ids stable for references
        data = json.loads(catalogue.CATALOGUE_PATH.read_text(encoding="utf-8"))
        data["occupations"] = [o for o in data["occupations"] if o["key"] != "zookeeper"]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(catalogue.sync_builtin(self.conn, p)["retired"], 1)
        self.assertIsNone(catalogue.find(self.conn, ALICE, "zookeeper"))
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM occupations WHERE key='zookeeper'").fetchone())

    def test_formula_text(self):
        self.assertEqual(self.occ("accountant").formula_text(), "EDU×4")
        self.assertEqual(self.occ("private_investigator").formula_text(), "EDU×2 + (DEX or STR)×2")

    def test_skill_base_values_corrected(self):
        bases = dict(self.conn.execute("SELECT key, base FROM skill_defs").fetchall())
        self.assertEqual((bases["library_use"], bases["stealth"], bases["whip"], bases["sword"]), (20, 20, 5, 20))


class BudgetAndChoiceTests(Base):
    def test_budgets(self):
        self.accountant()
        c = self.svc.check(ALICE, self.inv.id)
        self.assertEqual((c.occupation_budget.total, c.interest_budget.total), (300, 160))
        self.svc.set_occupation(ALICE, self.inv.id, "private_investigator", force=True)
        c = self.svc.check(ALICE, self.inv.id)
        self.assertEqual(c.occupation_budget.total, 75 * 2 + 70 * 2)      # higher of STR 60 / DEX 70
        self.assertIn("higher of DEX/STR", c.occupation_budget.explanation)

    def test_picks_validation(self):
        self.svc.set_occupation(ALICE, self.inv.id, "accountant")
        for slot, pick, skill in [(6, 0, "accounting"),          # already fixed
                                  (6, 0, "credit_rating"),       # never selectable
                                  (6, 0, "cthulhu_mythos"),
                                  (0, 0, "listen"),              # fixed slot
                                  (6, 5, "listen")]:             # no such pick
            with self.subTest(skill=skill), self.assertRaises(ValidationError):
                self.svc.set_pick(ALICE, self.inv.id, slot, pick, skill)
        self.svc.set_pick(ALICE, self.inv.id, 6, 0, "charm")
        with self.assertRaises(ValidationError):
            self.svc.set_pick(ALICE, self.inv.id, 6, 1, "Charm")  # duplicate
        self.svc.set_pick(ALICE, self.inv.id, 6, 1, "dodge")
        self.assertEqual(load_picks(self.conn, self.inv.id), {(6, 0): "charm", (6, 1): "dodge"})

    def test_choose_slot_options_enforced(self):
        self.svc.set_occupation(ALICE, self.inv.id, "soldiers_marines")
        # slot 0: Climb or Swim
        with self.assertRaises(ValidationError):
            self.svc.set_pick(ALICE, self.inv.id, 0, 0, "jump")
        self.svc.set_pick(ALICE, self.inv.id, 0, 0, "swim")
        inv = self.inv_svc.get(ALICE, self.inv.id)
        self.assertEqual(self.svc.pick_options(inv, 6, 0), ["first_aid", "mechanical_repair", "language_foreign"])
        self.svc.set_pick(ALICE, self.inv.id, 6, 0, "first_aid")
        self.assertNotIn("first_aid", self.svc.pick_options(inv, 6, 1))

    def test_next_step(self):
        fresh = self.inv_svc.create(ALICE, "Blank")
        self.assertEqual(self.svc.next_step(fresh), "characteristics")
        self.assertEqual(self.svc.next_step(self.inv_svc.get(ALICE, self.inv.id)), "occupation")
        self.svc.set_occupation(ALICE, self.inv.id, "accountant")
        self.assertEqual(self.svc.next_step(self.inv_svc.get(ALICE, self.inv.id)), "choices")
        self.accountant()
        self.assertEqual(self.svc.next_step(self.inv_svc.get(ALICE, self.inv.id)), "allocation")


class AllocationTests(Base):
    def setUp(self):
        super().setUp()
        self.accountant()

    def test_occupation_points_only_on_occupation_skills(self):
        with self.assertRaises(ValidationError):
            self.svc.allocate(ALICE, self.inv.id, "climb", occupation_points=10)
        self.svc.allocate(ALICE, self.inv.id, "climb", interest_points=10)       # interest: any skill
        self.svc.allocate(ALICE, self.inv.id, "psychology", occupation_points=40)  # a picked skill
        self.svc.allocate(ALICE, self.inv.id, "credit_rating", occupation_points=30)
        self.assertEqual(load_points(self.conn, self.inv.id),
                         {"climb": (0, 10), "psychology": (40, 0), "credit_rating": (30, 0)})

    def test_values_mirrored_and_used_by_check(self):
        inv = self.svc.allocate(ALICE, self.inv.id, "Spot Hidden", occupation_points=40, interest_points=10)
        self.assertEqual(inv.skills["spot_hidden"], 25 + 40 + 10)
        self.inv_svc.select(ALICE, inv.id, GUILD)
        self.assertEqual(resolve_check_target(self.conn, ALICE, GUILD, "侦查").value, 75)
        inv = self.svc.allocate(ALICE, self.inv.id, "spot hidden", occupation_points="", interest_points=0)
        self.assertNotIn("spot_hidden", inv.skills)  # back to base

    def test_limits(self):
        cases = [
            dict(skill="listen", occupation_points=80),                    # 20 + 80 > 99
            dict(skill="cthulhu_mythos", interest_points=5),
            dict(skill="credit_rating", occupation_points=71),             # above Accountant max 70
            dict(skill="climb", interest_points=161),                      # interest budget 160
            dict(skill="listen", occupation_points=-1),
        ]
        for kw in cases:
            with self.subTest(**kw), self.assertRaises(ValidationError):
                skill = kw.pop("skill")
                self.svc.allocate(ALICE, self.inv.id, skill, **kw)

    def test_budget_overspend_rejected(self):
        for skill in ("accounting", "law", "listen", "persuade"):
            self.svc.allocate(ALICE, self.inv.id, skill, occupation_points=70)  # 280 of 300
        with self.assertRaises(ValidationError) as cm:
            self.svc.allocate(ALICE, self.inv.id, "spot_hidden", occupation_points=21)
        self.assertIn("20 occupation points", str(cm.exception))

    def test_derived_base_follows_characteristics(self):
        inv = self.svc.allocate(ALICE, self.inv.id, "dodge", interest_points=20)
        self.assertEqual(inv.skills["dodge"], 35 + 20)
        inv = self.inv_svc.set_characteristics(ALICE, self.inv.id, {"DEX": 50})
        self.assertEqual(inv.skills["dodge"], 25 + 20)

    def test_lower_edu_makes_budget_overspent_but_keeps_points(self):
        self.svc.allocate(ALICE, self.inv.id, "accounting", occupation_points=70)
        self.svc.allocate(ALICE, self.inv.id, "law", occupation_points=70)
        self.svc.allocate(ALICE, self.inv.id, "library_use", occupation_points=70)
        self.inv_svc.set_characteristics(ALICE, self.inv.id, {"EDU": 50})       # budget 300 -> 200
        c = self.svc.check(ALICE, self.inv.id)
        self.assertIn("Occupation points overspent by 10.", c.errors)
        self.assertEqual(load_points(self.conn, self.inv.id)["law"], (70, 0))

    def test_manual_edits_blocked_during_creation(self):
        self.svc.allocate(ALICE, self.inv.id, "law", occupation_points=30)
        for skill, value in (("law", 90), ("occult", 15), ("cthulhu_mythos", 30), ("spot hidden", 150)):
            with self.subTest(skill=skill), self.assertRaises(InvalidState):
                self.inv_svc.set_skill(ALICE, self.inv.id, skill, value)
        self.assertEqual(self.inv_svc.get(ALICE, self.inv.id).skills, {"law": 35})


class ReviewAndFinishTests(Base):
    def setUp(self):
        super().setUp()
        self.accountant()

    def test_credit_rating_required_to_finish(self):
        c = self.svc.check(ALICE, self.inv.id)
        self.assertIn("Credit Rating is 0; this occupation requires 30-70.", c.errors)
        with self.assertRaises(InvalidState) as cm:
            self.inv_svc.finalize(ALICE, self.inv.id)
        self.assertIn("Credit Rating", str(cm.exception))

    def test_finish_with_valid_allocation(self):
        self.svc.allocate(ALICE, self.inv.id, "credit_rating", occupation_points=40)
        self.svc.allocate(ALICE, self.inv.id, "accounting", occupation_points=60)
        c = self.svc.check(ALICE, self.inv.id)
        self.assertTrue(c.ok, c.errors)
        self.assertTrue(any("200 occupation points unspent" in w for w in c.warnings))
        with self.assertRaises(UnspentPoints) as cm:          # unspent points need explicit confirmation
            self.inv_svc.finalize(ALICE, self.inv.id)
        self.assertEqual((cm.exception.occupation, cm.exception.interest), (200, 160))
        self.assertEqual(self.inv_svc.get(ALICE, self.inv.id).status, "draft")
        inv = self.inv_svc.finalize(ALICE, self.inv.id, confirm_unspent=True)
        self.assertEqual((inv.status, inv.creation_validated), ("ready", True))
        # creation steps are closed once finished; progression edits are manual again
        with self.assertRaises(InvalidState):
            self.svc.allocate(ALICE, self.inv.id, "law", occupation_points=10)
        self.assertEqual(self.inv_svc.set_skill(ALICE, self.inv.id, "accounting", 70).skills["accounting"], 70)

    def test_basic_sheet_path_unchanged_without_occupation(self):
        other = self.inv_svc.create(ALICE, "Basic")
        self.inv_svc.set_characteristics(ALICE, other.id, FULL)
        inv = self.inv_svc.finalize(ALICE, other.id)
        self.assertEqual((inv.status, inv.creation_validated), ("ready", False))


class OccupationChangeTests(Base):
    def test_change_requires_confirmation_and_keeps_interest(self):
        self.accountant()
        self.svc.allocate(ALICE, self.inv.id, "accounting", occupation_points=50, interest_points=5)
        self.svc.allocate(ALICE, self.inv.id, "climb", interest_points=10)
        with self.assertRaises(InvalidState):
            self.svc.set_occupation(ALICE, self.inv.id, "antiquarian")
        inv = self.svc.set_occupation(ALICE, self.inv.id, "antiquarian", force=True)
        self.assertEqual(inv.occupation, "Antiquarian")
        self.assertEqual(load_picks(self.conn, self.inv.id), {})
        self.assertEqual(load_points(self.conn, self.inv.id), {"accounting": (0, 5), "climb": (0, 10)})
        self.assertEqual(inv.skills["accounting"], 5 + 5)

    def test_replacing_a_pick_drops_its_occupation_points(self):
        self.accountant()  # picks psychology, charm
        self.svc.allocate(ALICE, self.inv.id, "charm", occupation_points=20, interest_points=5)
        self.svc.set_pick(ALICE, self.inv.id, 6, 1, "dodge")
        self.assertEqual(load_points(self.conn, self.inv.id)["charm"], (0, 5))


class OwnershipAndStateTests(Base):
    def test_other_user_cannot_touch_creation(self):
        self.svc.set_occupation(ALICE, self.inv.id, "accountant")
        calls = [
            lambda: self.svc.set_occupation(BOB, self.inv.id, "antiquarian", force=True),
            lambda: self.svc.set_pick(BOB, self.inv.id, 6, 0, "charm"),
            lambda: self.svc.allocate(BOB, self.inv.id, "law", occupation_points=10),
            lambda: self.svc.check(BOB, self.inv.id),
            lambda: self.svc.create_homebrew(BOB, self.inv.id, "Thief", "5-40", "EDU*4"),
        ]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(NotFound):
                call()

    def test_finished_investigator_cannot_change_occupation(self):
        basic = self.inv_svc.create(ALICE, "Basic")
        self.inv_svc.set_characteristics(ALICE, basic.id, FULL)
        self.inv_svc.finalize(ALICE, basic.id)
        with self.assertRaises(InvalidState):
            self.svc.set_occupation(ALICE, basic.id, "accountant")


class HomebrewTests(Base):
    def test_homebrew_occupation(self):
        inv = self.svc.create_homebrew(ALICE, self.inv.id, "Ghost Hunter", "10-40", "EDU*2 + POW*2")
        occ = self.svc.occupation_of(inv)
        self.assertTrue(occ.is_homebrew)
        self.assertEqual((occ.credit_min, occ.credit_max, occ.formula_text()), (10, 40, "EDU×2 + POW×2"))
        self.assertEqual(sum(s.count for s in occ.slots), 8)
        self.assertEqual(self.svc.check(ALICE, inv.id).occupation_budget.total, 75 * 2 + 45 * 2)
        for n, skill in enumerate(["occult", "history", "library_use", "spot_hidden", "listen",
                                   "electronics", "photography", "stealth"]):
            self.svc.set_pick(ALICE, inv.id, 0, n, skill)
        self.assertEqual(self.svc.next_step(self.inv_svc.get(ALICE, inv.id)), "allocation")
        # private to its creator
        self.assertTrue(catalogue.search(self.conn, ALICE, "ghost"))
        self.assertFalse(catalogue.search(self.conn, BOB, "ghost"))
        self.assertIsNone(catalogue.find(self.conn, BOB, str(occ.id)))

    def test_homebrew_validation(self):
        for rng, formula in [("40-10", "EDU*4"), ("abc", "EDU*4"), ("0-100", "EDU*4"), ("10-40", "EDU*2+LUCK*2"),
                             ("10-40", "EDU*3")]:
            with self.subTest(rng=rng, formula=formula), self.assertRaises(ValidationError):
                self.svc.create_homebrew(ALICE, self.inv.id, "X", rng, formula)


class PersistenceAndUpgradeTests(unittest.TestCase):
    def test_creation_survives_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            inv_svc, svc = InvestigatorService(conn), CreationService(conn)
            inv = inv_svc.create(ALICE, "Ada")
            inv_svc.set_characteristics(ALICE, inv.id, FULL)
            svc.set_occupation(ALICE, inv.id, "accountant")
            svc.set_pick(ALICE, inv.id, 6, 0, "charm")
            svc.allocate(ALICE, inv.id, "credit_rating", occupation_points=35)
            conn.close()

            conn2 = fresh_db(path)
            svc2 = CreationService(conn2)
            inv2 = InvestigatorService(conn2).get(ALICE, inv.id)
            self.assertEqual(svc2.occupation_of(inv2).key, "accountant")
            self.assertEqual(load_picks(conn2, inv.id), {(6, 0): "charm"})
            self.assertEqual(inv2.skills["credit_rating"], 35)
            self.assertEqual(svc2.next_step(inv2), "choices")   # resumes where it stopped
            conn2.close()

    def test_m1_database_upgrades_without_losing_investigators(self):
        """A database at the Milestone 1 schema (001-008) gains M2 without data loss."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m1.sqlite3")
            raw = sqlite3.connect(path)
            raw.execute("PRAGMA foreign_keys = ON")
            raw.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                        "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            for p in sorted(MIGRATIONS_DIR.glob("00[1-8]_*")):
                if p.suffix == ".sql":
                    raw.executescript(p.read_text(encoding="utf-8"))
                raw.execute("INSERT INTO schema_migrations(version, name) VALUES (?, ?)", (int(p.name[:3]), p.name))
            raw.execute("INSERT INTO investigators (owner_user_id, name, status, char_str, char_dex, luck) "
                        "VALUES ('1001', 'Old Ada', 'ready', 50, 60, 40)")
            iid = raw.execute("SELECT investigator_id FROM investigators").fetchone()[0]
            raw.execute("INSERT INTO investigator_skills (investigator_id, skill_key, value) VALUES (?, 'listen', 55)",
                        (iid,))
            raw.commit()
            raw.close()

            conn = connect(path)
            applied = bootstrap(conn)
            self.assertEqual(applied, ["009_fix_skill_base_values.sql", "010_occupations.sql",
                                       "011_skill_point_stat_choice.sql", "012_user_preferences.sql",
                                       "013_campaigns.sql", "014_rolls.sql",
                                       "015_rolls_target_range.py", "016_investigator_imports.sql"])
            inv = InvestigatorService(conn).get("1001", iid)
            self.assertEqual((inv.name, inv.status, inv.occupation_id, inv.skills), ("Old Ada", "ready", None,
                                                                                      {"listen": 55}))
            self.assertEqual(conn.execute("SELECT source FROM investigator_skills").fetchone()[0], "manual")
            conn.close()


class ManualSkillBypassRegressionTests(Base):
    """Review finding: hand-entered skills must never reach creation_validated."""

    def _finishable(self, inv_id):
        self.svc.set_occupation(ALICE, inv_id, "accountant")
        self.svc.set_pick(ALICE, inv_id, 6, 0, "psychology")
        self.svc.set_pick(ALICE, inv_id, 6, 1, "charm")
        self.svc.allocate(ALICE, inv_id, "credit_rating", occupation_points=40)

    def _manual_before_occupation(self, values):
        """Hand-entered values made while the draft had no occupation (Milestone 1 behaviour)."""
        for skill, value in values.items():
            self.inv_svc.set_skill(ALICE, self.inv.id, skill, value)
        self._finishable(self.inv.id)

    def test_service_bypass_is_closed(self):
        self._manual_before_occupation({"cthulhu_mythos": 30, "occult": 150, "listen": 60})
        check = self.svc.check(ALICE, self.inv.id)
        self.assertFalse(check.ok)
        joined = " ".join(check.errors)
        self.assertIn("Cthulhu Mythos is 30", joined)
        self.assertIn("Occult is 150", joined)
        self.assertIn("Listen is 60", joined)
        for confirm in (False, True):
            with self.assertRaises(InvalidState):
                self.inv_svc.finalize(ALICE, self.inv.id, confirm_unspent=confirm)
        inv = self.inv_svc.get(ALICE, self.inv.id)
        self.assertEqual((inv.status, inv.creation_validated), ("draft", False))
        # nothing was deleted behind the player's back
        self.assertEqual({k: inv.skills[k] for k in ("cthulhu_mythos", "occult", "listen")},
                         {"cthulhu_mythos": 30, "occult": 150, "listen": 60})
        self.assertEqual(self.svc.next_step(inv), "review")

    def test_value_equal_to_base_is_harmless(self):
        self._manual_before_occupation({"listen": 20})
        self.assertTrue(self.svc.check(ALICE, self.inv.id).ok)

    def test_reconcile_convert_and_discard(self):
        self._manual_before_occupation({"cthulhu_mythos": 30, "occult": 150, "listen": 60, "accounting": 45})
        # Cthulhu Mythos cannot be converted; a value above 99 cannot be converted
        with self.assertRaises(ValidationError):
            self.svc.reconcile(ALICE, self.inv.id, "cthulhu_mythos", "convert")
        with self.assertRaises(ValidationError):
            self.svc.reconcile(ALICE, self.inv.id, "occult", "convert")
        self.svc.reconcile(ALICE, self.inv.id, "cthulhu_mythos", "discard")
        self.svc.reconcile(ALICE, self.inv.id, "occult", "discard")
        # Listen is an occupation skill: 40 occupation points; Climb-like skills would use interest
        inv = self.svc.reconcile(ALICE, self.inv.id, "listen", "convert")
        self.assertEqual(load_points(self.conn, self.inv.id)["listen"], (40, 0))
        self.assertEqual(inv.skills["listen"], 60)
        inv = self.svc.reconcile(ALICE, self.inv.id, "accounting", "convert")
        self.assertEqual(inv.skills["accounting"], 45)
        self.assertNotIn("cthulhu_mythos", inv.skills)
        self.assertNotIn("occult", inv.skills)
        self.assertEqual(self.svc.unreconciled(inv), {})
        inv = self.inv_svc.finalize(ALICE, self.inv.id, confirm_unspent=True)
        self.assertTrue(inv.creation_validated)

    def test_convert_non_occupation_skill_uses_interest_and_respects_budget(self):
        self.inv_svc.set_skill(ALICE, self.inv.id, "climb", 70)       # base 20 -> 50 interest
        self.inv_svc.set_skill(ALICE, self.inv.id, "swim", 99)        # base 20 -> 79 interest
        self.inv_svc.set_skill(ALICE, self.inv.id, "jump", 90)        # base 20 -> 70 interest
        self._finishable(self.inv.id)
        self.svc.reconcile(ALICE, self.inv.id, "climb", "convert")
        self.svc.reconcile(ALICE, self.inv.id, "swim", "convert")      # 129 of 160 interest used
        self.assertEqual(load_points(self.conn, self.inv.id)["climb"], (0, 50))
        with self.assertRaises(ValidationError) as cm:
            self.svc.reconcile(ALICE, self.inv.id, "jump", "convert")
        self.assertIn("31 personal interest points are left", str(cm.exception))
        self.assertEqual(self.inv_svc.get(ALICE, self.inv.id).skills["jump"], 90)   # unchanged

    def test_allocate_on_unreconciled_skill_refused(self):
        self._manual_before_occupation({"listen": 60})
        with self.assertRaises(InvalidState):
            self.svc.allocate(ALICE, self.inv.id, "listen", occupation_points=10)

    def test_other_user_cannot_reconcile(self):
        self._manual_before_occupation({"listen": 60})
        with self.assertRaises(NotFound):
            self.svc.reconcile(BOB, self.inv.id, "listen", "discard")

    def test_m1_ready_investigators_untouched(self):
        basic = self.inv_svc.create(ALICE, "Basic")
        self.inv_svc.set_characteristics(ALICE, basic.id, FULL)
        self.inv_svc.set_skill(ALICE, basic.id, "cthulhu_mythos", 12)
        self.inv_svc.finalize(ALICE, basic.id)
        inv = self.inv_svc.set_skill(ALICE, basic.id, "occult", 150)      # progression edits still allowed
        self.assertEqual((inv.skills["cthulhu_mythos"], inv.skills["occult"], inv.creation_validated),
                         (12, 150, False))


class FormulaChoiceTests(Base):
    def test_choice_persisted_and_used(self):
        self.svc.set_occupation(ALICE, self.inv.id, "private_investigator")   # EDU×2 + (DEX or STR)×2
        c = self.svc.check(ALICE, self.inv.id)
        self.assertEqual(c.occupation_budget.total, 150 + 140)             # suggested: DEX 70
        self.assertIn("suggested", c.occupation_budget.explanation)
        inv = self.svc.set_formula_stat(ALICE, self.inv.id, "str")
        self.assertEqual(inv.skill_point_stat, "STR")
        c = self.svc.check(ALICE, self.inv.id)
        self.assertEqual(c.occupation_budget.total, 150 + 120)
        self.assertIn("chosen from DEX/STR", c.occupation_budget.explanation)
        with self.assertRaises(ValidationError):
            self.svc.set_formula_stat(ALICE, self.inv.id, "APP")
        inv = self.svc.set_formula_stat(ALICE, self.inv.id, None)
        self.assertIsNone(inv.skill_point_stat)

    def test_choice_cleared_on_occupation_change_and_rejected_without_alternatives(self):
        self.svc.set_occupation(ALICE, self.inv.id, "private_investigator")
        self.svc.set_formula_stat(ALICE, self.inv.id, "STR")
        inv = self.svc.set_occupation(ALICE, self.inv.id, "accountant", force=True)
        self.assertIsNone(inv.skill_point_stat)
        with self.assertRaises(ValidationError):
            self.svc.set_formula_stat(ALICE, self.inv.id, "STR")

    def test_lower_choice_can_overspend_but_keeps_points(self):
        self.svc.set_occupation(ALICE, self.inv.id, "private_investigator")
        for skill in ("law", "library_use", "psychology", "stealth"):
            self.svc.allocate(ALICE, self.inv.id, skill, occupation_points=70)      # 280 of 290
        self.svc.set_formula_stat(ALICE, self.inv.id, "STR")                       # budget 270
        self.assertIn("Occupation points overspent by 10.", self.svc.check(ALICE, self.inv.id).errors)
        self.assertEqual(load_points(self.conn, self.inv.id)["law"], (70, 0))


class CatalogueReplaceabilityTests(unittest.TestCase):
    def _bootstrap_with(self, value):
        import cocbot.occupations.catalogue as cat
        old = os.environ.get("COC_OCCUPATIONS_PATH")
        os.environ["COC_OCCUPATIONS_PATH"] = value
        try:
            conn = connect(":memory:")
            logs = []
            bootstrap(conn, log=logs.append)
            n = conn.execute("SELECT COUNT(*) FROM occupations").fetchone()[0]
            conn.close()
            return n, logs, cat
        finally:
            if old is None:
                os.environ.pop("COC_OCCUPATIONS_PATH", None)
            else:
                os.environ["COC_OCCUPATIONS_PATH"] = old

    def test_catalogue_can_be_disabled(self):
        n, logs, _ = self._bootstrap_with("none")
        self.assertEqual(n, 0)
        self.assertTrue(any("disabled" in line for line in logs))

    def test_empty_value_means_default(self):
        n, _, _ = self._bootstrap_with("")
        self.assertEqual(n, 113)

    def test_missing_file_is_reported_not_fatal(self):
        n, logs, _ = self._bootstrap_with("/nonexistent/occupations.json")
        self.assertEqual(n, 0)
        self.assertTrue(any("not found" in line for line in logs))

    def test_alternative_catalogue_file(self):
        data = json.loads(catalogue.CATALOGUE_PATH.read_text(encoding="utf-8"))
        data["occupations"] = data["occupations"][:3]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mine.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            n, logs, _ = self._bootstrap_with(str(p))
        self.assertEqual(n, 3)


if __name__ == "__main__":
    unittest.main()
