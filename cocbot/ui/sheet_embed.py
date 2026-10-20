from __future__ import annotations

from typing import Mapping, Optional, Sequence

import discord

from cocbot.characters.models import Investigator
from cocbot.ui.sheet_text import build_list_text, build_sheet_text

_STATUS_COLOR = {
    "draft": discord.Color.light_grey(),
    "ready": discord.Color.dark_teal(),
    "archived": discord.Color.dark_grey(),
}


def build_sheet_embed(
    inv: Investigator, skill_names: Mapping[str, str], active_here: bool = False
) -> discord.Embed:
    text = build_sheet_text(inv, skill_names, active_here)
    e = discord.Embed(title=text.title, description=text.description,
                      color=_STATUS_COLOR.get(inv.status, discord.Color.blurple()))
    for name, value, inline in text.fields:
        e.add_field(name=name, value=value or "—", inline=inline)
    e.set_footer(text=text.footer)
    return e


def build_list_embed(invs: Sequence[Investigator], active_id: Optional[int], archived_count: int = 0) -> discord.Embed:
    e = discord.Embed(title="Your investigators", description=build_list_text(invs, active_id),
                      color=discord.Color.dark_teal())
    footer = "⭐ = active here. Pick one below to open it."
    if archived_count:
        footer += f"  ·  {archived_count} archived (use show_archived)"
    e.set_footer(text=footer)
    return e
