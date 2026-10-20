import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cocbot.db.connection import connect
from cocbot.db.legacy_import import import_legacy_characters
from cocbot.db.migrate import MIGRATIONS_DIR, applied_versions, discover, migrate


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


class MigrationTests(unittest.TestCase):
    def test_fresh_database(self):
        conn = connect(":memory:")
        applied = migrate(conn)
        self.assertEqual([m.name for m in discover()], applied)
        self.assertTrue({"investigators", "investigator_skills", "active_investigators", "skill_defs"} <= _tables(conn))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM skill_defs").fetchone()[0], 85)
        self.assertEqual(migrate(conn), [])  # idempotent
        conn.close()

    def test_versions_unique_and_ordered(self):
        versions = [m.version for m in discover()]
        self.assertEqual(versions, sorted(set(versions)))

    def test_upgrade_database_created_by_old_apply_sql(self):
        """Old apply_sql.py ran 001-005 without tracking. Data must survive."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.sqlite3")
            raw = sqlite3.connect(path)
            for p in sorted(Path(MIGRATIONS_DIR).glob("00[1-5]_*.sql")):
                raw.executescript(p.read_text(encoding="utf-8"))
            # a user customisation that must not be lost
            raw.execute(
                "INSERT INTO skill_def_aliases(lang, alias, skill_id) "
                "SELECT 'en', 'sh', skill_id FROM skill_defs WHERE key='spot_hidden'"
            )
            raw.commit()
            raw.close()

            conn = connect(path)
            migrate(conn)
            self.assertEqual(applied_versions(conn), {m.version for m in discover()})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM skill_defs").fetchone()[0], 85)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM skill_def_aliases WHERE alias='sh'").fetchone())
            conn.close()

    def test_legacy_tables_imported_and_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "legacy.sqlite3")
            raw = sqlite3.connect(path)
            raw.executescript(
                """
                CREATE TABLE guild_settings (guild_id TEXT PRIMARY KEY, active_character_id INTEGER);
                CREATE TABLE characters (id INTEGER PRIMARY KEY, guild_id TEXT, user_id TEXT, name TEXT,
                                         hp INTEGER, san INTEGER, mp INTEGER);
                CREATE TABLE attributes (character_id INTEGER PRIMARY KEY, str INT, con INT, siz INT, dex INT,
                                         app INT, int INT, pow INT, edu INT);
                INSERT INTO characters VALUES (1, 'g1', '555', 'Old Timer', 9, 50, 8);
                INSERT INTO attributes VALUES (1, 50, 50, 50, 50, 50, 50, 50, 50);
                INSERT INTO attributes VALUES (2, 40, 40, 40, 40, 40, 40, 40, 40);  -- no owner known
                INSERT INTO guild_settings VALUES ('g1', 1);
                """
            )
            raw.commit()
            raw.close()

            conn = connect(path)
            migrate(conn)
            rows = conn.execute("SELECT * FROM investigators").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["owner_user_id"], rows[0]["name"], rows[0]["char_pow"], rows[0]["hp_current"]),
                             ("555", "Old Timer", 50, 9))
            # legacy tables untouched
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attributes").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM guild_settings").fetchone()[0], 1)

            # operator assigns the orphan; re-running is idempotent
            report = import_legacy_characters(conn, default_owner="777")
            conn.commit()
            self.assertEqual(report.imported, [2])
            self.assertEqual(report.already_imported, [1])
            again = import_legacy_characters(conn, default_owner="777")
            self.assertEqual(again.imported, [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM investigators").fetchone()[0], 2)
            conn.close()


if __name__ == "__main__":
    unittest.main()
