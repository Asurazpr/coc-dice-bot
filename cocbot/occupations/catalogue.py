"""Occupation catalogue: built-in entries synced from JSON, plus per-user homebrew.

Built-in occupations live in ``data/occupations/occupations.json`` (generated
by ``scripts/import_occupations.py`` and committed). ``sync_builtin`` upserts
them by key at start-up, so catalogue corrections need no migration. Entries
that disappear from the JSON are marked retired, never deleted.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from cocbot.config import repo_root
from cocbot.occupations.zh_parser import CATEGORY_OPTIONS

CATALOGUE_PATH = repo_root() / "data" / "occupations" / "occupations.json"
STATS = ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU")
NOT_SELECTABLE = ("credit_rating", "cthulhu_mythos")   # never fill an occupation slot
HOMEBREW_SKILL_COUNT = 8


class CatalogueError(ValueError):
    """User-facing problem with an occupation definition."""


@dataclass(frozen=True)
class SlotDef:
    kind: str                       # fixed | choose | any
    count: int
    options: Tuple[str, ...]        # empty for "any"
    source_zh: str = ""
    note: Optional[str] = None


@dataclass(frozen=True)
class Occupation:
    id: int
    key: str
    source: str
    owner_user_id: Optional[str]
    name_en: str
    name_zh: Optional[str]
    era: Optional[str]
    rulebook_variant: bool
    credit_min: int
    credit_max: int
    skill_points: Tuple[dict, ...]
    slots: Tuple[SlotDef, ...]
    keeper_note_zh: Optional[str]
    verified: bool
    issues: Tuple[str, ...]
    retired: bool

    @property
    def display_name(self) -> str:
        if self.name_zh and self.name_zh != self.name_en:
            return f"{self.name_en} / {self.name_zh}"
        return self.name_en

    @property
    def is_homebrew(self) -> bool:
        return self.source == "homebrew"

    @property
    def fixed_skills(self) -> List[str]:
        return [s.options[0] for s in self.slots if s.kind == "fixed"]

    def formula_text(self) -> str:
        parts = []
        for t in self.skill_points:
            stats = " or ".join(t["stats"])
            parts.append(f"({stats})×{t['mult']}" if len(t["stats"]) > 1 else f"{stats}×{t['mult']}")
        return " + ".join(parts)


def _row_to_occupation(r: sqlite3.Row) -> Occupation:
    slots = tuple(
        SlotDef(s["kind"], int(s["count"]), tuple(s.get("options", [])), s.get("source_zh", ""), s.get("note"))
        for s in json.loads(r["slots_json"])
    )
    return Occupation(
        id=int(r["occupation_id"]), key=r["key"], source=r["source"], owner_user_id=r["owner_user_id"],
        name_en=r["name_en"], name_zh=r["name_zh"], era=r["era"], rulebook_variant=bool(r["rulebook_variant"]),
        credit_min=int(r["credit_min"]), credit_max=int(r["credit_max"]),
        skill_points=tuple(json.loads(r["skill_points_json"])), slots=slots,
        keeper_note_zh=r["keeper_note_zh"], verified=bool(r["verified"]),
        issues=tuple(json.loads(r["issues_json"])), retired=bool(r["retired"]),
    )


# --- built-in sync ---------------------------------------------------------------

def _validate_entry(conn: sqlite3.Connection, o: dict) -> None:
    known = {r[0] for r in conn.execute("SELECT key FROM skill_defs")}
    for slot in o["slots"]:
        if slot["kind"] not in ("fixed", "choose", "any"):
            raise CatalogueError(f"{o['key']}: bad slot kind {slot['kind']}")
        for k in slot.get("options", []):
            if k not in known:
                raise CatalogueError(f"{o['key']}: unknown skill {k}")
    for term in o["skill_points"]:
        if not set(term["stats"]) <= set(STATS):
            raise CatalogueError(f"{o['key']}: bad formula {term}")


def sync_builtin(conn: sqlite3.Connection, path: Path = CATALOGUE_PATH) -> Dict[str, int]:
    """Upsert built-in occupations from JSON; retire ones no longer listed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise CatalogueError(f"unsupported catalogue schema {data.get('schema')}")
    keys = []
    for o in data["occupations"]:
        _validate_entry(conn, o)
        keys.append(o["key"])
        conn.execute(
            """
            INSERT INTO occupations (key, source, name_en, name_zh, era, rulebook_variant, credit_min, credit_max,
                                     skill_points_json, slots_json, keeper_note_zh, verified, issues_json, retired)
            VALUES (?, 'builtin', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(key) DO UPDATE SET
              name_en=excluded.name_en, name_zh=excluded.name_zh, era=excluded.era,
              rulebook_variant=excluded.rulebook_variant, credit_min=excluded.credit_min,
              credit_max=excluded.credit_max, skill_points_json=excluded.skill_points_json,
              slots_json=excluded.slots_json, keeper_note_zh=excluded.keeper_note_zh,
              verified=excluded.verified, issues_json=excluded.issues_json, retired=0,
              updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE occupations.source = 'builtin'
            """,
            (o["key"], o["name_en"], o.get("name_zh"), o.get("era"), int(bool(o.get("rulebook_variant"))),
             o["credit_rating"][0], o["credit_rating"][1], json.dumps(o["skill_points"]),
             json.dumps(o["slots"], ensure_ascii=False), o.get("keeper_note_zh"), int(bool(o.get("verified"))),
             json.dumps(o.get("issues", []), ensure_ascii=False)),
        )
    marks = ",".join("?" for _ in keys) or "''"
    retired = conn.execute(
        f"UPDATE occupations SET retired=1 WHERE source='builtin' AND retired=0 AND key NOT IN ({marks})", keys
    ).rowcount
    conn.commit()
    return {"builtin": len(keys), "retired": retired}


# --- queries ---------------------------------------------------------------------

def get(conn: sqlite3.Connection, occupation_id: int) -> Optional[Occupation]:
    r = conn.execute("SELECT * FROM occupations WHERE occupation_id=?", (int(occupation_id),)).fetchone()
    return _row_to_occupation(r) if r else None


def visible_to(occ: Occupation, user_id: object) -> bool:
    return occ.source == "builtin" or occ.owner_user_id == str(user_id)


def search(conn: sqlite3.Connection, user_id: object, query: str = "", limit: int = 25,
           offset: int = 0) -> List[Occupation]:
    """Built-in + the user's own homebrew, matching English/Chinese name or key."""
    q = (query or "").strip().lower()
    rows = conn.execute(
        """
        SELECT * FROM occupations
        WHERE retired = 0 AND (source = 'builtin' OR owner_user_id = ?)
          AND (? = '' OR lower(name_en) LIKE '%' || ? || '%' OR name_zh LIKE '%' || ? || '%'
               OR key LIKE '%' || ? || '%')
        ORDER BY source = 'builtin', lower(name_en)
        LIMIT ? OFFSET ?
        """,
        (str(user_id), q, q, query.strip(), q.replace(" ", "_"), int(limit), int(offset)),
    ).fetchall()
    return [_row_to_occupation(r) for r in rows]


def count_visible(conn: sqlite3.Connection, user_id: object, query: str = "") -> int:
    q = (query or "").strip().lower()
    return int(conn.execute(
        """
        SELECT COUNT(*) FROM occupations
        WHERE retired = 0 AND (source = 'builtin' OR owner_user_id = ?)
          AND (? = '' OR lower(name_en) LIKE '%' || ? || '%' OR name_zh LIKE '%' || ? || '%'
               OR key LIKE '%' || ? || '%')
        """,
        (str(user_id), q, q, query.strip(), q.replace(" ", "_")),
    ).fetchone()[0])


def find(conn: sqlite3.Connection, user_id: object, ref: str) -> Optional[Occupation]:
    """By id (from autocomplete), exact key, or exact English/Chinese name."""
    s = (ref or "").strip()
    if s.isdigit():
        occ = get(conn, int(s))
        return occ if occ and visible_to(occ, user_id) and not occ.retired else None
    for occ in search(conn, user_id, "", limit=1000):
        if s.lower() in (occ.key, occ.name_en.lower()) or s == (occ.name_zh or ""):
            return occ
    return None


# --- homebrew --------------------------------------------------------------------

def parse_formula_choice(text: str) -> Tuple[dict, ...]:
    """'EDU*4' or 'EDU*2+DEX*2' / 'EDU×2 + STR or DEX ×2' (player-entered)."""
    t = (text or "").upper().replace(" ", "").replace("×", "*").replace("OR", "/")
    if t in ("", "EDU*4", "EDU"):
        return ({"stats": ["EDU"], "mult": 4},)
    m = re.fullmatch(r"EDU\*2\+([A-Z/]+)\*2", t) or re.fullmatch(r"([A-Z/]+)", t)
    if not m:
        raise CatalogueError("Formula must be EDU*4 or EDU*2 + one characteristic ×2 (e.g. EDU*2+DEX*2).")
    stats = sorted(set(m.group(1).split("/")))
    if not stats or not set(stats) <= set(STATS) or "EDU" in stats:
        raise CatalogueError(f"Second formula characteristic must be one of {', '.join(s for s in STATS if s != 'EDU')}.")
    return ({"stats": ["EDU"], "mult": 2}, {"stats": stats, "mult": 2})


def create_homebrew(conn: sqlite3.Connection, owner_user_id: object, name: str,
                    credit_min: int, credit_max: int, formula: Sequence[dict]) -> Occupation:
    """A Keeper-agreed custom occupation: any 8 skills, chosen in the next step."""
    name = " ".join((name or "").split())
    if not name or len(name) > 64:
        raise CatalogueError("Occupation name must be 1-64 characters.")
    if not (0 <= credit_min <= credit_max <= 99):
        raise CatalogueError("Credit Rating range must satisfy 0 ≤ min ≤ max ≤ 99.")
    key = f"homebrew_{owner_user_id}_{re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'occupation'}"
    n = 1
    base = key
    while conn.execute("SELECT 1 FROM occupations WHERE key=?", (key,)).fetchone():
        n += 1
        key = f"{base}_{n}"
    slots = [{"kind": "any", "count": HOMEBREW_SKILL_COUNT, "options": [],
              "source_zh": "", "note": "custom occupation: any 8 skills agreed with your Keeper"}]
    cur = conn.execute(
        """
        INSERT INTO occupations (key, source, owner_user_id, name_en, name_zh, credit_min, credit_max,
                                 skill_points_json, slots_json, issues_json)
        VALUES (?, 'homebrew', ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (key, str(owner_user_id), name, credit_min, credit_max, json.dumps(list(formula)),
         json.dumps(slots), json.dumps(["homebrew: confirm with your Keeper"])),
    )
    conn.commit()
    return get(conn, cur.lastrowid)  # type: ignore[return-value]


__all__ = [
    "CATEGORY_OPTIONS", "CatalogueError", "NOT_SELECTABLE", "Occupation", "SlotDef",
    "count_visible", "create_homebrew", "find", "get", "parse_formula_choice", "search",
    "sync_builtin", "visible_to",
]
