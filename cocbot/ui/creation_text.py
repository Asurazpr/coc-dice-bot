"""Discord-independent text for the occupation creation steps (unit-testable)."""
from __future__ import annotations

from typing import List, Mapping, Optional, Sequence, Tuple

from cocbot.occupations.catalogue import Occupation, SlotDef
from cocbot.occupations.rules import CreationCheck, SkillRow

STEP_TITLES = {
    "characteristics": "1 · Characteristics",
    "occupation": "2 · Occupation",
    "choices": "3 · Occupation skill choices",
    "allocation": "4 · Skill points",
    "review": "5 · Review",
}
ALLOC_PAGE = 5          # Discord modals hold at most 5 inputs
FIELD_LIMIT = 1024


def _n(names: Mapping[str, str], key: str) -> str:
    return names.get(key, key)


def occupation_label(occ: Occupation) -> str:
    tags = []
    if occ.era:
        tags.append(occ.era)
    if occ.is_homebrew:
        tags.append("homebrew")
    suffix = f" [{', '.join(tags)}]" if tags else ""
    return f"{occ.display_name}{suffix}"


def slot_text(slot: SlotDef, names: Mapping[str, str]) -> str:
    if slot.kind == "fixed":
        return _n(names, slot.options[0])
    if slot.kind == "any":
        return f"any {slot.count} other skill{'s' if slot.count > 1 else ''}"
    opts = [_n(names, k) for k in slot.options]
    shown = ", ".join(opts[:6]) + (f" … (+{len(opts) - 6})" if len(opts) > 6 else "")
    return f"{slot.count} of: {shown}"


def occupation_detail(occ: Occupation, names: Mapping[str, str]) -> List[Tuple[str, str]]:
    """(field name, value) pairs for an occupation summary embed."""
    fields = [
        ("Credit Rating", f"{occ.credit_min}–{occ.credit_max}"),
        ("Occupation skill points", occ.formula_text()),
        ("Occupation skills", _clip("\n".join("• " + slot_text(s, names) for s in occ.slots) + "\n• Credit Rating")),
    ]
    notes = []
    if not occ.verified and not occ.is_homebrew:
        notes.append("Imported from a community sheet; not yet verified against the rulebook.")
    if occ.is_homebrew:
        notes.append("Homebrew occupation: agree its skills and Credit Rating with your Keeper.")
    notes += [f"⚠ {i}" for i in occ.issues[:4] if not i.startswith("homebrew")]
    if occ.keeper_note_zh:
        notes.append(f"Keeper option: {occ.keeper_note_zh}")
    if notes:
        fields.append(("Notes", _clip("\n".join(notes))))
    return fields


def choices_text(occ: Occupation, picks: Mapping[Tuple[int, int], str], names: Mapping[str, str],
                 current: Optional[Tuple[int, int]] = None) -> str:
    lines = []
    for i, s in enumerate(occ.slots):
        if s.kind == "fixed":
            lines.append(f"✔ {_n(names, s.options[0])}")
            continue
        for p in range(s.count):
            k = picks.get((i, p))
            marker = "👉" if (i, p) == current else ("✔" if k else "○")
            label = slot_text(s, names) if s.count == 1 else f"{slot_text(s, names)} ({p + 1}/{s.count})"
            lines.append(f"{marker} {_n(names, k) if k else '—'}  ·  _{label}_")
    lines.append("✔ Credit Rating (always an occupation skill)")
    return _clip("\n".join(lines), 4000)


def budget_line(check: CreationCheck) -> str:
    def b(total, spent, label):
        if total is None:
            return f"**{label}:** —"
        return f"**{label}:** {spent}/{total} used · **{total - spent} left**"
    return (b(check.occupation_budget.total, check.occupation_spent, "Occupation") + "\n"
            + b(check.interest_budget.total, check.interest_spent, "Personal interest") + "\n"
            + f"_{check.occupation_budget.explanation}; {check.interest_budget.explanation}_")


def rows_table(rows: Sequence[SkillRow], names: Mapping[str, str]) -> str:
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


def review_text(check: CreationCheck) -> str:
    parts = []
    if check.errors:
        parts.append("**Must fix before finishing:**\n" + "\n".join(f"❌ {e}" for e in check.errors))
    else:
        parts.append("✅ Meets the occupation and skill-point rules checked by the bot.")
    if check.warnings:
        parts.append("**Notes:**\n" + "\n".join(f"• {w}" for w in check.warnings))
    return _clip("\n\n".join(parts), 4000)


def _clip(text: str, limit: int = FIELD_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
