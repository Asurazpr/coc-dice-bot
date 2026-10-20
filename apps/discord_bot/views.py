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
from cocbot.ui.sheet_embed import build_sheet_embed

log = logging.getLogger(__name__)

PANEL_TIMEOUT = 15 * 60  # ephemeral interaction tokens live 15 minutes


@dataclass(frozen=True)
class Ctx:
    db_path: str
    owner_id: int
    guild_id: Optional[int]


@contextmanager
def service(ctx: Ctx) -> Iterator[InvestigatorService]:
    with get_conn(ctx.db_path) as conn:
        yield InvestigatorService(conn)


def sheet_embed(svc: InvestigatorService, inv: Investigator, guild_id: Optional[int]) -> discord.Embed:
    names = skill_display_names(svc.conn, inv.skills.keys())
    active = svc.active(inv.owner_user_id, guild_id)
    return build_sheet_embed(inv, names, active_here=bool(active and active.id == inv.id))


def render_panel(ctx: Ctx, investigator_id: int) -> Tuple[discord.Embed, "EditorView"]:
    with service(ctx) as svc:
        inv = svc.get(ctx.owner_id, investigator_id)
        embed = sheet_embed(svc, inv, ctx.guild_id)
    return embed, EditorView(ctx, inv)


async def reply_error(interaction: discord.Interaction, err: Exception) -> None:
    msg = f"⚠️ {err}" if isinstance(err, CharacterError) else "⚠️ Something went wrong. Please try again."
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
        await reply_error(interaction, error)

    async def apply(self, svc: InvestigatorService) -> Investigator:  # pragma: no cover - overridden
        raise NotImplementedError

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            with service(self.ctx) as svc:
                inv = await self.apply(svc)
        except CharacterError as e:
            await reply_error(interaction, e)
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
        return (f"✅ Created **{inv.name}**. Next: enter characteristics (or roll them), "
                f"add skills, then press **Finish**.")


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
        await reply_error(interaction, error)


class EditorView(OwnedView):
    """The investigator sheet editor: one button per step of the sheet."""

    def __init__(self, ctx: Ctx, inv: Investigator, confirm: Optional[str] = None):
        super().__init__(ctx)
        self.inv = inv
        self.confirm = confirm  # "roll" | "archive" when awaiting a second click
        draft = inv.status == STATUS_DRAFT
        if inv.is_archived:
            for child in list(self.children):
                self.remove_item(child)
            return
        if not draft:
            self.remove_item(self.roll_button)
            self.remove_item(self.finish_button)
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
                embed=embed, view=EditorView(self.ctx, inv, confirm="roll"))
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
        where = "in this server" if self.ctx.guild_id else "everywhere"
        await refresh_panel(interaction, self.ctx, inv.id, content=f"⭐ **{inv.name}** is now active {where}.")

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.success, emoji="✅", row=2)
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        with service(self.ctx) as svc:
            inv = svc.finalize(self.ctx.owner_id, self.inv.id)
        await refresh_panel(interaction, self.ctx, inv.id, content=f"✅ **{inv.name}** is ready to play.")

    @discord.ui.button(label="Archive", style=discord.ButtonStyle.secondary, emoji="🗄️", row=2)
    async def archive_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.confirm != "archive":
            embed, _ = render_panel(self.ctx, self.inv.id)
            await interaction.response.edit_message(
                content="Archiving hides this investigator and clears it as active. "
                        "You can restore it later with `/character restore`. Click again to confirm.",
                embed=embed, view=EditorView(self.ctx, self._fresh(), confirm="archive"))
            return
        with service(self.ctx) as svc:
            inv = svc.archive(self.ctx.owner_id, self.inv.id)
        await refresh_panel(interaction, self.ctx, inv.id, content=f"🗄️ **{inv.name}** archived.")

    def _fresh(self) -> Investigator:
        with service(self.ctx) as svc:
            return svc.get(self.ctx.owner_id, self.inv.id, include_archived=False)


class CollectionView(OwnedView):
    """`/character list`: a select menu that opens an investigator's editor."""

    def __init__(self, ctx: Ctx, invs: list):
        super().__init__(ctx)
        options = [
            discord.SelectOption(
                label=inv.name[:100],
                value=str(inv.id),
                description=(f"{inv.occupation or 'No occupation'} · {inv.status}")[:100],
            )
            for inv in invs[:25]
        ]
        if options:
            select = discord.ui.Select(placeholder="Open an investigator…", options=options)
            select.callback = self._on_select  # type: ignore[assignment]
            self.select = select
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        investigator_id = int(self.select.values[0])
        try:
            embed, view = render_panel(self.ctx, investigator_id)
        except CharacterError as e:
            await reply_error(interaction, e)
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
    return "everywhere (default)" if scope == GLOBAL_SCOPE else "this server" if scope.startswith("guild:") else scope


__all__ = [
    "Ctx", "BioModal", "EditorView", "CollectionView", "ConfirmDeleteView",
    "render_panel", "reply_error", "service", "sheet_embed", "guild_scope", "scope_label",
]
