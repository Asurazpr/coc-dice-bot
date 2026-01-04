from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from cocbot.config import settings
from cocbot.mechanics.dice import parse_and_roll
from cocbot.mechanics.dice import d100_check


class CocBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        # Later: load cogs here
        # await self.load_extension("apps.discord_bot.cogs.rolls")

        # Sync commands
        if settings.DISCORD_GUILD_ID:
            guild = discord.Object(id=settings.DISCORD_GUILD_ID)
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


# sanity command so we know the bot works
@bot.tree.command(name="ping", description="Check if the bot is alive.")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("pong")


@bot.tree.command(name="roll", description="Roll dice like d20, 2d6+1, 1d100.")
@app_commands.describe(expr="Dice expression (e.g., d20, 2d6+1, 1d100)")
async def roll(interaction: discord.Interaction, expr: str) -> None:
    try:
        result = parse_and_roll(expr)
    except Exception as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    await interaction.response.send_message(f"🎲 `{expr}` → **{result}**")


@bot.tree.command(name="check", description="Call of Cthulhu 7e skill/stat check (d100).")
@app_commands.describe(
    target="Target value (e.g. skill or stat, 1–100)",
    bonus_penalty="Bonus (+) or penalty (-) dice count (optional)"
)
async def check(
    interaction: discord.Interaction,
    target: int,
    bonus_penalty: int = 0,
) -> None:
    if target < 1 or target > 100:
        await interaction.response.send_message(
            "❌ Target must be between 1 and 100.",
            ephemeral=True,
        )
        return

    try:
        result = d100_check(target=target, bp=bonus_penalty)
    except Exception as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return

    bp_text = ""
    if bonus_penalty > 0:
        bp_text = f" (+{bonus_penalty} bonus)"
    elif bonus_penalty < 0:
        bp_text = f" ({bonus_penalty} penalty)"

    msg = (
        f"🎯 **Check** (Target **{target}**{bp_text})\n"
        f"🎲 Roll: **{result.roll}**\n"
        f"➡️ **{result.level.value}**"
    )

    await interaction.response.send_message(msg)


def main() -> None:
    if not settings.DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Set it in environment variables or .env (if you use python-dotenv)."
        )
    bot.run(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
