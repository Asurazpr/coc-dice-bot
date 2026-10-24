"""Discord UI for occupation-based creation.

Steps: Occupation → Occupation skill choices → Skill points → Review.
Each step is one ephemeral message that edits itself in place; every action
is saved immediately, so a player can stop and resume later from the sheet
editor ("Occupation & skills"). All rules and ownership checks live in
``cocbot.occupations.service``; these views only gather input and render.

Text follows the player's display language (``Ctx.lang``: en / zh / both).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import discord

from apps.discord_bot.views import (
    PANEL_TIMEOUT,
    Ctx,
    OwnedView,
    _text,
    reply_error,
)
from cocbot.characters.models import STATUS_DRAFT, Investigator
from cocbot.characters.service import CharacterError, InvalidState, InvestigatorService, UnspentPoints
from cocbot.db.connection import get_conn
from cocbot.i18n import CATALOG, Msg, label, localized_names, occupation_name, render, skill_names
from cocbot.occupations import catalogue
from cocbot.occupations.rules import MODE_ADD, MODE_SET, CTHULHU_MYTHOS, alternative_stats, pick_positions, suggested_stat
from cocbot.occupations.service import BatchRejected, CreationService, load_picks, load_points, skill_bases
from cocbot.ui.creation_text import (
    STEP_TITLES,  # noqa: F401  (re-exported for compatibility)
    allocation_pages,
    batch_result_text,
    budget_line,
    parse_points,
    choices_text,
    occupation_detail,
    occupation_label,
    slot_text,
    review_text,
    rows_table,
    step_title,
)
from cocbot.ui.skill_picker import PickerPage, build_search_index, group_label, picker_page

OPTIONS_PER_PAGE = 25          # Discord select-menu limit
STEPS = ("occupation", "choices", "allocation", "review")
STEP_NAV = {"occupation": "nav_occupation", "choices": "nav_choices", "allocation": "nav_allocation",
            "review": "nav_review"}
# Raw English template ("{name}" placeholder), kept for callers that .format() it;
# prefer creation_finished_text(name, lang).
CREATION_FINISHED_MESSAGE = CATALOG["creation_finished"][0]


def creation_finished_text(name: str, lang: str = "en") -> str:
    return render("creation_finished", lang, name=name)


@contextmanager
def creation(ctx: Ctx) -> Iterator[Tuple[InvestigatorService, CreationService]]:
    with get_conn(ctx.db_path) as conn:
        svc = CreationService(conn)
        yield svc.investigators, svc


@dataclass
class R:
    """Everything a step builder needs to render in the player's language."""
    ctx: Ctx
    svc: CreationService
    inv: Investigator
    names: Dict[str, str]                  # skill key -> name in ctx.lang
    nbl: Dict[str, Dict[str, str]]         # names by language, for localizing Msg

    @property
    def lang(self) -> str:
        return self.ctx.lang

    def t(self, code: str, **kw) -> str:
        return render(code, self.lang, self.nbl, **kw)

    def lbl(self, code: str, limit: int, **kw) -> str:
        return label(code, self.lang, limit, self.nbl, **kw)


def render_step(ctx: Ctx, investigator_id: int, step: Optional[str] = None, **state) -> Tuple[discord.Embed,
                                                                                              discord.ui.View]:
    """Embed + view for a creation step (default: where the player left off)."""
    with creation(ctx) as (inv_svc, svc):
        inv = inv_svc.get(ctx.owner_id, investigator_id, include_archived=False)
        if inv.status != STATUS_DRAFT:
            raise InvalidState(Msg("not_draft"))
        if step is None:
            step = svc.next_step(inv)
        if step == "characteristics":
            raise InvalidState(Msg("enter_chars_first"))
        occ = svc.occupation_of(inv)
        if occ is None and step != "occupation":
            step = "occupation"
        r = R(ctx, svc, inv, skill_names(svc.conn, ctx.lang), localized_names(svc.conn))
        builder = {"occupation": _occupation_step, "choices": _choices_step,
                   "allocation": _allocation_step, "review": _review_step}[step]
        return builder(r, **state)


def _base_embed(r: R, step: str) -> discord.Embed:
    e = discord.Embed(title=f"{r.inv.name} — {step_title(step, r.lang)}"[:256], color=discord.Color.dark_teal())
    e.set_footer(text=r.t("footer_saves")[:2048])
    return e


class StepView(OwnedView):
    def __init__(self, ctx: Ctx, investigator_id: int, step: str):
        super().__init__(ctx, timeout=PANEL_TIMEOUT)
        self.investigator_id = investigator_id
        for s in STEPS:
            b = discord.ui.Button(label=label(STEP_NAV[s], ctx.lang, 80), row=4,
                                  style=discord.ButtonStyle.primary if s == step else discord.ButtonStyle.secondary,
                                  disabled=s == step)
            b.callback = self._goto(s)  # type: ignore[assignment]
            self.add_item(b)
        sheet = discord.ui.Button(label=label("nav_sheet", ctx.lang, 80), emoji="📄", row=4,
                                  style=discord.ButtonStyle.secondary)
        sheet.callback = self._to_sheet  # type: ignore[assignment]
        self.add_item(sheet)

    def t(self, code: str, **kw) -> str:
        return render(code, self.ctx.lang, **kw)

    def _goto(self, step: str):
        async def cb(interaction: discord.Interaction) -> None:
            await self.show(interaction, step)
        return cb

    async def show(self, interaction: discord.Interaction, step: Optional[str] = None, content: Optional[str] = None,
                   **state) -> None:
        try:
            embed, view = render_step(self.ctx, self.investigator_id, step, **state)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await interaction.response.edit_message(content=content, embed=embed, view=view)

    async def _to_sheet(self, interaction: discord.Interaction) -> None:
        from apps.discord_bot.views import refresh_panel
        await refresh_panel(interaction, self.ctx, self.investigator_id)


# --- shared skill picker ---------------------------------------------------------------------

class PickerMixin:
    """Adds category select, skill select, search and paging to a StepView.

    Subclasses implement ``picker_navigate(interaction, category, query, page)``
    and ``picker_selected(interaction, skill_key)``.
    """

    ctx: Ctx

    def add_picker(self, pp: PickerPage, names: Dict[str, str], bases: Dict[str, Optional[int]],
                   placeholder: str, current: Optional[str] = None,
                   extra_desc: Optional[Dict[str, str]] = None, nav_row: int = 2,
                   multi: Optional[Tuple[Sequence[str], int]] = None) -> None:
        """``multi=(selected keys, max)`` turns the skill menu into a multi-select (grouped choices)."""
        lang = self.ctx.lang
        self.picker = pp
        self.multi = multi
        if pp.available > OPTIONS_PER_PAGE and pp.categories:
            cat = discord.ui.Select(
                placeholder=label("picker_category_ph", lang, 150, n=pp.available), row=0,
                options=[discord.SelectOption(label=f"{group_label(g, lang)} ({n})"[:100], value=g,
                                              default=g == pp.category) for g, n in pp.categories[:25]])
            cat.callback = self._on_category  # type: ignore[assignment]
            self.category_select = cat
            self.add_item(cat)  # type: ignore[attr-defined]
        if pp.keys:
            ph = placeholder + (render("picker_page_suffix", lang, p=pp.page + 1, pages=pp.pages)
                                if pp.pages > 1 else "")
            opts = []
            for k in pp.keys:
                desc = render("picker_option_desc", lang, base=bases.get(k) if bases.get(k) is not None else "?")
                if extra_desc and extra_desc.get(k):
                    desc += f" · {extra_desc[k]}"
                chosen = (k in multi[0]) if multi else k == current
                opts.append(discord.SelectOption(label=names.get(k, k)[:100], value=k, description=desc[:100],
                                                 default=chosen))
            if multi:
                sel = discord.ui.Select(placeholder=ph[:150], row=1, options=opts, min_values=0,
                                        max_values=max(1, min(len(opts), multi[1])))
            else:
                sel = discord.ui.Select(placeholder=ph[:150], row=1, options=opts)
            sel.callback = self._on_skill  # type: ignore[assignment]
            self.skill_select = sel
            self.add_item(sel)  # type: ignore[attr-defined]
        search = discord.ui.Button(label=label("btn_search", lang, 80), emoji="🔎", row=nav_row,
                                   style=discord.ButtonStyle.secondary)
        search.callback = self._on_search  # type: ignore[assignment]
        self.add_item(search)  # type: ignore[attr-defined]
        if pp.mode in ("category", "search"):
            clear = discord.ui.Button(label=label("btn_all_categories", lang, 80), row=nav_row,
                                      style=discord.ButtonStyle.secondary)
            clear.callback = self._on_clear  # type: ignore[assignment]
            self.add_item(clear)  # type: ignore[attr-defined]
        if pp.pages > 1:
            for code, delta in (("btn_prev_page", -1), ("btn_next_page", 1)):
                b = discord.ui.Button(label=label(code, lang, 80), row=3, style=discord.ButtonStyle.secondary,
                                      disabled=not (0 <= pp.page + delta < pp.pages))
                b.callback = self._on_page(delta)  # type: ignore[assignment]
                self.add_item(b)  # type: ignore[attr-defined]

    async def _on_category(self, interaction: discord.Interaction) -> None:
        await self.picker_navigate(interaction, category=self.category_select.values[0], query="", page=0)

    async def _on_skill(self, interaction: discord.Interaction) -> None:
        if getattr(self, "multi", None):
            await self.picker_selected_many(interaction, list(self.skill_select.values))
            return
        await self.picker_selected(interaction, self.skill_select.values[0])

    async def _on_search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SkillSearchModal(self))

    async def _on_clear(self, interaction: discord.Interaction) -> None:
        await self.picker_navigate(interaction, category=None, query="", page=0)

    def _on_page(self, delta: int):
        async def cb(interaction: discord.Interaction) -> None:
            pp = self.picker
            await self.picker_navigate(interaction, category=pp.category, query=pp.query, page=pp.page + delta)
        return cb

    async def picker_navigate(self, interaction: discord.Interaction, category: Optional[str], query: str,
                              page: int) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def picker_selected(self, interaction: discord.Interaction, skill_key: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def picker_selected_many(self, interaction: discord.Interaction,
                                   keys: List[str]) -> None:  # pragma: no cover
        raise NotImplementedError


class SkillSearchModal(discord.ui.Modal):
    def __init__(self, parent: PickerMixin):
        lang = parent.ctx.lang
        super().__init__(title=label("picker_search_title", lang, 45), timeout=PANEL_TIMEOUT)
        self.parent = parent
        self.query_input = _text(label("picker_search_label", lang, 45), getattr(parent.picker, "query", "") or None,
                                 required=True, max_length=40, placeholder="Spot Hidden / 侦查")
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent.picker_navigate(interaction, category=None, query=self.query_input.value.strip(), page=0)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.parent.ctx)


def _picker_status(r: R, pp: PickerPage) -> str:
    if pp.mode == "search":
        return r.t("picker_results", query=pp.query, n=pp.total) if pp.total else \
            r.t("picker_no_results", query=pp.query)
    if pp.mode == "category":
        return r.t("picker_category_shown", name=group_label(pp.category, r.lang), n=pp.total)
    if pp.mode == "categories":
        return r.t("picker_hint_categories")
    return ""


# --- step: occupation ----------------------------------------------------------------

def _occupation_step(r: R, page: int = 0, query: str = "",
                     pending: Optional[int] = None) -> Tuple[discord.Embed, discord.ui.View]:
    e = _base_embed(r, "occupation")
    occ = r.svc.occupation_of(r.inv)
    total = catalogue.count_visible(r.svc.conn, r.ctx.owner_id, query)
    pages = max(1, -(-total // OPTIONS_PER_PAGE))
    page = min(max(0, page), pages - 1)
    found = catalogue.search(r.svc.conn, r.ctx.owner_id, query, OPTIONS_PER_PAGE, page * OPTIONS_PER_PAGE)
    if occ:
        e.description = r.t("occ_current", name=occupation_label(occ, r.lang))
        for name, value in occupation_detail(occ, r.names, r.lang):
            e.add_field(name=name[:256], value=value, inline=False)
    else:
        e.description = r.t("occ_intro")
    if query:
        e.description += "\n" + r.t("occ_search_result", query=query, n=total)
    if pending:
        target = catalogue.get(r.svc.conn, pending)
        e.add_field(name=r.t("occ_confirm_title"), value=r.t(
            "occ_confirm_body", name=occupation_name(target, r.lang) if target else "?")[:1024], inline=False)
    return e, OccupationStepView(r.ctx, r.inv.id, found, page, pages, query, pending)


class OccupationStepView(StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, found: List[catalogue.Occupation], page: int, pages: int,
                 query: str, pending: Optional[int]):
        super().__init__(ctx, investigator_id, "occupation")
        self.page, self.pages, self.query, self.pending = page, pages, query, pending
        lang = ctx.lang
        if found:
            sel = discord.ui.Select(
                placeholder=label("occ_placeholder", lang, 150, page=page + 1, pages=pages), row=0,
                options=[discord.SelectOption(
                    label=occupation_label(o, lang)[:100], value=str(o.id),
                    description=render("occ_option_desc", lang if lang != "both" else "en", min=o.credit_min,
                                       max=o.credit_max, formula=o.formula_text())[:100])
                    for o in found])
            sel.callback = self._on_pick  # type: ignore[assignment]
            self.sel = sel
            self.add_item(sel)
        for text, delta in (("◀", -1), ("▶", 1)):
            b = discord.ui.Button(label=text, row=1, style=discord.ButtonStyle.secondary,
                                  disabled=not (0 <= page + delta < pages))
            b.callback = self._page(delta)  # type: ignore[assignment]
            self.add_item(b)
        search = discord.ui.Button(label=label("btn_search", lang, 80), emoji="🔎", row=1,
                                   style=discord.ButtonStyle.secondary)
        search.callback = self._search  # type: ignore[assignment]
        self.add_item(search)
        custom = discord.ui.Button(label=label("btn_custom", lang, 80), emoji="✏️", row=1,
                                   style=discord.ButtonStyle.secondary)
        custom.callback = self._custom  # type: ignore[assignment]
        self.add_item(custom)
        if pending:
            ok = discord.ui.Button(label=label("btn_confirm_change", lang, 80), row=2,
                                   style=discord.ButtonStyle.danger)
            ok.callback = self._confirm  # type: ignore[assignment]
            cancel = discord.ui.Button(label=label("btn_cancel", lang, 80), row=2,
                                       style=discord.ButtonStyle.secondary)
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
                occ = svc.occupation_of(inv)
        except InvalidState as e:
            if getattr(e.args[0] if e.args else None, "code", None) == "confirm_occupation_change":
                await self.show(interaction, "occupation", page=self.page, query=self.query, pending=occupation_id)
                return
            await reply_error(interaction, e, self.ctx)
            return
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        name = occupation_name(occ, self.ctx.lang) if occ else inv.occupation
        await self.show(interaction, next_step, content=self.t("occ_set", name=name))

    async def _search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(OccupationSearchModal(self))

    async def _custom(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(HomebrewModal(self))


class OccupationSearchModal(discord.ui.Modal):
    def __init__(self, parent: OccupationStepView):
        lang = parent.ctx.lang
        super().__init__(title=label("occ_search_title", lang, 45), timeout=PANEL_TIMEOUT)
        self.parent = parent
        self.query_input = _text(label("occ_search_label", lang, 45), parent.query, max_length=40,
                                 placeholder="doctor / 医生")
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent.show(interaction, "occupation", page=0, query=self.query_input.value.strip())

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.parent.ctx)


class HomebrewModal(discord.ui.Modal):
    def __init__(self, parent: OccupationStepView):
        lang = parent.ctx.lang
        super().__init__(title=label("hb_title", lang, 45), timeout=PANEL_TIMEOUT)
        self.parent = parent
        self.name_input = _text(label("hb_label_name", lang, 45), required=True, max_length=64)
        self.credit_input = _text(label("hb_label_credit", lang, 45), required=True, max_length=7,
                                  placeholder="10-40")
        self.formula_input = _text(label("hb_label_formula", lang, 45), "EDU*4", required=True, max_length=20,
                                   placeholder="EDU*4 / EDU*2+DEX*2")
        for item in (self.name_input, self.credit_input, self.formula_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            with creation(self.parent.ctx) as (_, svc):
                inv = svc.create_homebrew(self.parent.ctx.owner_id, self.parent.investigator_id,
                                          self.name_input.value, self.credit_input.value, self.formula_input.value)
        except CharacterError as e:
            await reply_error(interaction, e, self.parent.ctx)
            return
        await self.parent.show(interaction, "choices",
                               content=render("hb_created", self.parent.ctx.lang, name=inv.occupation))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.parent.ctx)


# --- step: choices ----------------------------------------------------------------------
#
# One panel per choice *group* (an occupation slot such as "any two other skills"). The
# player ticks skills in a multi-select — across categories, search results and pages —
# sees the selection and any point refunds, and saves the whole group with one button.
# Nothing is written until **Save choices**; switching group discards unsaved ticks.

def choice_slots(occ) -> List[int]:
    return [i for i, s in enumerate(occ.slots) if s.kind != "fixed"]


def _choices_step(r: R, group: Optional[int] = None, draft: Optional[Sequence[str]] = None,
                  category: Optional[str] = None, query: str = "", page: int = 0,
                  pos: Optional[int] = None) -> Tuple[discord.Embed, discord.ui.View]:
    occ = r.svc.occupation_of(r.inv)
    slots = choice_slots(occ)
    picks = load_picks(r.svc.conn, r.inv.id)
    e = _base_embed(r, "choices")
    if not slots:
        e.description = r.t("choices_none", occ=occupation_label(occ, r.lang))
        return e, ChoicesStepView(r.ctx, r.inv.id, [], 0, None, (), (), r.names, {}, 0)
    if group is None and pos is not None:            # old callers: a pick position
        positions = pick_positions(occ)
        group = slots.index(positions[min(max(0, pos), len(positions) - 1)][0])
    if group is None:                                # first group with an empty pick
        group = next((g for g, i in enumerate(slots)
                      if any((i, p) not in picks for p in range(occ.slots[i].count))), 0)
    group = min(max(0, group), len(slots) - 1)
    slot_i = slots[group]
    slot = occ.slots[slot_i]
    saved = tuple(picks[(slot_i, p)] for p in range(slot.count) if (slot_i, p) in picks)
    draft = tuple(saved if draft is None else draft)
    # options: everything this slot allows that no other slot (or a fixed skill) uses
    options = r.svc.pick_options(r.inv, slot_i, 0)
    options = list(dict.fromkeys([*options, *[k for k in saved if k not in options]]))
    pp = picker_page(options, build_search_index(r.svc.conn), category, query, page,
                     keep_order=slot.kind == "choose")
    refunds: List[Tuple[str, int]] = []
    if draft != saved:
        try:
            _, refunds = r.svc.preview_slot_picks(r.inv, slot_i, draft)
        except CharacterError:
            refunds = []
    positions = pick_positions(occ)
    done = sum(1 for p in positions if p in picks)
    lines = [r.t("choices_header", occ=occupation_label(occ, r.lang), done=done, total=len(positions),
                 left=len(positions) - done), "",
             choices_text(occ, picks, r.names, current=(slot_i, 0) if slot.count == 1 else None, lang=r.lang), "",
             r.t("group_title", i=group + 1, n=len(slots), slot=slot_text(slot, r.names, r.lang))]
    chosen = ", ".join(r.names.get(k, k) for k in draft)
    lines.append(r.t("group_selected", k=len(draft), n=slot.count, skills=chosen) if draft
                 else r.t("group_selected_none"))
    lines.append(r.t("group_unsaved") if draft != saved else r.t("group_saved_state") if saved else "")
    for skill, points in refunds:
        lines.append("↩️ " + r.t("group_refund", skill=r.names.get(skill, skill), points=points))
    status = _picker_status(r, pp)
    if status:
        lines += ["", status]
    e.description = "\n".join(x for x in lines if x is not None)[:4096]
    return e, ChoicesStepView(r.ctx, r.inv.id, slots, group, pp, draft, saved, r.names,
                              skill_bases(r.svc.conn, r.inv), slot.count)


class ChoicesStepView(PickerMixin, StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, slots: List[int], group: int, pp: Optional[PickerPage],
                 draft: Sequence[str], saved: Sequence[str], names: Dict[str, str],
                 bases: Dict[str, Optional[int]], count: int):
        StepView.__init__(self, ctx, investigator_id, "choices")
        self.slots, self.group, self.draft, self.saved, self.count = slots, group, tuple(draft), tuple(saved), count
        lang = ctx.lang
        if pp is not None:
            self.add_picker(pp, names, bases, label("group_ph", lang, 120, i=group + 1, n=len(slots), k=count),
                            multi=(self.draft, count))
        if len(slots) > 1:
            for code, target in (("btn_prev_group", group - 1), ("btn_next_group", group + 1)):
                b = discord.ui.Button(label=label(code, lang, 80), row=2, style=discord.ButtonStyle.secondary,
                                      disabled=not 0 <= target < len(slots))
                b.callback = self._goto_group(target)  # type: ignore[assignment]
                self.add_item(b)
        if slots:
            self.save_button = discord.ui.Button(label=label("btn_save_group", lang, 80), emoji="✅", row=3,
                                                 style=discord.ButtonStyle.success,
                                                 disabled=self.draft == self.saved)
            self.save_button.callback = self._save  # type: ignore[assignment]
            self.add_item(self.save_button)
            self.clear_button = discord.ui.Button(label=label("btn_clear_group", lang, 80), row=3,
                                                  style=discord.ButtonStyle.secondary, disabled=not self.draft)
            self.clear_button.callback = self._clear  # type: ignore[assignment]
            self.add_item(self.clear_button)

    def _state(self, **kw):
        pp = getattr(self, "picker", None)
        state = {"group": self.group, "draft": self.draft, "category": pp.category if pp else None,
                 "query": pp.query if pp else "", "page": pp.page if pp else 0}
        state.update(kw)
        return state

    def _goto_group(self, target: int):
        async def cb(interaction: discord.Interaction) -> None:
            await self.show(interaction, "choices", group=target)
        return cb

    async def picker_navigate(self, interaction: discord.Interaction, category: Optional[str], query: str,
                              page: int) -> None:
        await self.show(interaction, "choices", **self._state(category=category, query=query, page=page))

    async def picker_selected_many(self, interaction: discord.Interaction, keys: List[str]) -> None:
        """Ticks on this page replace this page's part of the draft; the rest of the draft stays."""
        on_page = set(self.picker.keys)
        new = [k for k in self.draft if k not in on_page] + [k for k in self.picker.keys if k in keys]
        if len(new) > self.count:
            await reply_error(interaction, InvalidState(Msg("group_too_many", n=self.count)), self.ctx)
            return
        await self.show(interaction, "choices", **self._state(draft=tuple(new)))

    async def picker_selected(self, interaction: discord.Interaction, skill_key: str) -> None:
        await self.picker_selected_many(interaction, [skill_key])

    async def _clear(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "choices", **self._state(draft=()))

    async def _save(self, interaction: discord.Interaction) -> None:
        await self.save_group(interaction, self.draft)

    async def save_group(self, interaction: discord.Interaction, keys: Sequence[str]) -> None:
        if tuple(keys) == self.saved:
            await self.show(interaction, "choices", content=self.t("group_unchanged"), **self._state())
            return
        slot_i = self.slots[self.group]
        try:
            with creation(self.ctx) as (inv_svc, svc):
                svc.set_slot_picks(self.ctx.owner_id, self.investigator_id, slot_i, list(keys))
                inv = inv_svc.get(self.ctx.owner_id, self.investigator_id)
                occ = svc.occupation_of(inv)
                picks = load_picks(svc.conn, inv.id)
                names = skill_names(svc.conn, self.ctx.lang)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        saved = self.t("group_saved_msg", skills=", ".join(names.get(k, k) for k in keys) or "—")
        open_groups = [g for g, i in enumerate(self.slots)
                       if any((i, p) not in picks for p in range(occ.slots[i].count))]
        if not open_groups:
            await self.show(interaction, "allocation", content=saved + "\n" + self.t("choices_done"))
            return
        if self.group in open_groups:                # partly filled: stay here
            await self.show(interaction, "choices", content=saved, group=self.group)
            return
        later = [g for g in open_groups if g > self.group]
        await self.show(interaction, "choices", content=saved, group=(later or open_groups)[0])

    async def apply_pick(self, interaction: discord.Interaction, skill_key: str) -> None:
        """Add one skill to this group and save it (used by commands and tests)."""
        keys = [k for k in self.draft if k != skill_key] + [skill_key]
        if len(keys) > self.count:
            keys = keys[-self.count:]
        await self.save_group(interaction, keys)


# --- step: allocation -----------------------------------------------------------------

def _allocation_step(r: R, page: int = 0, mode: str = MODE_SET, picking: bool = False,
                     category: Optional[str] = None, query: str = "", ppage: int = 0,
                     screen: str = "overview", selected: Optional[str] = None, last: Optional[str] = None,
                     confirm_remove: bool = False, origin: str = "overview",
                     ) -> Tuple[discord.Embed, discord.ui.View]:
    check = r.svc.check_for(r.inv)
    left = (check.occupation_remaining, check.interest_remaining)
    if picking:
        return _interest_picker_step(r, check, mode, page, category, query, ppage, origin, last)
    if screen == "interest":
        return _interest_screen(r, check, mode, page, selected, last, confirm_remove)
    pages, other = allocation_pages(check)
    page = min(max(0, page), len(pages) - 1)
    e = _base_embed(r, "allocation")
    e.description = budget_line(check, r.lang)[:4096]
    e.add_field(name=r.t("alloc_page_field", p=page + 1, pages=len(pages))[:256],
                value=rows_table(pages[page], r.names, r.lang), inline=False)
    if other:
        e.add_field(name=r.t("alloc_other_field")[:256], value=rows_table(other[:12], r.names, r.lang), inline=False)
    how = r.t("alloc_how_set" if mode == MODE_SET else "alloc_how_add") + "\n" + r.t("alloc_how_common")
    e.add_field(name=r.t("alloc_how_field")[:256], value=how[:1024], inline=False)
    occ = r.svc.occupation_of(r.inv)
    alts = alternative_stats(occ)
    stat_options = []
    if alts:
        suggested = suggested_stat(occ, r.inv.characteristics)
        chosen = r.inv.skill_point_stat if r.inv.skill_point_stat in alts else None
        mult = next(t["mult"] for t in occ.skill_points if len(t["stats"]) > 1)
        for s in alts:
            v = r.inv.characteristics.get(s)
            text = r.t("stat_option", stat=s, mult=mult, value=v * mult if v is not None else "?")
            if s == suggested:
                text += render("stat_suggested", r.lang if r.lang != "both" else "en")
            stat_options.append((s, text, s == (chosen or suggested)))
        e.add_field(name=r.t("formula_field")[:256], value=(
            r.t("formula_choose", formula=occ.formula_text())
            + (r.t("formula_chosen", stat=chosen) if chosen else r.t("formula_suggested", stat=suggested)))[:1024],
            inline=False)
    editable = sorted((row for row in check.rows if row.is_occupation_skill or row.interest_points),
                      key=lambda row: (not row.is_occupation_skill, r.names.get(row.key, row.key)))
    return e, AllocationStepView(r.ctx, r.inv.id, [row.key for row in pages[page]], page, len(pages), r.names,
                                 {row.key: (row.base, row.occupation_points, row.interest_points)
                                  for row in check.rows},
                                 stat_options, mode, left,
                                 [(row.key, row.is_occupation_skill) for row in editable][:25])


class AllocationStepView(StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, keys: List[str], page: int, pages: int, names: dict,
                 values: dict, stat_options: Optional[list] = None, mode: str = MODE_SET,
                 left: Tuple[Optional[int], Optional[int]] = (None, None),
                 editable: Optional[List[Tuple[str, bool]]] = None):
        super().__init__(ctx, investigator_id, "allocation")
        self.keys, self.page, self.names, self.values, self.mode, self.left = keys, page, names, values, mode, left
        self.occupation_keys = {k for k, occ in (editable or []) if occ}
        lang = ctx.lang
        if editable:
            opts = []
            for key, _ in editable:
                base, o, i = values.get(key, (0, 0, 0))
                opts.append(discord.SelectOption(
                    label=names.get(key, key)[:100], value=key,
                    description=render("edit_one_desc", lang, occ=o, interest=i, total=(base or 0) + o + i)[:100]))
            self.edit_select = discord.ui.Select(placeholder=label("edit_one_ph", lang, 150), row=3, options=opts)
            self.edit_select.callback = self._on_edit_one  # type: ignore[assignment]
            self.add_item(self.edit_select)
        interest = discord.ui.Button(label=label("btn_interest_skills", lang, 80), emoji="🎯", row=0,
                                     style=discord.ButtonStyle.secondary)
        interest.callback = self._interest  # type: ignore[assignment]
        self.add_item(interest)
        if stat_options:
            sel = discord.ui.Select(placeholder=label("stat_placeholder", lang, 150), row=2, options=[
                discord.SelectOption(label=text[:100], value=stat, default=default)
                for stat, text, default in stat_options])
            sel.callback = self._on_stat  # type: ignore[assignment]
            self.stat_select = sel
            self.add_item(sel)
        for code, budget, style in (("btn_occ_points", "occupation", discord.ButtonStyle.primary),
                                    ("btn_int_points", "interest", discord.ButtonStyle.secondary)):
            b = discord.ui.Button(label=label(code, lang, 80), row=0, style=style, disabled=not keys)
            b.callback = self._open(budget)  # type: ignore[assignment]
            self.add_item(b)
        other = discord.ui.Button(label=label("btn_other_skill", lang, 80), emoji="➕", row=0,
                                  style=discord.ButtonStyle.secondary)
        other.callback = self._other  # type: ignore[assignment]
        self.add_item(other)
        toggle = discord.ui.Button(
            label=label("btn_mode_set" if mode == MODE_SET else "btn_mode_add", lang, 80),
            emoji="✏️" if mode == MODE_SET else "➕", row=1, style=discord.ButtonStyle.success)
        toggle.callback = self._toggle_mode  # type: ignore[assignment]
        self.mode_button = toggle
        self.add_item(toggle)
        for code, delta in (("btn_prev_page", -1), ("btn_next_page", 1)):
            b = discord.ui.Button(label=label(code, lang, 80), row=1, style=discord.ButtonStyle.secondary,
                                  disabled=not (0 <= page + delta < pages))
            b.callback = self._page(delta)  # type: ignore[assignment]
            self.add_item(b)

    def _page(self, delta: int):
        async def cb(interaction: discord.Interaction) -> None:
            await self.show(interaction, "allocation", page=self.page + delta, mode=self.mode)
        return cb

    async def _toggle_mode(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "allocation", page=self.page, mode=MODE_ADD if self.mode == MODE_SET else MODE_SET)

    async def _on_stat(self, interaction: discord.Interaction) -> None:
        await self.choose_stat(interaction, self.stat_select.values[0])

    async def choose_stat(self, interaction: discord.Interaction, stat: str) -> None:
        try:
            with creation(self.ctx) as (_, svc):
                svc.set_formula_stat(self.ctx.owner_id, self.investigator_id, stat)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await self.show(interaction, "allocation", content=self.t("stat_set", stat=stat), page=self.page,
                        mode=self.mode)

    def _open(self, budget: str):
        async def cb(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(PointsModal(self, budget))
        return cb

    async def _other(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "allocation", page=self.page, mode=self.mode, picking=True)

    async def _interest(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "allocation", page=self.page, mode=self.mode, screen="interest")

    async def _on_edit_one(self, interaction: discord.Interaction) -> None:
        key = self.edit_select.values[0]
        await interaction.response.send_modal(SkillEditModal(self, key, key in self.occupation_keys))

    async def submit_batch(self, interaction: discord.Interaction,
                           proposals: Dict[str, Tuple[Optional[int], Optional[int]]],
                           parse_errors: List[Msg]) -> None:
        await submit_batch(self, interaction, proposals, parse_errors, self.page, self.mode)


async def submit_batch(view: StepView, interaction: discord.Interaction,
                       proposals: Dict[str, Tuple[Optional[int], Optional[int]]], parse_errors: List[Msg],
                       page: int, mode: str, **state) -> None:
    """Validate and save a whole form at once; show what changed, or why nothing was saved."""
    from cocbot.occupations.rules import BatchResult
    with creation(view.ctx) as (_, svc):
        nbl = localized_names(svc.conn)
        names = skill_names(svc.conn, view.ctx.lang)
        if parse_errors:
            result = BatchResult([], list(parse_errors), (None, None), (0, 0), (0, 0), {})
        else:
            try:
                _, result = svc.allocate_batch(view.ctx.owner_id, view.investigator_id, proposals, mode)
            except BatchRejected as e:
                result = e.result
            except CharacterError as e:
                await reply_error(interaction, e, view.ctx)
                return
    await view.show(interaction, "allocation", page=page, mode=mode,
                    content=batch_result_text(result, names, view.ctx.lang, nbl), **state)


class PointsModal(discord.ui.Modal):
    """Up to 5 skills of the current page for one budget, submitted as one atomic batch."""

    def __init__(self, parent: AllocationStepView, budget: str):
        lang = parent.ctx.lang
        left = parent.left[0] if budget == "occupation" else parent.left[1]
        code = f"modal_{'set' if parent.mode == MODE_SET else 'add'}_{'occ' if budget == 'occupation' else 'int'}"
        super().__init__(title=label(code, lang, 45, left="—" if left is None else left), timeout=PANEL_TIMEOUT)
        self.parent, self.budget = parent, budget
        self.inputs = {}
        for key in parent.keys:
            base, o, i = parent.values.get(key, (0, 0, 0))
            current = o if budget == "occupation" else i
            name = parent.names.get(key, key)
            field = label("field_label", lang, 45, skill=name, current=current, base=base)
            if parent.mode == MODE_SET:
                ti = _text(field, current, max_length=3, placeholder=label("field_ph_set", lang, 100))
            else:
                ti = _text(field, None, max_length=4, placeholder=label("field_ph_add", lang, 100))
            self.inputs[key] = (ti, current)
            self.add_item(ti)

    def proposals(self) -> Tuple[Dict[str, Tuple[Optional[int], Optional[int]]], List[Msg]]:
        proposals: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
        errors: List[Msg] = []
        names_en = {}
        for key, (ti, current) in self.inputs.items():
            try:
                value = parse_points(ti.value, self.parent.mode, current)
            except ValueError as e:
                errors.append(Msg("batch_bad_number", names_en, skill=key, text=str(e)))
                continue
            if value is None:
                continue
            proposals[key] = (value, None) if self.budget == "occupation" else (None, value)
        return proposals, errors

    async def on_submit(self, interaction: discord.Interaction) -> None:
        proposals, errors = self.proposals()
        await self.parent.submit_batch(interaction, proposals, errors)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.parent.ctx)


# --- allocation: personal interest skill picker -------------------------------------------------

def _interest_picker_step(r: R, check, mode: str, page: int, category: Optional[str], query: str,
                          ppage: int, origin: str = "overview",
                          last: Optional[str] = None) -> Tuple[discord.Embed, discord.ui.View]:
    keys = [k for k in r.names if k != CTHULHU_MYTHOS]
    pp = picker_page(keys, build_search_index(r.svc.conn), category, query, ppage)
    points = load_points(r.svc.conn, r.inv.id)
    e = _base_embed(r, "allocation")
    status = _picker_status(r, pp)
    e.description = (r.t("interest_pick_header") + "\n" + budget_line(check, r.lang)
                     + (f"\n\n{status}" if status else ""))[:4096]
    extra = {k: f"+{points[k][1]}" for k in pp.keys if points.get(k, (0, 0))[1]}
    return e, InterestPickerView(r.ctx, r.inv.id, pp, r.names, skill_bases(r.svc.conn, r.inv), extra, page, mode,
                                 (check.occupation_remaining, check.interest_remaining), points, origin, last)


class InterestPickerView(PickerMixin, StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, pp: PickerPage, names: Dict[str, str],
                 bases: Dict[str, Optional[int]], extra: Dict[str, str], alloc_page: int, mode: str,
                 left: Tuple[Optional[int], Optional[int]], points: Dict[str, Tuple[int, int]],
                 origin: str = "overview", last: Optional[str] = None):
        StepView.__init__(self, ctx, investigator_id, "allocation")
        self.alloc_page, self.mode, self.left, self.points, self.names = alloc_page, mode, left, points, names
        self.origin, self.last = origin, last
        self.bases = bases
        self.add_picker(pp, names, bases, label("picker_skill_ph_any", ctx.lang, 120), extra_desc=extra)
        back = discord.ui.Button(label=label("btn_back", ctx.lang, 80), emoji="↩️", row=2,
                                 style=discord.ButtonStyle.secondary)
        back.callback = self._back  # type: ignore[assignment]
        self.add_item(back)

    async def _back(self, interaction: discord.Interaction) -> None:
        if self.origin == "interest":
            await self.show(interaction, "allocation", page=self.alloc_page, mode=self.mode, screen="interest",
                            last=self.last)
            return
        await self.show(interaction, "allocation", page=self.alloc_page, mode=self.mode)

    async def picker_navigate(self, interaction: discord.Interaction, category: Optional[str], query: str,
                              page: int) -> None:
        await self.show(interaction, "allocation", page=self.alloc_page, mode=self.mode, picking=True,
                        category=category, query=query, ppage=page, origin=self.origin, last=self.last)

    async def picker_selected(self, interaction: discord.Interaction, skill_key: str) -> None:
        await interaction.response.send_modal(InterestPointsModal(self, skill_key))

    async def submit_batch(self, interaction: discord.Interaction,
                           proposals: Dict[str, Tuple[Optional[int], Optional[int]]],
                           parse_errors: List[Msg], skill_key: Optional[str] = None) -> None:
        # after an edit, stay in personal-interest management with that skill selected
        await submit_batch(self, interaction, proposals, parse_errors, self.alloc_page, self.mode,
                           screen="interest", selected=skill_key, last=skill_key or self.last)


class InterestPointsModal(discord.ui.Modal):
    def __init__(self, parent: InterestPickerView, skill_key: str):
        lang = parent.ctx.lang
        name = parent.names.get(skill_key, skill_key)
        current = parent.points.get(skill_key, (0, 0))[1]
        left = parent.left[1]
        super().__init__(title=label("interest_modal_title", lang, 45, skill=name), timeout=PANEL_TIMEOUT)
        self.parent, self.key, self.current = parent, skill_key, current
        code = "interest_modal_label_set" if parent.mode == MODE_SET else "interest_modal_label_add"
        field = label(code, lang, 45, current=current, left="—" if left is None else left)
        if parent.mode == MODE_SET:
            self.points_input = _text(field, current, max_length=3, placeholder=label("field_ph_set", lang, 100))
        else:
            self.points_input = _text(field, None, max_length=4, placeholder=label("field_ph_add", lang, 100))
        self.add_item(self.points_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = parse_points(self.points_input.value, self.parent.mode, self.current)
        except ValueError as e:
            await self.parent.submit_batch(interaction, {}, [Msg("batch_bad_number", skill=self.key, text=str(e))],
                                           skill_key=self.key)
            return
        proposals = {} if value is None else {self.key: (None, value)}
        await self.parent.submit_batch(interaction, proposals, [], skill_key=self.key)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.parent.ctx)


# --- allocation: personal interest management --------------------------------------------------

def _interest_screen(r: R, check, mode: str, page: int, selected: Optional[str], last: Optional[str],
                     confirm_remove: bool) -> Tuple[discord.Embed, discord.ui.View]:
    rows = sorted((row for row in check.rows if row.interest_points),
                  key=lambda row: r.names.get(row.key, row.key))
    by_key = {row.key: row for row in rows}
    if selected not in by_key:
        selected = None
        confirm_remove = False
    e = _base_embed(r, "allocation")
    e.description = budget_line(check, r.lang)[:4096]
    listing = [("👉 " if row.key == selected else "• ")
               + r.t("interest_row", skill=r.names.get(row.key, row.key), interest=row.interest_points,
                     total=row.total) for row in rows] or [r.t("interest_none")]
    e.add_field(name=r.t("interest_screen_field")[:256], value="\n".join(listing)[:1024], inline=False)
    if selected:
        row = by_key[selected]
        text = r.t("interest_selected", skill=r.names.get(selected, selected), interest=row.interest_points,
                   total=row.total)
        if confirm_remove:
            text += "\n" + r.t("interest_remove_confirm", skill=r.names.get(selected, selected),
                                points=row.interest_points)
        e.add_field(name="🎯", value=text[:1024], inline=False)
    how = r.t("alloc_how_set" if mode == MODE_SET else "alloc_how_add")
    e.add_field(name=r.t("alloc_how_field")[:256], value=how[:1024], inline=False)
    points = load_points(r.svc.conn, r.inv.id)
    return e, InterestScreenView(r.ctx, r.inv.id, [row.key for row in rows][:25], selected, last, confirm_remove,
                                 page, mode, r.names, points, (check.occupation_remaining, check.interest_remaining))


class InterestScreenView(StepView):
    """Browse the skills that have interest points; edit, remove or add one, then come back here."""

    def __init__(self, ctx: Ctx, investigator_id: int, keys: List[str], selected: Optional[str],
                 last: Optional[str], confirm_remove: bool, alloc_page: int, mode: str, names: Dict[str, str],
                 points: Dict[str, Tuple[int, int]], left: Tuple[Optional[int], Optional[int]]):
        StepView.__init__(self, ctx, investigator_id, "allocation")
        self.keys, self.selected, self.last, self.confirm_remove = keys, selected, last, confirm_remove
        self.alloc_page, self.mode, self.names, self.points, self.left = alloc_page, mode, names, points, left
        lang = ctx.lang
        if keys:
            self.skill_select = discord.ui.Select(
                placeholder=label("interest_pick_ph", lang, 150), row=0,
                options=[discord.SelectOption(label=names.get(k, k)[:100], value=k, default=k == selected,
                                              description=f"+{points.get(k, (0, 0))[1]}") for k in keys])
            self.skill_select.callback = self._on_select  # type: ignore[assignment]
            self.add_item(self.skill_select)
        self.edit_button = discord.ui.Button(label=label("btn_edit_skill", lang, 80), emoji="✏️", row=1,
                                             style=discord.ButtonStyle.primary, disabled=selected is None)
        self.edit_button.callback = self._edit  # type: ignore[assignment]
        self.remove_button = discord.ui.Button(
            label=label("btn_confirm_remove" if confirm_remove else "btn_remove_skill", lang, 80), emoji="🗑️", row=1,
            style=discord.ButtonStyle.danger, disabled=selected is None)
        self.remove_button.callback = self._remove  # type: ignore[assignment]
        self.add_button = discord.ui.Button(label=label("btn_add_skill", lang, 80), emoji="➕", row=1,
                                            style=discord.ButtonStyle.success)
        self.add_button.callback = self._add  # type: ignore[assignment]
        for b in (self.edit_button, self.remove_button, self.add_button):
            self.add_item(b)
        if last and last != selected:
            self.previous_button = discord.ui.Button(
                label=label("btn_edit_previous", lang, 80, skill=names.get(last, last)), emoji="↩️", row=2,
                style=discord.ButtonStyle.secondary)
            self.previous_button.callback = self._edit_previous  # type: ignore[assignment]
            self.add_item(self.previous_button)
        toggle = discord.ui.Button(label=label("btn_mode_set" if mode == MODE_SET else "btn_mode_add", lang, 80),
                                   emoji="✏️" if mode == MODE_SET else "➕", row=2, style=discord.ButtonStyle.success)
        toggle.callback = self._toggle_mode  # type: ignore[assignment]
        self.add_item(toggle)
        self.overview_button = discord.ui.Button(label=label("btn_overview", lang, 80), emoji="📊", row=2,
                                                 style=discord.ButtonStyle.secondary)
        self.overview_button.callback = self._overview  # type: ignore[assignment]
        self.add_item(self.overview_button)

    def _here(self, **kw):
        state = {"page": self.alloc_page, "mode": self.mode, "screen": "interest", "selected": self.selected,
                 "last": self.last}
        state.update(kw)
        return state

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "allocation", **self._here(selected=self.skill_select.values[0]))

    async def _edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(InterestPointsModal(self, self.selected))

    async def _edit_previous(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(InterestPointsModal(self, self.last))

    async def _remove(self, interaction: discord.Interaction) -> None:
        if not self.confirm_remove:
            await self.show(interaction, "allocation", **self._here(confirm_remove=True))
            return
        await self.submit_batch(interaction, {self.selected: (None, 0)}, [], skill_key=self.selected,
                                mode=MODE_SET, keep_selection=False)

    async def _add(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "allocation", page=self.alloc_page, mode=self.mode, picking=True,
                        origin="interest", last=self.last or self.selected)

    async def _toggle_mode(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "allocation", **self._here(mode=MODE_ADD if self.mode == MODE_SET else MODE_SET))

    async def _overview(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, "allocation", page=self.alloc_page, mode=self.mode)

    async def submit_batch(self, interaction: discord.Interaction,
                           proposals: Dict[str, Tuple[Optional[int], Optional[int]]],
                           parse_errors: List[Msg], skill_key: Optional[str] = None, mode: Optional[str] = None,
                           keep_selection: bool = True) -> None:
        mode = mode or self.mode
        from cocbot.occupations.rules import BatchResult
        with creation(self.ctx) as (_, svc):
            nbl = localized_names(svc.conn)
            names = skill_names(svc.conn, self.ctx.lang)
            if parse_errors:
                result = BatchResult([], list(parse_errors), (None, None), (0, 0), (0, 0), {})
            else:
                try:
                    _, result = svc.allocate_batch(self.ctx.owner_id, self.investigator_id, proposals, mode)
                except BatchRejected as e:
                    result = e.result
                except CharacterError as e:
                    await reply_error(interaction, e, self.ctx)
                    return
        await self.show(interaction, "allocation", content=batch_result_text(result, names, self.ctx.lang, nbl),
                        **self._here(selected=skill_key if keep_selection else None, last=skill_key or self.last,
                                     confirm_remove=False))


class SkillEditModal(discord.ui.Modal):
    """One skill, both budgets (occupation points only for occupation skills), saved as one atomic batch."""

    def __init__(self, parent: AllocationStepView, skill_key: str, is_occupation: bool):
        lang = parent.ctx.lang
        name = parent.names.get(skill_key, skill_key)
        super().__init__(title=name[:45], timeout=PANEL_TIMEOUT)
        self.parent, self.key = parent, skill_key
        _, o, i = parent.values.get(skill_key, (0, 0, 0))
        self.current = (o, i)
        set_mode = parent.mode == MODE_SET
        ph = label("field_ph_set" if set_mode else "field_ph_add", lang, 100)
        self.occ_input = None
        if is_occupation:
            self.occ_input = _text(label("skill_modal_occ", lang, 45, current=o), o if set_mode else None,
                                   max_length=3 if set_mode else 4, placeholder=ph)
            self.add_item(self.occ_input)
        self.int_input = _text(label("skill_modal_int", lang, 45, current=i), i if set_mode else None,
                               max_length=3 if set_mode else 4, placeholder=ph)
        self.add_item(self.int_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values: List[Optional[int]] = []
        for ti, current in ((self.occ_input, self.current[0]), (self.int_input, self.current[1])):
            if ti is None:
                values.append(None)
                continue
            try:
                values.append(parse_points(ti.value, self.parent.mode, current))
            except ValueError as e:
                await self.parent.submit_batch(interaction, {}, [Msg("batch_bad_number", skill=self.key,
                                                                     text=str(e))])
                return
        proposals = {} if values == [None, None] else {self.key: (values[0], values[1])}
        await self.parent.submit_batch(interaction, proposals, [])

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.parent.ctx)


# --- step: review -------------------------------------------------------------------------

def _review_step(r: R, confirm: bool = False) -> Tuple[discord.Embed, discord.ui.View]:
    occ = r.svc.occupation_of(r.inv)
    check = r.svc.check_for(r.inv)
    manual = r.svc.unreconciled(r.inv)
    bases = skill_bases(r.svc.conn, r.inv)
    e = _base_embed(r, "review")
    e.description = (f"**{occupation_label(occ, r.lang)}**\n{budget_line(check, r.lang)}\n\n"
                     f"{review_text(check, r.lang, r.nbl)}")[:4096]
    rows = [row for row in check.rows if row.occupation_points or row.interest_points or row.is_occupation_skill]
    e.add_field(name=r.t("review_skills"), value=rows_table(rows[:15], r.names, r.lang), inline=False)
    if manual:
        lines = [r.t("manual_line", skill=r.names.get(k, k), value=v, base=bases.get(k, "?"))
                 for k, v in sorted(manual.items())]
        e.add_field(name=r.t("review_manual_title")[:256],
                    value=(r.t("review_manual_body") + "\n" + "\n".join(lines))[:1024], inline=False)
    unspent = check.unspent if check.ok else (0, 0)
    return e, ReviewStepView(r.ctx, r.inv.id, check.ok, unspent, confirm,
                             [(k, r.names.get(k, k), v) for k, v in sorted(manual.items())])


def check_bases(svc: CreationService, inv: Investigator, keys) -> dict:
    all_bases = skill_bases(svc.conn, inv)
    return {k: all_bases.get(k) for k in keys}


class ReviewStepView(StepView):
    def __init__(self, ctx: Ctx, investigator_id: int, ok: bool, unspent: Tuple[int, int] = (0, 0),
                 confirm: bool = False, manual: Optional[list] = None):
        super().__init__(ctx, investigator_id, "review")
        self.unspent, self.confirm = unspent, confirm
        self.manual_names = {k: n for k, n, _ in (manual or [])}
        lang = ctx.lang
        left = sum(unspent)
        if not left:
            text, style = label("btn_finish", lang, 80), discord.ButtonStyle.success
        elif not confirm:
            text, style = label("btn_finish_unspent", lang, 80, n=left), discord.ButtonStyle.secondary
        else:
            text, style = label("btn_finish_confirm", lang, 80, n=left), discord.ButtonStyle.danger
        b = discord.ui.Button(label=text, emoji="✅", row=0, style=style, disabled=not ok)
        b.callback = self._finish  # type: ignore[assignment]
        self.finish_btn = b
        self.add_item(b)
        if manual:
            opts = []
            for key, name, value in manual[:12]:
                for action in ("convert", "discard"):
                    opts.append(discord.SelectOption(
                        label=label(f"reconcile_{action}", lang, 100, skill=name, value=value),
                        value=f"{action}:{key}",
                        description=label(f"reconcile_{action}_desc", lang, 100)))
            sel = discord.ui.Select(placeholder=label("reconcile_ph", lang, 150), row=1, options=opts)
            sel.callback = self._on_reconcile  # type: ignore[assignment]
            self.reconcile_select = sel
            self.add_item(sel)

    async def _on_reconcile(self, interaction: discord.Interaction) -> None:
        await self.apply_reconcile(interaction, self.reconcile_select.values[0])

    async def apply_reconcile(self, interaction: discord.Interaction, choice: str) -> None:
        action, key = choice.split(":", 1)
        try:
            with creation(self.ctx) as (_, svc):
                svc.reconcile(self.ctx.owner_id, self.investigator_id, key, action)
                name = skill_names(svc.conn, self.ctx.lang).get(key, key)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await self.show(interaction, "review", content=self.t(f"reconciled_{action}", skill=name))

    async def _finish(self, interaction: discord.Interaction) -> None:
        from apps.discord_bot.views import refresh_panel
        if sum(self.unspent) and not self.confirm:
            await self.show(interaction, "review", confirm=True, content=self.t("unspent_confirm"))
            return
        try:
            with creation(self.ctx) as (inv_svc, _):
                inv = inv_svc.finalize(self.ctx.owner_id, self.investigator_id, confirm_unspent=self.confirm)
        except UnspentPoints:
            await self.show(interaction, "review", confirm=True, content=self.t("unspent_confirm"))
            return
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await refresh_panel(interaction, self.ctx, inv.id, content=creation_finished_text(inv.name, self.ctx.lang))


async def open_creation(interaction: discord.Interaction, ctx: Ctx, investigator_id: int,
                        step: Optional[str] = None, *, edit: bool = True, content: Optional[str] = None) -> None:
    """Show a creation step, editing the current message (from a button) or as a new ephemeral reply."""
    try:
        embed, view = render_step(ctx, investigator_id, step)
    except CharacterError as e:
        await reply_error(interaction, e, ctx)
        return
    if edit:
        await interaction.response.edit_message(content=content, embed=embed, view=view)
    else:
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)
