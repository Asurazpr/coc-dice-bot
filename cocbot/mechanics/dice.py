from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Optional, List, Tuple

from cocbot.mechanics.checks import CheckResult, success_level


@dataclass(frozen=True)
class D100Roll:
    value: int
    tens: int
    ones: int


# NEW: carries bonus/penalty candidate info
@dataclass(frozen=True)
class D100BPCandidates:
    chosen: int
    candidates: List[int]          # all candidate d100 outcomes
    ones: int
    tens_candidates: List[int]     # raw tens digits rolled (0-9)


def roll_d10() -> int:
    return random.randint(0, 9)


def roll_d100_raw() -> D100Roll:
    tens = roll_d10()
    ones = roll_d10()
    value = tens * 10 + ones
    # In CoC, 00 is treated as 100
    if value == 0:
        value = 100
    return D100Roll(value=value, tens=tens, ones=ones)


# NEW: returns chosen roll + all BP candidates
def roll_d100_bonus_penalty_candidates(bp: int = 0) -> D100BPCandidates:
    """
    Bonus/Penalty dice:
    - Roll ones once
    - Roll (abs(bp)+1) tens dice
    - Combine each tens with the ones -> candidate d100 results
    - Choose lowest candidate for bonus, highest for penalty, single for none
    """
    bp = int(bp)
    ones = roll_d10()
    tens_candidates = [roll_d10() for _ in range(abs(bp) + 1)]

    candidates: List[int] = []
    for tens in tens_candidates:
        v = tens * 10 + ones
        if v == 0:
            v = 100
        candidates.append(v)

    if bp > 0:
        chosen = min(candidates)
    elif bp < 0:
        chosen = max(candidates)
    else:
        chosen = candidates[0]

    return D100BPCandidates(
        chosen=chosen,
        candidates=candidates,
        ones=ones,
        tens_candidates=tens_candidates,
    )


def roll_d100_bonus_penalty(bp: int = 0) -> int:
    """
    Back-compat: returns only the chosen d100 value.
    """
    return roll_d100_bonus_penalty_candidates(bp=bp).chosen


_DICE_RE = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)


def parse_and_roll(expr: str) -> int:
    """
    Supports NdM+K (e.g., 2d6+1, d20, 1d100-10).
    """
    m = _DICE_RE.match(expr)
    if not m:
        raise ValueError("Invalid dice expression. Use like 1d100, 2d6+1, d20.")

    n_str, sides_str, mod_str = m.groups()
    n = int(n_str) if n_str else 1
    sides = int(sides_str)
    mod = int(mod_str.replace(" ", "")) if mod_str else 0

    if n <= 0 or sides <= 0:
        raise ValueError("Dice count and sides must be positive.")

    total = sum(random.randint(1, sides) for _ in range(n)) + mod
    return total


def d100_check(target: int, bp: int = 0) -> CheckResult:
    roll = roll_d100_bonus_penalty(bp=bp)
    lvl = success_level(roll, int(target))
    return CheckResult(roll=roll, target=int(target), level=lvl)


# NEW: use this for /check embed so you can show candidates
def d100_check_details(target: int, bp: int = 0) -> Tuple[CheckResult, Optional[List[int]]]:
    r = roll_d100_bonus_penalty_candidates(bp=bp)
    lvl = success_level(r.chosen, int(target))
    # Only show candidates when bp != 0
    bp_candidates = r.candidates if int(bp) != 0 else None
    return CheckResult(roll=r.chosen, target=int(target), level=lvl), bp_candidates
