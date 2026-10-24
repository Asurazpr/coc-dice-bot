"""Per-campaign rule profile. Only settings the engine actually implements are offered.

Stored as JSON in ``campaign_rules`` (one row per version). Unknown keys are
rejected, so a toggle can never appear to work without engine support.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping

from cocbot.i18n import Msg
from cocbot.mechanics.checks import CheckRules

SCHEMA = 1
CRITICAL_MAX_RANGE = (1, 5)
FUMBLE_MODES = ("raw", "only_100")
VISIBILITY_MODES = ("public", "private")


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class CampaignRules:
    critical_max: int = 1          # rolls 01..N are Criticals (RAW: 1)
    fumble: str = "raw"            # "raw": 96-100 below skill 50, else 100; "only_100"
    roll_visibility: str = "public"

    def to_check_rules(self) -> CheckRules:
        return CheckRules(
            critical_max=self.critical_max,
            fumble_low_skill_threshold=50 if self.fumble == "raw" else 0,
            fumble_low_skill_min=96,
        )

    @property
    def is_raw(self) -> bool:
        return self.critical_max == 1 and self.fumble == "raw"

    def to_json(self) -> str:
        return json.dumps({"schema": SCHEMA, **asdict(self)}, sort_keys=True)


RAW_PROFILE = CampaignRules()


def validate(values: Mapping[str, Any]) -> CampaignRules:
    data: Dict[str, Any] = dict(values)
    data.pop("schema", None)
    unknown = set(data) - {"critical_max", "fumble", "roll_visibility"}
    if unknown:
        raise RulesError(Msg("rules_unknown", keys=", ".join(sorted(unknown))))
    rules = CampaignRules(**{**asdict(RAW_PROFILE), **data})
    lo, hi = CRITICAL_MAX_RANGE
    if not isinstance(rules.critical_max, int) or not lo <= rules.critical_max <= hi:
        raise RulesError(Msg("rules_critical_range", lo=lo, hi=hi))
    if rules.fumble not in FUMBLE_MODES:
        raise RulesError(Msg("rules_fumble_mode", modes=", ".join(FUMBLE_MODES)))
    if rules.roll_visibility not in VISIBILITY_MODES:
        raise RulesError(Msg("rules_visibility_mode", modes=", ".join(VISIBILITY_MODES)))
    return rules


def from_json(text: str) -> CampaignRules:
    data = json.loads(text)
    if data.get("schema", SCHEMA) != SCHEMA:
        raise RulesError(f"unsupported rules schema {data.get('schema')}")
    return validate(data)
