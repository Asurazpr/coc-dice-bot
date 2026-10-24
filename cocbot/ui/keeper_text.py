"""Text for campaign and Keeper Console screens (no Discord dependency).

Everything here is pure formatting of service results, rendered in the
viewer's `/language` preference, so it can be unit-tested without discord.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from cocbot.campaigns.rules import CampaignRules
from cocbot.campaigns.service import RESOURCES, Event, ResourceChange, RosterEntry, resource_max, resource_value
from cocbot.characters.service import ValidationError
from cocbot.i18n import Msg, label, render


def mention(user_id: object) -> str:
    return f"<@{user_id}>"


def resource_name(field: str, lang: str) -> str:
    return render(f"res_{field}", lang)


def rules_summary(rules: CampaignRules, lang: str) -> str:
    return render("rules_summary", lang, critical=f"{rules.critical_max:02d}",
                  fumble=render(f"rules_fumble_{rules.fumble}", lang),
                  visibility=render(f"rules_vis_{rules.roll_visibility}", lang),
                  luck=render("rules_on" if rules.luck_spending else "rules_off", lang))


def role_name(role: str, lang: str) -> str:
    return render(f"kp_role_{role}", lang)


def campaign_sheet_note(campaign_name: str, condition_labels: Sequence[str], lang: str) -> Tuple[str, str]:
    title = label("sheet_campaign_title", lang, 256, name=campaign_name)
    text = render("sheet_campaign_text", lang)
    if condition_labels:
        text += "\n" + render("sheet_conditions", lang, labels=", ".join(condition_labels))
    return title, text[:1024]


def resource_line(inv, lang: str) -> str:
    parts = []
    for field in RESOURCES:
        value, maximum = resource_value(inv, field), resource_max(inv, field)
        v = "—" if value is None else str(value)
        parts.append(f"{resource_name(field, lang)} {v}" + (f"/{maximum}" if maximum is not None and field != "luck"
                                                            else ""))
    return " · ".join(parts)


def roster_line(entry: RosterEntry, lang: str) -> str:
    inv = entry.investigator
    line = f"**{inv.name}** ({mention(entry.participant.user_id)}) — {resource_line(inv, lang)}"
    if entry.conditions:
        line += " · " + ", ".join(f"`{c.label}`" for c in entry.conditions)
    return line


@dataclass(frozen=True)
class Screen:
    """Embed-shaped text: title, description, (name, value) fields, footer."""
    title: str
    description: str
    fields: Tuple[Tuple[str, str], ...] = ()
    footer: str = ""


def roster_screen(campaign_name: str, entries: Sequence[RosterEntry], pending_count: int, rules_version: int,
                  rules: CampaignRules, role: str, lang: str) -> Screen:
    lines = [roster_line(e, lang) for e in entries] or [render("kp_roster_empty", lang)]
    if pending_count:
        lines.append("")
        lines.append(render("kp_pending", lang, count=pending_count))
    footer = render("kp_footer", lang, version=rules_version, rules=rules_summary(rules, lang),
                    role=role_name(role, lang))
    return Screen(label("kp_roster_title", lang, 256, name=campaign_name), "\n".join(lines)[:4000], (), footer[:2048])


def event_line(ev: Event, lang: str, names: Optional[Mapping[int, str]] = None) -> str:
    """One history line: '#12 · 2026-10-21 18:04 · @actor · Ada · HP 12 → 7 — reason'."""
    params = {"old": ev.old_value or "", "new": ev.new_value or "", "ref": ev.reverts_event_id or "",
              "kind": ev.kind}
    if ev.kind in ("invite", "deny", "assistant_add"):
        params["new"] = mention(ev.new_value)
    if ev.kind in ("remove", "assistant_remove"):
        params["old"] = mention(ev.old_value)
    if ev.kind in ("channel_link",):
        params["new"] = f"<#{ev.new_value}>"
    if ev.kind in ("channel_unlink",):
        params["old"] = f"<#{ev.old_value}>"
    code = f"ev_{ev.kind}"
    if ev.kind == "revert" and ev.field in RESOURCES:
        what = render("ev_resource", lang, field=resource_name(ev.field, lang), old=ev.old_value, new=ev.new_value)
        text = f"{render(code, lang, **params)}: {what}"
    elif ev.kind == "revert":
        text = render(code, lang, **params)
    elif ev.kind == "resource":
        text = render(code, lang, field=resource_name(ev.field or "", lang) if ev.field in RESOURCES else ev.field,
                      old=ev.old_value, new=ev.new_value)
    else:
        try:
            text = render(code, lang, **params)
        except KeyError:
            text = render("ev_other", lang, kind=ev.kind)
    if ev.reverted_by_event_id is not None:
        text += render("ev_reverted_mark", lang)
    who = names.get(ev.investigator_id) if names and ev.investigator_id is not None else None
    stamp = ev.created_at[:16].replace("T", " ")
    parts = [f"#{ev.id}", stamp, mention(ev.actor_user_id)]
    if who:
        parts.append(f"**{who}**")
    line = " · ".join(parts) + f" · {text}"
    if ev.reason:
        line += f" — _{ev.reason}_"
    return line


def history_text(events: Sequence[Event], lang: str, names: Optional[Mapping[int, str]] = None,
                 limit: int = 4000) -> str:
    if not events:
        return render("kp_no_history", lang)
    out: List[str] = []
    size = 0
    for ev in events:
        line = event_line(ev, lang, names)
        if size + len(line) + 1 > limit:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def detail_screen(entry: RosterEntry, recent: Sequence[Event], lang: str) -> Screen:
    inv = entry.investigator
    resources = []
    for field in RESOURCES:
        value, maximum = resource_value(inv, field), resource_max(inv, field)
        v = "—" if value is None else str(value)
        resources.append(f"**{resource_name(field, lang)}** {v}" + (f" / {maximum}" if maximum is not None else ""))
    conditions = ", ".join(f"`{c.label}`" for c in entry.conditions) or render("kp_none", lang)
    title = inv.name + (f" — {inv.occupation}" if inv.occupation else "")
    fields = (
        (render("kp_detail_resources", lang), "\n".join(resources)),
        (render("kp_detail_conditions", lang), conditions[:1024]),
        (render("kp_recent", lang), history_text(recent, lang, limit=1024)),
    )
    return Screen(title[:256], render("kp_detail_player", lang, user=mention(entry.participant.user_id)), fields)


def change_lines(changes: Sequence[ResourceChange], lang: str) -> List[str]:
    out = []
    for c in changes:
        maximum = f" / {c.maximum}" if c.maximum is not None and c.field != "luck" else ""
        line = render("kp_change_line", lang, field=resource_name(c.field, lang),
                      old="—" if c.old is None else c.old, new=c.new, max=maximum)
        if c.new == 0:
            line += render("kp_reaches_zero", lang)
        out.append(f"• {line}")
    return out


def parse_adjust(raw: Mapping[str, str], current: Mapping[str, Optional[int]]) -> Dict[str, int]:
    """Turn Keeper input into absolute values: '7' sets, '+2' / '-3' change, blank keeps.

    Range checks against maxima happen in the service.
    """
    out: Dict[str, int] = {}
    for field, text in raw.items():
        text = (text or "").strip().replace(" ", "")
        if not text:
            continue
        sign = text[0] if text[0] in "+-" else ""
        digits = text[1:] if sign else text
        if not digits.isdigit() or len(digits) > 3:
            raise ValidationError(Msg("adjust_invalid", field=field.upper()))
        n = int(digits)
        if sign:
            base = current.get(field)
            if base is None:
                raise ValidationError(Msg("resource_unknown_max", field=field.upper()))
            n = base + n if sign == "+" else base - n
        out[field] = n
    return out


def parse_user_id(text: object) -> str:
    """Accept '<@123>', '<@!123>' or '123'."""
    s = str(text or "").strip()
    if s.startswith("<@") and s.endswith(">"):
        s = s[2:-1].lstrip("!")
    if not s.isdigit():
        raise ValidationError(Msg("user_mention_invalid"))
    return s
