"""007: import hand-made pre-006 `characters` / `attributes` rows, if any.

Non-destructive: legacy tables are only read. Rows without a determinable
owner are left for an operator to assign with
``python scripts/import_legacy_characters.py --owner <discord_user_id>``.
"""
from __future__ import annotations

import sqlite3

from cocbot.db.legacy_import import import_legacy_characters


def upgrade(conn: sqlite3.Connection) -> None:
    report = import_legacy_characters(conn)
    if report.imported or report.skipped_no_owner:
        print(
            f"[migrate] legacy characters imported={report.imported} "
            f"needs_owner={report.skipped_no_owner}"
        )
