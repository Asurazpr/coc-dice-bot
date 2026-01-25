from __future__ import annotations

from typing import Optional, Tuple
import sqlite3

from cocbot.db.characters import get_active_character_id, get_character_stats
from cocbot.mechanics.derived import eval_derived_formula

def resolve_skill_base(
    conn: sqlite3.Connection,
    guild_id: str,
    skill_id: int,
) -> Tuple[Optional[int], str]:
    """
    Returns (target_or_None, label_for_display)

    Normal skill:
      -> (base, "Base X")

    Derived skill:
      - if no active character: (None, "Base DEX/2 (no active character)")
      - if missing stat: (None, "Base DEX/2 (DEX missing)")
      - if computable: (val, "Base val (DEX=.. → DEX/2=..)")
    """
    row = conn.execute(
        "SELECT base, is_derived, derived_formula FROM skill_defs WHERE skill_id=?",
        (int(skill_id),),
    ).fetchone()
    if not row:
        return None, "Base ?"

    base, is_derived, formula = row[0], int(row[1] or 0), row[2]

    if not is_derived:
        b = int(base or 0)
        return b, f"Base {b}"

    f = (formula or "").strip()
    cid = get_active_character_id(conn, guild_id)
    if cid is None:
        return None, f"Base {f} (no active character)" if f else "Base (derived)"

    stats = get_character_stats(conn, cid)
    val, explain = eval_derived_formula(f, stats)
    if val is None:
        return None, f"Base {f} ({explain})" if f else f"Base (derived) ({explain})"

    return int(val), f"Base {int(val)} ({explain})"
