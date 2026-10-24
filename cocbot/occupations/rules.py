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
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from cocbot.i18n import Msg
from cocbot.occupations.catalogue import NOT_SELECTABLE, Occupation, SlotDef

SKILL_CREATION_MAX = 99
CREDIT_RATING = "credit_rating"
CTHULHU_MYTHOS = "cthulhu_mythos"


@dataclass(frozen=True)
class BudgetTerm:
    stat: str
    mult: int
    value: int
    alternatives: Tuple[str, ...] = ()   # non-empty when the player could choose
    chosen: bool = False                 # True: player's choice; False: suggestion


@dataclass(frozen=True)
class Budget:
    total: Optional[int]                 # None when a needed characteristic is missing
    terms: Tuple[BudgetTerm, ...] = ()
    missing: Tuple[str, ...] = ()

    @property
    def explanation(self) -> str:
        """English summary (the UI renders localized text from ``terms``)."""
        if self.total is None:
            return f"needs {', '.join(self.missing)}"
        parts = []
        for t in self.terms:
            note = ""
            if t.alternatives:
                alts = "/".join(t.alternatives)
                note = f" (chosen from {alts})" if t.chosen else f" (suggested: higher of {alts})"
            parts.append(f"{t.stat}×{t.mult}{note} = {t.value * t.mult}")
        return " + ".join(parts)


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
    total, terms = 0, []
    for term in occ.skill_points:
        values = {s: chars.get(s) for s in term["stats"]}
        known = {s: v for s, v in values.items() if v is not None}
        if len(known) < len(values):
            return Budget(None, missing=tuple(s for s, v in values.items() if v is None))
        alts = tuple(term["stats"]) if len(values) > 1 else ()
        if alts and chosen_stat in values:
            stat, value, chosen = chosen_stat, known[chosen_stat], True
        else:
            (stat, value), chosen = max(known.items(), key=lambda kv: kv[1]), False
        total += value * term["mult"]
        terms.append(BudgetTerm(stat, term["mult"], value, alts, chosen))
    return Budget(total, tuple(terms))


def interest_budget(chars: Mapping[str, Optional[int]]) -> Budget:
    v = chars.get("INT")
    if v is None:
        return Budget(None, missing=("INT",))
    return Budget(v * 2, (BudgetTerm("INT", 2, v),))


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
        return Msg("pick_no_such")
    slot = occ.slots[slot_index]
    if slot.kind == "fixed" or not 0 <= pick_index < slot.count:
        return Msg("pick_no_such")
    if skill_key not in slot_options(slot, all_skills):
        return Msg("pick_not_option")
    taken = set(occ.fixed_skills) | {k for pos, k in picks.items() if pos != (slot_index, pick_index)}
    if skill_key in taken:
        return Msg("pick_taken")
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
    names = dict(skill_names or {})

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
        missing_stats = list(dict.fromkeys(ob.missing + ib.missing))
        c.errors.append(Msg("enter_characteristics", stats=", ".join(missing_stats)))
    if missing:
        c.errors.append(Msg("picks_missing", n=missing))
    if ob.total is not None and spent_o > ob.total:
        c.errors.append(Msg("occ_overspent", n=spent_o - ob.total))
    if ib.total is not None and spent_i > ib.total:
        c.errors.append(Msg("int_overspent", n=spent_i - ib.total))
    for r in rows:
        if r.occupation_points and not r.is_occupation_skill:
            c.errors.append(Msg("not_occ_skill_has_points", names, skill=r.key))
        if r.key == CTHULHU_MYTHOS and (r.occupation_points or r.interest_points):
            c.errors.append(Msg("mythos_no_raise"))
        if r.total > SKILL_CREATION_MAX:
            c.errors.append(Msg("over_max", names, skill=r.key, total=r.total, max=SKILL_CREATION_MAX))
    cr = next(r for r in rows if r.key == CREDIT_RATING)
    if not occ.credit_min <= cr.total <= occ.credit_max:
        c.errors.append(Msg("credit_range", value=cr.total, min=occ.credit_min, max=occ.credit_max))

    if (c.occupation_remaining or 0) > 0:
        c.warnings.append(Msg("warn_occ_unspent", n=c.occupation_remaining))
    if (c.interest_remaining or 0) > 0:
        c.warnings.append(Msg("warn_int_unspent", n=c.interest_remaining))
    for key, value in sorted((manual_values or {}).items()):
        base = bases.get(key)
        if key == CTHULHU_MYTHOS and value:
            c.errors.append(Msg("manual_mythos", value=value))
        elif base is None or value != base:
            c.errors.append(Msg("manual_value", names, skill=key, value=value,
                                base=base if base is not None else "?"))
    if occ.issues:
        c.warnings.append(Msg("warn_occ_issues", issues="; ".join(occ.issues[:3])))
    c.warnings.append(Msg("warn_age"))
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
    """Validate one skill's allocation at the moment it is entered (used when converting)."""
    if new_occ < 0 or new_int < 0:
        return Msg("points_negative")
    if skill_key == CTHULHU_MYTHOS and (new_occ or new_int):
        return Msg("mythos_no_raise")
    if new_occ and skill_key not in occupation_skills(occ, picks):
        return Msg("occ_points_not_allowed")
    if base + new_occ + new_int > SKILL_CREATION_MAX:
        return Msg("over_max_creation", total=base + new_occ + new_int, max=SKILL_CREATION_MAX)
    ob, ib = budgets
    if ob is None or ib is None:
        return Msg("budgets_unknown")
    if spent_elsewhere[0] + new_occ > ob:
        return Msg("occ_left", n=ob - spent_elsewhere[0])
    if spent_elsewhere[1] + new_int > ib:
        return Msg("int_left", n=ib - spent_elsewhere[1])
    if skill_key == CREDIT_RATING and base + new_occ + new_int > occ.credit_max:
        return Msg("credit_max", max=occ.credit_max)
    return None


# --- atomic batch allocation --------------------------------------------------------------

MODE_SET = "set"      # values are the new totals per budget
MODE_ADD = "add"      # values are signed deltas per budget


@dataclass(frozen=True)
class AllocationChange:
    key: str
    base: int
    old: Tuple[int, int]      # (occupation, interest) before
    new: Tuple[int, int]      # after

    @property
    def old_total(self) -> int:
        return self.base + sum(self.old)

    @property
    def new_total(self) -> int:
        return self.base + sum(self.new)


@dataclass
class BatchResult:
    changes: List[AllocationChange]
    errors: List[Msg]
    budgets: Tuple[Optional[int], Optional[int]]
    spent_before: Tuple[int, int]
    spent_after: Tuple[int, int]
    final_points: Dict[str, Tuple[int, int]]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def remaining_after(self) -> Tuple[Optional[int], Optional[int]]:
        ob, ib = self.budgets
        return (None if ob is None else ob - self.spent_after[0],
                None if ib is None else ib - self.spent_after[1])


def evaluate_batch(
    occ: Occupation,
    picks: Mapping[Tuple[int, int], str],
    current: Mapping[str, Tuple[int, int]],
    proposals: Mapping[str, Tuple[Optional[int], Optional[int]]],
    bases: Mapping[str, Optional[int]],
    budgets: Tuple[Optional[int], Optional[int]],
    mode: str = MODE_SET,
    unreconciled: Optional[Mapping[str, int]] = None,
    names: Optional[Mapping[str, str]] = None,
) -> BatchResult:
    """Validate a whole set of allocation edits against the *final* state.

    ``proposals`` maps skill -> (occupation, interest); ``None`` leaves that
    budget unchanged. In MODE_SET the numbers are new totals, in MODE_ADD
    signed deltas. Budgets are checked on final totals only, so moving points
    from one skill to another in one batch works even when the budget was
    fully spent. A budget may stay over its limit (e.g. after EDU was lowered)
    as long as the batch does not increase its spending. Nothing is written
    here; the caller applies ``final_points`` only if ``ok``.
    """
    names = dict(names or {})
    errors: List[Msg] = []
    final: Dict[str, Tuple[int, int]] = {k: (int(o), int(i)) for k, (o, i) in current.items()}
    occ_skills = set(occupation_skills(occ, picks))
    changes: List[AllocationChange] = []

    for key, (po, pi) in proposals.items():
        old_o, old_i = final.get(key, (0, 0))
        if mode == MODE_ADD:
            new_o = old_o + (po or 0)
            new_i = old_i + (pi or 0)
            for delta, have in ((po, old_o), (pi, old_i)):
                if delta is not None and have + delta < 0:
                    errors.append(Msg("batch_remove_too_many", names, skill=key, n=-delta, have=have))
            new_o, new_i = max(0, new_o), max(0, new_i)
        else:
            new_o = old_o if po is None else po
            new_i = old_i if pi is None else pi
            if new_o < 0 or new_i < 0:
                errors.append(Msg("points_negative"))
                continue
        if (new_o, new_i) == (old_o, old_i):
            continue
        base = bases.get(key)
        if base is None:
            errors.append(Msg("base_unknown", names, skill=key))
            continue
        if unreconciled and key in unreconciled:
            errors.append(Msg("reconcile_first", names, skill=key, value=unreconciled[key]))
            continue
        if key == CTHULHU_MYTHOS and (new_o or new_i):
            errors.append(Msg("batch_mythos"))
        if new_o and key not in occ_skills:
            errors.append(Msg("batch_occ_not_allowed", names, skill=key))
        total = int(base) + new_o + new_i
        if total > SKILL_CREATION_MAX:
            errors.append(Msg("batch_over_max", names, skill=key, total=total, max=SKILL_CREATION_MAX))
        elif key == CREDIT_RATING and total > occ.credit_max:
            errors.append(Msg("batch_credit_max", total=total, max=occ.credit_max))
        final[key] = (new_o, new_i)
        changes.append(AllocationChange(key, int(base), (old_o, old_i), (new_o, new_i)))

    before = (sum(o for o, _ in current.values()), sum(i for _, i in current.values()))
    after = (sum(o for o, _ in final.values()), sum(i for _, i in final.values()))
    ob, ib = budgets
    if changes and (ob is None or ib is None) and (after[0] > before[0] or after[1] > before[1]):
        errors.append(Msg("budgets_unknown"))
    if ob is not None and after[0] > ob and after[0] > before[0]:
        errors.append(Msg("batch_occ_over", over=after[0] - ob, after=after[0], total=ob, left=ob - before[0]))
    if ib is not None and after[1] > ib and after[1] > before[1]:
        errors.append(Msg("batch_int_over", over=after[1] - ib, after=after[1], total=ib, left=ib - before[1]))

    final = {k: v for k, v in final.items() if v != (0, 0)}
    return BatchResult(changes, errors, (ob, ib), before, after, final)
