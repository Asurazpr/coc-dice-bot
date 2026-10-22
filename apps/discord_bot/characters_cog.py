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
    error_text,
    BioModal,
    ConfirmDeleteView,
    Ctx,
    render_collection,
    render_panel,
    reply_error,
    scope_label,
    service,
    sheet_embed,
)
from cocbot.characters.service import CharacterError, GLOBAL_SCOPE, guild_scope
from cocbot.db.connection import get_conn
from cocbot.db.repo_skill_defs import resolve_skill, search_skills
from cocbot.i18n import occupation_name, render

INVESTIGATOR_DESC = "Your investigator (name or pick from the list)"


class CharacterCog(commands.GroupCog, group_name="character", group_description="Manage your investigators"):
    def __init__(self, bot: commands.Bot, db_path: str) -> None:
        self.bot = bot
        self.db_path = db_path
        super().__init__()

    def ctx(self, interaction: discord.Interaction) -> Ctx:
        from cocbot.preferences import get_language
        with get_conn(self.db_path) as conn:
            lang = get_language(conn, interaction.user.id, getattr(interaction, "locale", None))
        return Ctx(db_path=self.db_path, owner_id=interaction.user.id, guild_id=interaction.guild_id, lang=lang,
                   channel_id=getattr(interaction, "channel_id", None))

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

    @app_commands.command(name="list", description="List your investigators and which one is active here")
    @app_commands.describe(show_archived="Include archived investigators")
    async def list_(self, interaction: discord.Interaction, show_archived: bool = False) -> None:
        embed, view = render_collection(self.ctx(interaction), show_archived)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="view", description="Show an investigator's sheet")
    @app_commands.describe(investigator=INVESTIGATOR_DESC, public="Post the sheet in the channel for everyone")
    async def view(self, interaction: discord.Interaction, investigator: Optional[str] = None,
                   public: bool = False) -> None:
        try:
            inv = self._resolve(interaction, investigator, include_archived=True)
            ctx = self.ctx(interaction)
            with service(ctx) as svc:
                embed = sheet_embed(svc, inv, interaction.guild_id, ctx.channel_id, ctx.lang)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx(interaction))
            return
        await interaction.response.send_message(embed=embed, ephemeral=not public)

    @app_commands.command(name="edit", description="Open the sheet editor for an investigator")
    @app_commands.describe(investigator=INVESTIGATOR_DESC)
    async def edit(self, interaction: discord.Interaction, investigator: Optional[str] = None) -> None:
        try:
            inv = self._resolve(interaction, investigator)
            embed, view = render_panel(self.ctx(interaction), inv.id)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx(interaction))
            return
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="select", description="Choose which investigator your rolls use")
    @app_commands.describe(
        investigator=INVESTIGATOR_DESC,
        everywhere="Set your default for all servers and DMs. A server where you chose someone else keeps that choice.",
    )
    async def select(self, interaction: discord.Interaction, investigator: str, everywhere: bool = False) -> None:
        try:
            with service(self.ctx(interaction)) as svc:
                inv = svc.find(interaction.user.id, investigator)
                svc.select(interaction.user.id, inv.id, interaction.guild_id, everywhere=everywhere)
                here, here_scope = svc.active_with_scope(interaction.user.id, interaction.guild_id)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx(interaction))
            return
        scope = GLOBAL_SCOPE if everywhere or interaction.guild_id is None else guild_scope(interaction.guild_id)
        msg = f"⭐ **{inv.name}** is now active {scope_label(scope)}."
        if here is not None and here.id != inv.id and here_scope and here_scope.startswith("guild:"):
            msg += (f"\nIn **this** server you previously chose **{here.name}**, and that still applies here. "
                    f"Use `/character unselect` to clear it so your default is used.")
        else:
            msg += " `/check` will use their values here."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="unselect", description="Clear your investigator choice for this server (or your default)")
    @app_commands.describe(everywhere="Clear your default for all servers instead of this server's choice")
    async def unselect(self, interaction: discord.Interaction, everywhere: bool = False) -> None:
        with service(self.ctx(interaction)) as svc:
            existed = svc.clear_selection(interaction.user.id, interaction.guild_id, everywhere=everywhere)
            now = svc.active(interaction.user.id, interaction.guild_id)
        what = "your default" if everywhere or interaction.guild_id is None else "this server's choice"
        if not existed:
            msg = f"There was no {what} to clear."
        else:
            msg = f"Cleared {what}."
        msg += (f" `/check` here now uses **{now.name}**." if now
                else " You have no active investigator here; `/check` uses base values.")
        await interaction.response.send_message(msg, ephemeral=True)

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
            await reply_error(interaction, e, self.ctx(interaction))
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
            await reply_error(interaction, e, self.ctx(interaction))
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
            await reply_error(interaction, e, self.ctx(interaction))
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
            await reply_error(interaction, e, self.ctx(interaction))
            return
        await interaction.response.send_message(
            f"Permanently delete **{inv.name}** and all their skills? This cannot be undone.",
            view=ConfirmDeleteView(ctx, inv), ephemeral=True)

    # --- occupation-based creation (Milestone 2) ------------------------------
    async def occupation_autocomplete(self, interaction: discord.Interaction, current: str):
        from cocbot.occupations import catalogue
        from cocbot.ui.creation_text import occupation_label
        from cocbot.preferences import get_language
        with get_conn(self.db_path) as conn:
            found = catalogue.search(conn, interaction.user.id, current, 25)
            lang = get_language(conn, interaction.user.id, getattr(interaction, "locale", None))
        return [app_commands.Choice(name=occupation_label(o, lang)[:100], value=str(o.id)) for o in found]

    @app_commands.command(name="creation", description="Open the occupation & skill-point creation steps")
    @app_commands.describe(investigator=INVESTIGATOR_DESC + " — defaults to your active one")
    async def creation(self, interaction: discord.Interaction, investigator: Optional[str] = None) -> None:
        from apps.discord_bot.creation_views import open_creation
        ctx = self.ctx(interaction)
        try:
            inv = self._resolve(interaction, investigator)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        await open_creation(interaction, ctx, inv.id, edit=False)

    @app_commands.command(name="occupation", description="Choose an investigator's occupation (searchable)")
    @app_commands.describe(occupation="Occupation (English or 中文)",
                           investigator=INVESTIGATOR_DESC + " — defaults to your active one")
    async def occupation(self, interaction: discord.Interaction, occupation: str,
                         investigator: Optional[str] = None) -> None:
        from apps.discord_bot.creation_views import creation, render_step
        from cocbot.characters.service import InvalidState
        from cocbot.occupations import catalogue
        ctx = self.ctx(interaction)
        try:
            inv = self._resolve(interaction, investigator)
            with creation(ctx) as (_, svc):
                try:
                    inv = svc.set_occupation(ctx.owner_id, inv.id, occupation)
                    occ = svc.occupation_of(inv)
                    content = render("occ_set", ctx.lang, name=occupation_name(occ, ctx.lang))
                    step, state = None, {}
                except InvalidState as e:
                    if getattr(e.args[0] if e.args else None, "code", None) != "confirm_occupation_change":
                        raise
                    target = catalogue.find(svc.conn, ctx.owner_id, occupation)
                    content = error_text(e, ctx).removeprefix("⚠️ ")
                    step, state = "occupation", {"pending": target.id}
            embed, view = render_step(ctx, inv.id, step, **state)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="allocate", description="Spend creation skill points on one skill")
    @app_commands.describe(skill="Skill (English or 中文)",
                           occupation_points="Occupation points on this skill (omit to keep, 0 to clear)",
                           interest_points="Personal interest points on this skill (omit to keep, 0 to clear)",
                           investigator=INVESTIGATOR_DESC + " — defaults to your active one")
    async def allocate(self, interaction: discord.Interaction, skill: str,
                       occupation_points: Optional[app_commands.Range[int, 0, 99]] = None,
                       interest_points: Optional[app_commands.Range[int, 0, 99]] = None,
                       investigator: Optional[str] = None) -> None:
        from apps.discord_bot.creation_views import creation
        from cocbot.i18n import skill_names
        from cocbot.ui.creation_text import budget_line
        ctx = self.ctx(interaction)
        if occupation_points is None and interest_points is None:
            await interaction.response.send_message("Give occupation_points and/or interest_points.", ephemeral=True)
            return
        try:
            inv = self._resolve(interaction, investigator)
            with creation(ctx) as (_, svc):
                inv = svc.allocate(ctx.owner_id, inv.id, skill, occupation_points, interest_points)
                sd = resolve_skill(skill, conn=svc.conn)
                check = svc.check_for(inv)
                names = skill_names(svc.conn, ctx.lang)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        value = inv.skills.get(sd.key) if sd else None
        shown = value if value is not None else render("value_base", ctx.lang)
        await interaction.response.send_message(
            render("alloc_reply", ctx.lang, inv=inv.name, skill=names.get(sd.key, skill) if sd else skill, value=shown)
            + "\n" + budget_line(check, ctx.lang), ephemeral=True)

    # autocomplete wiring
    creation.autocomplete("investigator")(live_autocomplete)
    occupation.autocomplete("investigator")(live_autocomplete)
    occupation.autocomplete("occupation")(occupation_autocomplete)
    allocate.autocomplete("investigator")(live_autocomplete)
    allocate.autocomplete("skill")(skill_autocomplete)
    view.autocomplete("investigator")(live_autocomplete)
    edit.autocomplete("investigator")(live_autocomplete)
    select.autocomplete("investigator")(live_autocomplete)
    skill.autocomplete("investigator")(live_autocomplete)
    skill.autocomplete("skill")(skill_autocomplete)
    archive.autocomplete("investigator")(live_autocomplete)
    restore.autocomplete("investigator")(archived_autocomplete)
    delete.autocomplete("investigator")(archived_autocomplete)
