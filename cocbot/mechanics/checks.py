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


def success_level(roll: int, target: int) -> SuccessLevel:
    """
    CoC 7e-ish success rules (simplified, but sane defaults):
    - 01 is always Critical
    - 100 is a fumble if target < 50, otherwise just a fail (common table rule)
    - roll <= target is success; thresholds for hard/extreme are target/2 and target/5
    """
    roll = int(roll)
    target = int(target)

    if roll == 1:
        return SuccessLevel.CRITICAL

    if roll == 100:
        return SuccessLevel.FUMBLE if target < 50 else SuccessLevel.FAIL

    if roll > target:
        # optional fumble rule: 96-99 fumble when target < 50
        if roll >= 96 and target < 50:
            return SuccessLevel.FUMBLE
        return SuccessLevel.FAIL

    # success tiers
    if roll <= max(1, target // 5):
        return SuccessLevel.EXTREME
    if roll <= max(1, target // 2):
        return SuccessLevel.HARD
    return SuccessLevel.SUCCESS
