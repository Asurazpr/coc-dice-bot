from __future__ import annotations

from typing import Optional, Dict
import sqlite3

def get_active_character_id(conn: sqlite3.Connection, guild_id: str) -> Optional[int]:
    row = conn.execute(
        "SELECT active_character_id FROM guild_settings WHERE guild_id=?",
        (guild_id,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])

def set_active_character_id(conn: sqlite3.Connection, guild_id: str, character_id: int) -> None:
    conn.execute(
        """
        INSERT INTO guild_settings (guild_id, active_character_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET active_character_id=excluded.active_character_id
        """,
        (guild_id, int(character_id)),
    )

def get_character_stats(conn: sqlite3.Connection, character_id: int) -> Dict[str, int]:
    """
    Read from attributes table (lowercase columns) and return uppercased dict.
    """
    row = conn.execute(
        """
        SELECT str, con, siz, dex, app, int, pow, edu
        FROM attributes
        WHERE character_id=?
        """,
        (int(character_id),),
    ).fetchone()

    if not row:
        return {}

    keys = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"]
    stats: Dict[str, int] = {}
    for k, v in zip(keys, row):
        if v is None:
            continue
        try:
            stats[k] = int(v)
        except Exception:
            pass
    return stats
