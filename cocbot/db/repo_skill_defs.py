from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from cocbot.db.connection import get_conn

SUPPORTED_LANGS: Sequence[str] = ("en", "zh")


@dataclass(frozen=True)
class SkillDef:
    skill_id: int
    key: str
    base: int
    category_key: str | None
    is_derived: int
    derived_formula: str | None
    display_name: str


def guess_lang(text: str) -> str:
    return "zh" if any("一" <= ch <= "鿿" for ch in text) else "en"


def _row_to_def(row: sqlite3.Row) -> SkillDef:
    return SkillDef(
        skill_id=int(row["skill_id"]),
        key=str(row["key"]),
        base=int(row["base"]),
        category_key=row["category_key"],
        is_derived=int(row["is_derived"]),
        derived_formula=row["derived_formula"],
        display_name=str(row["display_name"] or row["key"]),
    )


_SELECT = """
    SELECT sd.skill_id, sd.key, sd.base, sd.category_key, sd.is_derived, sd.derived_formula,
           i.name AS display_name
"""


def _resolve(conn: sqlite3.Connection, q: str, lang: str, display_lang: str) -> Optional[SkillDef]:
    # 1) alias (exact, then case-insensitive)
    for cmp in ("a.alias = ?", "lower(a.alias) = lower(?)"):
        row = conn.execute(
            _SELECT
            + f"""
            FROM skill_def_aliases a
            JOIN skill_defs sd ON sd.skill_id = a.skill_id
            LEFT JOIN skill_def_i18n i ON i.skill_id = sd.skill_id AND i.lang = ?
            WHERE a.lang = ? AND {cmp}
            """,
            (display_lang, lang, q),
        ).fetchone()
        if row is not None:
            return _row_to_def(row)

    # 2) localized display name (case-insensitive)
    row = conn.execute(
        _SELECT
        + """
        FROM skill_def_i18n n
        JOIN skill_defs sd ON sd.skill_id = n.skill_id
        LEFT JOIN skill_def_i18n i ON i.skill_id = sd.skill_id AND i.lang = ?
        WHERE n.lang = ? AND lower(n.name) = lower(?)
        """,
        (display_lang, lang, q),
    ).fetchone()
    return _row_to_def(row) if row is not None else None


def resolve_skill(
    query: str,
    lang: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[SkillDef]:
    """
    Resolve user input -> skill definition.

    Order: aliases, then localized names (the guessed language first, then the
    other supported languages), then the canonical key (``spot_hidden`` /
    ``spot hidden``). Matching is case-insensitive. The display name is
    returned in the language the user typed.
    """
    q = (query or "").strip()
    if not q:
        return None
    primary = lang or guess_lang(q)
    langs: List[str] = [primary] + [l for l in SUPPORTED_LANGS if l != primary]

    def run(c: sqlite3.Connection) -> Optional[SkillDef]:
        for l in langs:
            found = _resolve(c, q, l, primary)
            if found:
                return found
        key = re.sub(r"[\s\-/]+", "_", q.lower())
        row = c.execute(
            _SELECT
            + """
            FROM skill_defs sd
            LEFT JOIN skill_def_i18n i ON i.skill_id = sd.skill_id AND i.lang = ?
            WHERE sd.key = ?
            """,
            (primary, key),
        ).fetchone()
        return _row_to_def(row) if row is not None else None

    if conn is not None:
        return run(conn)
    with get_conn() as c:
        return run(c)


def skill_display_names(conn: sqlite3.Connection, keys: Iterable[str], lang: str = "en") -> dict:
    keys = list(keys)
    if not keys:
        return {}
    marks = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"""
        SELECT sd.key, COALESCE(i.name, sd.key) AS name
        FROM skill_defs sd
        LEFT JOIN skill_def_i18n i ON i.skill_id = sd.skill_id AND i.lang = ?
        WHERE sd.key IN ({marks})
        """,
        (lang, *keys),
    ).fetchall()
    return {r["key"]: r["name"] for r in rows}


def search_skills(conn: sqlite3.Connection, prefix: str, limit: int = 25) -> List[tuple]:
    """(key, display) pairs for autocomplete across all languages."""
    p = (prefix or "").strip().lower()
    rows = conn.execute(
        """
        SELECT sd.key AS key,
               MAX(CASE WHEN i.lang='en' THEN i.name END) AS en,
               MAX(CASE WHEN i.lang='zh' THEN i.name END) AS zh
        FROM skill_defs sd
        LEFT JOIN skill_def_i18n i ON i.skill_id = sd.skill_id
        GROUP BY sd.key
        HAVING ? = '' OR lower(sd.key) LIKE '%' || ? || '%'
            OR lower(COALESCE(en,'')) LIKE '%' || ? || '%'
            OR COALESCE(zh,'') LIKE '%' || ? || '%'
        ORDER BY COALESCE(en, sd.key)
        LIMIT ?
        """,
        (p, p.replace(" ", "_"), p, p, limit),
    ).fetchall()
    out = []
    for r in rows:
        label = r["en"] or r["key"]
        if r["zh"]:
            label = f"{label} / {r['zh']}"
        out.append((r["key"], label))
    return out
