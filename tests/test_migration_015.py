"""Migration 015: forward upgrade for databases that applied the first (1-100 target) version of 014."""
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cocbot.campaigns.rules import CampaignRules
from cocbot.db.bootstrap import bootstrap
from cocbot.db.connection import connect
from cocbot.db.migrate import MIGRATIONS_DIR, migrate

FIXTURE = Path(__file__).parent / "fixtures" / "014_rolls_v1.sql"
ALICE = "1001"


def load_015():
    path = MIGRATIONS_DIR / "015_rolls_target_range.py"
    spec = importlib.util.spec_from_file_location("m015", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_sql(conn):
    return conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='rolls'").fetchone()[0]


class Migration015Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "bot.sqlite3")
        self.m015 = load_015()

    def tearDown(self):
        self.tmp.cleanup()

    def old_database(self):
        """A database at 001-013 plus the first version of 014, with some roll history."""
        mig_dir = Path(self.tmp.name) / "old_migrations"
        mig_dir.mkdir()
        for p in MIGRATIONS_DIR.iterdir():
            if p.is_file() and p.name[:3].isdigit() and int(p.name[:3]) <= 13:
                shutil.copy(p, mig_dir / p.name)
            elif p.is_dir() and p.name == "lang":
                shutil.copytree(p, mig_dir / p.name)
        shutil.copy(FIXTURE, mig_dir / "014_rolls.sql")
        conn = connect(self.path)
        migrate(conn, mig_dir)
        self.assertTrue(self.m015.needs_upgrade(conn))
        cols = ("user_id, luck_scope, subject_kind, subject_label, target, dice_json, natural, natural_level, "
                "rules_json, visibility, attempt, parent_roll_id, effective, effective_level")
        rules = CampaignRules().to_json()
        dice = json.dumps({"candidates": [62], "tens": [6], "ones": 2})
        for _ in range(3):
            conn.execute(f"INSERT INTO rolls ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ALICE, "personal", "skill", "Spot Hidden", 60, dice, 62, "FAIL", rules, "public",
                          "original", None, 62, "FAIL"))
        conn.execute(f"INSERT INTO rolls ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (ALICE, "personal", "skill", "Spot Hidden", 60, dice, 40, "SUCCESS", rules, "public",
                      "pushed", 3, 40, "SUCCESS"))
        conn.execute("UPDATE rolls SET luck_spent=2, effective=60, effective_level='SUCCESS', state_version=1 "
                     "WHERE roll_id=2")
        conn.execute("INSERT INTO roll_events (roll_id, actor_user_id, kind, old_effective, new_effective, "
                     "luck_before, luck_after, request_id) VALUES (2, ?, 'luck_spent', 62, 60, 50, 48, 'i-1')",
                     (ALICE,))
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):          # the old constraint really is there
            conn.execute(f"INSERT INTO rolls ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ALICE, "personal", "skill", "Cthulhu Mythos", 0, dice, 50, "FAIL", rules, "public",
                          "original", None, 50, "FAIL"))
        conn.rollback()
        return conn

    def test_fresh_database_needs_nothing(self):
        conn = connect(self.path)
        applied = bootstrap(conn)
        self.assertIn("015_rolls_target_range.py", applied)
        self.assertIn("BETWEEN 0 AND 999", table_sql(conn))
        self.assertFalse(self.m015.needs_upgrade(conn))
        conn.close()

    def test_old_database_is_rebuilt_without_losing_anything(self):
        conn = self.old_database()
        before = [tuple(r) for r in conn.execute("SELECT * FROM rolls ORDER BY roll_id")]
        events_before = [tuple(r) for r in conn.execute("SELECT * FROM roll_events")]
        applied = bootstrap(conn)
        self.assertEqual([a for a in applied if a.startswith("015")], ["015_rolls_target_range.py"])
        self.assertFalse(self.m015.needs_upgrade(conn))
        self.assertIn("BETWEEN 0 AND 999", table_sql(conn))
        self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM rolls ORDER BY roll_id")], before)
        self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM roll_events")], events_before)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        # every index and trigger is back
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='rolls'")}
        for name in ("idx_rolls_user", "idx_rolls_investigator", "idx_rolls_campaign", "trg_rolls_pushed_parent",
                     "trg_rolls_immutable", "trg_rolls_no_delete"):
            self.assertIn(name, names)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE rolls SET natural=1 WHERE roll_id=1")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM rolls WHERE roll_id=1")
        with self.assertRaises(sqlite3.IntegrityError):          # still a self-referencing foreign key
            conn.execute("INSERT INTO roll_events (roll_id, actor_user_id, kind) VALUES (999, 'x', 'luck_spent')")
        conn.rollback()
        conn.close()

    def test_new_targets_and_ids_continue_after_upgrade(self):
        from cocbot.campaigns.checks import plan_check
        from cocbot.characters.service import InvestigatorService
        from cocbot.mechanics import dice
        from cocbot.rolls.service import RollService
        from tests.helpers import SeqRandom
        conn = self.old_database()
        bootstrap(conn)
        inv_svc = InvestigatorService(conn)
        inv = inv_svc.create(ALICE, "Ada")
        inv_svc.set_characteristics(ALICE, inv.id, {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50,
                                                    "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50})
        inv_svc.finalize(ALICE, inv.id)
        inv_svc.select(ALICE, inv.id, 42)
        inv_svc.set_skill(ALICE, inv.id, "Cthulhu Mythos", 0)
        dice.set_rng(SeqRandom([0, 5]))
        try:
            roll = RollService(conn).roll_check(plan_check(conn, ALICE, 42, None, "Cthulhu Mythos"), ALICE)
        finally:
            import random
            dice.set_rng(random.Random())
        self.assertEqual((roll.id, roll.target), (5, 0))          # AUTOINCREMENT continued after the 4 old rows
        conn.close()

    def test_failure_leaves_the_database_unchanged(self):
        conn = self.old_database()
        conn.execute("CREATE TABLE rolls_new (x INTEGER)")       # makes the rebuild fail half-way
        conn.commit()
        before = [tuple(r) for r in conn.execute("SELECT * FROM rolls ORDER BY roll_id")]
        with self.assertRaises(sqlite3.OperationalError):
            self.m015.upgrade(conn)
        self.assertTrue(self.m015.needs_upgrade(conn))
        self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM rolls ORDER BY roll_id")], before)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_idempotent(self):
        conn = self.old_database()
        self.m015.upgrade(conn)
        sql = table_sql(conn)
        self.m015.upgrade(conn)
        self.assertEqual(table_sql(conn), sql)
        conn.close()


if __name__ == "__main__":
    unittest.main()
