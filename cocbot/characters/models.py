from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from cocbot.mechanics.derived import CHARACTERISTICS, DerivedStats, compute_derived

STATUS_DRAFT = "draft"
STATUS_READY = "ready"
STATUS_ARCHIVED = "archived"

# column name in `investigators` for each characteristic key
CHAR_COLUMNS: Dict[str, str] = {k: f"char_{k.lower()}" for k in CHARACTERISTICS}

BIO_FIELDS = ("name", "occupation", "age", "pronouns", "residence", "birthplace", "notes")


@dataclass(frozen=True)
class Investigator:
    id: int
    owner_user_id: str
    name: str
    status: str
    occupation: Optional[str] = None
    age: Optional[int] = None
    pronouns: Optional[str] = None
    residence: Optional[str] = None
    birthplace: Optional[str] = None
    characteristics: Mapping[str, Optional[int]] = field(default_factory=dict)
    luck: Optional[int] = None
    hp_current: Optional[int] = None
    mp_current: Optional[int] = None
    san_current: Optional[int] = None
    creation_step: Optional[str] = None
    notes: Optional[str] = None
    status_before_archive: Optional[str] = None
    occupation_id: Optional[int] = None          # set when created with the occupation rules
    creation_validated: bool = False             # finished with occupation/skill-point validation
    created_at: str = ""
    updated_at: str = ""
    skills: Mapping[str, int] = field(default_factory=dict)  # trained values by skill key

    # --- derived ---------------------------------------------------------
    @property
    def missing_characteristics(self) -> List[str]:
        missing = [k for k in CHARACTERISTICS if self.characteristics.get(k) is None]
        if self.luck is None:
            missing.append("LUCK")
        return missing

    @property
    def is_complete(self) -> bool:
        return not self.missing_characteristics

    @property
    def is_archived(self) -> bool:
        return self.status == STATUS_ARCHIVED

    def derived(self) -> DerivedStats:
        return compute_derived(
            self.characteristics, age=self.age, cthulhu_mythos=self.skills.get("cthulhu_mythos", 0)
        )

    @staticmethod
    def _current(stored: Optional[int], maximum: Optional[int]) -> Optional[int]:
        """Stored value capped at the maximum; NULL means "at maximum"; 0 stays 0."""
        if stored is None:
            return maximum
        return min(int(stored), maximum) if maximum is not None else int(stored)

    @property
    def hp(self) -> Optional[int]:
        return self._current(self.hp_current, self.derived().hp_max)

    @property
    def mp(self) -> Optional[int]:
        return self._current(self.mp_current, self.derived().mp_max)

    @property
    def san(self) -> Optional[int]:
        """Current SAN, never above Maximum SAN (99 - Cthulhu Mythos).

        The cap applies both to the implicit starting value (POW) and to an
        explicitly stored current SAN, e.g. one recorded before Cthulhu Mythos
        increased.
        """
        d = self.derived()
        value = self.san_current if self.san_current is not None else d.san_start
        if value is None:
            return None
        return max(0, min(int(value), d.san_max))

    def stat(self, key: str) -> Optional[int]:
        key = key.upper()
        if key == "LUCK":
            return self.luck
        if key == "SAN":
            return self.san
        return self.characteristics.get(key)
