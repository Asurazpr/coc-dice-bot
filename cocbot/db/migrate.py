"""Versioned, non-destructive schema migrations.

Migrations live in ``data/sql`` and are named ``NNN_description.sql`` or
``NNN_description.py``. Each one is applied exactly once and recorded in the
``schema_migrations`` table.

* ``.sql`` files are run with ``executescript``. They manage their own
  ``BEGIN``/``COMMIT`` (the original seed files already do).
* ``.py`` files expose ``upgrade(conn)`` and run inside a transaction that the
  runner commits, so data migrations can inspect the database first.

Databases created by the old ``apply_sql.py`` (no ``schema_migrations``
table) are handled transparently: migrations 001-005 are idempotent
(``IF NOT EXISTS`` / ``INSERT OR IGNORE`` / ``ON CONFLICT``), so replaying
them is safe and they are then recorded.

Migrations never drop user data. One exception to "never rebuild a table":
``015_rolls_target_range.py`` rebuilds ``rolls`` (copying every row) on
databases that applied the first version of 014, because SQLite cannot alter
a CHECK constraint in place; it manages its own transaction to do so.
"""
from __future__ import annotations

import importlib.util
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from cocbot.config import repo_root

MIGRATIONS_DIR = repo_root() / "data" / "sql"
_NAME_RE = re.compile(r"^(\d{3})_[\w\-]+\.(sql|py)$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def discover(directory: Path = MIGRATIONS_DIR) -> List[Migration]:
    found: List[Migration] = []
    seen = {}
    for p in sorted(directory.iterdir()):
        m = _NAME_RE.match(p.name)
        if not m:
            continue
        version = int(m.group(1))
        if version in seen:
            raise RuntimeError(f"Duplicate migration version {version}: {seen[version]} and {p.name}")
        seen[version] = p.name
        found.append(Migration(version=version, name=p.name, path=p))
    return sorted(found, key=lambda m: m.version)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version    INTEGER PRIMARY KEY,
          name       TEXT NOT NULL,
          applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    conn.commit()


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    _ensure_table(conn)
    return {int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")}


def _run_python(conn: sqlite3.Connection, path: Path) -> None:
    spec = importlib.util.spec_from_file_location(f"cocbot_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    upgrade: Callable[[sqlite3.Connection], None] = module.upgrade
    upgrade(conn)


def migrate(
    conn: sqlite3.Connection,
    directory: Path = MIGRATIONS_DIR,
    log: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Apply all pending migrations. Returns the names that were applied."""
    log = log or (lambda _msg: None)
    done = applied_versions(conn)
    applied: List[str] = []
    for mig in discover(directory):
        if mig.version in done:
            continue
        try:
            if mig.path.suffix == ".sql":
                conn.executescript(mig.path.read_text(encoding="utf-8"))
            else:
                _run_python(conn, mig.path)
            conn.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (mig.version, mig.name),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        # executescript can leave foreign_keys toggled by the script; restore.
        conn.execute("PRAGMA foreign_keys = ON;")
        applied.append(mig.name)
        log(f"[migrate] applied {mig.name}")
    return applied
