"""Text for the Excel import preview, re-import diff and import status (no Discord dependency)."""
from __future__ import annotations

from typing import List, Mapping, Optional, Sequence

from cocbot.i18n import localize, render
from cocbot.imports.model import ERROR, INFO, WARNING, Issue
from cocbot.imports.service import Change, ImportPreview, ImportRecord, UpdatePlan
from cocbot.mechanics.derived import CHARACTERISTICS
from cocbot.ui.keeper_text import Screen

ICONS = {ERROR: "⛔", WARNING: "⚠️", INFO: "ℹ️"}
MAX_ISSUES = 12


def _clip(lines: Sequence[str], limit: int = 1024) -> str:
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > limit - 20:
            out.append(f"… (+{len(lines) - i})")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out) or "—"


def issue_lines(issues: Sequence[Issue], lang: str, names: Optional[Mapping] = None) -> List[str]:
    ordered = sorted(issues, key=lambda i: (ERROR, WARNING, INFO).index(i.level))
    lines = [f"{ICONS[i.level]} {localize(i.msg, lang, names)}" for i in ordered[:MAX_ISSUES]]
    if len(ordered) > MAX_ISSUES:
        lines.append(render("imp_more_issues", lang, n=len(ordered) - MAX_ISSUES))
    return lines


def status_lines(preview: ImportPreview, lang: str) -> List[str]:
    s = preview.sheet
    parsed = render("imp_status_parsed_err" if s.errors else "imp_status_parsed_ok", lang,
                    warnings=len(s.warnings), errors=len(s.errors))
    complete = (render("imp_status_complete", lang) if preview.complete
                else render("imp_status_incomplete", lang, fields=", ".join(s.missing_characteristics)))
    return [parsed, complete, render("imp_status_unverified", lang)]


def preview_screen(p: ImportPreview, skill_names: Mapping[str, str], lang: str,
                   nbl: Optional[Mapping] = None) -> Screen:
    s = p.sheet
    d = p.derived
    bio = [render("imp_bio_line", lang, label=render(f"imp_f_{f}", lang), value=v)
           for f, v in (("occupation", s.occupation), ("age", s.age), ("residence", s.residence),
                        ("birthplace", s.birthplace)) if v not in (None, "")]
    if p.occupation_match:
        bio.append(render("imp_occupation_match", lang, name=p.occupation_match))
    chars = " · ".join(f"{k} {'—' if s.characteristics.get(k) is None else s.characteristics[k]}"
                       for k in CHARACTERISTICS) + f" · Luck {'—' if s.luck is None else s.luck}"

    def res(cur, mx):
        return f"{'—' if mx is None else (mx if cur is None else cur)}/{'—' if mx is None else mx}"
    derived = (f"HP {res(s.hp, d.hp_max)} · MP {res(s.mp, d.mp_max)} · SAN "
               f"{s.san if s.san is not None else (d.san_start if d.san_start is not None else '—')}"
               f" · MOV {d.mov if d.mov is not None else '—'} · DB {d.damage_bonus or '—'}"
               f" · Build {d.build if d.build is not None else '—'}")
    skills = sorted(p.skills.items(), key=lambda kv: -kv[1])
    skill_text = ", ".join(f"{skill_names.get(k, k)} {v}" for k, v in skills) or render("imp_no_skills", lang)
    kept = []
    for u in s.notes.get("unmapped_skills", []):
        kept.append(f"• {u['label']}" + (f" {u['value']}" if u.get("value") is not None else ""))
    for key, entries in s.notes.get("specializations", {}).items():
        kept.append("• " + skill_names.get(key, key) + ": " +
                    ", ".join(f"{e['specialization']} {e['value']}" for e in entries))
    other = [k for k in ("player", "era", "sex", "backstory", "weapons", "possessions", "mythos", "background",
                         "investigator_notes", "cash", "spending_level") if s.notes.get(k)]
    if other:
        kept.append(render("imp_kept_notes", lang, items=", ".join(render(f"imp_n_{k}", lang) for k in other)))
    fields = [(render("imp_h_status", lang), "\n".join(status_lines(p, lang))),
              (render("imp_h_bio", lang), "\n".join(bio) or "—"),
              (render("imp_h_characteristics", lang), chars),
              (render("imp_h_derived", lang), derived),
              (render("imp_h_skills", lang, n=len(skills)), skill_text[:1024])]
    if p.advisory:
        adv = [render("imp_advisory_line", lang, budget=render(f"imp_budget_{b}", lang), spent=spent,
                      total="?" if total is None else total)
               for b, (spent, total) in p.advisory.items()]
        fields.append((render("imp_h_advisory", lang), "\n".join(adv)))
    if kept:
        fields.append((render("imp_h_kept", lang), _clip(kept)))
    if s.issues:
        fields.append((render("imp_h_issues", lang, n=len(s.issues)), _clip(issue_lines(s.issues, lang, nbl))))
    if p.candidates:
        fields.append((render("imp_h_existing", lang), render("imp_existing", lang,
                                                             investigators=", ".join(c.name for c in p.candidates))))
    title = render("imp_title", lang, name=s.name or "?")
    return Screen(title[:256], render("imp_template", lang, label=p.template_label),
                  tuple((n[:256], v[:1024]) for n, v in fields), render("imp_footer", lang)[:2048])


def change_line(c: Change, skill_names: Mapping[str, str], lang: str) -> str:
    def v(x):
        return "—" if x in (None, "") else x
    if c.kind.startswith("skill_"):
        name = skill_names.get(c.name, c.name)
    elif c.kind == "field":
        name = render(f"imp_f_{c.name}", lang)
    else:
        name = c.name.upper()
    mark = {"skill_added": "＋", "skill_removed": "－"}.get(c.kind, "•")
    return f"{mark} {name}: {v(c.old)} → {v(c.new)}"


def diff_screen(plan: UpdatePlan, skill_names: Mapping[str, str], lang: str) -> Screen:
    lines = [change_line(c, skill_names, lang) for c in plan.changes] or [render("imp_no_changes", lang)]
    notes = []
    if plan.blocked is not None:
        notes.append("⛔ " + localize(plan.blocked, lang))
    if plan.drops_validation:
        notes.append("⚠️ " + render("imp_drops_validation", lang))
    if plan.campaigns:
        notes.append("🎭 " + render("imp_campaigns_untouched", lang, campaigns=", ".join(plan.campaigns)))
    notes.append(render("imp_status_unverified", lang))
    fields = ((render("imp_h_changes", lang, n=len(plan.changes)), _clip(lines)),
              (render("imp_h_notes", lang), "\n".join(notes)))
    return Screen(render("imp_diff_title", lang, name=plan.investigator.name)[:256],
                  render("imp_diff_intro", lang), fields)


def record_lines(rec: Optional[ImportRecord], lang: str) -> List[str]:
    """Import status for sheets (owner) and the Keeper Console."""
    if rec is None:
        return []
    warnings = [i for i in rec.issues if i.get("level") == WARNING]
    lines = [render("imp_record", lang, date=rec.created_at[:10], template=rec.template),
             render("imp_status_unverified", lang)]
    if rec.completeness != "complete":
        lines.append(render("imp_record_incomplete", lang))
    if warnings:
        lines.append(render("imp_record_warnings", lang, n=len(warnings)))
        for w in warnings[:5]:
            try:
                lines.append(f"⚠️ {render(w['code'], lang, **w.get('params', {}))}")
            except (KeyError, IndexError, ValueError):
                continue
    unmapped = rec.notes.get("unmapped_skills") or []
    if unmapped:
        lines.append(render("imp_record_unmapped", lang, items=", ".join(
            f"{u['label']}" + (f" {u['value']}" if u.get("value") is not None else "") for u in unmapped[:8])))
    return lines


__all__: List[str] = ["diff_screen", "issue_lines", "preview_screen", "record_lines", "status_lines"]
