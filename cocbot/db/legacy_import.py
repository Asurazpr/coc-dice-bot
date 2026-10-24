"""Best-effort, non-destructive import of pre-006 character data.

The previous code *read* from ``characters`` / ``attributes`` /
``guild_settings`` tables that no migration in the repository ever created,
so any such tables in a real database were made by hand or by an earlier,
unpublished script. Their exact shape is unknown, so this module inspects
the columns before reading and never modifies or drops the legacy tables.

Rules:
* A legacy row is imported only when an owner can be determined (an owner
  column in ``characters``, or ``default_owner`` passed by an operator).
* Each legacy id is imported at most once (``legacy_character_map``).
* Name clashes with the owner's existing investigators get a ``(legacy N)``
  suffix instead of failing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_OWNER_COLUMNS = ("owner_user_id", "user_id", "discord_user_id", "owner_id", "player_id")
_ID_COLUMNS = ("character_id", "id")
_NAME_COLUMNS = ("name", "character_name")
_CHARS = ("str", "con", "siz", "dex", "app", "int", "pow", "edu")


@dataclass
class LegacyImportReport:
    imported: List[int] = field(default_factory=list)          # legacy ids
    skipped_no_owner: List[int] = field(default_factory=list)
    already_imported: List[int] = field(default_factory=list)


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(r[1]).lower() for r in rows]


def _pick(cols: List[str], candidates) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    return None


def _unique_name(conn: sqlite3.Connection, owner: str, name: str, legacy_id: int) -> str:
    clash = conn.execute(
        "SELECT 1 FROM investigators WHERE owner_user_id=? AND name=? COLLATE NOCASE AND status<>'archived'",
        (owner, name),
    ).fetchone()
    return f"{name} (legacy {legacy_id})"[:64] if clash else name[:64]


def import_legacy_characters(
    conn: sqlite3.Connection, default_owner: Optional[str] = None
) -> LegacyImportReport:
    report = LegacyImportReport()
    tables = {r[0].lower() for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    legacy: Dict[int, Dict[str, object]] = {}

    if "characters" in tables:
        cols = _table_columns(conn, "characters")
        id_col = _pick(cols, _ID_COLUMNS)
        if id_col:
            owner_col = _pick(cols, _OWNER_COLUMNS)
            name_col = _pick(cols, _NAME_COLUMNS)
            for row in conn.execute("SELECT * FROM characters"):
                d = {k.lower(): row[k] for k in row.keys()}
                lid = int(d[id_col])
                legacy[lid] = {
                    "owner": d.get(owner_col) if owner_col else None,
                    "name": d.get(name_col) if name_col else None,
                    "hp": d.get("hp"),
                    "mp": d.get("mp"),
                    "san": d.get("san"),
                    "luck": d.get("luck"),
                }

    if "attributes" in tables:
        cols = _table_columns(conn, "attributes")
        if "character_id" in cols:
            for row in conn.execute("SELECT * FROM attributes"):
                d = {k.lower(): row[k] for k in row.keys()}
                lid = int(d["character_id"])
                entry = legacy.setdefault(lid, {"owner": None, "name": None})
                for c in _CHARS:
                    entry[c] = d.get(c)
                if d.get("luck") is not None:
                    entry["luck"] = d.get("luck")

    def _int(v) -> Optional[int]:
        try:
            return None if v is None or v == "" else int(float(v))
        except (TypeError, ValueError):
            return None

    for lid in sorted(legacy):
        done = conn.execute(
            "SELECT 1 FROM legacy_character_map WHERE legacy_character_id=?", (lid,)
        ).fetchone()
        if done:
            report.already_imported.append(lid)
            continue
        data = legacy[lid]
        owner = data.get("owner") or default_owner
        if not owner:
            report.skipped_no_owner.append(lid)
            continue
        owner = str(owner)
        name = str(data.get("name") or f"Legacy character {lid}").strip() or f"Legacy character {lid}"
        name = _unique_name(conn, owner, name, lid)
        chars = {c: _int(data.get(c)) for c in _CHARS}
        complete = all(v is not None for v in chars.values())
        cur = conn.execute(
            """
            INSERT INTO investigators (owner_user_id, name, status,
              char_str, char_con, char_siz, char_dex, char_app, char_int, char_pow, char_edu,
              luck, hp_current, mp_current, san_current, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner, name, "ready" if complete else "draft",
                *(chars[c] for c in _CHARS),
                _int(data.get("luck")), _int(data.get("hp")), _int(data.get("mp")), _int(data.get("san")),
                f"Imported from legacy character #{lid}.",
            ),
        )
        conn.execute(
            "INSERT INTO legacy_character_map(legacy_character_id, investigator_id) VALUES (?, ?)",
            (lid, cur.lastrowid),
        )
        report.imported.append(lid)

    return report
