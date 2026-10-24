"""Discord UI components for investigator management.

All game/ownership logic lives in ``cocbot.characters.service``; these classes
only collect input, call the service with the *interacting* user's ID, and
render the result. Every component re-checks that the clicking user is the
owner, even though panels are sent ephemerally.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

import discord

from cocbot.characters.models import STATUS_DRAFT, Investigator
from cocbot.characters.service import CharacterError, InvestigatorService, guild_scope, GLOBAL_SCOPE
from cocbot.db.connection import get_conn
from cocbot.db.repo_skill_defs import skill_display_names
from cocbot.mechanics.generation import ROLL_FORMULAS
from cocbot.ui.sheet_embed import build_list_embed, build_sheet_embed
from cocbot.ui.sheet_text import build_list_page, selection_summary

log = logging.getLogger(__name__)

PANEL_TIMEOUT = 15 * 60  # ephemeral interaction tokens live 15 minutes

# "Finish basic sheet" (no occupation chosen) only certifies the characteristics.
FINISHED_MESSAGE = (
    "✅ **{name}**'s basic sheet is complete: all characteristics and Luck are filled in, "
    "so the bot can use it for checks. No occupation was chosen, so occupation and skill-point "
    "rules were **not** checked (use **Occupation & skills** on a draft for that). "
    "Age adjustments are not automated; confirm them with your Keeper."
)


@dataclass(frozen=True)
class Ctx:
    db_path: str
    owner_id: int
    guild_id: Optional[int]
    lang: str = "en"          # display language: 'en' | 'zh' | 'both'
    channel_id: Optional[int] = None   # campaign channels show campaign values


@contextmanager
def service(ctx: Ctx) -> Iterator[InvestigatorService]:
    with get_conn(ctx.db_path) as conn:
        yield InvestigatorService(conn)


def campaign_context_for(svc: InvestigatorService, inv: Investigator, channel_id: Optional[int]):
    """The campaign context when ``inv`` is its owner's investigator in this channel's campaign."""
    from cocbot.campaigns.service import CampaignService
    cctx = CampaignService(svc.conn).context(inv.owner_user_id, channel_id)
    return cctx if cctx is not None and cctx.investigator.id == inv.id else None


def sheet_embed(svc: InvestigatorService, inv: Investigator, guild_id: Optional[int],
                channel_id: Optional[int] = None, lang: str = "en") -> discord.Embed:
    """Personal sheet, or — in a campaign channel the investigator plays in — the campaign view."""
    from cocbot.campaigns.service import CampaignService
    from cocbot.ui.keeper_text import campaign_sheet_note
    cctx = campaign_context_for(svc, inv, channel_id)
    shown = cctx.investigator if cctx is not None else inv
    names = skill_display_names(svc.conn, shown.skills.keys())
    active = svc.active(inv.owner_user_id, guild_id)
    embed = build_sheet_embed(shown, names, active_here=bool(active and active.id == inv.id))
    from cocbot.imports.service import ImportService
    from cocbot.ui.import_text import record_lines
    record = ImportService(svc.conn)._latest(inv.id)      # inv was loaded for its owner
    if record is not None:
        embed.add_field(name="📥", value="\n".join(record_lines(record, lang))[:1024], inline=False)
    if cctx is not None:
        conditions = CampaignService(svc.conn).conditions(cctx.campaign.id, inv.id)
        title, text = campaign_sheet_note(cctx.campaign.name, [c.label for c in conditions], lang)
        embed.add_field(name=title, value=text, inline=False)
    return embed


def render_panel(ctx: Ctx, investigator_id: int) -> Tuple[discord.Embed, "EditorView"]:
    with service(ctx) as svc:
        inv = svc.get(ctx.owner_id, investigator_id)
        embed = sheet_embed(svc, inv, ctx.guild_id, ctx.channel_id, ctx.lang)
        in_campaign = campaign_context_for(svc, inv, ctx.channel_id) is not None
    return embed, EditorView(ctx, inv, in_campaign=in_campaign)


def error_text(err: Exception, ctx: Optional[Ctx] = None) -> str:
    """User-facing text for an error, localized when it carries a Msg and the language is known."""
    from cocbot.i18n import Msg, localize, localized_names
    if not isinstance(err, CharacterError):
        return "⚠️ Something went wrong. Please try again."
    payload = err.args[0] if err.args else str(err)
    if ctx is not None and ctx.lang != "en" and isinstance(payload, Msg):
        with get_conn(ctx.db_path) as conn:
            return f"⚠️ {localize(payload, ctx.lang, localized_names(conn))}"
    return f"⚠️ {err}"


async def reply_error(interaction: discord.Interaction, err: Exception, ctx: Optional[Ctx] = None) -> None:
    msg = error_text(err, ctx)
    if not isinstance(err, CharacterError):
        log.exception("Unhandled error in character UI", exc_info=err)
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


async def refresh_panel(interaction: discord.Interaction, ctx: Ctx, investigator_id: int,
                        content: Optional[str] = None) -> None:
    embed, view = render_panel(ctx, investigator_id)
    await interaction.response.edit_message(content=content, embed=embed, view=view)


# --- modals -------------------------------------------------------------------

def _text(label: str, default: Optional[object] = None, *, required: bool = False, max_length: int = 100,
          placeholder: Optional[str] = None, style: discord.TextStyle = discord.TextStyle.short) -> discord.ui.TextInput:
    return discord.ui.TextInput(
        label=label,
        default=None if default is None else str(default),
        required=required,
        max_length=max_length,
        placeholder=placeholder,
        style=style,
    )


class OwnedModal(discord.ui.Modal):
    def __init__(self, ctx: Ctx, title: str, investigator_id: Optional[int] = None, from_panel: bool = True):
        super().__init__(title=title, timeout=PANEL_TIMEOUT)
        self.ctx = ctx
        self.investigator_id = investigator_id
        self.from_panel = from_panel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.owner_id

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.ctx)

    async def apply(self, svc: InvestigatorService) -> Investigator:  # pragma: no cover - overridden
        raise NotImplementedError

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            with service(self.ctx) as svc:
                inv = await self.apply(svc)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        if self.from_panel:
            await refresh_panel(interaction, self.ctx, inv.id)
        else:
            embed, view = render_panel(self.ctx, inv.id)
            await interaction.response.send_message(
                content=self.created_message(inv), embed=embed, view=view, ephemeral=True
            )

    def created_message(self, inv: Investigator) -> Optional[str]:
        return None


class BioModal(OwnedModal):
    """Create a new investigator (investigator_id=None) or edit biography."""

    def __init__(self, ctx: Ctx, inv: Optional[Investigator] = None, from_panel: bool = True):
        super().__init__(ctx, "New investigator" if inv is None else f"Biography — {inv.name}"[:45],
                         None if inv is None else inv.id, from_panel)
        self.name_input = _text("Name", inv.name if inv else None, required=True, max_length=64)
        self.occupation_input = _text("Occupation", inv.occupation if inv else None, placeholder="e.g. Antiquarian")
        self.age_input = _text("Age", inv.age if inv else None, max_length=3, placeholder="15-89 is typical")
        self.residence_input = _text("Residence", inv.residence if inv else None)
        self.birthplace_input = _text("Birthplace", inv.birthplace if inv else None)
        for item in (self.name_input, self.occupation_input, self.age_input, self.residence_input, self.birthplace_input):
            self.add_item(item)

    async def apply(self, svc: InvestigatorService) -> Investigator:
        bio = dict(occupation=self.occupation_input.value, age=self.age_input.value,
                   residence=self.residence_input.value, birthplace=self.birthplace_input.value)
        if self.investigator_id is None:
            inv = svc.create(self.ctx.owner_id, self.name_input.value, **bio)
            # First investigator in this context becomes active automatically.
            if svc.active(self.ctx.owner_id, self.ctx.guild_id) is None:
                svc.select(self.ctx.owner_id, inv.id, self.ctx.guild_id)
            return inv
        return svc.update_bio(self.ctx.owner_id, self.investigator_id, name=self.name_input.value, **bio)

    def created_message(self, inv: Investigator) -> Optional[str]:
        return (f"✅ Created **{inv.name}** as a draft. Next: enter or roll characteristics, then "
                f"**Occupation & skills** to choose an occupation and spend skill points. "
                f"Everything saves as you go, so you can stop and continue later.")


class CharacteristicsModal(OwnedModal):
    def __init__(self, ctx: Ctx, inv: Investigator, keys: Tuple[str, ...]):
        super().__init__(ctx, f"Characteristics — {inv.name}"[:45], inv.id)
        self.inputs = {}
        for key in keys:
            current = inv.luck if key == "LUCK" else inv.characteristics.get(key)
            label = f"{'Luck' if key == 'LUCK' else key} (RAW roll: {ROLL_FORMULAS[key]})"
            ti = _text(label, current, max_length=3, placeholder="0-99")
            self.inputs[key] = ti
            self.add_item(ti)

    async def apply(self, svc: InvestigatorService) -> Investigator:
        return svc.set_characteristics(
            self.ctx.owner_id, self.investigator_id, {k: ti.value for k, ti in self.inputs.items()}
        )


class SkillModal(OwnedModal):
    def __init__(self, ctx: Ctx, inv: Investigator):
        super().__init__(ctx, f"Skill — {inv.name}"[:45], inv.id)
        self.skill_input = _text("Skill (English or 中文)", required=True, max_length=60, placeholder="Spot Hidden / 侦查")
        self.value_input = _text("Value (blank = untrained, use base)", max_length=3, placeholder="0-200")
        self.add_item(self.skill_input)
        self.add_item(self.value_input)

    async def apply(self, svc: InvestigatorService) -> Investigator:
        return svc.set_skill(self.ctx.owner_id, self.investigator_id, self.skill_input.value, self.value_input.value)


class ResourcesModal(OwnedModal):
    def __init__(self, ctx: Ctx, inv: Investigator):
        super().__init__(ctx, f"Current values — {inv.name}"[:45], inv.id)
        d = inv.derived()
        hp_label = f"HP (max {d.hp_max}; blank = full)" if d.hp_max is not None else "HP (set CON and SIZ first)"
        mp_label = f"MP (max {d.mp_max}; blank = full)" if d.mp_max is not None else "MP (set POW first)"
        self.hp_input = _text(hp_label, inv.hp_current, max_length=3)
        self.mp_input = _text(mp_label, inv.mp_current, max_length=3)
        self.san_input = _text(f"SAN (max {d.san_max}; blank = starting POW)", inv.san_current, max_length=3)
        self.luck_input = _text("Luck", inv.luck, required=True, max_length=3)
        for item in (self.hp_input, self.mp_input, self.san_input, self.luck_input):
            self.add_item(item)

    async def apply(self, svc: InvestigatorService) -> Investigator:
        return svc.set_resources(self.ctx.owner_id, self.investigator_id, hp=self.hp_input.value,
                                 mp=self.mp_input.value, san=self.san_input.value, luck=self.luck_input.value)


# --- views --------------------------------------------------------------------

class OwnedView(discord.ui.View):
    def __init__(self, ctx: Ctx, timeout: float = PANEL_TIMEOUT):
        super().__init__(timeout=timeout)
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.owner_id:
            await interaction.response.send_message("This panel belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.ctx)


class EditorView(OwnedView):
    """The investigator sheet editor: one button per step of the sheet."""

    def __init__(self, ctx: Ctx, inv: Investigator, confirm: Optional[str] = None, in_campaign: bool = False):
        super().__init__(ctx)
        self.inv = inv
        self.in_campaign = in_campaign
        if in_campaign:
            # Campaign HP/MP/SAN/Luck belong to the Keeper; personal values are edited outside the campaign.
            self.resources_button.disabled = True
            self.resources_button.label = "HP·MP·SAN·Luck (Keeper-managed here)"
        self.confirm = confirm  # "roll" | "archive" when awaiting a second click
        draft = inv.status == STATUS_DRAFT
        if inv.is_archived:
            for child in list(self.children):
                self.remove_item(child)
            return
        if not draft:
            self.remove_item(self.roll_button)
            self.remove_item(self.finish_button)
            self.remove_item(self.creation_button)
        elif inv.occupation_id:
            # Occupation-based creation: finishing happens on the Review step (with its
            # reconcile and unspent-points confirmations); skills come from points only.
            self.finish_button.label = "Review & finish"
            self.skill_button.disabled = True
        if confirm == "roll":
            self.roll_button.label = "Confirm: overwrite with new rolls"
            self.roll_button.style = discord.ButtonStyle.danger
        if confirm == "archive":
            self.archive_button.label = "Confirm archive"
            self.archive_button.style = discord.ButtonStyle.danger
        if inv.is_complete is False:
            self.finish_button.disabled = True

    @discord.ui.button(label="Biography", style=discord.ButtonStyle.secondary, emoji="🪪", row=0)
    async def bio_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(BioModal(self.ctx, self._fresh()))

    @discord.ui.button(label="STR·CON·SIZ·DEX·APP", style=discord.ButtonStyle.primary, row=0)
    async def chars1_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CharacteristicsModal(self.ctx, self._fresh(), ("STR", "CON", "SIZ", "DEX", "APP")))

    @discord.ui.button(label="INT·POW·EDU·Luck", style=discord.ButtonStyle.primary, row=0)
    async def chars2_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CharacteristicsModal(self.ctx, self._fresh(), ("INT", "POW", "EDU", "LUCK")))

    @discord.ui.button(label="Roll characteristics", style=discord.ButtonStyle.secondary, emoji="🎲", row=0)
    async def roll_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        inv = self._fresh()
        has_values = any(v is not None for v in inv.characteristics.values()) or inv.luck is not None
        if has_values and self.confirm != "roll":
            embed, _ = render_panel(self.ctx, inv.id)
            await interaction.response.edit_message(
                content="This replaces every characteristic and Luck. Click again to confirm.",
                embed=embed, view=EditorView(self.ctx, inv, confirm="roll", in_campaign=self.in_campaign))
            return
        with service(self.ctx) as svc:
            inv = svc.roll_characteristics(self.ctx.owner_id, inv.id)
        await refresh_panel(interaction, self.ctx, inv.id,
                            content="🎲 Rolled with the standard method: 3D6×5 (STR, CON, DEX, APP, POW, Luck), "
                                    "(2D6+6)×5 (SIZ, INT, EDU).")

    @discord.ui.button(label="Skill", style=discord.ButtonStyle.primary, emoji="📚", row=1)
    async def skill_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SkillModal(self.ctx, self._fresh()))

    @discord.ui.button(label="HP·MP·SAN·Luck", style=discord.ButtonStyle.secondary, emoji="❤️", row=1)
    async def resources_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ResourcesModal(self.ctx, self._fresh()))

    @discord.ui.button(label="Make active here", style=discord.ButtonStyle.success, emoji="⭐", row=1)
    async def select_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        with service(self.ctx) as svc:
            inv = svc.select(self.ctx.owner_id, self.inv.id, self.ctx.guild_id)
        where = ("in this server (overrides your default here)" if self.ctx.guild_id
                 else "as your default everywhere (servers with their own choice keep it)")
        await refresh_panel(interaction, self.ctx, inv.id, content=f"⭐ **{inv.name}** is now active {where}.")

    @discord.ui.button(label="Occupation & skills", style=discord.ButtonStyle.primary, emoji="🧭", row=2)
    async def creation_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from apps.discord_bot.creation_views import open_creation
        await open_creation(interaction, self.ctx, self.inv.id)

    @discord.ui.button(label="Finish basic sheet", style=discord.ButtonStyle.success, emoji="✅", row=2)
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.inv.occupation_id:
            from apps.discord_bot.creation_views import open_creation
            await open_creation(interaction, self.ctx, self.inv.id, "review")
            return
        with service(self.ctx) as svc:
            inv = svc.finalize(self.ctx.owner_id, self.inv.id)
        if inv.creation_validated:
            from apps.discord_bot.creation_views import CREATION_FINISHED_MESSAGE
            message = CREATION_FINISHED_MESSAGE.format(name=inv.name)
        else:
            message = FINISHED_MESSAGE.format(name=inv.name)
        await refresh_panel(interaction, self.ctx, inv.id, content=message)


    @discord.ui.button(label="Archive", style=discord.ButtonStyle.secondary, emoji="🗄️", row=2)
    async def archive_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.confirm != "archive":
            embed, _ = render_panel(self.ctx, self.inv.id)
            await interaction.response.edit_message(
                content="Archiving hides this investigator and clears it as active. "
                        "You can restore it later with `/character restore`. Click again to confirm.",
                embed=embed, view=EditorView(self.ctx, self._fresh(), confirm="archive",
                                              in_campaign=self.in_campaign))
            return
        with service(self.ctx) as svc:
            inv = svc.archive(self.ctx.owner_id, self.inv.id)
        await refresh_panel(interaction, self.ctx, inv.id, content=f"🗄️ **{inv.name}** archived.")

    def _fresh(self) -> Investigator:
        with service(self.ctx) as svc:
            return svc.get(self.ctx.owner_id, self.inv.id, include_archived=False)


def render_collection(ctx: Ctx, show_archived: bool = False, page: int = 0) -> Tuple[discord.Embed, "CollectionView"]:
    """`/character list` page: who is active here and why, plus a bounded page of investigators."""
    with service(ctx) as svc:
        all_invs = svc.list(ctx.owner_id, include_archived=True)
        active = svc.active(ctx.owner_id, ctx.guild_id)
        sel = svc.selections(ctx.owner_id, ctx.guild_id)
    shown = all_invs if show_archived else [i for i in all_invs if not i.is_archived]
    hidden = 0 if show_archived else sum(1 for i in all_invs if i.is_archived)
    summary = selection_summary(sel.get("server"), sel.get("default"), in_guild=ctx.guild_id is not None)
    list_page = build_list_page(shown, active.id if active else None, page, summary)
    live = [i for i in all_invs if not i.is_archived]  # at most MAX_LIVE_INVESTIGATORS (25)
    view = CollectionView(ctx, live, show_archived, list_page.page, list_page.pages)
    return build_list_embed(list_page, hidden), view


class CollectionView(OwnedView):
    """`/character list`: page buttons plus a select menu that opens an editor."""

    def __init__(self, ctx: Ctx, live: list, show_archived: bool = False, page: int = 0, pages: int = 1):
        super().__init__(ctx)
        self.show_archived = show_archived
        self.page = page
        options = [
            discord.SelectOption(
                label=inv.name[:100],
                value=str(inv.id),
                description=(f"{inv.occupation or 'No occupation'} · {inv.status}")[:100],
            )
            for inv in live[:25]
        ]
        if options:
            select = discord.ui.Select(placeholder="Open an investigator…", options=options, row=0)
            select.callback = self._on_select  # type: ignore[assignment]
            self.select = select
            self.add_item(select)
        if pages > 1:
            prev_b = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary,
                                       disabled=page <= 0, row=1)
            next_b = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary,
                                       disabled=page >= pages - 1, row=1)
            prev_b.callback = self._prev  # type: ignore[assignment]
            next_b.callback = self._next  # type: ignore[assignment]
            self.add_item(prev_b)
            self.add_item(next_b)

    async def _go(self, interaction: discord.Interaction, page: int) -> None:
        embed, view = render_collection(self.ctx, self.show_archived, page)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _prev(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, self.page - 1)

    async def _next(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, self.page + 1)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        investigator_id = int(self.select.values[0])
        try:
            embed, view = render_panel(self.ctx, investigator_id)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class ConfirmDeleteView(OwnedView):
    def __init__(self, ctx: Ctx, inv: Investigator):
        super().__init__(ctx, timeout=120)
        self.inv = inv

    @discord.ui.button(label="Delete permanently", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        with service(self.ctx) as svc:
            svc.delete(self.ctx.owner_id, self.inv.id)
        await interaction.response.edit_message(content=f"🗑️ **{self.inv.name}** was deleted.", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


def scope_label(scope: str) -> str:
    if scope == GLOBAL_SCOPE:
        return "as your default everywhere (servers where you chose someone else keep that choice)"
    if scope.startswith("guild:"):
        return "in this server (overrides your default here)"
    return scope


__all__ = [
    "Ctx", "BioModal", "EditorView", "CollectionView", "ConfirmDeleteView", "render_collection",
    "render_panel", "reply_error", "service", "sheet_embed", "guild_scope", "scope_label",
]
