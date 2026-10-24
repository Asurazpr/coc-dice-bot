"""Discord UI for stored rolls: the result embed, the Spend Luck flow and `/rolls`.

The Luck button sits on the (possibly public) check message, so it does not
use ``OwnedView``: anyone may press it, and ``RollService`` answers "not
yours" for everyone but the roller. Every press re-reads the roll; the
confirmation carries the roll's ``state_version`` and the interaction ID, so
a stale or replayed confirmation cannot charge Luck twice.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

from cocbot.campaigns.checks import rules_note
from cocbot.campaigns.service import CampaignService
from cocbot.characters.service import CharacterError, NotFound
from cocbot.db.connection import get_conn
from cocbot.i18n import label, localize, render
from cocbot.rolls.rules import LUCK_WINDOW_SECONDS
from cocbot.rolls.service import LuckStatus, Roll, RollService
from cocbot.ui.check_embed_old import CheckEmbedInput, build_check_embed_old
from cocbot.ui.roll_text import detail_fields, difficulty_name, luck_prompt, recent_line, result_lines

log = logging.getLogger(__name__)


def _lang(db_path: str, user_id: int, locale=None) -> str:
    from cocbot.preferences import get_language
    with get_conn(db_path) as conn:
        return get_language(conn, user_id, locale)


def _err(err: Exception, lang: str) -> str:
    if isinstance(err, CharacterError):
        payload = err.args[0] if err.args else str(err)
        return f"⚠️ {localize(payload, lang)}"
    log.exception("Unhandled error in roll UI", exc_info=err)
    return "⚠️ Something went wrong. Please try again."


async def _reply(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


def campaign_name(conn, roll: Roll) -> Optional[str]:
    if roll.campaign_id is None:
        return None
    camp = CampaignService(conn)._campaign(roll.campaign_id)
    return camp.name if camp else None


def roll_embed(db_path: str, roll: Roll, display_name: str, lang: str) -> discord.Embed:
    """The check result, built only from the stored roll (so it looks the same before and after Luck)."""
    with get_conn(db_path) as conn:
        name = campaign_name(conn, roll)
    actor = f"{roll.investigator_name} · {display_name}" if roll.investigator_name else display_name
    candidates = roll.dice.get("candidates") if roll.bonus_penalty else None
    inp = CheckEmbedInput(
        actor_name=actor,
        skill_name_display=roll.subject_label,
        skill_value=roll.target,
        rolled=roll.natural,
        bp_dice=abs(roll.bonus_penalty),
        bp_mode="bonus" if roll.bonus_penalty > 0 else "penalty" if roll.bonus_penalty < 0 else None,
        bp_candidates=candidates,
        base_value=roll.base_value,
        rules=roll.rules.to_check_rules(),
        context_note=rules_note(name, roll.rules_version, roll.rules) if name else None,
        level_override=roll.effective_level,
        extra_lines=result_lines(roll, lang),
        roll_id=roll.id,
    )
    return build_check_embed_old(inp)


class LuckButtonView(discord.ui.View):
    """Attached to a check result whose roll can take Luck."""

    def __init__(self, db_path: str, roll: Roll, display_name: str, lang: str):
        super().__init__(timeout=LUCK_WINDOW_SECONDS)
        self.db_path, self.roll_id, self.display_name, self.lang = db_path, roll.id, display_name, lang
        self.private = roll.private
        self.luck_button = discord.ui.Button(label=label("luck_button", lang, 80), emoji="🍀",
                                             style=discord.ButtonStyle.secondary)
        self.luck_button.callback = self._open  # type: ignore[assignment]
        self.add_item(self.luck_button)

    async def _open(self, interaction: discord.Interaction) -> None:
        lang = _lang(self.db_path, interaction.user.id, getattr(interaction, "locale", None))
        try:
            with get_conn(self.db_path) as conn:
                status = RollService(conn).luck_status(interaction.user.id, self.roll_id)
                name = campaign_name(conn, status.roll)
        except NotFound:
            await _reply(interaction, f"⚠️ {render('luck_not_yours', lang)}")
            return
        except CharacterError as e:
            await _reply(interaction, _err(e, lang))
            return
        if not status.eligible:
            await _reply(interaction, f"⚠️ {render(status.reason, lang)}")
            return
        view = LuckConfirmView(self.db_path, status, self.display_name, lang,
                               public_message=None if self.private else getattr(interaction, "message", None))
        embed = discord.Embed(description=luck_prompt(status, lang, name)[:4000], color=discord.Color.green())
        if self.private:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class LuckConfirmView(discord.ui.View):
    """Private confirmation: one button per reachable difficulty, plus Cancel."""

    def __init__(self, db_path: str, status: LuckStatus, display_name: str, lang: str,
                 public_message: Optional[discord.Message] = None):
        super().__init__(timeout=LUCK_WINDOW_SECONDS)
        self.db_path, self.display_name, self.lang = db_path, display_name, lang
        self.roll = status.roll
        self.owner = status.roll.user_id
        self.version = status.roll.state_version
        self.public_message = public_message
        self.option_buttons = []
        for option in status.options:
            text = render("luck_option_short", lang, difficulty=difficulty_name(option.difficulty, lang),
                          cost=option.cost)
            b = discord.ui.Button(label=text[:80], style=discord.ButtonStyle.success, emoji="🍀")
            b.callback = self._spend(option.difficulty)  # type: ignore[assignment]
            self.option_buttons.append(b)
            self.add_item(b)
        self.cancel_button = discord.ui.Button(label=label("kp_btn_cancel", lang, 80))
        self.cancel_button.callback = self._cancel  # type: ignore[assignment]
        self.add_item(self.cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner:
            await _reply(interaction, f"⚠️ {render('luck_not_yours', self.lang)}")
            return False
        return True

    def _spend(self, difficulty: str):
        async def cb(interaction: discord.Interaction) -> None:
            try:
                with get_conn(self.db_path) as conn:
                    result = RollService(conn).spend_luck(interaction.user.id, self.roll.id, difficulty,
                                                          request_id=interaction.id,
                                                          expected_version=self.version)
            except CharacterError as e:
                await _reply(interaction, _err(e, self.lang))
                return
            r = result.roll
            done = render("luck_spent_done", self.lang, cost=r.luck_spent, natural=f"{r.natural:02d}",
                          effective=f"{r.effective:02d}", level=render(f"lvl_{r.effective_level.name}", self.lang),
                          before=result.luck_before, after=result.luck_after)
            embed = roll_embed(self.db_path, r, self.display_name, self.lang)
            if self.public_message is None:
                await interaction.response.edit_message(content=done, embed=embed, view=None)
                return
            await interaction.response.edit_message(content=done, embed=None, view=None)
            try:
                await self.public_message.edit(embed=embed, view=None)
            except discord.HTTPException:   # pragma: no cover - message deleted or not editable
                log.warning("Could not update public roll message for roll %s", r.id)
        return cb

    async def _cancel(self, interaction: discord.Interaction) -> None:
        cancelled = render("kp_cancelled", self.lang)
        if self.public_message is None:
            with get_conn(self.db_path) as conn:
                roll = RollService(conn).get(interaction.user.id, self.roll.id)
            embed = roll_embed(self.db_path, roll, self.display_name, self.lang)
            await interaction.response.edit_message(
                content=cancelled, embed=embed, view=LuckButtonView(self.db_path, roll, self.display_name, self.lang))
            return
        await interaction.response.edit_message(content=cancelled, embed=None, view=None)


def recent_embed(db_path: str, user_id: int, lang: str) -> discord.Embed:
    with get_conn(db_path) as conn:
        rolls = RollService(conn).recent(user_id, limit=10)
        names = {}
        for r in rolls:
            if r.campaign_id and r.campaign_id not in names:
                names[r.campaign_id] = campaign_name(conn, r) or "?"
    lines = [recent_line(r, lang, names) for r in rolls] or [render("rolls_empty", lang)]
    return discord.Embed(title=render("rolls_title", lang)[:256], description="\n".join(lines)[:4000],
                         color=discord.Color.blurple())


def detail_embed(db_path: str, user_id: int, roll_id: int, lang: str) -> discord.Embed:
    with get_conn(db_path) as conn:
        svc = RollService(conn)
        roll = svc.get(user_id, roll_id)               # NotFound unless it is the caller's
        events = svc.events(user_id, roll_id)
        status = svc.luck_status(user_id, roll_id)
        name = campaign_name(conn, roll)
    e = discord.Embed(title=render("rolls_detail_title", lang, id=roll.id, subject=roll.subject_label)[:256],
                      description=recent_line(roll, lang, {roll.campaign_id: name} if name else None),
                      color=discord.Color.blurple())
    for n, v in detail_fields(roll, events, status, lang, name):
        e.add_field(name=n, value=v or "—", inline=False)
    return e


__all__ = ["LuckButtonView", "LuckConfirmView", "detail_embed", "recent_embed", "roll_embed"]
