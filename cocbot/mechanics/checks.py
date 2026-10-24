from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SuccessLevel(str, Enum):
    FUMBLE = "Fumble"
    FAIL = "Fail"
    SUCCESS = "Success"
    HARD = "Hard Success"
    EXTREME = "Extreme Success"
    CRITICAL = "Critical"


@dataclass(frozen=True)
class CheckResult:
    roll: int
    target: int
    level: SuccessLevel


@dataclass(frozen=True)
class CheckRules:
    """Rules that decide how a d100 roll maps to a success level.

    The defaults are Rules-As-Written for Call of Cthulhu 7th Edition
    (Keeper Rulebook, ch. 5 "Skill Rolls"):

    * a roll of 01 is a Critical success;
    * if the skill/target is below 50, a roll of 96-100 is a Fumble;
      otherwise only 100 is a Fumble.

    Keeper house rules (e.g. a widened critical range) are expressed by
    constructing a different ``CheckRules`` rather than editing this logic.
    """

    critical_max: int = 1
    fumble_low_skill_threshold: int = 50
    fumble_low_skill_min: int = 96


RAW_RULES = CheckRules()


def success_level(roll: int, target: int, rules: CheckRules = RAW_RULES) -> SuccessLevel:
    """Classify a d100 roll against a target using CoC 7e rules.

    Hard success is a roll <= target/2, Extreme success <= target/5
    (both rounded down, as in the 7e character sheet's half/fifth values).
    """
    roll = int(roll)
    target = int(target)

    if roll <= rules.critical_max:
        return SuccessLevel.CRITICAL

    if roll == 100:
        return SuccessLevel.FUMBLE
    if target < rules.fumble_low_skill_threshold and roll >= rules.fumble_low_skill_min:
        return SuccessLevel.FUMBLE

    if roll > target:
        return SuccessLevel.FAIL

    if roll <= target // 5:
        return SuccessLevel.EXTREME
    if roll <= target // 2:
        return SuccessLevel.HARD
    return SuccessLevel.SUCCESS
