"""Derived statistics for CoC 7e investigators.

Everything here is pure (no I/O, no Discord) and follows the Keeper Rulebook,
chapter 3 "Creating Investigators". Anything not covered by RAW is called out
explicitly in docstrings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

CHARACTERISTICS: Tuple[str, ...] = ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU")
STAT_KEYS = set(CHARACTERISTICS)


def eval_derived_formula(formula: str, stats: Mapping[str, int]) -> Tuple[Optional[int], str]:
    """
    Evaluate a skill's derived base formula (from ``skill_defs.derived_formula``).

    Supports:
      - STAT (e.g., EDU)        -> Language (Own)
      - STAT/n (e.g., DEX/2)    -> Dodge
    Returns (value_or_None, explanation).
    """
    f = (formula or "").strip().upper()
    if not f:
        return None, ""

    if f in STAT_KEYS:
        v = stats.get(f)
        if v is None:
            return None, f"{f} missing"
        return int(v), f"{f}={v}"

    m = re.fullmatch(r"(STR|CON|SIZ|DEX|APP|INT|POW|EDU)\s*/\s*(\d+)", f)
    if m:
        stat = m.group(1)
        div = int(m.group(2))
        v = stats.get(stat)
        if v is None:
            return None, f"{stat} missing"
        return int(v // div), f"{stat}={v} → {stat}/{div}={v // div}"

    return None, f"unsupported: {formula}"


# --- Damage Bonus & Build -------------------------------------------------

# (inclusive upper bound of STR+SIZ, damage bonus, build)
_DB_BUILD_TABLE: Tuple[Tuple[int, str, int], ...] = (
    (64, "-2", -2),
    (84, "-1", -1),
    (124, "0", 0),
    (164, "+1D4", 1),
    (204, "+1D6", 2),
    (284, "+2D6", 3),
    (364, "+3D6", 4),
    (444, "+4D6", 5),
    (524, "+5D6", 6),
)


def damage_bonus_and_build(str_: int, siz: int) -> Tuple[str, int]:
    """Damage Bonus and Build from STR+SIZ (Keeper Rulebook table I).

    Above 524: each additional 80 points *or fraction thereof* adds +1D6 and
    +1 Build (RAW). STR+SIZ below 2 is not on the table; it is treated as the
    lowest band.
    """
    total = int(str_) + int(siz)
    for upper, db, build in _DB_BUILD_TABLE:
        if total <= upper:
            return db, build
    extra = -(-(total - 524) // 80)  # ceil division
    return f"+{5 + extra}D6", 6 + extra


# --- Movement rate -----------------------------------------------------------

def movement_rate(str_: int, dex: int, siz: int, age: Optional[int] = None) -> int:
    """MOV per RAW.

    * STR and DEX both less than SIZ -> 7
    * STR and DEX both greater than SIZ -> 9
    * otherwise (either >= SIZ, or all equal) -> 8

    Age: -1 per decade from the 40s (40-49: -1 ... 80-89: -5). RAW stops at
    the 80s; ages 90+ keep the 80s value (-5) rather than inventing a rule.
    """
    str_, dex, siz = int(str_), int(dex), int(siz)
    if str_ < siz and dex < siz:
        mov = 7
    elif str_ > siz and dex > siz:
        mov = 9
    else:
        mov = 8
    if age is not None and age >= 40:
        mov -= min(5, (int(age) - 30) // 10)
    return mov


# --- Aggregate ---------------------------------------------------------------

@dataclass(frozen=True)
class DerivedStats:
    hp_max: Optional[int]
    mp_max: Optional[int]
    san_start: Optional[int]
    san_max: int
    damage_bonus: Optional[str]
    build: Optional[int]
    mov: Optional[int]
    dodge_base: Optional[int]
    language_own_base: Optional[int]

    def as_dict(self) -> Dict[str, object]:
        return dict(self.__dict__)


def compute_derived(
    stats: Mapping[str, Optional[int]],
    age: Optional[int] = None,
    cthulhu_mythos: int = 0,
) -> DerivedStats:
    """Compute every derived value that the available characteristics allow.

    Missing inputs yield ``None`` for the values that depend on them, so a
    draft character can show partial information.

    * HP  = (CON + SIZ) / 10, rounded down
    * MP  = POW / 5, rounded down
    * Starting SAN = POW; Maximum SAN = 99 - Cthulhu Mythos
    * Dodge base = DEX / 2; Language (Own) base = EDU
    """
    g = {k: (int(v) if v is not None else None) for k, v in stats.items()}
    STR, CON, SIZ, DEX, POW, EDU = (g.get(k) for k in ("STR", "CON", "SIZ", "DEX", "POW", "EDU"))

    hp = (CON + SIZ) // 10 if CON is not None and SIZ is not None else None
    mp = POW // 5 if POW is not None else None
    db = build = None
    if STR is not None and SIZ is not None:
        db, build = damage_bonus_and_build(STR, SIZ)
    mov = movement_rate(STR, DEX, SIZ, age) if None not in (STR, DEX, SIZ) else None

    return DerivedStats(
        hp_max=hp,
        mp_max=mp,
        san_start=POW,
        san_max=99 - int(cthulhu_mythos or 0),
        damage_bonus=db,
        build=build,
        mov=mov,
        dodge_base=DEX // 2 if DEX is not None else None,
        language_own_base=EDU,
    )
