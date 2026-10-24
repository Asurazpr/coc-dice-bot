"""Keeper Console: private roster, investigator detail, adjustments, conditions, history.

Views never decide permissions. Each button, select and modal calls
``CampaignService`` with the *clicking* user's ID, so a Keeper who loses
their role while a panel is open is refused on the next click. ``OwnedView``
additionally keeps other users from pressing someone else's panel.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Awaitable, Callable, Dict, Iterator, List, Optional, Tuple

import discord

from apps.discord_bot.views import PANEL_TIMEOUT, Ctx, OwnedView, reply_error
from cocbot.campaigns.service import RESOURCES, CampaignService, RosterEntry, resource_max, resource_value
from cocbot.characters.service import CharacterError, NotFound
from cocbot.db.connection import get_conn
from cocbot.i18n import Msg, label, render
from cocbot.ui.keeper_text import (
    Screen,
    change_lines,
    detail_screen,
    event_line,
    history_text,
    mention,
    parse_adjust,
    resource_name,
    roster_screen,
)

ROSTER_COLOR = discord.Color.dark_purple()


@contextmanager
def campaigns(ctx: Ctx) -> Iterator[CampaignService]:
    with get_conn(ctx.db_path) as conn:
        yield CampaignService(conn)


def to_embed(screen: Screen, color: discord.Color = ROSTER_COLOR) -> discord.Embed:
    e = discord.Embed(title=screen.title, description=screen.description or None, color=color)
    for name, value in screen.fields:
        e.add_field(name=name[:256], value=(value or "—")[:1024], inline=False)
    if screen.footer:
        e.set_footer(text=screen.footer)
    return e


def t(ctx: Ctx, code: str, **kw) -> str:
    return render(code, ctx.lang, **kw)


def btn(ctx: Ctx, code: str, style=discord.ButtonStyle.secondary, emoji: Optional[str] = None,
        row: int = 0, disabled: bool = False) -> discord.ui.Button:
    return discord.ui.Button(label=label(code, ctx.lang, 80), style=style, emoji=emoji, row=row, disabled=disabled)


# --- screens -----------------------------------------------------------------------------

def render_roster(ctx: Ctx, campaign_id: int) -> Tuple[discord.Embed, "RosterView"]:
    with campaigns(ctx) as svc:
        camp, member = svc.get(ctx.owner_id, campaign_id)
        entries = svc.roster(ctx.owner_id, campaign_id)          # Keeper roles only
        pending = svc.pending(ctx.owner_id, campaign_id)
        version, rules = svc.rules(ctx.owner_id, campaign_id)
    screen = roster_screen(camp.name, entries, len(pending), version, rules, member.role, ctx.lang)
    return to_embed(screen), RosterView(ctx, campaign_id, entries, pending)


def render_detail(ctx: Ctx, campaign_id: int, investigator_id: int) -> Tuple[discord.Embed, "DetailView"]:
    with campaigns(ctx) as svc:
        entry = svc.entry(ctx.owner_id, campaign_id, investigator_id)
        recent = svc.history(ctx.owner_id, campaign_id, investigator_id, limit=5)
    return to_embed(detail_screen(entry, recent, ctx.lang)), DetailView(ctx, campaign_id, entry)


def render_history(ctx: Ctx, campaign_id: int, investigator_id: Optional[int]) -> Tuple[discord.Embed, "HistoryView"]:
    with campaigns(ctx) as svc:
        camp, _ = svc.get(ctx.owner_id, campaign_id)
        events = svc.history(ctx.owner_id, campaign_id, investigator_id, limit=15)
        names: Dict[int, str] = {e.investigator.id: e.investigator.name for e in svc.roster(ctx.owner_id, campaign_id)}
    title_name = names.get(investigator_id, camp.name) if investigator_id else camp.name
    screen = Screen(label("kp_history_title", ctx.lang, 256, name=title_name),
                    history_text(events, ctx.lang, names))
    return to_embed(screen), HistoryView(ctx, campaign_id, investigator_id, events, names)


async def show(interaction: discord.Interaction, ctx: Ctx, builder: Callable[[], Tuple[discord.Embed, discord.ui.View]],
               content: Optional[str] = None) -> None:
    """Rebuild a screen and replace the panel message with it (errors go to an ephemeral reply)."""
    try:
        embed, view = builder()
    except CharacterError as e:
        await reply_error(interaction, e, ctx)
        return
    await interaction.response.edit_message(content=content, embed=embed, view=view)


# --- generic confirmation ----------------------------------------------------------------

class ConfirmView(OwnedView):
    """Two-step confirmation: the action runs only on Confirm, and is re-authorized then."""

    def __init__(self, ctx: Ctx, on_confirm: Callable[[discord.Interaction], Awaitable[None]],
                 on_cancel: Callable[[discord.Interaction], Awaitable[None]]):
        super().__init__(ctx)
        self._on_confirm, self._on_cancel = on_confirm, on_cancel
        self.confirm_button = btn(ctx, "kp_btn_confirm", discord.ButtonStyle.danger, "✔️")
        self.cancel_button = btn(ctx, "kp_btn_cancel")
        self.confirm_button.callback = self._confirm  # type: ignore[assignment]
        self.cancel_button.callback = self._cancel    # type: ignore[assignment]
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await self._on_confirm(interaction)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)


async def ask_confirmation(interaction: discord.Interaction, ctx: Ctx, prompt: str,
                           action: Callable[[], str], back: Callable[[], Tuple[discord.Embed, discord.ui.View]],
                           ) -> None:
    """Replace the panel with a confirm/cancel prompt. ``action`` returns the success text."""

    async def confirm(it: discord.Interaction) -> None:
        try:
            done = action()
        except CharacterError as e:
            await reply_error(it, e, ctx)
            return
        await show(it, ctx, back, content=done)

    async def cancel(it: discord.Interaction) -> None:
        await show(it, ctx, back, content=t(ctx, "kp_cancelled"))

    embed = discord.Embed(description=prompt[:4000], color=discord.Color.orange())
    await interaction.response.edit_message(content=None, embed=embed, view=ConfirmView(ctx, confirm, cancel))


# --- roster --------------------------------------------------------------------------------

class RosterView(OwnedView):
    def __init__(self, ctx: Ctx, campaign_id: int, entries: List[RosterEntry], pending):
        super().__init__(ctx)
        self.campaign_id = campaign_id
        if entries:
            self.pick = discord.ui.Select(placeholder=label("kp_pick_investigator", ctx.lang, 150), row=0, options=[
                discord.SelectOption(label=e.investigator.name[:100], value=str(e.investigator.id),
                                     description=_resources_short(e)[:100])
                for e in entries[:25]])
            self.pick.callback = self._pick  # type: ignore[assignment]
            self.add_item(self.pick)
        if pending:
            options = []
            for m, inv in pending[:25]:
                text = (render("kp_request_line", ctx.lang, user=m.user_id, name=inv.name) if inv
                        else render("kp_request_gone", ctx.lang, user=m.user_id)).replace("**", "")
                options.append(discord.SelectOption(label=(inv.name if inv else m.user_id)[:100], value=m.user_id,
                                                    description=text[:100]))
            self.requests = discord.ui.Select(placeholder=label("kp_pick_request", ctx.lang, 150), row=1,
                                              options=options)
            self.requests.callback = self._request  # type: ignore[assignment]
            self.add_item(self.requests)
        refresh = btn(ctx, "kp_btn_refresh", emoji="🔄", row=2)
        refresh.callback = self._refresh  # type: ignore[assignment]
        history = btn(ctx, "kp_btn_campaign_history", emoji="📜", row=2)
        history.callback = self._history  # type: ignore[assignment]
        self.add_item(refresh)
        self.add_item(history)

    async def _pick(self, interaction: discord.Interaction) -> None:
        inv_id = int(self.pick.values[0])
        await show(interaction, self.ctx, lambda: render_detail(self.ctx, self.campaign_id, inv_id))

    async def _request(self, interaction: discord.Interaction) -> None:
        user_id = self.requests.values[0]
        await show(interaction, self.ctx, lambda: render_request(self.ctx, self.campaign_id, user_id))

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, lambda: render_roster(self.ctx, self.campaign_id))

    async def _history(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, lambda: render_history(self.ctx, self.campaign_id, None))


def _resources_short(entry: RosterEntry) -> str:
    inv = entry.investigator
    parts = []
    for f in RESOURCES:
        v, m = resource_value(inv, f), resource_max(inv, f)
        parts.append(f"{f.upper()} {'—' if v is None else v}" + (f"/{m}" if m is not None and f != "luck" else ""))
    return " · ".join(parts)


# --- join requests -------------------------------------------------------------------------

def render_request(ctx: Ctx, campaign_id: int, user_id: str) -> Tuple[discord.Embed, "RequestView"]:
    with campaigns(ctx) as svc:
        pending = {m.user_id: inv for m, inv in svc.pending(ctx.owner_id, campaign_id)}
    if user_id not in pending:
        raise NotFound(Msg("no_request"))
    inv = pending[user_id]
    text = (t(ctx, "kp_request_line", user=mention(user_id), name=inv.name) if inv
            else t(ctx, "kp_request_gone", user=mention(user_id)))
    return discord.Embed(description=text, color=ROSTER_COLOR), RequestView(ctx, campaign_id, user_id,
                                                                            inv.id if inv else None)


class RequestView(OwnedView):
    def __init__(self, ctx: Ctx, campaign_id: int, user_id: str, investigator_id: Optional[int] = None):
        super().__init__(ctx)
        self.campaign_id, self.user_id, self.investigator_id = campaign_id, user_id, investigator_id
        if investigator_id is not None:
            self.sheet_button = btn(ctx, "kp_btn_full_sheet", emoji="📄")
            self.sheet_button.callback = self._sheet  # type: ignore[assignment]
            self.add_item(self.sheet_button)
        for code, style, cb in (("kp_btn_approve", discord.ButtonStyle.success, self._approve),
                                ("kp_btn_deny", discord.ButtonStyle.danger, self._deny),
                                ("kp_btn_back", discord.ButtonStyle.secondary, self._back)):
            b = btn(ctx, code, style)
            b.callback = cb  # type: ignore[assignment]
            self.add_item(b)

    def _roster(self):
        return render_roster(self.ctx, self.campaign_id)

    async def _sheet(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, lambda: render_full_sheet(
            self.ctx, self.campaign_id, self.investigator_id,
            back=lambda: render_request(self.ctx, self.campaign_id, self.user_id)))

    async def _approve(self, interaction: discord.Interaction) -> None:
        try:
            with campaigns(self.ctx) as svc:
                svc.approve(interaction.user.id, self.campaign_id, self.user_id)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await show(interaction, self.ctx, self._roster, content=t(self.ctx, "kp_approved"))

    async def _deny(self, interaction: discord.Interaction) -> None:
        def action() -> str:
            with campaigns(self.ctx) as svc:
                svc.deny(interaction.user.id, self.campaign_id, self.user_id)
            return t(self.ctx, "kp_denied")
        await ask_confirmation(interaction, self.ctx, t(self.ctx, "kp_btn_deny") + f" — {mention(self.user_id)}?",
                               action, self._roster)

    async def _back(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, self._roster)


# --- investigator detail -------------------------------------------------------------------

class DetailView(OwnedView):
    def __init__(self, ctx: Ctx, campaign_id: int, entry: RosterEntry):
        super().__init__(ctx)
        self.campaign_id = campaign_id
        self.investigator_id = entry.investigator.id
        self.name = entry.investigator.name
        adjust = btn(ctx, "kp_btn_adjust", discord.ButtonStyle.primary, "❤️", row=0)
        adjust.callback = self._adjust  # type: ignore[assignment]
        add = btn(ctx, "kp_btn_add_condition", emoji="🏷️", row=0)
        add.callback = self._add_condition  # type: ignore[assignment]
        self.add_item(adjust)
        self.add_item(add)
        self.sheet_button = btn(ctx, "kp_btn_full_sheet", emoji="📄", row=0)
        self.sheet_button.callback = self._sheet  # type: ignore[assignment]
        self.add_item(self.sheet_button)
        if entry.conditions:
            self.remove_select = discord.ui.Select(
                placeholder=label("kp_btn_remove_condition", ctx.lang, 150), row=1,
                options=[discord.SelectOption(label=c.label[:100], value=str(c.id)) for c in entry.conditions[:25]])
            self.remove_select.callback = self._remove_condition  # type: ignore[assignment]
            self.add_item(self.remove_select)
        for code, emoji, cb in (("kp_btn_history", "📜", self._history), ("kp_btn_refresh", "🔄", self._refresh),
                                ("kp_btn_back", "⬅️", self._back)):
            b = btn(ctx, code, emoji=emoji, row=2)
            b.callback = cb  # type: ignore[assignment]
            self.add_item(b)

    def _detail(self):
        return render_detail(self.ctx, self.campaign_id, self.investigator_id)

    async def _adjust(self, interaction: discord.Interaction) -> None:
        try:
            with campaigns(self.ctx) as svc:
                entry = svc.entry(interaction.user.id, self.campaign_id, self.investigator_id)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await interaction.response.send_modal(AdjustModal(self.ctx, self.campaign_id, entry))

    async def _add_condition(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ConditionModal(self.ctx, self.campaign_id, self.investigator_id,
                                                             self.name))

    async def _remove_condition(self, interaction: discord.Interaction) -> None:
        condition_id = int(self.remove_select.values[0])
        labels = {o.value: o.label for o in self.remove_select.options}

        def action() -> str:
            with campaigns(self.ctx) as svc:
                svc.remove_condition(interaction.user.id, self.campaign_id, condition_id)
            return t(self.ctx, "kp_applied")
        await ask_confirmation(interaction, self.ctx,
                               t(self.ctx, "kp_confirm_remove_condition", label=labels.get(str(condition_id), "?"),
                                 name=self.name), action, self._detail)

    async def _history(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, lambda: render_history(self.ctx, self.campaign_id, self.investigator_id))

    async def _sheet(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, lambda: render_full_sheet(self.ctx, self.campaign_id, self.investigator_id,
                                                                    back=self._detail))

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, self._detail)

    async def _back(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, lambda: render_roster(self.ctx, self.campaign_id))


class KeeperModal(discord.ui.Modal):
    def __init__(self, ctx: Ctx, title: str):
        super().__init__(title=title[:45], timeout=PANEL_TIMEOUT)
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.owner_id

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:  # type: ignore[override]
        await reply_error(interaction, error, self.ctx)


class AdjustModal(KeeperModal):
    """Absolute (`7`) or relative (`+2`, `-3`) values; decreases and 0 go through a confirmation."""

    def __init__(self, ctx: Ctx, campaign_id: int, entry: RosterEntry):
        super().__init__(ctx, render("kp_adjust_title", ctx.lang, name=entry.investigator.name))
        self.campaign_id = campaign_id
        self.investigator_id = entry.investigator.id
        self.name = entry.investigator.name
        inv = entry.investigator
        self.seen = {f: resource_value(inv, f) for f in RESOURCES}
        self.inputs: Dict[str, discord.ui.TextInput] = {}
        hint = label("kp_adjust_hint", ctx.lang, 100)
        for f in RESOURCES:
            v, m = self.seen[f], resource_max(inv, f)
            title = f"{resource_name(f, ctx.lang)} ({'—' if v is None else v}" + (f"/{m}" if m is not None else "") + ")"
            ti = discord.ui.TextInput(label=title[:45], required=False, max_length=4, placeholder=hint)
            self.inputs[f] = ti
            self.add_item(ti)
        self.reason = discord.ui.TextInput(label=label("kp_reason", ctx.lang, 45), required=False, max_length=200)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = interaction.user.id
        reason = self.reason.value or None
        detail = lambda: render_detail(self.ctx, self.campaign_id, self.investigator_id)  # noqa: E731
        try:
            values = parse_adjust({f: ti.value for f, ti in self.inputs.items()}, self.seen)
            with campaigns(self.ctx) as svc:
                changes = svc.preview_adjust(actor, self.campaign_id, self.investigator_id, values)
                if not changes:
                    await show(interaction, self.ctx, detail, content=t(self.ctx, "adjust_nothing"))
                    return
                expected = {c.field: c.old for c in changes}
                if not any(c.consequential for c in changes):
                    svc.adjust(actor, self.campaign_id, self.investigator_id, values, reason, expected=expected)
                    await show(interaction, self.ctx, detail, content=t(self.ctx, "kp_applied"))
                    return
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return

        def action() -> str:
            with campaigns(self.ctx) as svc:
                svc.adjust(self.ctx.owner_id, self.campaign_id, self.investigator_id, values, reason,
                           confirmed=True, expected=expected)
            return t(self.ctx, "kp_applied")
        lines = [t(self.ctx, "kp_confirm_changes", name=self.name), *change_lines(changes, self.ctx.lang)]
        if reason:
            lines.append(f"_{reason}_")
        await ask_confirmation(interaction, self.ctx, "\n".join(lines), action, detail)


class ConditionModal(KeeperModal):
    def __init__(self, ctx: Ctx, campaign_id: int, investigator_id: int, name: str):
        super().__init__(ctx, render("kp_btn_add_condition", ctx.lang) + f" — {name}")
        self.campaign_id, self.investigator_id = campaign_id, investigator_id
        self.label_input = discord.ui.TextInput(label=label("kp_condition_label", ctx.lang, 45), max_length=40)
        self.reason = discord.ui.TextInput(label=label("kp_reason", ctx.lang, 45), required=False, max_length=200)
        self.add_item(self.label_input)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            with campaigns(self.ctx) as svc:
                svc.add_condition(interaction.user.id, self.campaign_id, self.investigator_id,
                                  self.label_input.value, reason=self.reason.value or None)
        except CharacterError as e:
            await reply_error(interaction, e, self.ctx)
            return
        await show(interaction, self.ctx, lambda: render_detail(self.ctx, self.campaign_id, self.investigator_id),
                   content=t(self.ctx, "kp_applied"))


# --- full investigator sheet (Keeper inspection) ---------------------------------------------

def render_full_sheet(ctx: Ctx, campaign_id: int, investigator_id: int, back) -> Tuple[discord.Embed, "SheetView"]:
    """Characteristics, skills, occupation and import status of a participant or pending applicant.

    ``CampaignService.inspect`` decides access (Keeper roles; investigator in this campaign or its
    pending request). The Keeper gets a read-only view; ownership never changes.
    """
    from cocbot.db.repo_skill_defs import skill_display_names
    from cocbot.imports.service import ImportService
    from cocbot.ui.import_text import record_lines
    from cocbot.ui.sheet_embed import build_sheet_embed
    with get_conn(ctx.db_path) as conn:
        inv, kind = CampaignService(conn).inspect(ctx.owner_id, campaign_id, investigator_id)
        record = ImportService(conn).latest_for_keeper(ctx.owner_id, campaign_id, investigator_id)
        names = skill_display_names(conn, inv.skills.keys())
    embed = build_sheet_embed(inv, names)
    embed.title = render("kp_sheet_title", ctx.lang, name=inv.name,
                         kind=render(f"kp_sheet_{kind}", ctx.lang))[:256]
    embed.add_field(name="👤", value=render("kp_detail_player", ctx.lang, user=mention(inv.owner_user_id)),
                    inline=False)
    if record is not None:
        provenance = "\n".join(record_lines(record, ctx.lang))
    elif inv.creation_validated:
        provenance = render("kp_sheet_validated", ctx.lang)
    else:
        provenance = render("kp_sheet_created_in_discord", ctx.lang)
    embed.add_field(name=render("kp_sheet_import", ctx.lang)[:256], value=provenance[:1024], inline=False)
    return embed, SheetView(ctx, back)


class SheetView(OwnedView):
    def __init__(self, ctx: Ctx, back):
        super().__init__(ctx)
        self.back = back
        self.back_button = btn(ctx, "kp_btn_back", emoji="⬅️")
        self.back_button.callback = self._back  # type: ignore[assignment]
        self.add_item(self.back_button)

    async def _back(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, self.back)


# --- history and reverts ---------------------------------------------------------------------

class HistoryView(OwnedView):
    def __init__(self, ctx: Ctx, campaign_id: int, investigator_id: Optional[int], events, names):
        super().__init__(ctx)
        self.campaign_id, self.investigator_id = campaign_id, investigator_id
        self.lines = {str(e.id): event_line(e, ctx.lang, names) for e in events}
        revertible = [e for e in events if e.revertible][:25]
        if revertible:
            self.revert_select = discord.ui.Select(
                placeholder=label("kp_pick_revert", ctx.lang, 150), row=0,
                options=[discord.SelectOption(label=_plain(event_line(e, ctx.lang, names))[:100], value=str(e.id))
                         for e in revertible])
            self.revert_select.callback = self._revert  # type: ignore[assignment]
            self.add_item(self.revert_select)
        for code, emoji, cb in (("kp_btn_refresh", "🔄", self._refresh), ("kp_btn_back", "⬅️", self._back)):
            b = btn(ctx, code, emoji=emoji, row=1)
            b.callback = cb  # type: ignore[assignment]
            self.add_item(b)

    def _self(self):
        return render_history(self.ctx, self.campaign_id, self.investigator_id)

    async def _revert(self, interaction: discord.Interaction) -> None:
        event_id = int(self.revert_select.values[0])

        def action() -> str:
            with campaigns(self.ctx) as svc:
                svc.revert(interaction.user.id, self.campaign_id, event_id)
            return t(self.ctx, "kp_reverted")
        await ask_confirmation(interaction, self.ctx,
                               t(self.ctx, "kp_confirm_revert", line=self.lines.get(str(event_id), f"#{event_id}")),
                               action, self._self)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await show(interaction, self.ctx, self._self)

    async def _back(self, interaction: discord.Interaction) -> None:
        if self.investigator_id is None:
            await show(interaction, self.ctx, lambda: render_roster(self.ctx, self.campaign_id))
        else:
            await show(interaction, self.ctx, lambda: render_detail(self.ctx, self.campaign_id, self.investigator_id))


def _plain(text: str) -> str:
    """Select-option labels do not render markdown or mentions."""
    import re
    text = re.sub(r"<@!?(\d+)>", lambda m: "@" + m.group(1)[-4:], text)
    return text.replace("**", "").replace("_", "")


__all__ = ["AdjustModal", "ConditionModal", "ConfirmView", "DetailView", "HistoryView",
           "RequestView", "RosterView", "ask_confirmation", "render_detail", "render_history", "render_request",
           "render_roster", "show"]
