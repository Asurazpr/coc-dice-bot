from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from cocbot.config import settings


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


def main() -> None:
    if not settings.DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Set it in environment variables or .env (if you use python-dotenv)."
        )
    bot.run(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
