"""`/character ...` slash commands: the player's personal investigator collection.

All responses that manage a collection are ephemeral (only the player sees
them). `/character view public:True` is the one deliberate exception, for
showing a sheet to the table.
"""
from __future__ import annotations

from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from apps.discord_bot.views import (
    BioModal,
    CollectionView,
    ConfirmDeleteView,
    Ctx,
    render_panel,
    reply_error,
    scope_label,
    service,
    sheet_embed,
)
from cocbot.characters.service import CharacterError, GLOBAL_SCOPE, guild_scope
from cocbot.db.connection import get_conn
from cocbot.db.repo_skill_defs import resolve_skill, search_skills
from cocbot.ui.sheet_embed import build_list_embed

INVESTIGATOR_DESC = "Your investigator (name or pick from the list)"


class CharacterCog(commands.GroupCog, group_name="character", group_description="Manage your investigators"):
    def __init__(self, bot: commands.Bot, db_path: str) -> None:
        self.bot = bot
        self.db_path = db_path
        super().__init__()

    def ctx(self, interaction: discord.Interaction) -> Ctx:
        return Ctx(db_path=self.db_path, owner_id=interaction.user.id, guild_id=interaction.guild_id)

    # --- autocomplete --------------------------------------------------------
    async def _choices(self, interaction: discord.Interaction, current: str, archived: Optional[bool]):
        with service(self.ctx(interaction)) as svc:
            invs = svc.list(interaction.user.id, include_archived=archived is not False)
        if archived is True:
            invs = [i for i in invs if i.is_archived]
        elif archived is False:
            invs = [i for i in invs if not i.is_archived]
        cur = current.lower().strip()
        out: List[app_commands.Choice[str]] = []
        for inv in invs:
            if cur and cur not in inv.name.lower():
                continue
            label = f"{inv.name}" + (f" — {inv.occupation}" if inv.occupation else "") + f" (#{inv.id})"
            out.append(app_commands.Choice(name=label[:100], value=str(inv.id)))
        return out[:25]

    async def live_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._choices(interaction, current, archived=False)

    async def archived_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._choices(interaction, current, archived=True)

    async def skill_autocomplete(self, interaction: discord.Interaction, current: str):
        with get_conn(self.db_path) as conn:
            return [app_commands.Choice(name=label[:100], value=key) for key, label in search_skills(conn, current)]

    def _resolve(self, interaction: discord.Interaction, ref: Optional[str], include_archived: bool = False):
        """Explicit reference, else the active investigator for this context."""
        with service(self.ctx(interaction)) as svc:
            if ref:
                return svc.find(interaction.user.id, ref, include_archived=include_archived)
            inv = svc.active(interaction.user.id, interaction.guild_id)
            if inv is None:
                raise CharacterError("You have no active investigator here. Pick one with `/character select`, "
                                     "or name one in this command.")
            return inv

    # --- commands ------------------------------------------------------------
    @app_commands.command(name="create", description="Create a new investigator (opens a form)")
    async def create(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BioModal(self.ctx(interaction), None, from_panel=False))

    @app_commands.command(name="list", description="List your investigators")
    @app_commands.describe(show_archived="Include archived investigators")
    async def list_(self, interaction: discord.Interaction, show_archived: bool = False) -> None:
        ctx = self.ctx(interaction)
        with service(ctx) as svc:
            all_invs = svc.list(ctx.owner_id, include_archived=True)
            active = svc.active(ctx.owner_id, ctx.guild_id)
        shown = all_invs if show_archived else [i for i in all_invs if not i.is_archived]
        archived = sum(1 for i in all_invs if i.is_archived)
        embed = build_list_embed(shown, active.id if active else None, 0 if show_archived else archived)
        live = [i for i in shown if not i.is_archived]
        await interaction.response.send_message(embed=embed, view=CollectionView(ctx, live), ephemeral=True)

    @app_commands.command(name="view", description="Show an investigator's sheet")
    @app_commands.describe(investigator=INVESTIGATOR_DESC, public="Post the sheet in the channel for everyone")
    async def view(self, interaction: discord.Interaction, investigator: Optional[str] = None,
                   public: bool = False) -> None:
        try:
            inv = self._resolve(interaction, investigator, include_archived=True)
            with service(self.ctx(interaction)) as svc:
                embed = sheet_embed(svc, inv, interaction.guild_id)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.send_message(embed=embed, ephemeral=not public)

    @app_commands.command(name="edit", description="Open the sheet editor for an investigator")
    @app_commands.describe(investigator=INVESTIGATOR_DESC)
    async def edit(self, interaction: discord.Interaction, investigator: Optional[str] = None) -> None:
        try:
            inv = self._resolve(interaction, investigator)
            embed, view = render_panel(self.ctx(interaction), inv.id)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="select", description="Choose which investigator your rolls use")
    @app_commands.describe(investigator=INVESTIGATOR_DESC,
                           everywhere="Use as your default in every server and DMs (otherwise: this server only)")
    async def select(self, interaction: discord.Interaction, investigator: str, everywhere: bool = False) -> None:
        try:
            with service(self.ctx(interaction)) as svc:
                inv = svc.find(interaction.user.id, investigator)
                svc.select(interaction.user.id, inv.id, interaction.guild_id, everywhere=everywhere)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        scope = GLOBAL_SCOPE if everywhere or interaction.guild_id is None else guild_scope(interaction.guild_id)
        await interaction.response.send_message(
            f"⭐ **{inv.name}** is now your active investigator {scope_label(scope)}. "
            f"`/check` will use their values.", ephemeral=True)

    @app_commands.command(name="skill", description="Set a trained skill value on an investigator")
    @app_commands.describe(skill="Skill name (English or 中文)", value="Skill value (0-200)",
                           investigator=INVESTIGATOR_DESC + " — defaults to your active one")
    async def skill(self, interaction: discord.Interaction, skill: str, value: app_commands.Range[int, 0, 200],
                    investigator: Optional[str] = None) -> None:
        try:
            inv = self._resolve(interaction, investigator)
            with service(self.ctx(interaction)) as svc:
                inv = svc.set_skill(interaction.user.id, inv.id, skill, value)
                sd = resolve_skill(skill, conn=svc.conn)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.send_message(
            f"📚 **{inv.name}**: {sd.display_name if sd else skill} = **{value}**", ephemeral=True)

    @app_commands.command(name="archive", description="Archive an investigator (reversible)")
    @app_commands.describe(investigator=INVESTIGATOR_DESC)
    async def archive(self, interaction: discord.Interaction, investigator: str) -> None:
        try:
            with service(self.ctx(interaction)) as svc:
                inv = svc.archive(interaction.user.id, svc.find(interaction.user.id, investigator).id)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.send_message(
            f"🗄️ **{inv.name}** archived. Restore with `/character restore`.", ephemeral=True)

    @app_commands.command(name="restore", description="Restore an archived investigator")
    @app_commands.describe(investigator="An archived investigator")
    async def restore(self, interaction: discord.Interaction, investigator: str) -> None:
        try:
            with service(self.ctx(interaction)) as svc:
                inv = svc.restore(interaction.user.id,
                                  svc.find(interaction.user.id, investigator, include_archived=True).id)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.send_message(f"♻️ **{inv.name}** restored.", ephemeral=True)

    @app_commands.command(name="delete", description="Permanently delete an archived investigator")
    @app_commands.describe(investigator="An archived investigator")
    async def delete(self, interaction: discord.Interaction, investigator: str) -> None:
        ctx = self.ctx(interaction)
        try:
            with service(ctx) as svc:
                inv = svc.find(interaction.user.id, investigator, include_archived=True)
                if not inv.is_archived:
                    raise CharacterError("Only archived investigators can be deleted. Archive it first.")
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.send_message(
            f"Permanently delete **{inv.name}** and all their skills? This cannot be undone.",
            view=ConfirmDeleteView(ctx, inv), ephemeral=True)

    # autocomplete wiring
    view.autocomplete("investigator")(live_autocomplete)
    edit.autocomplete("investigator")(live_autocomplete)
    select.autocomplete("investigator")(live_autocomplete)
    skill.autocomplete("investigator")(live_autocomplete)
    skill.autocomplete("skill")(skill_autocomplete)
    archive.autocomplete("investigator")(live_autocomplete)
    restore.autocomplete("investigator")(archived_autocomplete)
    delete.autocomplete("investigator")(archived_autocomplete)
