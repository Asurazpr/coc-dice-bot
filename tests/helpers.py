"""Shared test fixtures (stdlib only)."""
from __future__ import annotations

import sqlite3
from typing import Iterable, List, Optional



def fresh_db(path: Optional[str] = None) -> sqlite3.Connection:
    """A fully migrated database (in memory unless a path is given)."""
    from cocbot.db.connection import connect
    from cocbot.db.migrate import migrate

    conn = connect(path or ":memory:")
    migrate(conn)
    return conn


class SeqRandom:
    """Deterministic stand-in for random.Random that returns queued values."""

    def __init__(self, values: Iterable[int]) -> None:
        self._values: List[int] = list(values)

    def randint(self, a: int, b: int) -> int:
        v = self._values.pop(0)
        assert a <= v <= b, f"queued {v} outside [{a}, {b}]"
        return v
