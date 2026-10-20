"""CoC 7e occupation skill-point rules (pure functions, no database).

Standard rules (Keeper Rulebook ch. 3):
* occupation skill points come from the occupation's formula;
* personal interest points = INT × 2;
* occupation points may be spent only on the occupation's skills and Credit Rating;
* personal interest points may be spent on any skill except Cthulhu Mythos;
* Credit Rating must end within the occupation's range;
* no skill may exceed 99; Cthulhu Mythos stays at 0 during creation.

Choices where the rules leave room are documented in docs/OCCUPATIONS.md §6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

from cocbot.occupations.catalogue import NOT_SELECTABLE, Occupation, SlotDef

SKILL_CREATION_MAX = 99
CREDIT_RATING = "credit_rating"
CTHULHU_MYTHOS = "cthulhu_mythos"


@dataclass(frozen=True)
class Budget:
    total: Optional[int]          # None when a needed characteristic is missing
    explanation: str


def alternative_stats(occ: Occupation) -> Tuple[str, ...]:
    """Characteristics the player may choose between (e.g. STR or DEX), or () if none."""
    for term in occ.skill_points:
        if len(term["stats"]) > 1:
            return tuple(term["stats"])
    return ()


def suggested_stat(occ: Occupation, chars: Mapping[str, Optional[int]]) -> Optional[str]:
    """The alternative giving the most points (the default suggestion)."""
    alts = [(s, chars.get(s)) for s in alternative_stats(occ)]
    known = [(s, v) for s, v in alts if v is not None]
    return max(known, key=lambda kv: kv[1])[0] if known and len(known) == len(alts) else None


def occupation_budget(occ: Occupation, chars: Mapping[str, Optional[int]],
                      chosen_stat: Optional[str] = None) -> Budget:
    """Sum of formula terms. For a term with alternatives the player's choice is
    used; without a (valid) choice the higher value is suggested and used."""
    total, parts = 0, []
    for term in occ.skill_points:
        values = {s: chars.get(s) for s in term["stats"]}
        known = {s: v for s, v in values.items() if v is not None}
        if len(known) < len(values):
            missing = [s for s, v in values.items() if v is None]
            return Budget(None, f"needs {', '.join(missing)}")
        if len(values) > 1 and chosen_stat in values:
            stat, value = chosen_stat, known[chosen_stat]
            note = f" (chosen from {'/'.join(term['stats'])})"
        else:
            stat, value = max(known.items(), key=lambda kv: kv[1])
            note = f" (suggested: higher of {'/'.join(term['stats'])})" if len(values) > 1 else ""
        total += value * term["mult"]
        parts.append(f"{stat}×{term['mult']}{note} = {value * term['mult']}")
    return Budget(total, " + ".join(parts))


def interest_budget(chars: Mapping[str, Optional[int]]) -> Budget:
    v = chars.get("INT")
    if v is None:
        return Budget(None, "needs INT")
    return Budget(v * 2, f"INT×2 = {v * 2}")


# --- picks -----------------------------------------------------------------------

def slot_options(slot: SlotDef, all_skills: Sequence[str]) -> List[str]:
    if slot.kind == "any":
        return [k for k in all_skills if k not in NOT_SELECTABLE]
    return [k for k in slot.options if k not in NOT_SELECTABLE]


def pick_positions(occ: Occupation) -> List[Tuple[int, int]]:
    """(slot_index, pick_index) for every choice the player must make."""
    return [(i, p) for i, s in enumerate(occ.slots) if s.kind != "fixed" for p in range(s.count)]


def occupation_skills(occ: Occupation, picks: Mapping[Tuple[int, int], str]) -> List[str]:
    """Fixed skills, chosen skills (in slot order) and Credit Rating."""
    out: List[str] = []
    for i, s in enumerate(occ.slots):
        if s.kind == "fixed":
            out.append(s.options[0])
        else:
            for p in range(s.count):
                k = picks.get((i, p))
                if k:
                    out.append(k)
    out.append(CREDIT_RATING)
    return list(dict.fromkeys(out))


def validate_pick(occ: Occupation, picks: Mapping[Tuple[int, int], str], slot_index: int, pick_index: int,
                  skill_key: str, all_skills: Sequence[str]) -> Optional[str]:
    """Error message, or None if the pick is allowed."""
    if not 0 <= slot_index < len(occ.slots):
        return "That occupation has no such skill choice."
    slot = occ.slots[slot_index]
    if slot.kind == "fixed" or not 0 <= pick_index < slot.count:
        return "That occupation has no such skill choice."
    if skill_key not in slot_options(slot, all_skills):
        return "That skill is not one of the options for this choice."
    taken = set(occ.fixed_skills) | {k for pos, k in picks.items() if pos != (slot_index, pick_index)}
    if skill_key in taken:
        return "That skill is already one of your occupation skills."
    return None


# --- allocation summary ------------------------------------------------------------

@dataclass(frozen=True)
class SkillRow:
    key: str
    base: int
    occupation_points: int
    interest_points: int
    is_occupation_skill: bool

    @property
    def total(self) -> int:
        return self.base + self.occupation_points + self.interest_points


@dataclass
class CreationCheck:
    occupation_budget: Budget
    interest_budget: Budget
    occupation_spent: int
    interest_spent: int
    rows: List[SkillRow]
    picks_missing: int
    errors: List[str] = field(default_factory=list)      # block finishing
    warnings: List[str] = field(default_factory=list)    # shown, do not block

    @property
    def occupation_remaining(self) -> Optional[int]:
        t = self.occupation_budget.total
        return None if t is None else t - self.occupation_spent

    @property
    def interest_remaining(self) -> Optional[int]:
        t = self.interest_budget.total
        return None if t is None else t - self.interest_spent

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def unspent(self) -> Tuple[int, int]:
        """(occupation, interest) points left over; finishing with any requires confirmation."""
        return max(0, self.occupation_remaining or 0), max(0, self.interest_remaining or 0)


def check_creation(
    occ: Occupation,
    chars: Mapping[str, Optional[int]],
    picks: Mapping[Tuple[int, int], str],
    points: Mapping[str, Tuple[int, int]],          # skill -> (occupation, interest)
    bases: Mapping[str, Optional[int]],             # skill -> base (derived bases resolved)
    manual_values: Mapping[str, int] = None,  # type: ignore[assignment]
    skill_names: Optional[Mapping[str, str]] = None,
    chosen_stat: Optional[str] = None,
) -> CreationCheck:
    """Validate a whole occupation-based creation.

    Every trained value must be explained by base + points: a value entered by
    hand (``manual_values``) that differs from the skill's base is an error
    until the player reconciles it (convert it into points, or discard it).
    """
    names = skill_names or {}

    def nm(k: str) -> str:
        return names.get(k, k)

    ob, ib = occupation_budget(occ, chars, chosen_stat), interest_budget(chars)
    occ_skills = occupation_skills(occ, picks)
    missing = len(pick_positions(occ)) - sum(1 for pos in pick_positions(occ) if picks.get(pos))

    keys = list(dict.fromkeys(list(occ_skills) + [k for k in points]))
    rows: List[SkillRow] = []
    for k in keys:
        o, i = points.get(k, (0, 0))
        rows.append(SkillRow(k, int(bases.get(k) or 0), int(o), int(i), k in occ_skills))
    spent_o = sum(r.occupation_points for r in rows)
    spent_i = sum(r.interest_points for r in rows)

    c = CreationCheck(ob, ib, spent_o, spent_i, rows, missing)
    if ob.total is None or ib.total is None:
        c.errors.append("Enter all characteristics first (" + "; ".join(
            b.explanation for b in (ob, ib) if b.total is None) + ").")
    if missing:
        c.errors.append(f"{missing} occupation skill choice(s) still to make.")
    if ob.total is not None and spent_o > ob.total:
        c.errors.append(f"Occupation points overspent by {spent_o - ob.total}.")
    if ib.total is not None and spent_i > ib.total:
        c.errors.append(f"Personal interest points overspent by {spent_i - ib.total}.")
    for r in rows:
        if r.occupation_points and not r.is_occupation_skill:
            c.errors.append(f"{nm(r.key)} is not an occupation skill but has occupation points.")
        if r.key == CTHULHU_MYTHOS and (r.occupation_points or r.interest_points):
            c.errors.append("Cthulhu Mythos cannot be raised during creation.")
        if r.total > SKILL_CREATION_MAX:
            c.errors.append(f"{nm(r.key)} would be {r.total}; the maximum is {SKILL_CREATION_MAX}.")
    cr = next(r for r in rows if r.key == CREDIT_RATING)
    if not occ.credit_min <= cr.total <= occ.credit_max:
        c.errors.append(f"Credit Rating is {cr.total}; this occupation requires {occ.credit_min}-{occ.credit_max}.")

    if (c.occupation_remaining or 0) > 0:
        c.warnings.append(f"{c.occupation_remaining} occupation points unspent (they are not carried over).")
    if (c.interest_remaining or 0) > 0:
        c.warnings.append(f"{c.interest_remaining} personal interest points unspent.")
    for key, value in sorted((manual_values or {}).items()):
        base = bases.get(key)
        if key == CTHULHU_MYTHOS and value:
            c.errors.append(f"Cthulhu Mythos is {value} (entered by hand); it must be 0 at creation. Discard it.")
        elif base is None or value != base:
            c.errors.append(f"{nm(key)} is {value}, entered by hand rather than paid for with points "
                            f"(base {base if base is not None else '?'}). Reconcile it: convert to points or discard.")
    if occ.issues:
        c.warnings.append("Occupation data needs Keeper review: " + "; ".join(occ.issues[:3]))
    c.warnings.append("Age adjustments (rulebook ch. 3) are not applied automatically; check them with your Keeper.")
    return c


def allocation_error(
    occ: Occupation,
    picks: Mapping[Tuple[int, int], str],
    skill_key: str,
    new_occ: int,
    new_int: int,
    base: int,
    budgets: Tuple[Optional[int], Optional[int]],
    spent_elsewhere: Tuple[int, int],
) -> Optional[str]:
    """Validate one skill's allocation at the moment it is entered."""
    if new_occ < 0 or new_int < 0:
        return "Points cannot be negative."
    if skill_key == CTHULHU_MYTHOS and (new_occ or new_int):
        return "Cthulhu Mythos cannot be raised during creation."
    if new_occ and skill_key not in occupation_skills(occ, picks):
        return "Occupation points can only go to this occupation's skills and Credit Rating."
    if base + new_occ + new_int > SKILL_CREATION_MAX:
        return f"That would make the skill {base + new_occ + new_int}; the maximum at creation is {SKILL_CREATION_MAX}."
    ob, ib = budgets
    if ob is None or ib is None:
        return "Enter all characteristics first so the point budgets can be calculated."
    if spent_elsewhere[0] + new_occ > ob:
        return f"Only {ob - spent_elsewhere[0]} occupation points are left."
    if spent_elsewhere[1] + new_int > ib:
        return f"Only {ib - spent_elsewhere[1]} personal interest points are left."
    if skill_key == CREDIT_RATING and base + new_occ + new_int > occ.credit_max:
        return f"Credit Rating for this occupation is at most {occ.credit_max}."
    return None
