from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from cocbot.db.connection import get_conn


@dataclass(frozen=True)
class SkillDef:
    skill_id: int
    key: str
    base: int
    category_key: str | None
    is_derived: int
    derived_formula: str | None
    display_name: str


def resolve_skill(query: str, lang: str = "en") -> Optional[SkillDef]:
    """
    Resolve user input -> skill definition using aliases first, then i18n name.
    """
    q = query.strip()
    if not q:
        return None

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """
            SELECT sd.skill_id, sd.key, sd.base, sd.category_key, sd.is_derived, sd.derived_formula,
                   i.name AS display_name
            FROM skill_def_aliases a
            JOIN skill_defs sd ON sd.skill_id = a.skill_id
            LEFT JOIN skill_def_i18n i ON i.skill_id = sd.skill_id AND i.lang = ?
            WHERE a.lang = ? AND a.alias = ?
            """,
            (lang, lang, q),
        ).fetchone()

        if row is None:
            row = conn.execute(
                """
                SELECT sd.skill_id, sd.key, sd.base, sd.category_key, sd.is_derived, sd.derived_formula,
                       i.name AS display_name
                FROM skill_def_i18n i
                JOIN skill_defs sd ON sd.skill_id = i.skill_id
                WHERE i.lang = ? AND i.name = ?
                """,
                (lang, q),
            ).fetchone()

        if row is None:
            return None

        display = row["display_name"] or row["key"]
        return SkillDef(
            skill_id=int(row["skill_id"]),
            key=str(row["key"]),
            base=int(row["base"]),
            category_key=row["category_key"],
            is_derived=int(row["is_derived"]),
            derived_formula=row["derived_formula"],
            display_name=str(display),
        )
