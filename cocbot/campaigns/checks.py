"""Campaign-aware `/check` resolution (no Discord).

In a channel linked to a campaign:

* the campaign's current rule profile decides critical/fumble bands and
  whether the roll is shown publicly or only to the roller;
* a participating player rolls with the *campaign view* of their investigator
  (campaign HP/MP/SAN/Luck, permanent characteristics and skills);
* anyone else (Keepers, non-members) rolls with their personal active
  investigator or a plain number, under the channel's campaign rules.

Outside campaign channels nothing changes: personal investigator, RAW rules,
public result.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from cocbot.campaigns.rules import RAW_PROFILE, CampaignRules
from cocbot.campaigns.service import Campaign, CampaignContext, CampaignService
from cocbot.characters.check_target import CheckTarget, resolve_check_target


@dataclass(frozen=True)
class CheckPlan:
    target: CheckTarget
    rules: CampaignRules = RAW_PROFILE
    campaign: Optional[Campaign] = None
    rules_version: Optional[int] = None
    context: Optional[CampaignContext] = None       # set when rolling as a campaign participant

    @property
    def private(self) -> bool:
        return self.campaign is not None and self.rules.roll_visibility == "private"

    @property
    def note(self) -> Optional[str]:
        """Footer text naming the campaign and rule profile used."""
        if self.campaign is None:
            return None
        parts = [f"{self.campaign.name} · rules v{self.rules_version}"]
        if not self.rules.is_raw:
            extras = []
            if self.rules.critical_max > 1:
                extras.append(f"critical 01-{self.rules.critical_max:02d}")
            if self.rules.fumble == "only_100":
                extras.append("fumble 100 only")
            if extras:
                parts.append(", ".join(extras))
        return " · ".join(parts)


def plan_check(conn: sqlite3.Connection, user_id: object, guild_id: Optional[object],
               channel_id: Optional[object], raw: str) -> CheckPlan:
    svc = CampaignService(conn)
    camp = svc.campaign_for_channel(channel_id)
    if camp is None or camp.status != "active":
        return CheckPlan(resolve_check_target(conn, user_id, guild_id, raw))
    ctx = svc.context(user_id, channel_id)
    if ctx is not None:
        target = resolve_check_target(conn, user_id, guild_id, raw, investigator=ctx.investigator)
        return CheckPlan(target, ctx.rules, ctx.campaign, ctx.rules_version, ctx)
    version, rules = svc.channel_rules(camp.id)
    return CheckPlan(resolve_check_target(conn, user_id, guild_id, raw), rules, camp, version)


def record_private_roll(conn: sqlite3.Connection, plan: CheckPlan, summary: str) -> None:
    """Private campaign rolls by participants are kept in the campaign history for the Keepers."""
    if plan.private and plan.context is not None:
        CampaignService(conn).record_roll(plan.context, summary)
