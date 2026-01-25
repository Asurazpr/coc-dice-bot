from __future__ import annotations

import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
import traceback

from cocbot.config import settings
from cocbot.mechanics.dice import parse_and_roll, d100_check_details
from cocbot.db.repo_skill_defs import resolve_skill
from cocbot.mechanics.skill_base import resolve_skill_base
from cocbot.db.characters import set_active_character_id
from cocbot.ui.check_embed_old import (
    CheckEmbedInput,
    build_check_embed_old,
)


def get_conn() -> sqlite3.Connection:
    # adjust if your DB path differs
    conn = sqlite3.connect("data/coc_bot.sqlite3")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


class CocBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        # Sync commands (guild for fast dev)
        if settings.DISCORD_GUILD_ID:
            guild = discord.Object(id=int(settings.DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"[discord] Synced commands to guild {settings.DISCORD_GUILD_ID}")
        else:
            await self.tree.sync()
            print("[discord] Synced global commands")


bot = CocBot()


@bot.event
async def on_ready() -> None:
    print(f"[discord] Logged in as {bot.user} (id={bot.user.id})")


@bot.tree.command(name="roll", description="Roll dice like d20, 2d6+1, 1d100.")
@app_commands.describe(expr="Dice expression (e.g., d20, 2d6+1, 1d100)")
async def roll(interaction: discord.Interaction, expr: str) -> None:
    try:
        result = parse_and_roll(expr)
    except Exception as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    await interaction.response.send_message(f"🎲 `{expr}` → **{result}**")


@bot.tree.command(name="setchar", description="Set active character id for this server (used for derived skills).")
@app_commands.describe(character_id="Character ID in the database (matches attributes.character_id)")
async def setchar(interaction: discord.Interaction, character_id: int) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
        return

    guild_id = str(interaction.guild_id)

    with get_conn() as conn:
        set_active_character_id(conn, guild_id, character_id)
        conn.commit()

    await interaction.response.send_message(f"✅ Active character set to `{character_id}`.", ephemeral=True)


@bot.tree.command(name="check", description="CoC 7e check: pass a target number OR a skill name (EN/CN).")
@app_commands.describe(
    target_or_skill="Number (1–100) or skill name (e.g., listen / 聆听)",
    bonus_penalty="Bonus (+) or penalty (-) dice count (optional)"
)
async def check(
    interaction: discord.Interaction,
    target_or_skill: str,
    bonus_penalty: int = 0,
) -> None:
    await interaction.response.defer(thinking=True)
    try:
        raw = target_or_skill.strip()
        if not raw:
            await interaction.followup.send("❌ Provide a target or skill name.", ephemeral=True)
            return

        # If numeric target: roll directly
        if raw.isdigit():
            target = int(raw)
            if target < 1 or target > 100:
                await interaction.followup.send("❌ Target must be between 1 and 100.", ephemeral=True)
                return
            label = f"Target {target}"
        else:
            # Resolve skill via aliases/i18n
            lang = "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in raw) else "en"
            skill = resolve_skill(raw, lang=lang)
            if not skill:
                await interaction.followup.send(f"❌ Unknown skill: `{raw}`.", ephemeral=True)
                return

            if interaction.guild_id is None:
                guild_id = "dm"
            else:
                guild_id = str(interaction.guild_id)

            with get_conn() as conn:
                target_opt, base_label = resolve_skill_base(conn, guild_id, skill.skill_id)

            if target_opt is None:
                await interaction.followup.send(
                    f"**{skill.display_name}**\n{base_label}\n"
                    f"Set active character: `/setchar <character_id>`",
                    ephemeral=True,
                )
                return

            target = int(target_opt)
            label = f"{skill.display_name} ({base_label})"

        # Perform check
        result, bp_candidates = d100_check_details(target=target, bp=bonus_penalty)
        embed = build_check_embed_old(
            CheckEmbedInput(
                actor_name=interaction.user.display_name,
                skill_name_display=label,  # already includes skill + base label
                skill_value=target,
                rolled=result.roll,

                # bonus / penalty
                bp_dice=abs(bonus_penalty),
                bp_mode=(
                    "bonus" if bonus_penalty > 0
                    else "penalty" if bonus_penalty < 0
                    else None
                ),

                # optional flags (safe defaults)
                pushed=False,
                luck_spent=0,
                luck_after=None,
                notes=None,

                # you don't currently track these — keep None
                base_value=None,
                mod_total=None,
                bp_candidates=bp_candidates,
            )
        )
        await interaction.followup.send(embed=embed)

    except Exception:
        traceback.print_exc()
        await interaction.followup.send("❌ Internal error. Check the bot terminal for traceback.")


def main() -> None:
    if not settings.DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing.")
    bot.run(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
