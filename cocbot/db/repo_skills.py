from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from cocbot.db.connection import get_conn


@dataclass(frozen=True)
class SkillMaster:
    key: str          # canonical EN key in DB
    zh: str           # Chinese display name in DB
    base: int         # base %


_punct_re = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)

def _norm(s: str) -> str:
    s = s.strip().lower()
    s = _punct_re.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_skill_master(query: str) -> Optional[SkillMaster]:
    """
    Looks up a skill in skills_master by:
    - exact key (case-insensitive)
    - exact zh
    - normalized match (ignores punctuation like (), /, -)
    """
    q_raw = query.strip()
    q_norm = _norm(q_raw)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, zh, base FROM skills_master"
        ).fetchall()

    # 1) exact match pass
    for r in rows:
        key = (r["key"] or "").strip()
        zh = (r["zh"] or "").strip()
        if key.lower() == q_raw.lower():
            return SkillMaster(key=key, zh=zh, base=int(r["base"]))
        if zh and zh == q_raw:
            return SkillMaster(key=key, zh=zh, base=int(r["base"]))

    # 2) normalized match pass
    for r in rows:
        key = (r["key"] or "").strip()
        zh = (r["zh"] or "").strip()
        if _norm(key) == q_norm:
            return SkillMaster(key=key, zh=zh, base=int(r["base"]))
        if zh and _norm(zh) == q_norm:
            return SkillMaster(key=key, zh=zh, base=int(r["base"]))

    return None
