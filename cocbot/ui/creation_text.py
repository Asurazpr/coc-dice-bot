"""Discord-independent text for the occupation creation steps (unit-testable).

Every function takes ``names`` (skill key -> display name in the player's
language) and ``lang`` ('en', 'zh', 'both'); English output is unchanged from
earlier versions.
"""
from __future__ import annotations

import re
from typing import List, Mapping, Optional, Sequence, Tuple

from cocbot.i18n import localize, render
from cocbot.occupations.catalogue import Occupation, SlotDef
from cocbot.occupations.rules import AllocationChange, BatchResult, Budget, CreationCheck, SkillRow

STEP_CODES = {
    "characteristics": "step_characteristics",
    "occupation": "step_occupation",
    "choices": "step_choices",
    "allocation": "step_allocation",
    "review": "step_review",
}
STEP_TITLES = {k: render(v, "en") for k, v in STEP_CODES.items()}   # English, kept for compatibility
ALLOC_PAGE = 5          # Discord modals hold at most 5 inputs
FIELD_LIMIT = 1024


def _n(names: Mapping[str, str], key: str) -> str:
    return names.get(key, key)


def step_title(step: str, lang: str = "en") -> str:
    return render(STEP_CODES[step], lang)


def occupation_label(occ: Occupation, lang: str = "en") -> str:
    from cocbot.i18n import occupation_name
    tags = []
    if occ.era in ("classic", "modern"):
        tags.append(render(f"tag_{occ.era}", lang))
    if occ.is_homebrew:
        tags.append(render("tag_homebrew", lang))
    suffix = f" [{', '.join(tags)}]" if tags else ""
    name = occ.display_name if lang == "en" else occupation_name(occ, lang)
    return f"{name}{suffix}"


def slot_text(slot: SlotDef, names: Mapping[str, str], lang: str = "en") -> str:
    if slot.kind == "fixed":
        return _n(names, slot.options[0])
    if slot.kind == "any":
        return render("slot_any_1", lang) if slot.count == 1 else render("slot_any_n", lang, n=slot.count)
    opts = [_n(names, k) for k in slot.options]
    sep = "、" if lang == "zh" else ", "
    shown = sep.join(opts[:6]) + (f" … (+{len(opts) - 6})" if len(opts) > 6 else "")
    return render("slot_choose", lang, n=slot.count, options=shown)


def occupation_detail(occ: Occupation, names: Mapping[str, str], lang: str = "en") -> List[Tuple[str, str]]:
    """(field name, value) pairs for an occupation summary embed."""
    fields = [
        (render("f_credit", lang), f"{occ.credit_min}–{occ.credit_max}"),
        (render("f_points", lang), occ.formula_text()),
        (render("f_skills", lang), _clip("\n".join("• " + slot_text(s, names, lang) for s in occ.slots)
                                         + "\n• " + names.get("credit_rating", "Credit Rating"))),
    ]
    notes = []
    if not occ.verified and not occ.is_homebrew:
        notes.append(render("note_unverified", lang))
    if occ.is_homebrew:
        notes.append(render("note_homebrew", lang))
    notes += [f"⚠ {i}" for i in occ.issues[:4] if not i.startswith("homebrew")]
    if occ.keeper_note_zh:
        notes.append(render("note_keeper", lang, text=occ.keeper_note_zh))
    if notes:
        fields.append((render("f_notes", lang), _clip("\n".join(notes))))
    return fields


def choices_text(occ: Occupation, picks: Mapping[Tuple[int, int], str], names: Mapping[str, str],
                 current: Optional[Tuple[int, int]] = None, lang: str = "en") -> str:
    lines = []
    for i, s in enumerate(occ.slots):
        if s.kind == "fixed":
            lines.append(f"✔ {_n(names, s.options[0])}")
            continue
        for p in range(s.count):
            k = picks.get((i, p))
            marker = "👉" if (i, p) == current else ("✔" if k else "○")
            label = slot_text(s, names, lang) if s.count == 1 else f"{slot_text(s, names, lang)} ({p + 1}/{s.count})"
            lines.append(f"{marker} {_n(names, k) if k else '—'}  ·  _{label}_")
    lines.append("✔ " + render("credit_rating_always", lang))
    return _clip("\n".join(lines), 4000)


def budget_explanation(b: Budget, lang: str = "en") -> str:
    if b.total is None:
        return render("enter_characteristics", lang, stats=", ".join(b.missing))
    parts = []
    for t in b.terms:
        note = ""
        if t.alternatives:
            alts = "/".join(t.alternatives)
            if lang == "zh":
                note = f"（从 {alts} 中选择）" if t.chosen else f"（建议：{alts} 中较高者）"
            else:
                note = f" (chosen from {alts})" if t.chosen else f" (suggested: higher of {alts})"
        parts.append(f"{t.stat}×{t.mult}{note} = {t.value * t.mult}")
    return " + ".join(parts)


def budget_line(check: CreationCheck, lang: str = "en") -> str:
    def b(total, spent, code):
        label = render(code, lang)
        if total is None:
            return f"**{label}:** —"
        return f"**{label}:** " + render("budget_used", lang, spent=spent, total=total, left=total - spent)
    return (b(check.occupation_budget.total, check.occupation_spent, "budget_occ") + "\n"
            + b(check.interest_budget.total, check.interest_spent, "budget_int") + "\n"
            + f"_{budget_explanation(check.occupation_budget, lang)}; "
              f"{budget_explanation(check.interest_budget, lang)}_")


def rows_table(rows: Sequence[SkillRow], names: Mapping[str, str], lang: str = "en") -> str:
    if lang != "en":
        # CJK text is double-width in code blocks, so use a list instead of columns.
        occ_l, int_l = ("职", "兴") if lang == "zh" else ("Occ", "Int")
        lines = [f"**{_n(names, r.key)}** {r.base} + {occ_l}{r.occupation_points} + {int_l}{r.interest_points}"
                 f" = **{r.total}**" for r in rows]
        return _clip("\n".join(lines) or "—")
    lines = [f"{'Skill':<20}{'Base':>5}{'Occ':>5}{'Int':>5}{'=':>5}"]
    for r in rows:
        nm = _n(names, r.key)
        nm = nm if len(nm) <= 19 else nm[:18] + "…"
        lines.append(f"{nm:<20}{r.base:>5}{r.occupation_points or '·':>5}{r.interest_points or '·':>5}{r.total:>5}")
    return "```\n" + "\n".join(lines) + "\n```"


def allocation_pages(check: CreationCheck) -> Tuple[List[List[SkillRow]], List[SkillRow]]:
    """Occupation skills in pages of 5 (one modal each), plus interest-only rows."""
    occ_rows = [r for r in check.rows if r.is_occupation_skill]
    other = [r for r in check.rows if not r.is_occupation_skill and (r.interest_points or r.occupation_points)]
    pages = [occ_rows[i:i + ALLOC_PAGE] for i in range(0, len(occ_rows), ALLOC_PAGE)] or [[]]
    return pages, other


def review_text(check: CreationCheck, lang: str = "en", names_by_lang=None) -> str:
    parts = []
    if check.errors:
        parts.append(render("review_must_fix", lang) + "\n"
                     + "\n".join(f"❌ {localize(e, lang, names_by_lang)}" for e in check.errors))
    else:
        parts.append(render("review_ok", lang))
    if check.warnings:
        parts.append(render("review_notes", lang) + "\n"
                     + "\n".join(f"• {localize(w, lang, names_by_lang)}" for w in check.warnings))
    return _clip("\n\n".join(parts), 4000)


# --- batch allocation feedback ----------------------------------------------------------------

def _change_lines(ch: AllocationChange, names: Mapping[str, str], lang: str) -> List[str]:
    out = []
    for idx, code in ((0, "budget_occ"), (1, "budget_int")):
        old, new = ch.old[idx], ch.new[idx]
        if old != new:
            delta = new - old
            out.append(render("change_line", lang, skill=_n(names, ch.key), budget=render(code, lang), old=old,
                              new=new, delta=f"{delta:+d}"))
    return out


def batch_result_text(result: BatchResult, names: Mapping[str, str], lang: str = "en",
                      names_by_lang=None) -> str:
    """Message after a batch: what changed and what is left, or why nothing was saved."""
    if not result.ok:
        return _clip(render("result_nothing_saved", lang) + "\n"
                     + "\n".join(f"❌ {localize(e, lang, names_by_lang)}" for e in result.errors), 2000)
    left_o, left_i = result.remaining_after
    remaining = render("remaining_line", lang, occ="—" if left_o is None else left_o,
                       int="—" if left_i is None else left_i)
    if not result.changes:
        return render("result_nothing_changed", lang) + "\n" + remaining
    lines = [line for ch in result.changes for line in _change_lines(ch, names, lang)]
    return _clip(render("result_saved", lang) + "\n" + "\n".join(lines) + "\n" + remaining, 2000)


_INT_RE = re.compile(r"^[+-]?\d{1,3}$")


def parse_points(raw: str, mode: str, current: int) -> Optional[int]:
    """Form field text -> batch value, or None for "no change".

    * set mode: the field is the new total (blank = 0);
    * add mode: the field is a signed change such as +10 or -5 (blank = no change).
    Raises ValueError (with the offending text) if it is not a whole number.
    """
    text = (raw or "").strip().replace(" ", "")
    if mode == "set":
        if text == "":
            value = 0
        elif _INT_RE.match(text):
            value = int(text)
        else:
            raise ValueError(text)
        return None if value == current else value
    if text in ("", "+0", "-0", "0"):
        return None
    if not _INT_RE.match(text):
        raise ValueError(text)
    return int(text)


def _clip(text: str, limit: int = FIELD_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
