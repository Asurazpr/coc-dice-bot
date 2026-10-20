"""Characteristic generation for CoC 7e (Keeper Rulebook, chapter 3).

Standard (RAW) rolling method:

* STR, CON, DEX, APP, POW : 3D6 x 5
* SIZ, INT, EDU           : (2D6 + 6) x 5
* Luck                    : 3D6 x 5

Not implemented yet (documented in docs/ROADMAP.md) and therefore *not*
applied silently: age-based characteristic adjustments, EDU improvement
checks, the quick-fire array and point-buy optional rules.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, Optional

ROLL_FORMULAS: Dict[str, str] = {
    "STR": "3D6×5",
    "CON": "3D6×5",
    "SIZ": "(2D6+6)×5",
    "DEX": "3D6×5",
    "APP": "3D6×5",
    "INT": "(2D6+6)×5",
    "POW": "3D6×5",
    "EDU": "(2D6+6)×5",
    "LUCK": "3D6×5",
}

# Accepted range for manually entered characteristics. Rolled human values
# fall in 15-90; EDU can reach 99 through improvement. 0 is allowed because
# the rules use it (e.g. STR 0 = cannot stand).
CHARACTERISTIC_MIN = 0
CHARACTERISTIC_MAX = 99
LUCK_MIN = 0
LUCK_MAX = 99


def _nd6(rng: random.Random, n: int) -> int:
    return sum(rng.randint(1, 6) for _ in range(n))


def roll_characteristic(key: str, rng: Optional[random.Random] = None) -> int:
    rng = rng or random.Random()
    key = key.upper()
    if key in ("SIZ", "INT", "EDU"):
        return (_nd6(rng, 2) + 6) * 5
    if key in ("STR", "CON", "DEX", "APP", "POW", "LUCK"):
        return _nd6(rng, 3) * 5
    raise KeyError(key)


@dataclass(frozen=True)
class RolledCharacteristics:
    values: Dict[str, int]  # keys: STR..EDU and LUCK


def roll_all(rng: Optional[random.Random] = None) -> RolledCharacteristics:
    rng = rng or random.Random()
    return RolledCharacteristics({k: roll_characteristic(k, rng) for k in ROLL_FORMULAS})
