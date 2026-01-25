from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import discord


# --- CoC result labeling helpers ---

def _success_band(roll: int, target: int) -> str:
    """
    CoC 7e success bands (common table):
      - Critical success: roll == 1
      - Fumble: roll == 100 OR (target < 50 and roll >= 96)
      - Extreme: roll <= floor(target/5)
      - Hard: roll <= floor(target/2)
      - Regular: roll <= target
      - Fail otherwise
    """
    if roll == 1:
        return "CRITICAL SUCCESS"
    # fumble rule
    if roll == 100 or (target < 50 and roll >= 96):
        return "FUMBLE"
    if roll <= target // 5:
        return "EXTREME SUCCESS"
    if roll <= target // 2:
        return "HARD SUCCESS"
    if roll <= target:
        return "SUCCESS"
    return "FAILURE"


def _emoji_for_band(band: str) -> str:
    return {
        "CRITICAL SUCCESS": "✨",
        "EXTREME SUCCESS": "🔥",
        "HARD SUCCESS": "✅",
        "SUCCESS": "✅",
        "FAILURE": "❌",
        "FUMBLE": "💥",
    }.get(band, "🎲")


def _color_for_band(band: str) -> discord.Color:
    return {
        "CRITICAL SUCCESS": discord.Color.gold(),
        "EXTREME SUCCESS": discord.Color.orange(),
        "HARD SUCCESS": discord.Color.green(),
        "SUCCESS": discord.Color.green(),
        "FAILURE": discord.Color.red(),
        "FUMBLE": discord.Color.dark_red(),
    }.get(band, discord.Color.blurple())


def _fmt_int(x: Optional[int]) -> str:
    return "—" if x is None else str(x)


@dataclass(frozen=True)
class CheckEmbedInput:
    # Identity
    actor_name: str                      # e.g. "Jason"
    skill_name_display: str              # e.g. "Spot Hidden"
    skill_value: int                     # final target number (after mods)

    # Roll core
    rolled: int                          # the final/selected d100 result (after B/P selection)
    raw_units: Optional[int] = None      # d10 ones digit (optional, for debugging)
    raw_tens: Optional[int] = None       # d10 tens digit (optional, for debugging)

    # Bonus/Penalty handling
    bp_dice: int = 0                     # absolute count
    bp_mode: Optional[str] = None        # "bonus" | "penalty" | None
    bp_candidates: Optional[Sequence[int]] = None  # list of candidate d100s, include rolled too

    # Extra context
    pushed: bool = False
    luck_spent: int = 0
    luck_after: Optional[int] = None
    notes: Optional[str] = None          # e.g. "Darkness: -20 applied"

    # Optional: show character sheet values
    base_value: Optional[int] = None     # pre-mod skill
    mod_total: Optional[int] = None      # total modifier applied to base to get skill_value


def build_check_embed_old(inp: CheckEmbedInput) -> discord.Embed:
    band = _success_band(inp.rolled, inp.skill_value)
    emoji = _emoji_for_band(band)
    color = _color_for_band(band)

    extreme = inp.skill_value // 5
    hard = inp.skill_value // 2

    title = f"{emoji} {inp.skill_name_display} — {band}"
    desc_lines = []

    # Big “roll vs target” line (old-style readability)
    # Big roll number as its own line
    desc_lines.append(f"## 🎲 `{inp.rolled:02d}`")
    desc_lines.append(f"**Target:** `{inp.skill_value}`")

    # Tier thresholds in one tight line
    desc_lines.append(f"**Hard:** `{hard}`   **Extreme:** `{extreme}`")

    # Optional base/mod display (keeps it readable, not spammy)
    if inp.base_value is not None or inp.mod_total is not None:
        desc_lines.append(
            f"**Base:** `{_fmt_int(inp.base_value)}`   **Mod:** `{_fmt_int(inp.mod_total)}`"
        )

    # Bonus/Penalty “candidate rolls” line (this is the part most newer embeds make ugly)
    if inp.bp_mode in ("bonus", "penalty") and inp.bp_dice > 0:
        mode = "Bonus" if inp.bp_mode == "bonus" else "Penalty"
        if inp.bp_candidates:
            # Example: [42, 12, 72] -> show and indicate chosen
            parts = []
            for r in inp.bp_candidates:
                if r == inp.rolled:
                    parts.append(f"`{r:02d}`*")
                else:
                    parts.append(f"`{r:02d}`")
            cand = " ".join(parts)
            desc_lines.append(f"**{mode} Dice ({inp.bp_dice}):** " + " ".join(parts))
        else:
            desc_lines.append(f"**{mode} Dice:** `{inp.bp_dice}`")

    # Pushed / luck lines (short)
    flags = []
    if inp.pushed:
        flags.append("**Pushed**")
    if inp.luck_spent > 0:
        if inp.luck_after is None:
            flags.append(f"**Luck Spent:** `{inp.luck_spent}`")
        else:
            flags.append(f"**Luck Spent:** `{inp.luck_spent}` → **Luck Now:** `{inp.luck_after}`")
    if flags:
        desc_lines.append(" • ".join(flags))

    if inp.notes:
        desc_lines.append(f"**Notes:** {inp.notes}")

    e = discord.Embed(title=title, description="\n".join(desc_lines), color=color)
    e.set_footer(text=f"Check by {inp.actor_name}")
    return e
