"""015: widen ``rolls.target`` to 0-999 on databases that applied the first version of 014.

The first version of migration 014 (PR #5 before its review fixes) declared
``target INTEGER NOT NULL CHECK (target BETWEEN 1 AND 100)``, which rejects
valid checks on 0-valued skills/Luck and on skills above 100. The current
014 declares ``BETWEEN 0 AND 999``, so fresh databases need nothing here.

SQLite cannot alter a CHECK constraint, so on an affected database the table
is rebuilt with SQLite's documented procedure (foreign keys off, copy every
row into a new table, drop, rename, recreate indexes and triggers, verify
foreign keys). Every row, ID and the AUTOINCREMENT counter are kept;
``roll_events`` and pushed-attempt links keep pointing at the same roll IDs.
The rebuild runs in its own transaction: on any error nothing changes.

Idempotent: a database whose ``rolls`` table already has the wide range (or
no ``rolls`` table) is left untouched.
"""
from __future__ import annotations

import re
import sqlite3

OLD_CHECK = re.compile(r"CHECK\s*\(\s*target\s+BETWEEN\s+1\s+AND\s+100\s*\)", re.IGNORECASE)
NEW_CHECK = "CHECK (target BETWEEN 0 AND 999)"
_CREATE = re.compile(r"^\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?[\"`\[]?rolls[\"`\]]?\s*\(", re.IGNORECASE)


def needs_upgrade(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='rolls'").fetchone()
    return bool(row and OLD_CHECK.search(row[0]))


def upgrade(conn: sqlite3.Connection) -> None:
    if not needs_upgrade(conn):
        return
    table_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='rolls'").fetchone()[0]
    new_sql, n = _CREATE.subn("CREATE TABLE rolls_new (", OLD_CHECK.sub(NEW_CHECK, table_sql), count=1)
    if n != 1:
        raise RuntimeError("015: unexpected rolls table definition; not rebuilding")
    dependents = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='rolls' AND type IN ('index', 'trigger') "
        "AND sql IS NOT NULL ORDER BY type, name")]
    columns = ", ".join(f'"{r[1]}"' for r in conn.execute("PRAGMA table_info(rolls)"))

    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")          # only takes effect outside a transaction
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = conn.execute("SELECT count(*), coalesce(max(roll_id), 0) FROM rolls").fetchone()
            conn.execute(new_sql)
            conn.execute(f"INSERT INTO rolls_new ({columns}) SELECT {columns} FROM rolls")
            conn.execute("DROP TABLE rolls")
            conn.execute("ALTER TABLE rolls_new RENAME TO rolls")
            for sql in dependents:
                conn.execute(sql)
            after = conn.execute("SELECT count(*), coalesce(max(roll_id), 0) FROM rolls").fetchone()
            if tuple(before) != tuple(after):
                raise RuntimeError(f"015: row count/IDs changed during rebuild ({tuple(before)} → {tuple(after)})")
            problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"015: foreign key check failed after rebuild: {problems[:5]}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
