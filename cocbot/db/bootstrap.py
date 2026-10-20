"""Bring a database fully up to date: schema migrations, then the occupation catalogue."""
from __future__ import annotations

import sqlite3
from typing import Callable, List, Optional

from cocbot.db.migrate import migrate


def bootstrap(conn: sqlite3.Connection, log: Optional[Callable[[str], None]] = None) -> List[str]:
    """Run pending migrations and sync built-in occupations. Returns applied migrations."""
    from cocbot.occupations.catalogue import sync_builtin

    applied = migrate(conn, log=log)
    counts = sync_builtin(conn)
    if log:
        log(f"[catalogue] {counts['builtin']} built-in occupations, {counts['retired']} retired")
    return applied
