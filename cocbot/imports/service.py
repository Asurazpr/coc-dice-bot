"""ImportService: preview, create and re-import investigators from spreadsheets.

* ``preview`` parses an upload in memory and returns everything the player
  must see before saving; nothing is written. The file is never stored.
* ``create`` / ``update`` save one preview atomically (investigator, skills,
  import record and campaign notices commit together or not at all).
* Ownership is checked on every write; campaign state (campaign HP/MP/SAN/Luck,
  conditions, history) is never changed by an import.
* Imports are never marked rules-validated (docs/EXCEL_IMPORT.md §4).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from cocbot.characters.models import CHAR_COLUMNS, STATUS_DRAFT, STATUS_READY, Investigator
from cocbot.characters.service import (
    MAX_LIVE_INVESTIGATORS,
    NAME_MAX,
    SKILL_MAX,
    CharacterError,
    Conflict,
    InvalidState,
    InvestigatorService,
    NotFound,
)
from cocbot.db import repo_investigators as repo
from cocbot.db.repo_skill_defs import resolve_skill
from cocbot.i18n import Msg
from cocbot.imports import adapters
from cocbot.imports.model import INFO, WARNING, Issue, ParsedSheet
from cocbot.imports.workbook import WorkbookRejected, open_book
from cocbot.mechanics.derived import CHARACTERISTICS, DerivedStats, compute_derived, eval_derived_formula


class ImportRejected(CharacterError):
    """The upload cannot be imported (unsupported layout, unreadable, blocking errors)."""


class DbSkillCatalog:
    """SkillCatalog on top of skill_defs / i18n (Chinese and English names, aliases)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._defs = {r["key"]: (r["base"], bool(r["is_derived"]), r["derived_formula"])
                      for r in conn.execute("SELECT key, base, is_derived, derived_formula FROM skill_defs")}

    def resolve(self, name: str) -> Optional[str]:
        sd = resolve_skill(name, conn=self.conn)
        return sd.key if sd else None

    def base(self, key: str, characteristics: Mapping[str, Optional[int]]) -> Optional[int]:
        d = self._defs.get(key)
        if d is None:
            return None
        base, derived, formula = d
        if derived:
            return eval_derived_formula(formula or "", characteristics)[0]
        return int(base)


@dataclass
class ImportPreview:
    sheet: ParsedSheet
    template: str
    template_label: str
    sha256: str
    skills: Dict[str, int]                       # skill key -> value that will be stored
    derived: DerivedStats
    complete: bool
    advisory: Dict[str, Tuple[int, Optional[int]]] = field(default_factory=dict)   # budget -> (spent, total)
    occupation_match: Optional[str] = None       # catalogue occupation name (informational)
    candidates: List[Investigator] = field(default_factory=list)                  # same-name investigators

    @property
    def errors(self) -> List[Issue]:
        return self.sheet.errors

    @property
    def can_save(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Change:
    kind: str          # field | characteristic | resource | skill_added | skill_changed | skill_removed
    name: str          # field name, characteristic key or skill key
    old: Any
    new: Any


@dataclass(frozen=True)
class ImportRecord:
    id: int
    investigator_id: int
    template: str
    action: str
    completeness: str
    verification: str
    issues: List[dict]
    notes: Dict[str, Any]
    created_at: str


@dataclass
class UpdatePlan:
    investigator: Investigator
    changes: List[Change]
    drops_validation: bool                 # wizard allocations / validation will be removed
    campaigns: List[str]                   # campaign names where it plays (campaign state untouched)
    blocked: Optional[Msg] = None
    fingerprint: str = ""                  # state the diff was computed from (stale-preview check)


def fingerprint(inv: Investigator) -> str:
    """Hash of everything an import can change, to detect edits between preview and confirm."""
    state = (inv.name, inv.status, inv.occupation, inv.age, inv.residence, inv.birthplace,
             sorted(inv.characteristics.items()), inv.luck, inv.hp_current, inv.mp_current, inv.san_current,
             sorted(inv.skills.items()), inv.occupation_id, inv.creation_validated)
    return hashlib.sha256(repr(state).encode("utf-8")).hexdigest()


BIO_FIELDS = ("name", "occupation", "age", "residence", "birthplace")
RESOURCE_FIELDS = (("hp", "hp_current"), ("mp", "mp_current"), ("san", "san_current"))


class ImportService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.investigators = InvestigatorService(conn)

    # --- preview ---------------------------------------------------------------------------
    def preview(self, owner_id: object, data: bytes) -> ImportPreview:
        try:
            book = open_book(data)
        except WorkbookRejected as e:
            raise ImportRejected(e.args[0]) from None
        adapter = adapters.detect(book)
        if adapter is None:
            raise ImportRejected(Msg("import_unsupported", supported="; ".join(adapters.supported_labels())))
        catalog = DbSkillCatalog(self.conn)
        sheet = adapter.parse(book, catalog)
        for notice in book.notices:
            sheet.issues.append(Issue(INFO, notice))
        skills = self._settle_skills(sheet, catalog)
        derived = compute_derived(sheet.characteristics, age=sheet.age,
                                  cthulhu_mythos=skills.get("cthulhu_mythos", 0))
        self._check_resources(sheet, derived)
        self._compare_cached(sheet, derived, skills, catalog)
        complete = not sheet.missing_characteristics
        preview = ImportPreview(sheet, adapter.key, adapter.label, hashlib.sha256(data).hexdigest(), skills,
                                derived, complete)
        self._advisory(preview)
        if sheet.name:
            preview.candidates = [i for i in repo.list_for_owner(self.conn, str(owner_id))
                                  if i.name.casefold() == sheet.name.casefold()]
        return preview

    def _settle_skills(self, sheet: ParsedSheet, catalog: DbSkillCatalog) -> Dict[str, int]:
        by_key: Dict[str, List] = {}
        for ps in sheet.skills:
            by_key.setdefault(ps.key, []).append(ps)
        out: Dict[str, int] = {}
        specs: Dict[str, List[Dict[str, Any]]] = {}
        for key, rows in by_key.items():
            best = max(rows, key=lambda p: p.total)
            base = catalog.base(key, sheet.characteristics)
            if sum(1 for p in rows if p.total != base) > 1:
                sheet.add(WARNING, "imp_duplicate_skill", best.where, skill=key,
                          labels=", ".join(f"{p.label} {p.total}" for p in rows), value=best.total)
            for p in rows:
                if p.specialization:
                    specs.setdefault(key, []).append({"specialization": p.specialization, "value": p.total})
            if best.total > SKILL_MAX:
                sheet.add(WARNING, "imp_out_of_range", best.where, field=best.label, value=best.total, lo=0,
                          hi=SKILL_MAX)
                sheet.notes.setdefault("unmapped_skills", []).append(
                    {"label": best.label, "value": best.total, "where": best.where})
                continue
            if best.total != base:
                out[key] = best.total
                best.stored = True
        if specs:
            sheet.notes["specializations"] = specs
        return out

    @staticmethod
    def _check_resources(sheet: ParsedSheet, d: DerivedStats) -> None:
        for res, maximum in (("hp", d.hp_max), ("mp", d.mp_max), ("san", d.san_max)):
            value = getattr(sheet, res)
            if value is not None and maximum is not None and value > maximum:
                sheet.add(WARNING, "imp_resource_above_max", field=res.upper(), value=value, max=maximum)
                setattr(sheet, res, None)

    @staticmethod
    def _compare_cached(sheet: ParsedSheet, d: DerivedStats, skills: Mapping[str, int],
                        catalog: DbSkillCatalog) -> None:
        dodge = skills.get("dodge", catalog.base("dodge", sheet.characteristics))
        ours = {"hp_max": d.hp_max, "mp_max": d.mp_max, "san_max": d.san_max, "mov": d.mov,
                "build": d.build, "damage_bonus": d.damage_bonus, "dodge": dodge}
        for name, cached in sheet.cached_derived.items():
            mine = ours.get(name)
            if cached in (None, "", "错误") or mine is None:
                continue
            if str(cached).replace(" ", "").upper() != str(mine).replace(" ", "").upper():
                sheet.add(INFO, "imp_recomputed", field=name, cached=cached, value=mine)

    def _advisory(self, preview: ImportPreview) -> None:
        """Compare the sheet's occupation/interest columns with the budgets — information only."""
        from cocbot.occupations import catalogue
        from cocbot.occupations.rules import interest_budget, occupation_budget
        sheet = preview.sheet
        spent = sheet.notes.get("points_on_sheet") or {}
        occ = None
        for ref in (sheet.occupation_zh, sheet.occupation):
            if ref:
                occ = catalogue.find(self.conn, "", ref)
                if occ:
                    break
        if occ is not None:
            preview.occupation_match = occ.name_en
            if spent.get("occupation"):
                preview.advisory["occupation"] = (spent["occupation"],
                                                  occupation_budget(occ, sheet.characteristics).total)
        if spent.get("interest"):
            preview.advisory["interest"] = (spent["interest"], interest_budget(sheet.characteristics).total)

    # --- create ------------------------------------------------------------------------------
    def _fields(self, p: ImportPreview) -> Dict[str, object]:
        s = p.sheet
        fields: Dict[str, object] = {
            "occupation": s.occupation, "age": s.age, "residence": s.residence, "birthplace": s.birthplace,
            "luck": s.luck, "hp_current": s.hp, "mp_current": s.mp, "san_current": s.san,
            "status": STATUS_READY if p.complete else STATUS_DRAFT, "creation_step": None,
            "occupation_id": None, "creation_validated": 0, "skill_point_stat": None,
        }
        for stat in CHARACTERISTICS:
            fields[CHAR_COLUMNS[stat]] = s.characteristics.get(stat)
        return fields

    def _record(self, inv_id: int, owner_id: object, p: ImportPreview, action: str) -> None:
        self.conn.execute(
            "INSERT INTO investigator_imports (investigator_id, owner_user_id, template, file_sha256, action, "
            "completeness, issues_json, notes_json) VALUES (?,?,?,?,?,?,?,?)",
            (inv_id, str(owner_id), p.template, p.sha256, action, "complete" if p.complete else "incomplete",
             json.dumps([i.to_dict() for i in p.sheet.issues], ensure_ascii=False),
             json.dumps(p.sheet.notes, ensure_ascii=False, default=str)))

    def free_name(self, owner_id: object, name: str) -> str:
        """``name``, or ``name (2)``, ``name (3)``… if the owner already uses it."""
        candidate, n = name, 2
        while repo.name_taken(self.conn, str(owner_id), candidate):
            suffix = f" ({n})"
            candidate = name[:NAME_MAX - len(suffix)] + suffix
            n += 1
        return candidate

    def create(self, owner_id: object, p: ImportPreview, name: Optional[str] = None) -> Investigator:
        if not p.can_save:
            raise ImportRejected(p.errors[0].msg)
        owner = str(owner_id)
        if repo.count_live(self.conn, owner) >= MAX_LIVE_INVESTIGATORS:
            raise Conflict(Msg("import_collection_full", max=MAX_LIVE_INVESTIGATORS))
        name = (name or p.sheet.name or "").strip()[:NAME_MAX]
        if repo.name_taken(self.conn, owner, name):
            raise Conflict(Msg("import_name_taken", name=name))
        try:
            inv_id = repo.insert(self.conn, owner, {"name": name, **self._fields(p)})
            for key, value in p.skills.items():
                repo.set_skill(self.conn, inv_id, key, value)
            self._record(inv_id, owner, p, "create")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.investigators.get(owner, inv_id)

    # --- re-import -----------------------------------------------------------------------------
    def plan_update(self, owner_id: object, investigator_id: int, p: ImportPreview) -> UpdatePlan:
        from cocbot.campaigns.service import CampaignService
        inv = self.investigators.get(owner_id, investigator_id, include_archived=False)   # ownership
        changes: List[Change] = []
        new = {"name": p.sheet.name, "occupation": p.sheet.occupation, "age": p.sheet.age,
               "residence": p.sheet.residence, "birthplace": p.sheet.birthplace}
        for f in BIO_FIELDS:
            if (getattr(inv, f) or None) != (new[f] or None):
                changes.append(Change("field", f, getattr(inv, f), new[f]))
        for stat in CHARACTERISTICS:
            if inv.characteristics.get(stat) != p.sheet.characteristics.get(stat):
                changes.append(Change("characteristic", stat, inv.characteristics.get(stat),
                                      p.sheet.characteristics.get(stat)))
        if inv.luck != p.sheet.luck:
            changes.append(Change("characteristic", "LUCK", inv.luck, p.sheet.luck))
        for res, column in RESOURCE_FIELDS:
            if getattr(inv, column) != getattr(p.sheet, res):
                changes.append(Change("resource", res, getattr(inv, column), getattr(p.sheet, res)))
        for key in sorted(set(inv.skills) | set(p.skills)):
            old, value = inv.skills.get(key), p.skills.get(key)
            if old is None and value is not None:
                changes.append(Change("skill_added", key, None, value))
            elif value is None and old is not None:
                changes.append(Change("skill_removed", key, old, None))
            elif old != value:
                changes.append(Change("skill_changed", key, old, value))
        drops = bool(inv.creation_validated or inv.occupation_id or self.conn.execute(
            "SELECT 1 FROM investigator_skill_points WHERE investigator_id=? LIMIT 1", (inv.id,)).fetchone())
        campaigns = [c.name for c in CampaignService(self.conn).participations(inv.id)]
        blocked = None
        if not p.can_save:
            blocked = p.errors[0].msg
        elif inv.status == STATUS_READY and not p.complete:
            blocked = Msg("import_update_incomplete", fields=", ".join(p.sheet.missing_characteristics))
        elif p.sheet.name and p.sheet.name.casefold() != inv.name.casefold() and \
                repo.name_taken(self.conn, str(owner_id), p.sheet.name, exclude_id=inv.id):
            blocked = Msg("import_name_taken", name=p.sheet.name)
        return UpdatePlan(inv, changes, drops, campaigns, blocked, fingerprint(inv))

    def update(self, owner_id: object, investigator_id: int, p: ImportPreview,
               expected_fingerprint: Optional[str] = None) -> Investigator:
        """Replace the permanent sheet with the import. Campaign state is not touched."""
        from cocbot.campaigns.service import CampaignService
        plan = self.plan_update(owner_id, investigator_id, p)
        inv = plan.investigator
        if plan.blocked is not None:
            raise InvalidState(plan.blocked)
        if expected_fingerprint is not None and plan.fingerprint != expected_fingerprint:
            raise InvalidState(Msg("import_changed_since", name=inv.name))
        fields = self._fields(p)
        fields["name"] = (p.sheet.name or inv.name)[:NAME_MAX]
        if inv.status == STATUS_READY:
            fields["status"] = STATUS_READY
        campaigns = CampaignService(self.conn)
        try:
            self.conn.execute("DELETE FROM investigator_skill_points WHERE investigator_id=?", (inv.id,))
            self.conn.execute("DELETE FROM investigator_occupation_picks WHERE investigator_id=?", (inv.id,))
            self.conn.execute("DELETE FROM investigator_skills WHERE investigator_id=? AND specialization=''",
                              (inv.id,))
            repo.update(self.conn, inv.id, fields)
            for key, value in p.skills.items():
                repo.set_skill(self.conn, inv.id, key, value)
            self._record(inv.id, owner_id, p, "update")
            for camp in campaigns.participations(inv.id):          # Keepers see that the sheet changed
                campaigns._log(camp.id, owner_id, "sheet_import", inv.id, "sheet", None,
                               f"{len(plan.changes)} change(s)", None)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.investigators.get(owner_id, inv.id)

    # --- records -------------------------------------------------------------------------------
    def _latest(self, investigator_id: int) -> Optional[ImportRecord]:
        r = self.conn.execute(
            "SELECT import_id, investigator_id, template, action, completeness, verification, issues_json, "
            "notes_json, created_at FROM investigator_imports WHERE investigator_id=? "
            "ORDER BY import_id DESC LIMIT 1", (int(investigator_id),)).fetchone()
        if r is None:
            return None
        return ImportRecord(int(r[0]), int(r[1]), r[2], r[3], r[4], r[5], json.loads(r[6]), json.loads(r[7]),
                            r[8])

    def latest_for_keeper(self, actor_id: object, campaign_id: int, investigator_id: int
                          ) -> Optional[ImportRecord]:
        from cocbot.campaigns.service import CampaignService
        CampaignService(self.conn).inspect(actor_id, campaign_id, investigator_id)       # Keeper + in campaign
        return self._latest(investigator_id)

    def latest_for_owner(self, owner_id: object, investigator_id: int) -> Optional[ImportRecord]:
        self.investigators.get(owner_id, investigator_id, include_archived=True)        # ownership
        return self._latest(investigator_id)


__all__ = ["Change", "DbSkillCatalog", "ImportPreview", "ImportRecord", "ImportRejected", "ImportService",
           "NotFound", "UpdatePlan"]
