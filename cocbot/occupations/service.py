"""Occupation-based creation for one investigator.

Ownership is enforced through ``InvestigatorService.get`` on every call; all
creation actions require a draft. Points live in ``investigator_skill_points``
and are mirrored into ``investigator_skills`` (source='allocation') so that
``/check`` and the sheet see base + points without knowing about creation.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

from cocbot.characters.models import STATUS_DRAFT, Investigator
from cocbot.characters.service import (
    CharacterError,
    InvalidState,
    InvestigatorService,
    NotFound,
    ValidationError,
    parse_int,
)
from cocbot.db import repo_investigators as repo
from cocbot.db.repo_skill_defs import resolve_skill, skill_display_names
from cocbot.i18n import Msg
from cocbot.mechanics.derived import eval_derived_formula
from cocbot.occupations import catalogue
from cocbot.occupations.catalogue import CatalogueError, Occupation
from cocbot.occupations.rules import (
    CTHULHU_MYTHOS,
    MODE_ADD,
    MODE_SET,
    BatchResult,
    CreationCheck,
    evaluate_batch,
    allocation_error,
    alternative_stats,
    check_creation,
    interest_budget,
    occupation_budget,
    occupation_skills,
    pick_positions,
    slot_options,
    validate_pick,
)

STEPS = ("characteristics", "occupation", "choices", "allocation", "review")
POINTS_MAX = 99


class BatchRejected(ValidationError):
    """A whole allocation batch was refused; nothing was saved. ``result`` explains why."""

    def __init__(self, result: BatchResult) -> None:
        self.result = result
        # Single error: keep the Msg itself so it can be localized; several: English summary
        # (the Discord layer renders ``result.errors`` individually).
        super().__init__(result.errors[0] if len(result.errors) == 1 else "\n".join(result.errors))


def skill_bases(conn: sqlite3.Connection, inv: Investigator) -> Dict[str, Optional[int]]:
    """Base value of every skill for this investigator (Dodge/Language (Own) derived)."""
    out: Dict[str, Optional[int]] = {}
    for r in conn.execute("SELECT key, base, is_derived, derived_formula FROM skill_defs"):
        if r["is_derived"]:
            out[r["key"]] = eval_derived_formula(r["derived_formula"] or "", inv.characteristics)[0]
        else:
            out[r["key"]] = int(r["base"])
    return out


def all_skill_keys(conn: sqlite3.Connection) -> List[str]:
    return [r[0] for r in conn.execute("SELECT key FROM skill_defs ORDER BY key")]


def load_picks(conn: sqlite3.Connection, investigator_id: int) -> Dict[Tuple[int, int], str]:
    rows = conn.execute(
        "SELECT slot_index, pick_index, skill_key FROM investigator_occupation_picks WHERE investigator_id=?",
        (investigator_id,),
    ).fetchall()
    return {(int(r[0]), int(r[1])): r[2] for r in rows}


def load_points(conn: sqlite3.Connection, investigator_id: int) -> Dict[str, Tuple[int, int]]:
    rows = conn.execute(
        "SELECT skill_key, occupation_points, interest_points FROM investigator_skill_points "
        "WHERE investigator_id=?", (investigator_id,),
    ).fetchall()
    return {r[0]: (int(r[1]), int(r[2])) for r in rows}


def load_manual_values(conn: sqlite3.Connection, investigator_id: int) -> Dict[str, int]:
    """Skill values typed in by hand (Milestone 1 style), not produced by points."""
    rows = conn.execute(
        "SELECT skill_key, value FROM investigator_skills WHERE investigator_id=? AND source='manual' "
        "AND specialization=''", (investigator_id,),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def sync_allocated_skills(conn: sqlite3.Connection, inv: Investigator) -> None:
    """Mirror allocations into investigator_skills (base + points). No commit."""
    if inv.status != STATUS_DRAFT:
        return  # finished investigators are frozen; progression is manual
    bases = skill_bases(conn, inv)
    points = load_points(conn, inv.id)
    for key, (o, i) in points.items():
        if o + i <= 0:
            continue
        value = int(bases.get(key) or 0) + o + i
        conn.execute(
            """
            INSERT INTO investigator_skills (investigator_id, skill_key, specialization, value, source)
            VALUES (?, ?, '', ?, 'allocation')
            ON CONFLICT(investigator_id, skill_key, specialization)
            DO UPDATE SET value=excluded.value, source='allocation',
                          updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (inv.id, key, value),
        )
    keep = [k for k, (o, i) in points.items() if o + i > 0]
    marks = ",".join("?" for _ in keep) or "''"
    conn.execute(
        f"DELETE FROM investigator_skills WHERE investigator_id=? AND source='allocation' "
        f"AND skill_key NOT IN ({marks})",
        (inv.id, *keep),
    )


class CreationService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.investigators = InvestigatorService(conn)

    # --- helpers ---------------------------------------------------------------
    def _draft(self, actor_id: object, investigator_id: int) -> Investigator:
        inv = self.investigators.get(actor_id, investigator_id, include_archived=False)
        if inv.status != STATUS_DRAFT:
            raise InvalidState(Msg("not_draft"))
        return inv

    def occupation_of(self, inv: Investigator) -> Optional[Occupation]:
        return catalogue.get(self.conn, inv.occupation_id) if inv.occupation_id else None

    def _require_occupation(self, inv: Investigator) -> Occupation:
        occ = self.occupation_of(inv)
        if occ is None:
            raise InvalidState(Msg("choose_occupation_first"))
        return occ

    def _commit_and_reload(self, actor_id: object, inv: Investigator, step: Optional[str] = None) -> Investigator:
        fresh = self.investigators.get(actor_id, inv.id)
        sync_allocated_skills(self.conn, fresh)
        if step:
            repo.update(self.conn, inv.id, {"creation_step": step})
        self.conn.commit()
        return self.investigators.get(actor_id, inv.id)

    # --- occupation ------------------------------------------------------------
    def has_occupation_progress(self, inv: Investigator) -> bool:
        if load_picks(self.conn, inv.id):
            return True
        return any(o for o, _ in load_points(self.conn, inv.id).values())

    def set_occupation(self, actor_id: object, investigator_id: int, occupation_ref: object,
                       force: bool = False) -> Investigator:
        inv = self._draft(actor_id, investigator_id)
        occ = catalogue.find(self.conn, actor_id, str(occupation_ref))
        if occ is None:
            raise NotFound(Msg("no_occupation_match", ref=repr(str(occupation_ref))))
        if inv.occupation_id == occ.id:
            return inv
        if inv.occupation_id and self.has_occupation_progress(inv) and not force:
            raise InvalidState(Msg("confirm_occupation_change"))
        try:
            self.conn.execute("DELETE FROM investigator_occupation_picks WHERE investigator_id=?", (inv.id,))
            self.conn.execute("UPDATE investigator_skill_points SET occupation_points=0 WHERE investigator_id=?",
                              (inv.id,))
            self.conn.execute("DELETE FROM investigator_skill_points WHERE investigator_id=? "
                              "AND occupation_points=0 AND interest_points=0", (inv.id,))
            repo.update(self.conn, inv.id, {"occupation_id": occ.id, "occupation": occ.name_en[:100],
                                            "skill_point_stat": None})
            return self._commit_and_reload(actor_id, inv, "choices" if pick_positions(occ) else "allocation")
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def create_homebrew(self, actor_id: object, investigator_id: int, name: str, credit_range: str,
                        formula: str) -> Investigator:
        self._draft(actor_id, investigator_id)
        parts = str(credit_range or "").replace("–", "-").split("-")
        try:
            lo, hi = (int(p) for p in parts) if len(parts) == 2 else (None, None)
        except ValueError:
            lo = hi = None
        if lo is None:
            raise ValidationError(Msg("credit_range_format"))
        try:
            occ = catalogue.create_homebrew(self.conn, actor_id, name, lo, hi, catalogue.parse_formula_choice(formula))
        except CatalogueError as e:
            raise ValidationError(e.args[0] if e.args else str(e)) from None
        return self.set_occupation(actor_id, investigator_id, occ.id, force=True)

    # --- choices ---------------------------------------------------------------
    def set_pick(self, actor_id: object, investigator_id: int, slot_index: int, pick_index: int,
                 skill: str) -> Investigator:
        inv = self._draft(actor_id, investigator_id)
        occ = self._require_occupation(inv)
        sd = resolve_skill(skill, conn=self.conn)
        if sd is None:
            raise ValidationError(Msg("unknown_skill", text=repr(str(skill))))
        picks = load_picks(self.conn, inv.id)
        err = validate_pick(occ, picks, slot_index, pick_index, sd.key, all_skill_keys(self.conn))
        if err:
            raise ValidationError(err)
        old = picks.get((slot_index, pick_index))
        try:
            self.conn.execute(
                """
                INSERT INTO investigator_occupation_picks (investigator_id, slot_index, pick_index, skill_key)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigator_id, slot_index, pick_index) DO UPDATE SET skill_key=excluded.skill_key
                """,
                (inv.id, slot_index, pick_index, sd.key),
            )
            if old and old != sd.key:
                self._drop_occupation_points(inv.id, old, occ, {**picks, (slot_index, pick_index): sd.key})
            done = len(load_picks(self.conn, inv.id)) == len(pick_positions(occ))
            return self._commit_and_reload(actor_id, inv, "allocation" if done else "choices")
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def _drop_occupation_points(self, investigator_id: int, skill_key: str, occ: Occupation, picks) -> None:
        if skill_key in occupation_skills(occ, picks):
            return
        self.conn.execute("UPDATE investigator_skill_points SET occupation_points=0 "
                          "WHERE investigator_id=? AND skill_key=?", (investigator_id, skill_key))
        self.conn.execute("DELETE FROM investigator_skill_points WHERE investigator_id=? AND skill_key=? "
                          "AND interest_points=0", (investigator_id, skill_key))

    def pick_options(self, inv: Investigator, slot_index: int, pick_index: int) -> List[str]:
        """Options still available for one pick (excludes skills used elsewhere)."""
        occ = self._require_occupation(inv)
        picks = load_picks(self.conn, inv.id)
        opts = slot_options(occ.slots[slot_index], all_skill_keys(self.conn))
        used = set(occ.fixed_skills) | {k for pos, k in picks.items() if pos != (slot_index, pick_index)}
        return [k for k in opts if k not in used]

    # --- allocation --------------------------------------------------------------
    def allocate(self, actor_id: object, investigator_id: int, skill: str,
                 occupation_points: object = None, interest_points: object = None) -> Investigator:
        """Set the points on one skill. None leaves that budget unchanged; blank means 0."""
        inv = self._draft(actor_id, investigator_id)
        self._require_occupation(inv)
        sd = resolve_skill(skill, conn=self.conn)
        if sd is None:
            raise ValidationError(Msg("unknown_skill", text=repr(str(skill))))
        new_o = None if occupation_points is None else (parse_int(occupation_points, "Occupation points", 0,
                                                                    POINTS_MAX) or 0)
        new_i = None if interest_points is None else (parse_int(interest_points, "Interest points", 0,
                                                                POINTS_MAX) or 0)
        names = skill_display_names(self.conn, [sd.key])
        if skill_bases(self.conn, inv).get(sd.key) is None:
            raise InvalidState(Msg("base_unknown", names, skill=sd.key))
        manual = self.unreconciled(inv)
        if sd.key in manual:
            raise InvalidState(Msg("reconcile_first", names, skill=sd.key, value=manual[sd.key]))
        inv, _ = self.allocate_batch(actor_id, investigator_id, {sd.key: (new_o, new_i)}, MODE_SET)
        return inv

    def preview_batch(self, inv: Investigator, proposals: Dict[str, Tuple[Optional[int], Optional[int]]],
                      mode: str = MODE_SET) -> BatchResult:
        """Evaluate an allocation batch without saving anything."""
        occ = self._require_occupation(inv)
        if mode not in (MODE_SET, MODE_ADD):
            raise ValidationError(f"Unknown mode {mode!r}.")
        current = load_points(self.conn, inv.id)
        budgets = (occupation_budget(occ, inv.characteristics, inv.skill_point_stat).total,
                   interest_budget(inv.characteristics).total)
        names = skill_display_names(self.conn, list(proposals))
        return evaluate_batch(occ, load_picks(self.conn, inv.id), current, proposals,
                              skill_bases(self.conn, inv), budgets, mode, self.unreconciled(inv), names)

    def allocate_batch(self, actor_id: object, investigator_id: int,
                       proposals: Dict[str, Tuple[Optional[int], Optional[int]]],
                       mode: str = MODE_SET) -> Tuple[Investigator, BatchResult]:
        """Apply several allocation edits atomically: all of them, or none.

        ``proposals`` maps skill key -> (occupation, interest); see
        ``rules.evaluate_batch`` for the semantics of ``mode``. Raises
        ``BatchRejected`` (nothing saved) if any edit or the final budgets break
        the rules.
        """
        inv = self._draft(actor_id, investigator_id)
        resolved: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
        for skill, value in proposals.items():
            sd = resolve_skill(skill, conn=self.conn)
            if sd is None:
                raise ValidationError(Msg("unknown_skill", text=repr(str(skill))))
            resolved[sd.key] = value
        result = self.preview_batch(inv, resolved, mode)
        if not result.ok:
            raise BatchRejected(result)
        if not result.changes:
            return inv, result
        try:
            for ch in result.changes:
                # a hand-entered value equal to base carries no information
                self.conn.execute("DELETE FROM investigator_skills WHERE investigator_id=? AND skill_key=? "
                                  "AND source='manual' AND specialization=''", (inv.id, ch.key))
                o, i = ch.new
                if o == 0 and i == 0:
                    self.conn.execute("DELETE FROM investigator_skill_points WHERE investigator_id=? "
                                      "AND skill_key=?", (inv.id, ch.key))
                else:
                    self.conn.execute(
                        """
                        INSERT INTO investigator_skill_points
                          (investigator_id, skill_key, occupation_points, interest_points)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(investigator_id, skill_key)
                        DO UPDATE SET occupation_points=excluded.occupation_points,
                                      interest_points=excluded.interest_points
                        """,
                        (inv.id, ch.key, o, i),
                    )
            return self._commit_and_reload(actor_id, inv, "allocation"), result
        except sqlite3.Error:
            self.conn.rollback()
            raise

    # --- review --------------------------------------------------------------------
    def check(self, actor_id: object, investigator_id: int) -> CreationCheck:
        inv = self.investigators.get(actor_id, investigator_id)
        return self.check_for(inv)

    def check_for(self, inv: Investigator) -> CreationCheck:
        occ = self._require_occupation(inv)
        manual = load_manual_values(self.conn, inv.id)
        points = load_points(self.conn, inv.id)
        names = skill_display_names(self.conn, list(points) + occupation_skills(occ, load_picks(self.conn, inv.id))
                                    + list(manual))
        return check_creation(occ, inv.characteristics, load_picks(self.conn, inv.id), points,
                              skill_bases(self.conn, inv), manual, names, inv.skill_point_stat)

    def next_step(self, inv: Investigator) -> str:
        if inv.missing_characteristics:
            return "characteristics"
        occ = self.occupation_of(inv)
        if occ is None:
            return "occupation"
        if len(load_picks(self.conn, inv.id)) < len(pick_positions(occ)):
            return "choices"
        if self.unreconciled(inv):
            return "review"
        c = self.check_for(inv)
        if any(c.unspent) or not c.ok:
            return "allocation"
        return "review"

    # --- formula choice ------------------------------------------------------------
    def set_formula_stat(self, actor_id: object, investigator_id: int, stat: Optional[str]) -> Investigator:
        """Choose which characteristic an "X or Y" formula term uses (None = use the suggestion)."""
        inv = self._draft(actor_id, investigator_id)
        occ = self._require_occupation(inv)
        alts = alternative_stats(occ)
        if stat is not None:
            stat = str(stat).upper()
            if stat not in alts:
                raise ValidationError(Msg("formula_choice_invalid", occupation={"en": occ.name_en,
                                                                                 "zh": occ.name_zh or occ.name_en},
                                          stats=", ".join(alts) or "—"))
        repo.update(self.conn, inv.id, {"skill_point_stat": stat})
        self.conn.commit()
        return self.investigators.get(actor_id, inv.id)

    # --- reconciling hand-entered values ------------------------------------------------
    def unreconciled(self, inv: Investigator) -> Dict[str, int]:
        """Hand-entered values that differ from base and therefore block validation."""
        bases = skill_bases(self.conn, inv)
        return {k: v for k, v in load_manual_values(self.conn, inv.id).items() if bases.get(k) != v}

    def reconcile(self, actor_id: object, investigator_id: int, skill: str, action: str) -> Investigator:
        """Resolve one hand-entered value: 'discard' it (back to base) or 'convert' it into points.

        Converting uses occupation points first when the skill is an occupation
        skill, then personal interest points, and only succeeds if the result
        satisfies the allocation rules. Nothing is removed without this explicit action.
        """
        inv = self._draft(actor_id, investigator_id)
        occ = self._require_occupation(inv)
        sd = resolve_skill(skill, conn=self.conn)
        if sd is None:
            raise ValidationError(Msg("unknown_skill", text=repr(str(skill))))
        manual = load_manual_values(self.conn, inv.id)
        names = skill_display_names(self.conn, [sd.key])
        if sd.key not in manual:
            raise NotFound(Msg("no_manual_value", names, skill=sd.key))
        value = manual[sd.key]
        if action == "discard":
            self.conn.execute("DELETE FROM investigator_skills WHERE investigator_id=? AND skill_key=? "
                              "AND source='manual' AND specialization=''", (inv.id, sd.key))
            return self._commit_and_reload(actor_id, inv)
        if action != "convert":
            raise ValidationError(Msg("reconcile_action"))

        base = skill_bases(self.conn, inv).get(sd.key)
        if base is None:
            raise InvalidState(Msg("base_unknown", names, skill=sd.key))
        if sd.key == CTHULHU_MYTHOS:
            raise ValidationError(Msg("mythos_cannot_convert"))
        needed = value - int(base)
        if needed < 0:
            raise ValidationError(Msg("below_base", names, skill=sd.key, value=value, base=base))
        points = load_points(self.conn, inv.id)
        cur_o, cur_i = points.get(sd.key, (0, 0))
        spent_o = sum(o for k, (o, _) in points.items() if k != sd.key)
        spent_i = sum(i for k, (_, i) in points.items() if k != sd.key)
        ob = occupation_budget(occ, inv.characteristics, inv.skill_point_stat).total
        ib = interest_budget(inv.characteristics).total
        picks = load_picks(self.conn, inv.id)
        use_o = 0
        if sd.key in occupation_skills(occ, picks) and ob is not None:
            use_o = max(0, min(needed, ob - spent_o))
        use_i = needed - use_o
        err = allocation_error(occ, picks, sd.key, use_o, use_i, int(base), (ob, ib), (spent_o, spent_i))
        if err:
            raise ValidationError(Msg("convert_failed", names, skill=sd.key, value=value, reason=err))
        try:
            self.conn.execute("DELETE FROM investigator_skills WHERE investigator_id=? AND skill_key=? "
                              "AND source='manual' AND specialization=''", (inv.id, sd.key))
            self.conn.execute(
                """
                INSERT INTO investigator_skill_points (investigator_id, skill_key, occupation_points, interest_points)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(investigator_id, skill_key)
                DO UPDATE SET occupation_points=excluded.occupation_points, interest_points=excluded.interest_points
                """,
                (inv.id, sd.key, use_o, use_i),
            )
            return self._commit_and_reload(actor_id, inv)
        except sqlite3.Error:
            self.conn.rollback()
            raise


__all__ = ["BatchRejected", "CharacterError", "CreationService", "MODE_ADD", "MODE_SET", "STEPS", "load_picks", "load_points", "skill_bases",
           "sync_allocated_skills"]
