from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

from cocbot.config import settings

PathLike = Union[str, Path]


def connect(db_path: Optional[PathLike] = None) -> sqlite3.Connection:
    """Open a SQLite connection with the project's standard settings.

    Every connection in the project should come from here so that foreign
    keys are enforced and rows are addressable by column name.
    ``":memory:"`` is accepted for tests.
    """
    path = str(db_path) if db_path is not None else str(settings.DB_PATH)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_conn(db_path: Optional[PathLike] = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
