from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from cocbot.mechanics.checks import CheckResult, CheckRules, RAW_RULES, success_level

# Technical guards (not game rules): keep a single command from asking the
# bot to roll millions of dice.
MAX_DICE_COUNT = 100
MAX_DIE_SIDES = 1000
MAX_BONUS_PENALTY_DICE = 5

# Module-level RNG so tests can reseed or replace it in one place.
_rng: random.Random = random.Random()


def set_rng(rng: random.Random) -> None:
    """Replace the RNG used by all dice functions (used by tests)."""
    global _rng
    _rng = rng


@dataclass(frozen=True)
class D100Roll:
    value: int
    tens: int
    ones: int


@dataclass(frozen=True)
class D100BPCandidates:
    chosen: int
    candidates: List[int]          # all candidate d100 outcomes
    ones: int
    tens_candidates: List[int]     # raw tens digits rolled (0-9)


def roll_d10() -> int:
    return _rng.randint(0, 9)


def roll_dice(count: int, sides: int) -> List[int]:
    return [_rng.randint(1, sides) for _ in range(count)]


def roll_d100_raw() -> D100Roll:
    tens = roll_d10()
    ones = roll_d10()
    value = tens * 10 + ones
    # In CoC, 00 + 0 is read as 100
    if value == 0:
        value = 100
    return D100Roll(value=value, tens=tens, ones=ones)


def _validate_bp(bp: int) -> int:
    bp = int(bp)
    if abs(bp) > MAX_BONUS_PENALTY_DICE:
        raise ValueError(
            f"Bonus/penalty dice must be between -{MAX_BONUS_PENALTY_DICE} and {MAX_BONUS_PENALTY_DICE}."
        )
    return bp


def roll_d100_bonus_penalty_candidates(bp: int = 0) -> D100BPCandidates:
    """
    Bonus/Penalty dice (CoC 7e RAW):
    - Roll the units die once
    - Roll (abs(bp)+1) tens dice
    - Combine each tens with the units -> candidate d100 results
    - Choose lowest candidate for bonus, highest for penalty, single for none
    """
    bp = _validate_bp(bp)
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
    """Back-compat: returns only the chosen d100 value."""
    return roll_d100_bonus_penalty_candidates(bp=bp).chosen


_DICE_RE = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)


def parse_and_roll(expr: str) -> int:
    """Supports NdM+K (e.g., 2d6+1, d20, 1d100-10)."""
    m = _DICE_RE.match(expr)
    if not m:
        raise ValueError("Invalid dice expression. Use like 1d100, 2d6+1, d20.")

    n_str, sides_str, mod_str = m.groups()
    n = int(n_str) if n_str else 1
    sides = int(sides_str)
    mod = int(mod_str.replace(" ", "")) if mod_str else 0

    if n <= 0 or sides <= 0:
        raise ValueError("Dice count and sides must be positive.")
    if n > MAX_DICE_COUNT or sides > MAX_DIE_SIDES:
        raise ValueError(f"At most {MAX_DICE_COUNT} dice with up to {MAX_DIE_SIDES} sides.")

    return sum(roll_dice(n, sides)) + mod


def d100_check(target: int, bp: int = 0, rules: CheckRules = RAW_RULES) -> CheckResult:
    roll = roll_d100_bonus_penalty(bp=bp)
    return CheckResult(roll=roll, target=int(target), level=success_level(roll, int(target), rules))


def d100_check_details(
    target: int, bp: int = 0, rules: CheckRules = RAW_RULES
) -> Tuple[CheckResult, Optional[List[int]]]:
    """Roll a check and also return bonus/penalty candidates (None when bp == 0)."""
    r = roll_d100_bonus_penalty_candidates(bp=bp)
    lvl = success_level(r.chosen, int(target), rules)
    bp_candidates = r.candidates if int(bp) != 0 else None
    return CheckResult(roll=r.chosen, target=int(target), level=lvl), bp_candidates
