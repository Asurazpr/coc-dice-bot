"""`/character import`: private preview, create or update, all owner-only.

The parsed sheet lives only in this view's memory until the player confirms
or the panel times out; the uploaded file is never written anywhere.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord

from apps.discord_bot.views import Ctx, OwnedView, render_panel, reply_error
from cocbot.characters.models import Investigator
from cocbot.characters.service import CharacterError
from cocbot.db.connection import get_conn
from cocbot.i18n import label, localize, localized_names, render, skill_names
from cocbot.imports.service import ImportPreview, ImportRejected, ImportService
from cocbot.imports.workbook import MAX_UPLOAD_BYTES
from cocbot.ui.import_text import diff_screen, preview_screen
from cocbot.ui.keeper_text import Screen

log = logging.getLogger(__name__)
IMPORT_COLOR = discord.Color.dark_green()


def to_embed(screen: Screen, color: discord.Color = IMPORT_COLOR) -> discord.Embed:
    e = discord.Embed(title=screen.title, description=screen.description or None, color=color)
    for name, value in screen.fields:
        e.add_field(name=name[:256], value=(value or "—")[:1024], inline=False)
    if screen.footer:
        e.set_footer(text=screen.footer[:2048])
    return e


def _parse(db_path: str, owner_id: int, data: bytes) -> ImportPreview:
    with get_conn(db_path) as conn:
        return ImportService(conn).preview(owner_id, data)


async def start_import(interaction: discord.Interaction, ctx: Ctx, attachment,
                       update_ref: Optional[str] = None) -> None:
    """Validate the attachment, parse it off the event loop, and show the private preview."""
    name = (getattr(attachment, "filename", "") or "").lower()
    if not name.endswith(".xlsx"):
        await interaction.response.send_message(f"⚠️ {render('import_wrong_type', ctx.lang)}", ephemeral=True)
        return
    if (getattr(attachment, "size", 0) or 0) > MAX_UPLOAD_BYTES:
        await interaction.response.send_message(
            f"⚠️ {render('import_too_large', ctx.lang, mb=MAX_UPLOAD_BYTES // (1024 * 1024))}", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await attachment.read()
        preview = await asyncio.to_thread(_parse, ctx.db_path, ctx.owner_id, data)
        target = None
        if update_ref:
            with get_conn(ctx.db_path) as conn:
                target = ImportService(conn).investigators.find(ctx.owner_id, update_ref)
    except ImportRejected as e:
        await interaction.followup.send(f"⚠️ {localize(e.args[0], ctx.lang)}", ephemeral=True)
        return
    except CharacterError as e:
        await interaction.followup.send(f"⚠️ {localize(e.args[0] if e.args else str(e), ctx.lang)}", ephemeral=True)
        return
    except Exception as e:  # never log or echo sheet contents
        log.warning("Excel import failed: %s", type(e).__name__)
        await interaction.followup.send(render("imp_parsing_failed", ctx.lang), ephemeral=True)
        return
    del data
    if target is not None and all(c.id != target.id for c in preview.candidates):
        preview.candidates.insert(0, target)
    embed, view = render_preview(ctx, preview)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def render_preview(ctx: Ctx, preview: ImportPreview):
    with get_conn(ctx.db_path) as conn:
        names = skill_names(conn, ctx.lang)
        nbl = localized_names(conn)
        new_name = ImportService(conn).free_name(ctx.owner_id, preview.sheet.name) if preview.sheet.name else None
    return to_embed(preview_screen(preview, names, ctx.lang, nbl)), ImportPreviewView(ctx, preview, new_name)


class ImportPreviewView(OwnedView):
    def __init__(self, ctx: Ctx, preview: ImportPreview, new_name: Optional[str]):
        super().__init__(ctx)
        self.preview, self.new_name = preview, new_name
        lang = ctx.lang
        same = new_name == preview.sheet.name
        text = label("imp_btn_create", lang, 80) if same else label("imp_btn_create_as", lang, 80, name=new_name)
        self.create_button = discord.ui.Button(label=text, emoji="📥", style=discord.ButtonStyle.success,
                                               disabled=not preview.can_save or not new_name)
        self.create_button.callback = self._create  # type: ignore[assignment]
        self.add_item(self.create_button)
        self.update_buttons = []
        for inv in preview.candidates[:3]:
            b = discord.ui.Button(label=label("imp_btn_update", lang, 80, name=inv.name), emoji="🔁",
                                  style=discord.ButtonStyle.primary, disabled=not preview.can_save)
            b.callback = self._update(inv)  # type: ignore[assignment]
            self.update_buttons.append(b)
            self.add_item(b)
        self.cancel_button = discord.ui.Button(label=label("kp_btn_cancel", lang, 80))
        self.cancel_button.callback = self._cancel  # type: ignore[assignment]
        self.add_item(self.cancel_button)

    async def _create(self, interaction: discord.Interaction) -> None:
        try:
            with get_conn(self.ctx.db_path) as conn:
                inv = ImportService(conn).create(self.ctx.owner_id, self.preview, name=self.new_name)
            embed, view = render_panel(self.ctx, inv.id)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        self.stop()
        await interaction.response.edit_message(content=render("imp_created", self.ctx.lang, name=inv.name),
                                                embed=embed, view=view)

    def _update(self, inv: Investigator):
        async def cb(interaction: discord.Interaction) -> None:
            try:
                with get_conn(self.ctx.db_path) as conn:
                    plan = ImportService(conn).plan_update(self.ctx.owner_id, inv.id, self.preview)
                    names = skill_names(conn, self.ctx.lang)
            except CharacterError as e:
                await reply_error(interaction, e, self.ctx)
                return
            await interaction.response.edit_message(
                embed=to_embed(diff_screen(plan, names, self.ctx.lang), discord.Color.orange()),
                view=ConfirmUpdateView(self.ctx, self.preview, inv.id, plan.fingerprint, self.new_name,
                                       blocked=plan.blocked is not None))
        return cb

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        self.preview = None   # type: ignore[assignment]  # drop the parsed data now
        await interaction.response.edit_message(content=render("imp_cancelled", self.ctx.lang), embed=None, view=None)


class ConfirmUpdateView(OwnedView):
    def __init__(self, ctx: Ctx, preview: ImportPreview, investigator_id: int, fingerprint: str,
                 new_name: Optional[str], blocked: bool = False):
        super().__init__(ctx)
        self.preview, self.investigator_id, self.fingerprint, self.new_name = preview, investigator_id, fingerprint, \
            new_name
        lang = ctx.lang
        self.confirm_button = discord.ui.Button(label=label("imp_btn_confirm_update", lang, 80), emoji="✔️",
                                                style=discord.ButtonStyle.danger, disabled=blocked)
        self.confirm_button.callback = self._confirm  # type: ignore[assignment]
        self.back_button = discord.ui.Button(label=label("kp_btn_back", lang, 80), emoji="⬅️")
        self.back_button.callback = self._back  # type: ignore[assignment]
        self.cancel_button = discord.ui.Button(label=label("kp_btn_cancel", lang, 80))
        self.cancel_button.callback = self._cancel  # type: ignore[assignment]
        for b in (self.confirm_button, self.back_button, self.cancel_button):
            self.add_item(b)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        try:
            with get_conn(self.ctx.db_path) as conn:
                svc = ImportService(conn)
                n = len(svc.plan_update(self.ctx.owner_id, self.investigator_id, self.preview).changes)
                inv = svc.update(self.ctx.owner_id, self.investigator_id, self.preview,
                                 expected_fingerprint=self.fingerprint)
            embed, view = render_panel(self.ctx, inv.id)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        self.stop()
        await interaction.response.edit_message(content=render("imp_updated", self.ctx.lang, name=inv.name, n=n),
                                                embed=embed, view=view)

    async def _back(self, interaction: discord.Interaction) -> None:
        embed, view = render_preview(self.ctx, self.preview)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        self.preview = None   # type: ignore[assignment]
        await interaction.response.edit_message(content=render("imp_cancelled", self.ctx.lang), embed=None, view=None)


__all__ = ["ConfirmUpdateView", "ImportPreviewView", "render_preview", "start_import"]
