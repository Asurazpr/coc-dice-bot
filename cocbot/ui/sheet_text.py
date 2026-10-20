"""Discord-independent text layout for investigator sheets and lists.

Kept free of ``discord`` imports so it can be unit-tested anywhere; the
embed builders in ``sheet_embed`` only wrap these strings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from cocbot.characters.models import STATUS_ARCHIVED, STATUS_DRAFT, Investigator
from cocbot.mechanics.derived import CHARACTERISTICS

STATUS_BADGE = {STATUS_DRAFT: "📝 Draft", "ready": "✅ Ready", STATUS_ARCHIVED: "🗄️ Archived"}


def _v(x: Optional[object]) -> str:
    return "—" if x is None else str(x)


@dataclass(frozen=True)
class SheetText:
    title: str
    description: str
    fields: List[Tuple[str, str, bool]]  # (name, value, inline)
    footer: str


def characteristic_line(inv: Investigator) -> str:
    """`STR 50 (25/10)` style entries showing full/half/fifth values."""
    parts = []
    for k in CHARACTERISTICS:
        v = inv.characteristics.get(k)
        parts.append(f"**{k}** {_v(v)}" + (f" ({v // 2}/{v // 5})" if v is not None else ""))
    return "\n".join(" · ".join(parts[i:i + 4]) for i in range(0, len(parts), 4))


def build_sheet_text(
    inv: Investigator,
    skill_names: Mapping[str, str],
    active_here: bool = False,
) -> SheetText:
    d = inv.derived()
    bio_bits = [b for b in (
        inv.occupation,
        f"age {inv.age}" if inv.age is not None else None,
        inv.pronouns,
        f"from {inv.birthplace}" if inv.birthplace else None,
        f"lives in {inv.residence}" if inv.residence else None,
    ) if b]
    desc = [STATUS_BADGE.get(inv.status, inv.status) + ("  ·  ⭐ Active here" if active_here else "")]
    if bio_bits:
        desc.append(" · ".join(bio_bits))

    fields: List[Tuple[str, str, bool]] = [
        ("Characteristics", characteristic_line(inv), False),
        (
            "Status",
            f"**HP** {_v(inv.hp)}/{_v(d.hp_max)} · **MP** {_v(inv.mp)}/{_v(d.mp_max)} · "
            f"**SAN** {_v(inv.san)}/{d.san_max} · **Luck** {_v(inv.luck)}",
            False,
        ),
        (
            "Derived",
            f"**DB** {_v(d.damage_bonus)} · **Build** {_v(d.build)} · **MOV** {_v(d.mov)} · "
            f"**Dodge** {_v(inv.skills.get('dodge', d.dodge_base))}",
            False,
        ),
    ]
    if inv.skills:
        items = sorted(inv.skills.items(), key=lambda kv: skill_names.get(kv[0], kv[0]).lower())
        lines = [f"{skill_names.get(k, k)} **{v}**" for k, v in items]
        fields.append((f"Trained skills ({len(lines)})", _chunk(lines, 1000), False))
    else:
        fields.append(("Trained skills", "None yet — skills use base values. Add with `/character skill`.", False))

    if inv.missing_characteristics and not inv.is_archived:
        fields.append(("To finish", "Missing: " + ", ".join(inv.missing_characteristics), False))
    if inv.notes:
        fields.append(("Notes", inv.notes[:1000], False))

    return SheetText(
        title=inv.name,
        description="\n".join(desc),
        fields=fields,
        footer=f"Investigator #{inv.id} · CoC 7e",
    )


def _chunk(lines: Sequence[str], limit: int) -> str:
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 3 > limit:
            out.append(f"… +{len(lines) - i} more")
            break
        out.append(line)
        size += len(line) + 3
    return " · ".join(out)


def build_list_text(invs: Sequence[Investigator], active_id: Optional[int]) -> str:
    if not invs:
        return "You have no investigators yet. Create one with `/character create`."
    lines = []
    for inv in invs:
        star = "⭐ " if inv.id == active_id else ""
        occ = f" — {inv.occupation}" if inv.occupation else ""
        lines.append(f"{star}**{inv.name}**{occ}  `#{inv.id}` · {STATUS_BADGE.get(inv.status, inv.status)}")
    return "\n".join(lines)
