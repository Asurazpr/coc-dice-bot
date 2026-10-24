from __future__ import annotations

import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from apps.discord_bot.campaign_cog import CampaignCog, KeeperCog
from apps.discord_bot.characters_cog import CharacterCog
from apps.discord_bot.views import reply_error
from cocbot.characters.check_target import (
    STAT_LABELS,
    CheckTarget,
    CheckTargetError,
)
from cocbot.campaigns.checks import CheckPlan, plan_check
from cocbot.rolls.service import RollService
from apps.discord_bot.roll_views import LuckButtonView, detail_embed, recent_embed, roll_embed
from cocbot.campaigns.service import CampaignService
from cocbot.characters.service import CharacterError, InvestigatorService
from cocbot.config import settings
from cocbot.db.connection import get_conn
from cocbot.db.bootstrap import bootstrap
from cocbot.db.repo_skill_defs import search_skills, skill_display_names
from cocbot.mechanics.checks import RAW_RULES
from cocbot.mechanics.dice import MAX_BONUS_PENALTY_DICE, parse_and_roll
from cocbot.ui.check_embed_old import CheckEmbedInput

log = logging.getLogger("cocbot")

DIFFICULTY_CHOICES = [app_commands.Choice(name="Regular / 常规", value="regular"),
                      app_commands.Choice(name="Hard / 困难", value="hard"),
                      app_commands.Choice(name="Extreme / 极难", value="extreme")]


class CocBot(commands.Bot):
    def __init__(self, db_path: Optional[str] = None) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.db_path = str(db_path or settings.DB_PATH)

    async def setup_hook(self) -> None:
        # Schema is always brought up to date before any command can run.
        with get_conn(self.db_path) as conn:
            applied = bootstrap(conn, log=log.info)
        log.info("Database ready at %s (%d migration(s) applied)", self.db_path, len(applied))

        await self.add_cog(CharacterCog(self, self.db_path))
        await self.add_cog(CampaignCog(self, self.db_path))
        await self.add_cog(KeeperCog(self, self.db_path))
        register_commands(self)

        if settings.DISCORD_GUILD_ID:
            guild = discord.Object(id=int(settings.DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to dev guild %s", settings.DISCORD_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Synced global commands (may take time to appear)")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")


def build_check_input(
    target: CheckTarget, user_display_name: str, roll: int, bonus_penalty: int, bp_candidates,
    plan: Optional[CheckPlan] = None,
) -> CheckEmbedInput:
    label = target.label if not target.detail else f"{target.label} ({target.detail})"
    actor = user_display_name
    if target.investigator is not None:
        actor = f"{target.investigator.name} · {user_display_name}"
    return CheckEmbedInput(
        actor_name=actor,
        skill_name_display=label,
        skill_value=target.value,
        rolled=roll,
        bp_dice=abs(bonus_penalty),
        bp_mode="bonus" if bonus_penalty > 0 else "penalty" if bonus_penalty < 0 else None,
        bp_candidates=bp_candidates,
        base_value=target.base_value if target.detail == "trained" else None,
        rules=plan.rules.to_check_rules() if plan is not None else RAW_RULES,
        context_note=plan.note if plan is not None else None,
    )


def register_commands(bot: CocBot) -> None:
    tree = bot.tree

    @tree.command(name="roll", description="Roll dice like d20, 2d6+1, 1d100.")
    @app_commands.describe(expr="Dice expression (e.g., d20, 2d6+1, 1d100)")
    async def roll(interaction: discord.Interaction, expr: str) -> None:
        try:
            result = parse_and_roll(expr)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        await interaction.response.send_message(f"🎲 `{expr}` → **{result}**")

    @tree.command(
        name="check",
        description="CoC 7e check: a target number, a skill (EN/中文) or a characteristic, using your investigator.",
    )
    @app_commands.describe(
        target_or_skill="Number (1-100), skill (listen / 聆听) or characteristic (STR, Luck, SAN)",
        bonus_penalty="Bonus (+) or penalty (-) dice",
        difficulty="Required difficulty (default Regular)",
    )
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES)
    async def check(
        interaction: discord.Interaction,
        target_or_skill: str,
        bonus_penalty: app_commands.Range[int, -MAX_BONUS_PENALTY_DICE, MAX_BONUS_PENALTY_DICE] = 0,
        difficulty: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        from cocbot.preferences import get_language
        try:
            with get_conn(bot.db_path) as conn:
                plan = plan_check(conn, interaction.user.id, interaction.guild_id,
                                  getattr(interaction, "channel_id", None), target_or_skill)
                roll = RollService(conn).roll_check(
                    plan, interaction.user.id, guild_id=interaction.guild_id,
                    channel_id=getattr(interaction, "channel_id", None), bonus_penalty=bonus_penalty,
                    difficulty=difficulty.value if difficulty else "regular")
                lang = get_language(conn, interaction.user.id, getattr(interaction, "locale", None))
                status = RollService(conn).luck_status(interaction.user.id, roll.id)
        except CheckTargetError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        except CharacterError as e:
            await reply_error(interaction, e)
            return

        embed = roll_embed(bot.db_path, roll, interaction.user.display_name, lang)
        kwargs = {"embed": embed}
        if status.eligible:
            kwargs["view"] = LuckButtonView(bot.db_path, roll, interaction.user.display_name, lang)
        if roll.private:
            kwargs["ephemeral"] = True
        await interaction.response.send_message(**kwargs)

    @tree.command(name="rolls", description="Your recent checks (private) / 你最近的检定")
    @app_commands.describe(roll="Show one roll in detail (its number, e.g. 12)")
    async def rolls(interaction: discord.Interaction, roll: Optional[app_commands.Range[int, 1]] = None) -> None:
        from cocbot.preferences import get_language
        with get_conn(bot.db_path) as conn:
            lang = get_language(conn, interaction.user.id, getattr(interaction, "locale", None))
        try:
            embed = (recent_embed(bot.db_path, interaction.user.id, lang) if roll is None
                     else detail_embed(bot.db_path, interaction.user.id, roll, lang))
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @check.autocomplete("target_or_skill")
    async def check_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        if current.strip().isdigit():
            return [app_commands.Choice(name=f"Target {current.strip()}", value=current.strip())]
        choices: List[app_commands.Choice[str]] = []
        with get_conn(bot.db_path) as conn:
            ctx = CampaignService(conn).context(interaction.user.id, getattr(interaction, "channel_id", None))
            inv = ctx.investigator if ctx is not None else \
                InvestigatorService(conn).active(interaction.user.id, interaction.guild_id)
            cur = current.strip().lower()
            if inv is not None:
                names = skill_display_names(conn, inv.skills.keys())
                for key, value in sorted(inv.skills.items(), key=lambda kv: -kv[1]):
                    label = names.get(key, key)
                    if not cur or cur in label.lower() or cur in key:
                        choices.append(app_commands.Choice(name=f"{label} {value} ({inv.name})"[:100], value=key))
                for stat, (en, zh) in STAT_LABELS.items():
                    value = inv.stat(stat)
                    if value is not None and (not cur or cur in en.lower() or cur in zh):
                        choices.append(app_commands.Choice(name=f"{en} / {zh} {value}", value=en))
            seen = {c.value for c in choices}
            for key, label in search_skills(conn, current):
                if key not in seen:
                    choices.append(app_commands.Choice(name=label[:100], value=key))
        return choices[:25]

    @tree.command(name="language", description="Display language for creation steps / 选择显示语言")
    @app_commands.describe(language="English, 简体中文, or both / 英文、简体中文或双语")
    @app_commands.choices(language=[
        app_commands.Choice(name="English", value="en"),
        app_commands.Choice(name="简体中文", value="zh"),
        app_commands.Choice(name="Bilingual / 双语", value="both"),
    ])
    async def language(interaction: discord.Interaction,
                       language: Optional[app_commands.Choice[str]] = None) -> None:
        from cocbot.i18n import LANG_NAMES, render
        from cocbot.preferences import get_language, set_language
        with get_conn(bot.db_path) as conn:
            if language is None:
                lang = get_language(conn, interaction.user.id, getattr(interaction, "locale", None))
                text = render("lang_current", lang, name=LANG_NAMES[lang])
            else:
                lang = set_language(conn, interaction.user.id, language.value)
                text = render("lang_set", lang, name=LANG_NAMES[lang])
        await interaction.response.send_message(text, ephemeral=True)

    @tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        # Single error funnel for every command (including the /character group):
        # user-facing CharacterErrors are shown as-is, anything else is logged.
        await reply_error(interaction, getattr(error, "original", error))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and set it.")
    CocBot().run(settings.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
