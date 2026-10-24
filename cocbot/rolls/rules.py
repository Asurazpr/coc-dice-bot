"""Required difficulty and Luck-spending rules (pure functions, no database).

Sources and verification status: docs/ROLLS.md §2. Every Luck restriction
lives in ``luck_eligibility`` so a rules correction is a single change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from cocbot.mechanics.checks import CheckRules, SuccessLevel, success_level

REGULAR, HARD, EXTREME = "regular", "hard", "extreme"
DIFFICULTIES = (REGULAR, HARD, EXTREME)
LUCK_WINDOW_SECONDS = 30 * 60      # Luck is spent right after the roll; old buttons expire

_LEVEL_RANK = {
    SuccessLevel.FUMBLE: -1, SuccessLevel.FAIL: 0, SuccessLevel.SUCCESS: 1,
    SuccessLevel.HARD: 2, SuccessLevel.EXTREME: 3, SuccessLevel.CRITICAL: 4,
}
_DIFFICULTY_RANK = {REGULAR: 1, HARD: 2, EXTREME: 3}
_NO_LUCK_SUBJECTS = {"LUCK": "luck_not_on_luck", "SAN": "luck_not_on_san"}


def level_from_name(name: str) -> SuccessLevel:
    return SuccessLevel[name] if name in SuccessLevel.__members__ else SuccessLevel(name)


def meets(level: SuccessLevel, difficulty: str) -> bool:
    """A Critical meets every difficulty; a Fumble or Failure meets none."""
    return _LEVEL_RANK[level] >= _DIFFICULTY_RANK[difficulty]


def threshold(target: int, difficulty: str) -> int:
    """Highest roll that meets ``difficulty`` (Hard = half, Extreme = fifth, rounded down)."""
    return {REGULAR: target, HARD: target // 2, EXTREME: target // 5}[difficulty]


def luck_adjusted_level(effective: int, target: int, rules: CheckRules) -> SuccessLevel:
    """A Luck-adjusted result is never a Critical: at most an Extreme success (docs/ROLLS.md §2)."""
    level = success_level(effective, target, rules)
    return SuccessLevel.EXTREME if level == SuccessLevel.CRITICAL else level


@dataclass(frozen=True)
class LuckOption:
    difficulty: str
    cost: int
    effective: int
    level: SuccessLevel


@dataclass(frozen=True)
class RollFacts:
    """The parts of a stored roll that decide Luck eligibility."""
    subject_kind: str
    subject_key: Optional[str]
    has_investigator: bool
    attempt: str
    natural: int
    natural_level: SuccessLevel
    target: int
    difficulty: str
    luck_spent: int
    rules: CheckRules
    rules_allow_luck: bool
    luck_scope: str


def upgrade_targets(facts: RollFacts) -> List[str]:
    """Difficulties Luck could buy: at least the required one and better than the natural result.

    A failed roll can be raised to the required level (or beyond); an existing
    success can be upgraded, e.g. Regular → Hard or Hard → Extreme (docs/ROLLS.md §2).
    """
    natural_rank = _LEVEL_RANK[facts.natural_level]
    return [d for d in DIFFICULTIES
            if _DIFFICULTY_RANK[d] >= _DIFFICULTY_RANK[facts.difficulty] and _DIFFICULTY_RANK[d] > natural_rank]


def luck_options(facts: RollFacts, luck_available: int) -> List[LuckOption]:
    """Every reachable upgrade (see ``upgrade_targets``) that the available Luck can pay for."""
    out: List[LuckOption] = []
    for diff in upgrade_targets(facts):
        th = threshold(facts.target, diff)
        if th < 1:
            continue
        cost = facts.natural - th
        if cost <= 0 or cost > luck_available:
            continue
        out.append(LuckOption(diff, cost, th, luck_adjusted_level(th, facts.target, facts.rules)))
    return out


def luck_eligibility(facts: RollFacts, *, current_luck: Optional[int], spending_allowed_now: bool,
                     is_latest: bool, age_seconds: float) -> Tuple[Optional[str], List[LuckOption]]:
    """``(None, options)`` when Luck can be spent, else ``(reason_code, [])``.

    Reason codes are i18n catalogue entries.
    """
    if facts.attempt != "original":
        return "luck_not_on_pushed", []
    if facts.subject_kind == "numeric" or not facts.has_investigator or facts.luck_scope == "none":
        return "luck_needs_investigator", []
    if facts.subject_kind == "characteristic" and facts.subject_key in _NO_LUCK_SUBJECTS:
        return _NO_LUCK_SUBJECTS[facts.subject_key], []
    if not (facts.rules_allow_luck and spending_allowed_now):
        return "luck_rule_off", []
    if facts.luck_spent:
        return "luck_already_spent", []
    if facts.natural_level == SuccessLevel.FUMBLE:
        return "luck_not_on_fumble", []
    targets = upgrade_targets(facts)
    if not targets:
        return "luck_no_upgrade", []       # already Extreme or Critical
    if not is_latest:
        return "luck_not_latest", []
    if age_seconds > LUCK_WINDOW_SECONDS:
        return "luck_expired", []
    if current_luck is None:
        return "luck_unavailable", []
    if all(threshold(facts.target, d) < 1 for d in targets):
        return "luck_unreachable", []
    options = luck_options(facts, current_luck)
    if not options:
        return "luck_not_enough", []
    return None, options
