"""Discord UI for occupation-based creation.

Steps: Occupation → Occupation skill choices → Skill points → Review.
Each step is one ephemeral message that edits itself in place; every action
is saved immediately, so a player can stop and resume later from the sheet
editor ("Occupation & skills"). All rules and ownership checks live in
``cocbot.occupations.service``; these views only gather input and render.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List, Optional, Tuple

import discord

from apps.discord_bot.views import (
    PANEL_TIMEOUT,
    Ctx,
    OwnedView,
    _text,
    reply_error,
)
from cocbot.characters.models import STATUS_DRAFT, Investigator
from cocbot.characters.service import CharacterError, InvalidState, InvestigatorService
from cocbot.db.connection import get_conn
from cocbot.occupations import catalogue
from cocbot.occupations.rules import pick_positions
from cocbot.occupations.service import CreationService, load_picks
from cocbot.ui.creation_text import (
    STEP_TITLES,
    allocation_pages,
    budget_line,
    choices_text,
    occupation_detail,
    occupation_label,
    review_text,
    rows_table,
)

OPTIONS_PER_PAGE = 25          # Discord select-menu limit
STEPS = ("occupation", "choices", "allocation", "review")
STEP_BUTTONS = {"occupation": "Occupation", "choices": "Choices", "allocation": "Points", "review": "Review"}
CREATION_FINISHED_MESSAGE = (
    "✅ **{name}** is created: occupation, skill choices and skill points meet the rules the bot checks. "
    "Age adjustments are not automated yet; confirm them with your Keeper."
)


@contextmanager
def creation(ctx: Ctx) -> Iterator[Tuple[InvestigatorService, CreationService]]:
    with get_conn(ctx.db_path) as conn:
        svc = CreationService(conn)
        yield svc.investigators, svc


def _all_names(conn) -> dict:
    return dict(conn.execute(
        "SELECT sd.key, COALESCE(i.name, sd.key) FROM skill_defs sd "
        "LEFT JOIN skill_def_i18n i ON i.skill_id = sd.skill_id AND i.lang = 'en'").fetchall())


def render_step(ctx: Ctx, investigator_id: int, step: Optional[str] = None, **state) -> Tuple[discord.Embed,
                                                                                              discord.ui.View]:
    """Embed + view for a creation step (default: where the player left off)."""
    with creation(ctx) as (inv_svc, svc):
        inv = inv_svc.get(ctx.owner_id, investigator_id, include_archived=False)
        if inv.status != STATUS_DRAFT:
            raise InvalidState("Creation steps are only available while the investigator is a draft.")
        if step is None:
            step = svc.next_step(inv)
        if step == "characteristics":
            raise InvalidState("Enter or roll all characteristics and Luck first (sheet editor buttons).")
        occ = svc.occupation_of(inv)
        if occ is None and step != "occupation":
            step = "occupation"
        names = _all_names(svc.conn)
        builder = {"occupation": _occupation_step, "choices": _choices_step,
                   "allocation": _allocation_step, "review": _review_step}[step]
        return builder(ctx, svc, inv, names, **state)


def _base_embed(inv: Investigator, step: str) -> discord.Embed:
    e = discord.Embed(title=f"{inv.name} — {STEP_TITLES[step]}", color=discord.Color.dark_teal())
    e.set_footer(text="Changes save immediately · resume any time from the sheet editor")
    return e


# --- step: occupation ----------------------------------------------------------------

def _occupation_step(ctx: Ctx, svc: CreationService, inv: Investigator, names: dict, page: int = 0,
                     query: str = "", pending: Optional[int] = None) -> Tuple[discord.Embed, discord.ui.View]:
    e = _base_embed(inv, "occupation")
    occ = svc.occupation_of(inv)
    total = catalogue.count_visible(svc.conn, ctx.owner_id, query)
    pages = max(1, -(-total // OPTIONS_PER_PAGE))
    page = min(max(0, page), pages - 1)
    found = catalogue.search(svc.conn, ctx.owner_id, query, OPTIONS_PER_PAGE, page * OPTIONS_PER_PAGE)
    if occ:
        e.description = f"Current occupation: **{occupation_label(occ)}**"
        for name, value in occupation_detail(occ, names):
            e.add_field(name=name, value=value, inline=False)
    else:
        e.description = "Pick an occupation from the menu, search by English or Chinese name, or define a custom one."
    if query:
        e.description += f"\n🔎 Search: `{query}` — {total} match(es)"
    if pending:
        target = catalogue.get(svc.conn, pending)
        e.add_field(name="Confirm change", value=(
            f"Switch to **{target.display_name if target else '?'}**? This clears your occupation skill choices and "
            f"occupation points. Personal interest points are kept."), inline=False)
    return e, OccupationStepView(ctx, inv.id, found, page, pages, query, pending)


class StepView(OwnedView):
    def __init__(self, ctx: Ctx, investigator_id: int, step: str):
        super().__init__(ctx, timeout=PANEL_TIMEOUT)
        self.investigator_id = investigator_id
        for s in STEPS:
            b = discord.ui.Button(label=STEP_BUTTONS[s], row=4,
                                  style=discord.ButtonStyle.primary if s == step else discord.ButtonStyle.secondary,
                                  disabled=s == step)
            b.callback = self._goto(s)  # type: ignore[assignment]
            self.add_item(b)
        sheet = discord.ui.Button(label="Sheet", emoji="📄", row=4, style=discord.ButtonStyle.secondary)
        sheet.callback = self._to_sheet  # type: ignore[assignment]
        self.add_item(sheet)

    def _goto(self, step: str):
        async def cb(interaction: discord.Interaction) -> None:
            await self.show(interaction, step)
        return cb

    async def show(self, interaction: discord.Interaction, step: Optional[str] = None, content: Optional[str] = None,
                   **state) -> None:
        try:
            embed, view = render_step(self.ctx, self.investigator_id, step, **state)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await interaction.response.edit_message(content=content, embed=embed, view=view)

    async def _to_sheet(self, interaction: discord.Interaction) -> None:
        from apps.discord_bot.views import refresh_panel
        await refresh_panel(interaction, self.ctx, self.investigator_id)


class OccupationStepView(StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, found: List[catalogue.Occupation], page: int, pages: int,
                 query: str, pending: Optional[int]):
        super().__init__(ctx, investigator_id, "occupation")
        self.page, self.pages, self.query, self.pending = page, pages, query, pending
        if found:
            sel = discord.ui.Select(
                placeholder=f"Choose an occupation… (page {page + 1}/{pages})", row=0,
                options=[discord.SelectOption(label=occupation_label(o)[:100], value=str(o.id),
                                              description=f"Credit {o.credit_min}-{o.credit_max} · {o.formula_text()}"[:100])
                         for o in found])
            sel.callback = self._on_pick  # type: ignore[assignment]
            self.sel = sel
            self.add_item(sel)
        for label, delta in (("◀", -1), ("▶", 1)):
            b = discord.ui.Button(label=label, row=1, style=discord.ButtonStyle.secondary,
                                  disabled=not (0 <= page + delta < pages))
            b.callback = self._page(delta)  # type: ignore[assignment]
            self.add_item(b)
        search = discord.ui.Button(label="Search", emoji="🔎", row=1, style=discord.ButtonStyle.secondary)
        search.callback = self._search  # type: ignore[assignment]
        self.add_item(search)
        custom = discord.ui.Button(label="Custom occupation", emoji="✏️", row=1, style=discord.ButtonStyle.secondary)
        custom.callback = self._custom  # type: ignore[assignment]
        self.add_item(custom)
        if pending:
            ok = discord.ui.Button(label="Confirm change", row=2, style=discord.ButtonStyle.danger)
            ok.callback = self._confirm  # type: ignore[assignment]
            cancel = discord.ui.Button(label="Cancel", row=2, style=discord.ButtonStyle.secondary)
            cancel.callback = self._page(0)  # type: ignore[assignment]
            self.add_item(ok)
            self.add_item(cancel)

    def _page(self, delta: int):
        async def cb(interaction: discord.Interaction) -> None:
            await self.show(interaction, "occupation", page=self.page + delta, query=self.query)
        return cb

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        await self.choose(interaction, int(self.sel.values[0]), force=False)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await self.choose(interaction, int(self.pending), force=True)  # type: ignore[arg-type]

    async def choose(self, interaction: discord.Interaction, occupation_id: int, force: bool) -> None:
        try:
            with creation(self.ctx) as (_, svc):
                inv = svc.set_occupation(self.ctx.owner_id, self.investigator_id, occupation_id, force=force)
                next_step = svc.next_step(inv)
        except InvalidState as e:
            if "Confirm" in str(e):
                await self.show(interaction, "occupation", page=self.page, query=self.query, pending=occupation_id)
                return
            await reply_error(interaction, e)
            return
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await self.show(interaction, next_step, content=f"Occupation set to **{inv.occupation}**.")

    async def _search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(OccupationSearchModal(self))

    async def _custom(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(HomebrewModal(self))


class OccupationSearchModal(discord.ui.Modal):
    def __init__(self, parent: OccupationStepView):
        super().__init__(title="Search occupations", timeout=PANEL_TIMEOUT)
        self.parent = parent
        self.query_input = _text("English or Chinese name (blank = all)", parent.query, max_length=40,
                                 placeholder="e.g. doctor / 医生")
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent.show(interaction, "occupation", page=0, query=self.query_input.value.strip())

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error)


class HomebrewModal(discord.ui.Modal):
    def __init__(self, parent: OccupationStepView):
        super().__init__(title="Custom occupation (agree with Keeper)", timeout=PANEL_TIMEOUT)
        self.parent = parent
        self.name_input = _text("Occupation name", required=True, max_length=64)
        self.credit_input = _text("Credit Rating range", required=True, max_length=7, placeholder="10-40")
        self.formula_input = _text("Skill points formula", "EDU*4", required=True, max_length=20,
                                   placeholder="EDU*4 or EDU*2+DEX*2")
        for item in (self.name_input, self.credit_input, self.formula_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            with creation(self.parent.ctx) as (_, svc):
                inv = svc.create_homebrew(self.parent.ctx.owner_id, self.parent.investigator_id,
                                          self.name_input.value, self.credit_input.value, self.formula_input.value)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await self.parent.show(interaction, "choices",
                               content=f"Custom occupation **{inv.occupation}** created. Now choose its 8 skills.")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error)


# --- step: choices ----------------------------------------------------------------------

def _choices_step(ctx: Ctx, svc: CreationService, inv: Investigator, names: dict, pos: Optional[int] = None,
                  page: int = 0) -> Tuple[discord.Embed, discord.ui.View]:
    occ = svc.occupation_of(inv)
    positions = pick_positions(occ)
    picks = load_picks(svc.conn, inv.id)
    e = _base_embed(inv, "choices")
    if not positions:
        e.description = f"**{occupation_label(occ)}** has no skills to choose. Continue to **Points**."
        return e, ChoicesStepView(ctx, inv.id, [], 0, [], 0, 1, None)
    if pos is None:
        pos = next((n for n, p in enumerate(positions) if p not in picks), 0)
    pos = min(max(0, pos), len(positions) - 1)
    slot_i, pick_i = positions[pos]
    options = svc.pick_options(inv, slot_i, pick_i)
    pages = max(1, -(-len(options) // OPTIONS_PER_PAGE))
    page = min(max(0, page), pages - 1)
    shown = options[page * OPTIONS_PER_PAGE:(page + 1) * OPTIONS_PER_PAGE]
    done = sum(1 for p in positions if p in picks)
    e.description = (f"**{occupation_label(occ)}** — {done}/{len(positions)} choices made.\n\n"
                     + choices_text(occ, picks, names, current=positions[pos]))
    return e, ChoicesStepView(ctx, inv.id, positions, pos, shown, page, pages, picks.get(positions[pos]),
                              names, len(options))


class ChoicesStepView(StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, positions: list, pos: int, shown: List[str], page: int,
                 pages: int, current: Optional[str], names: Optional[dict] = None, n_options: int = 0):
        super().__init__(ctx, investigator_id, "choices")
        self.positions, self.pos, self.page = positions, pos, page
        names = names or {}
        if shown:
            sel = discord.ui.Select(
                placeholder=f"Choice {pos + 1}/{len(positions)} — pick a skill"
                            + (f" (options page {page + 1}/{pages})" if pages > 1 else ""),
                row=0,
                options=[discord.SelectOption(label=names.get(k, k)[:100], value=k, default=k == current)
                         for k in shown])
            sel.callback = self._on_pick  # type: ignore[assignment]
            self.sel = sel
            self.add_item(sel)
        nav = [("◀ Choice", lambda: (self.pos - 1, 0), pos > 0),
               ("Choice ▶", lambda: (self.pos + 1, 0), pos < len(positions) - 1)]
        if pages > 1:
            nav += [("◀ Options", lambda: (self.pos, self.page - 1), page > 0),
                    ("Options ▶", lambda: (self.pos, self.page + 1), page < pages - 1)]
        for label, target, enabled in nav:
            b = discord.ui.Button(label=label, row=1, style=discord.ButtonStyle.secondary, disabled=not enabled)
            b.callback = self._nav(target)  # type: ignore[assignment]
            self.add_item(b)

    def _nav(self, target):
        async def cb(interaction: discord.Interaction) -> None:
            pos, page = target()
            await self.show(interaction, "choices", pos=pos, page=page)
        return cb

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        await self.apply_pick(interaction, self.sel.values[0])

    async def apply_pick(self, interaction: discord.Interaction, skill_key: str) -> None:
        slot_i, pick_i = self.positions[self.pos]
        try:
            with creation(self.ctx) as (inv_svc, svc):
                svc.set_pick(self.ctx.owner_id, self.investigator_id, slot_i, pick_i, skill_key)
                inv = inv_svc.get(self.ctx.owner_id, self.investigator_id)
                picks = load_picks(svc.conn, inv.id)
                remaining = [n for n, p in enumerate(self.positions) if p not in picks]
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        if remaining:
            await self.show(interaction, "choices", pos=remaining[0])
        else:
            await self.show(interaction, "allocation", content="All occupation skills chosen. Now spend your points.")


# --- step: allocation -----------------------------------------------------------------

def _allocation_step(ctx: Ctx, svc: CreationService, inv: Investigator, names: dict,
                     page: int = 0) -> Tuple[discord.Embed, discord.ui.View]:
    check = svc.check_for(inv)
    pages, other = allocation_pages(check)
    page = min(max(0, page), len(pages) - 1)
    e = _base_embed(inv, "allocation")
    e.description = budget_line(check)
    e.add_field(name=f"Occupation skills (page {page + 1}/{len(pages)})", value=rows_table(pages[page], names),
                inline=False)
    if other:
        e.add_field(name="Other skills (personal interest)", value=rows_table(other[:12], names), inline=False)
    e.add_field(name="How", value=("**Occupation points** / **Interest points** edit the skills on this page. "
                                   "**Other skill** adds personal interest points to any skill. "
                                   "Max 99 per skill; Credit Rating must end in the occupation's range."),
                inline=False)
    return e, AllocationStepView(ctx, inv.id, [r.key for r in pages[page]], page, len(pages), names,
                                 {r.key: (r.base, r.occupation_points, r.interest_points) for r in check.rows})


class AllocationStepView(StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, keys: List[str], page: int, pages: int, names: dict,
                 values: dict):
        super().__init__(ctx, investigator_id, "allocation")
        self.keys, self.page, self.names, self.values = keys, page, names, values
        for label, budget, style in (("Occupation points", "occupation", discord.ButtonStyle.primary),
                                     ("Interest points", "interest", discord.ButtonStyle.secondary)):
            b = discord.ui.Button(label=label, row=0, style=style, disabled=not keys)
            b.callback = self._open(budget)  # type: ignore[assignment]
            self.add_item(b)
        other = discord.ui.Button(label="Other skill", emoji="➕", row=0, style=discord.ButtonStyle.secondary)
        other.callback = self._other  # type: ignore[assignment]
        self.add_item(other)
        for label, delta in (("◀ Page", -1), ("Page ▶", 1)):
            b = discord.ui.Button(label=label, row=1, style=discord.ButtonStyle.secondary,
                                  disabled=not (0 <= page + delta < pages))
            b.callback = self._page(delta)  # type: ignore[assignment]
            self.add_item(b)

    def _page(self, delta: int):
        async def cb(interaction: discord.Interaction) -> None:
            await self.show(interaction, "allocation", page=self.page + delta)
        return cb

    def _open(self, budget: str):
        async def cb(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(PointsModal(self, budget))
        return cb

    async def _other(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(OtherSkillModal(self))


class PointsModal(discord.ui.Modal):
    """Up to 5 skills of the current page; one number each for the chosen budget."""

    def __init__(self, parent: AllocationStepView, budget: str):
        super().__init__(title=("Occupation" if budget == "occupation" else "Personal interest") + " points",
                         timeout=PANEL_TIMEOUT)
        self.parent, self.budget = parent, budget
        self.inputs = {}
        for key in parent.keys:
            base, o, i = parent.values.get(key, (0, 0, 0))
            current = o if budget == "occupation" else i
            label = f"{parent.names.get(key, key)} (base {base})"[:45]
            ti = _text(label, current or None, max_length=2, placeholder="0")
            self.inputs[key] = (ti, current)
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        applied, failed = [], []
        with creation(self.parent.ctx) as (_, svc):
            for key, (ti, current) in self.inputs.items():
                raw = ti.value.strip()
                if (int(raw) if raw.isdigit() else (0 if raw == "" else None)) == current:
                    continue
                kw = {"occupation_points": raw} if self.budget == "occupation" else {"interest_points": raw}
                try:
                    svc.allocate(self.parent.ctx.owner_id, self.parent.investigator_id, key, **kw)
                    applied.append(self.parent.names.get(key, key))
                except CharacterError as e:
                    failed.append(f"{self.parent.names.get(key, key)}: {e}")
        msg = ("Saved: " + ", ".join(applied) + ".") if applied else "No changes."
        if failed:
            msg += "\n⚠️ Not saved:\n- " + "\n- ".join(failed)
        await self.parent.show(interaction, "allocation", content=msg, page=self.parent.page)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error)


class OtherSkillModal(discord.ui.Modal):
    def __init__(self, parent: AllocationStepView):
        super().__init__(title="Personal interest: any skill", timeout=PANEL_TIMEOUT)
        self.parent = parent
        self.skill_input = _text("Skill (English or 中文)", required=True, max_length=60,
                                 placeholder="Charm / 魅惑")
        self.points_input = _text("Personal interest points (0 removes)", required=True, max_length=2,
                                  placeholder="20")
        self.add_item(self.skill_input)
        self.add_item(self.points_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            with creation(self.parent.ctx) as (_, svc):
                svc.allocate(self.parent.ctx.owner_id, self.parent.investigator_id, self.skill_input.value,
                             interest_points=self.points_input.value)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await self.parent.show(interaction, "allocation", content="Saved.", page=self.parent.page)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error)


# --- step: review -------------------------------------------------------------------------

def _review_step(ctx: Ctx, svc: CreationService, inv: Investigator, names: dict) -> Tuple[discord.Embed,
                                                                                          discord.ui.View]:
    occ = svc.occupation_of(inv)
    check = svc.check_for(inv)
    e = _base_embed(inv, "review")
    e.description = f"**{occupation_label(occ)}**\n{budget_line(check)}\n\n{review_text(check)}"
    rows = [r for r in check.rows if r.occupation_points or r.interest_points or r.is_occupation_skill]
    e.add_field(name="Skills", value=rows_table(rows[:15], names), inline=False)
    return e, ReviewStepView(ctx, inv.id, check.ok)


class ReviewStepView(StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, ok: bool):
        super().__init__(ctx, investigator_id, "review")
        b = discord.ui.Button(label="Finish creation", emoji="✅", row=0, style=discord.ButtonStyle.success,
                              disabled=not ok)
        b.callback = self._finish  # type: ignore[assignment]
        self.add_item(b)

    async def _finish(self, interaction: discord.Interaction) -> None:
        from apps.discord_bot.views import refresh_panel
        try:
            with creation(self.ctx) as (inv_svc, _):
                inv = inv_svc.finalize(self.ctx.owner_id, self.investigator_id)
        except CharacterError as e:
            await reply_error(interaction, e)
            return
        await refresh_panel(interaction, self.ctx, inv.id, content=CREATION_FINISHED_MESSAGE.format(name=inv.name))


async def open_creation(interaction: discord.Interaction, ctx: Ctx, investigator_id: int,
                        step: Optional[str] = None, *, edit: bool = True, content: Optional[str] = None) -> None:
    """Show a creation step, editing the current message (from a button) or as a new ephemeral reply."""
    try:
        embed, view = render_step(ctx, investigator_id, step)
    except CharacterError as e:
        await reply_error(interaction, e)
        return
    if edit:
        await interaction.response.edit_message(content=content, embed=embed, view=view)
    else:
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)
