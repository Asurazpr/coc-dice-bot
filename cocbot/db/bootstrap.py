"""Bring a database fully up to date: schema migrations, then the occupation catalogue."""
from __future__ import annotations

import sqlite3
from typing import Callable, List, Optional

from cocbot.db.migrate import migrate


def bootstrap(conn: sqlite3.Connection, log: Optional[Callable[[str], None]] = None) -> List[str]:
    """Run pending migrations and sync built-in occupations. Returns applied migrations."""
    from cocbot.occupations.catalogue import configured_catalogue_path, sync_builtin

    applied = migrate(conn, log=log)
    path = configured_catalogue_path()
    if path is None:
        if log:
            log("[catalogue] built-in occupation catalogue disabled (COC_OCCUPATIONS_PATH=none)")
    elif not path.exists():
        # Never retire existing entries because a file is missing; just report it.
        if log:
            log(f"[catalogue] WARNING: {path} not found; built-in occupations not updated")
    else:
        counts = sync_builtin(conn, path)
        if log:
            log(f"[catalogue] {counts['builtin']} built-in occupations from {path.name}, "
                f"{counts['retired']} retired")
    return applied
