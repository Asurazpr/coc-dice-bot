"""What an adapter extracts from a workbook (no database, no Discord)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from cocbot.i18n import Msg

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass(frozen=True)
class Issue:
    level: str          # error | warning | info
    msg: Msg            # rendered in the viewer's language
    where: str = ""     # cell reference, e.g. "人物卡!J3" (no cell contents)

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.msg.code, "params": _plain(self.msg.params), "where": self.where}


def _plain(params: Mapping[str, Any]) -> dict:
    return {k: (v if isinstance(v, (int, float, str)) or v is None else str(v)) for k, v in params.items()}


@dataclass
class ParsedSkill:
    label: str                       # as written on the sheet (name, plus specialization)
    key: Optional[str]               # canonical skill key, None if not mapped
    total: Optional[int]             # final value to store
    where: str
    specialization: Optional[str] = None
    parts: Optional[Dict[str, int]] = None   # initial / growth / occupation / interest
    stored: bool = False             # set by the service: differs from base and kept


@dataclass
class ParsedSheet:
    template: str
    name: Optional[str] = None
    occupation: Optional[str] = None
    occupation_index: Optional[int] = None
    occupation_zh: Optional[str] = None        # 职业列表 name for the index
    age: Optional[int] = None
    residence: Optional[str] = None
    birthplace: Optional[str] = None
    characteristics: Dict[str, Optional[int]] = field(default_factory=dict)
    luck: Optional[int] = None
    hp: Optional[int] = None
    mp: Optional[int] = None
    san: Optional[int] = None
    skills: List[ParsedSkill] = field(default_factory=list)
    cached_derived: Dict[str, Any] = field(default_factory=dict)   # what the sheet displayed
    notes: Dict[str, Any] = field(default_factory=dict)            # preserved, not mapped
    issues: List[Issue] = field(default_factory=list)

    def add(self, level: str, code: str, where: str = "", **params: Any) -> None:
        self.issues.append(Issue(level, Msg(code, **params), where))

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def missing_characteristics(self) -> List[str]:
        missing = [k for k, v in self.characteristics.items() if v is None]
        if self.luck is None:
            missing.append("LUCK")
        return missing


class SkillCatalog(Protocol):
    """Skill lookups the adapters need (implemented on top of the database by the service)."""

    def resolve(self, name: str) -> Optional[str]: ...

    def base(self, key: str, characteristics: Mapping[str, Optional[int]]) -> Optional[int]: ...


class SheetAdapter(Protocol):
    key: str
    label: str

    def detect(self, book) -> bool: ...

    def parse(self, book, catalog: SkillCatalog) -> ParsedSheet: ...


__all__: Sequence[str] = ["ERROR", "INFO", "Issue", "ParsedSheet", "ParsedSkill", "SheetAdapter", "SkillCatalog",
                          "WARNING"]
