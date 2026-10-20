"""SQL access for investigators. No permission logic lives here.

Callers outside ``cocbot.characters.service`` should not use this module
directly: the service is what enforces ownership.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Mapping, Optional

from cocbot.characters.models import CHAR_COLUMNS, Investigator

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"

_UPDATABLE = {
    "name", "status", "occupation", "age", "pronouns", "residence", "birthplace",
    "luck", "hp_current", "mp_current", "san_current", "creation_step", "notes", "archived_at",
    *CHAR_COLUMNS.values(),
}


def _row_to_investigator(row: sqlite3.Row, skills: Mapping[str, int]) -> Investigator:
    return Investigator(
        id=int(row["investigator_id"]),
        owner_user_id=str(row["owner_user_id"]),
        name=row["name"],
        status=row["status"],
        occupation=row["occupation"],
        age=row["age"],
        pronouns=row["pronouns"],
        residence=row["residence"],
        birthplace=row["birthplace"],
        characteristics={k: row[col] for k, col in CHAR_COLUMNS.items()},
        luck=row["luck"],
        hp_current=row["hp_current"],
        mp_current=row["mp_current"],
        san_current=row["san_current"],
        creation_step=row["creation_step"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        skills=dict(skills),
    )


def load_skills(conn: sqlite3.Connection, investigator_id: int) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT skill_key, value FROM investigator_skills WHERE investigator_id=? AND specialization=''",
        (investigator_id,),
    ).fetchall()
    return {r["skill_key"]: int(r["value"]) for r in rows}


def get(conn: sqlite3.Connection, investigator_id: int) -> Optional[Investigator]:
    row = conn.execute("SELECT * FROM investigators WHERE investigator_id=?", (int(investigator_id),)).fetchone()
    if row is None:
        return None
    return _row_to_investigator(row, load_skills(conn, int(investigator_id)))


def list_for_owner(conn: sqlite3.Connection, owner_user_id: str, include_archived: bool = False) -> List[Investigator]:
    sql = "SELECT * FROM investigators WHERE owner_user_id=?"
    if not include_archived:
        sql += " AND status<>'archived'"
    sql += " ORDER BY status='archived', lower(name), investigator_id"
    rows = conn.execute(sql, (str(owner_user_id),)).fetchall()
    return [_row_to_investigator(r, load_skills(conn, int(r["investigator_id"]))) for r in rows]


def count_live(conn: sqlite3.Connection, owner_user_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM investigators WHERE owner_user_id=? AND status<>'archived'",
        (str(owner_user_id),),
    ).fetchone()[0])


def name_taken(conn: sqlite3.Connection, owner_user_id: str, name: str, exclude_id: Optional[int] = None) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM investigators
        WHERE owner_user_id=? AND name=? COLLATE NOCASE AND status<>'archived'
          AND (? IS NULL OR investigator_id<>?)
        """,
        (str(owner_user_id), name, exclude_id, exclude_id),
    ).fetchone()
    return row is not None


def insert(conn: sqlite3.Connection, owner_user_id: str, fields: Mapping[str, object]) -> int:
    cols = ["owner_user_id"] + list(fields)
    for c in fields:
        if c not in _UPDATABLE:
            raise KeyError(c)
    marks = ",".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO investigators ({','.join(cols)}) VALUES ({marks})",
        (str(owner_user_id), *fields.values()),
    )
    return int(cur.lastrowid)


def update(conn: sqlite3.Connection, investigator_id: int, fields: Mapping[str, object]) -> None:
    if not fields:
        return
    for c in fields:
        if c not in _UPDATABLE:
            raise KeyError(c)
    assignments = ", ".join(f"{c}=?" for c in fields)
    conn.execute(
        f"UPDATE investigators SET {assignments}, updated_at={NOW} WHERE investigator_id=?",
        (*fields.values(), int(investigator_id)),
    )


def delete(conn: sqlite3.Connection, investigator_id: int) -> None:
    conn.execute("DELETE FROM investigators WHERE investigator_id=?", (int(investigator_id),))


def set_skill(conn: sqlite3.Connection, investigator_id: int, skill_key: str, value: int) -> None:
    conn.execute(
        f"""
        INSERT INTO investigator_skills (investigator_id, skill_key, specialization, value)
        VALUES (?, ?, '', ?)
        ON CONFLICT(investigator_id, skill_key, specialization)
        DO UPDATE SET value=excluded.value, updated_at={NOW}
        """,
        (int(investigator_id), skill_key, int(value)),
    )
    conn.execute(f"UPDATE investigators SET updated_at={NOW} WHERE investigator_id=?", (int(investigator_id),))


def remove_skill(conn: sqlite3.Connection, investigator_id: int, skill_key: str) -> None:
    conn.execute(
        "DELETE FROM investigator_skills WHERE investigator_id=? AND skill_key=? AND specialization=''",
        (int(investigator_id), skill_key),
    )
    conn.execute(f"UPDATE investigators SET updated_at={NOW} WHERE investigator_id=?", (int(investigator_id),))


# --- active selection -----------------------------------------------------

def set_active(conn: sqlite3.Connection, user_id: str, scope: str, investigator_id: int) -> None:
    conn.execute(
        f"""
        INSERT INTO active_investigators (user_id, scope, investigator_id) VALUES (?, ?, ?)
        ON CONFLICT(user_id, scope) DO UPDATE SET investigator_id=excluded.investigator_id, updated_at={NOW}
        """,
        (str(user_id), scope, int(investigator_id)),
    )


def clear_active(conn: sqlite3.Connection, user_id: str, scope: str) -> None:
    conn.execute("DELETE FROM active_investigators WHERE user_id=? AND scope=?", (str(user_id), scope))


def active_ids(conn: sqlite3.Connection, user_id: str, scopes: Iterable[str]) -> Dict[str, int]:
    scopes = list(scopes)
    marks = ",".join("?" for _ in scopes)
    rows = conn.execute(
        f"SELECT scope, investigator_id FROM active_investigators WHERE user_id=? AND scope IN ({marks})",
        (str(user_id), *scopes),
    ).fetchall()
    return {r["scope"]: int(r["investigator_id"]) for r in rows}


def active_scopes_for(conn: sqlite3.Connection, user_id: str, investigator_id: int) -> List[str]:
    rows = conn.execute(
        "SELECT scope FROM active_investigators WHERE user_id=? AND investigator_id=? ORDER BY scope",
        (str(user_id), int(investigator_id)),
    ).fetchall()
    return [r["scope"] for r in rows]
