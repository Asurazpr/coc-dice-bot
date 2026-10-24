"""Resolve what a `/check` argument means for the invoking player.

Pure application logic (no Discord). Given the raw text a player typed, the
player's ID and the guild, decide the target number:

1. A number (1-100)              -> used as-is, no character needed.
2. A characteristic / Luck / SAN -> the active investigator's current value.
3. A skill name (EN/ZH/alias)    -> the active investigator's trained value,
                                    else the skill's base (derived bases such
                                    as Dodge = DEX/2 use the investigator).
   With no active investigator, non-derived skills fall back to the base
   value, as before; derived skills need an investigator.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional

from cocbot.characters.models import Investigator
from cocbot.characters.service import InvestigatorService
from cocbot.db.repo_skill_defs import guess_lang, resolve_skill
from cocbot.mechanics.derived import eval_derived_formula

NUMERIC_MIN, NUMERIC_MAX = 1, 100

# key -> (EN display, ZH display)
STAT_LABELS: Dict[str, tuple] = {
    "STR": ("STR", "力量"), "CON": ("CON", "体质"), "SIZ": ("SIZ", "体型"), "DEX": ("DEX", "敏捷"),
    "APP": ("APP", "外貌"), "INT": ("INT", "智力"), "POW": ("POW", "意志"), "EDU": ("EDU", "教育"),
    "LUCK": ("Luck", "幸运"), "SAN": ("SAN", "理智"),
}

_STAT_ALIASES: Dict[str, str] = {
    "str": "STR", "strength": "STR", "力量": "STR",
    "con": "CON", "constitution": "CON", "体质": "CON",
    "siz": "SIZ", "size": "SIZ", "体型": "SIZ",
    "dex": "DEX", "dexterity": "DEX", "敏捷": "DEX",
    "app": "APP", "appearance": "APP", "外貌": "APP", "外表": "APP",
    "int": "INT", "intelligence": "INT", "idea": "INT", "智力": "INT", "灵感": "INT",
    "pow": "POW", "power": "POW", "意志": "POW",
    "edu": "EDU", "education": "EDU", "know": "EDU", "教育": "EDU", "知识": "EDU",
    "luck": "LUCK", "幸运": "LUCK", "运气": "LUCK",
    "san": "SAN", "sanity": "SAN", "理智": "SAN", "san值": "SAN",
}


def stat_key_for(text: str) -> Optional[str]:
    return _STAT_ALIASES.get(text.strip().lower())


class CheckTargetError(Exception):
    """User-facing explanation of why the check cannot be rolled."""


@dataclass(frozen=True)
class CheckTarget:
    value: int
    label: str                      # e.g. "Spot Hidden" / "STR" / "Target 60"
    detail: str                     # e.g. "trained", "base", "DEX=60 → DEX/2=30"
    kind: str                       # "numeric" | "characteristic" | "skill"
    investigator: Optional[Investigator] = None
    base_value: Optional[int] = None


def resolve_check_target(
    conn: sqlite3.Connection,
    user_id: object,
    guild_id: Optional[object],
    raw: str,
) -> CheckTarget:
    text = (raw or "").strip()
    if not text:
        raise CheckTargetError("Provide a target number or a skill name.")

    if text.isdigit():
        n = int(text)
        if not NUMERIC_MIN <= n <= NUMERIC_MAX:
            raise CheckTargetError(f"Target must be between {NUMERIC_MIN} and {NUMERIC_MAX}.")
        return CheckTarget(value=n, label=f"Target {n}", detail="", kind="numeric")

    service = InvestigatorService(conn)
    inv = service.active(user_id, guild_id)
    lang = guess_lang(text)

    stat = stat_key_for(text)
    if stat:
        label = STAT_LABELS[stat][1 if lang == "zh" else 0]
        if inv is None:
            raise CheckTargetError(
                f"**{label}** needs an active investigator. Use `/character select` (or `/check <number>`)."
            )
        value = inv.stat(stat)
        if value is None:
            raise CheckTargetError(f"**{inv.name}** has no {label} value yet. Set it with `/character edit`.")
        detail = "current" if stat == "SAN" else "characteristic"
        return CheckTarget(value=int(value), label=label, detail=detail, kind="characteristic", investigator=inv)

    skill = resolve_skill(text, lang=lang, conn=conn)
    if skill is None:
        raise CheckTargetError(f"Unknown skill or characteristic: `{text}`.")

    # Base value: plain, or derived from the investigator's characteristics.
    base: Optional[int]
    base_note = ""
    if skill.is_derived:
        if inv is None:
            raise CheckTargetError(
                f"**{skill.display_name}** is based on {skill.derived_formula}, so it needs an active "
                f"investigator. Use `/character select`."
            )
        base, base_note = eval_derived_formula(skill.derived_formula or "", inv.characteristics)
        if base is None:
            raise CheckTargetError(
                f"**{inv.name}** cannot roll {skill.display_name} yet ({base_note}). Set characteristics first."
            )
    else:
        base = int(skill.base)

    if inv is not None and skill.key in inv.skills:
        return CheckTarget(
            value=int(inv.skills[skill.key]), label=skill.display_name, detail="trained",
            kind="skill", investigator=inv, base_value=base,
        )

    if inv is not None:
        detail = f"base, {base_note}" if base_note else "base"
    else:
        detail = "base — no active investigator"
    return CheckTarget(
        value=int(base), label=skill.display_name, detail=detail,
        kind="skill", investigator=inv, base_value=base,
    )
