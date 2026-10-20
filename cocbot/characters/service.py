"""Investigator collection service: the single place that enforces ownership.

Every public method takes the *acting* Discord user ID and refuses to touch an
investigator that user does not own. "Not found" and "not yours" produce the
same error so one user cannot probe another's collection.

The service is Discord-agnostic; IDs are plain strings. Each mutating method
commits its own transaction.
"""
from __future__ import annotations

import random
import sqlite3
from typing import Dict, List, Mapping, Optional, Tuple

from cocbot.characters.models import (
    CHAR_COLUMNS,
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_READY,
    Investigator,
)
from cocbot.db import repo_investigators as repo
from cocbot.db.repo_skill_defs import resolve_skill
from cocbot.mechanics import generation
from cocbot.mechanics.derived import CHARACTERISTICS, compute_derived

MAX_LIVE_INVESTIGATORS = 25   # Discord select menus hold at most 25 options
NAME_MAX = 64
TEXT_MAX = 100
NOTES_MAX = 1000
AGE_MIN, AGE_MAX = 1, 150
SKILL_MIN, SKILL_MAX = 0, 200  # sanity guard; 7e skills can exceed 99 via improvement

GLOBAL_SCOPE = "global"


# --- errors ------------------------------------------------------------------

class CharacterError(Exception):
    """Base class. ``str(err)`` is safe to show to the user."""


class NotFound(CharacterError):
    pass


class ValidationError(CharacterError):
    pass


class Conflict(CharacterError):
    pass


class InvalidState(CharacterError):
    pass


class UnspentPoints(InvalidState):
    """Finishing would forfeit points; the player must confirm explicitly."""

    def __init__(self, occupation: int, interest: int) -> None:
        self.occupation, self.interest = occupation, interest
        parts = [f"{n} {label}" for n, label in ((occupation, "occupation"), (interest, "personal interest")) if n]
        super().__init__(f"{' and '.join(parts)} points are unspent and will be lost. "
                         f"Confirm to finish anyway.")


# --- scopes ------------------------------------------------------------------

def guild_scope(guild_id: Optional[object]) -> str:
    return f"guild:{guild_id}" if guild_id else GLOBAL_SCOPE


def resolution_scopes(guild_id: Optional[object]) -> List[str]:
    """Most specific first. Campaign scopes slot in ahead of the guild later."""
    return [guild_scope(guild_id), GLOBAL_SCOPE] if guild_id else [GLOBAL_SCOPE]


# --- validation helpers ------------------------------------------------------

def _clean_text(value: Optional[object], field: str, max_len: int = TEXT_MAX) -> Optional[str]:
    if value is None:
        return None
    s = " ".join(str(value).split())  # collapse whitespace/newlines
    if not s:
        return None
    if len(s) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return s


def _clean_name(value: Optional[object]) -> str:
    name = _clean_text(value, "Name", NAME_MAX)
    if not name:
        raise ValidationError("Name cannot be empty.")
    if name.isdigit():
        raise ValidationError("Name cannot be only digits (digits are used to refer to investigator IDs).")
    return name


def parse_int(value: Optional[object], field: str, lo: int, hi: int) -> Optional[int]:
    """Parse optional integer input from a form. Blank -> None."""
    if value is None:
        return None
    if isinstance(value, int):
        n = value
    else:
        s = str(value).strip()
        if s == "":
            return None
        try:
            n = int(s)
        except ValueError:
            raise ValidationError(f"{field} must be a whole number (got {s!r}).") from None
    if not lo <= n <= hi:
        raise ValidationError(f"{field} must be between {lo} and {hi} (got {n}).")
    return n


class InvestigatorService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- reads -----------------------------------------------------------
    def get(self, actor_id: object, investigator_id: int, include_archived: bool = True) -> Investigator:
        inv = repo.get(self.conn, int(investigator_id))
        if inv is None or inv.owner_user_id != str(actor_id):
            raise NotFound("Investigator not found in your collection.")
        if inv.is_archived and not include_archived:
            raise NotFound("That investigator is archived.")
        return inv

    def list(self, actor_id: object, include_archived: bool = False) -> List[Investigator]:
        return repo.list_for_owner(self.conn, str(actor_id), include_archived)

    def find(self, actor_id: object, ref: object, include_archived: bool = False) -> Investigator:
        """Look up by ID (digits, e.g. from autocomplete) or by case-insensitive name."""
        s = str(ref).strip().lstrip("#")
        if s.isdigit():
            return self.get(actor_id, int(s), include_archived=include_archived)
        matches = [i for i in self.list(actor_id, include_archived) if i.name.lower() == s.lower()]
        if not matches:
            raise NotFound(f"You have no investigator named {s!r}.")
        # live investigators first (names are unique among live ones)
        matches.sort(key=lambda i: i.is_archived)
        return matches[0]

    def active_with_scope(
        self, actor_id: object, guild_id: Optional[object] = None
    ) -> Tuple[Optional[Investigator], Optional[str]]:
        """The investigator /check uses here, and the scope that provided it.

        A server-specific selection (``guild:<id>``) always wins over the
        global default; the default only applies where no server selection exists.
        """
        scopes = resolution_scopes(guild_id)
        ids = repo.active_ids(self.conn, str(actor_id), scopes)
        for scope in scopes:
            if scope in ids:
                inv = repo.get(self.conn, ids[scope])
                # triggers keep this consistent, but never trust it blindly
                if inv and inv.owner_user_id == str(actor_id) and not inv.is_archived:
                    return inv, scope
        return None, None

    def active(self, actor_id: object, guild_id: Optional[object] = None) -> Optional[Investigator]:
        return self.active_with_scope(actor_id, guild_id)[0]

    def selections(self, actor_id: object, guild_id: Optional[object] = None) -> Dict[str, Optional[Investigator]]:
        """Both layers for display: {'server': ..., 'default': ...} (server only in a guild)."""
        out: Dict[str, Optional[Investigator]] = {}
        ids = repo.active_ids(self.conn, str(actor_id), resolution_scopes(guild_id))
        for label, scope in (("server", guild_scope(guild_id) if guild_id else None), ("default", GLOBAL_SCOPE)):
            if scope is None:
                continue
            inv = repo.get(self.conn, ids[scope]) if scope in ids else None
            out[label] = inv if inv and inv.owner_user_id == str(actor_id) and not inv.is_archived else None
        return out

    def active_scopes(self, actor_id: object, investigator_id: int) -> List[str]:
        return repo.active_scopes_for(self.conn, str(actor_id), investigator_id)

    # --- create / edit ---------------------------------------------------------
    def create(self, actor_id: object, name: object, **bio: object) -> Investigator:
        owner = str(actor_id)
        clean_name = _clean_name(name)
        if repo.count_live(self.conn, owner) >= MAX_LIVE_INVESTIGATORS:
            raise Conflict(
                f"You already have {MAX_LIVE_INVESTIGATORS} investigators. Archive one before creating another."
            )
        if repo.name_taken(self.conn, owner, clean_name):
            raise Conflict(f"You already have an investigator named {clean_name!r}.")
        fields = {"name": clean_name, "status": STATUS_DRAFT, "creation_step": "characteristics"}
        fields.update(self._clean_bio(bio))
        try:
            new_id = repo.insert(self.conn, owner, fields)
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise Conflict(f"You already have an investigator named {clean_name!r}.") from None
        return self.get(owner, new_id)

    def _clean_bio(self, bio: Mapping[str, object]) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for key, value in bio.items():
            if key == "age":
                out["age"] = parse_int(value, "Age", AGE_MIN, AGE_MAX)
            elif key == "notes":
                out["notes"] = _clean_text(value, "Notes", NOTES_MAX)
            elif key in ("occupation", "pronouns", "residence", "birthplace"):
                out[key] = _clean_text(value, key.capitalize())
            else:
                raise ValidationError(f"Unknown field {key!r}.")
        return out

    def update_bio(self, actor_id: object, investigator_id: int, **fields: object) -> Investigator:
        inv = self.get(actor_id, investigator_id, include_archived=False)
        changes: Dict[str, object] = {}
        if "name" in fields:
            new_name = _clean_name(fields.pop("name"))
            if new_name.lower() != inv.name.lower() and repo.name_taken(
                self.conn, inv.owner_user_id, new_name, exclude_id=inv.id
            ):
                raise Conflict(f"You already have an investigator named {new_name!r}.")
            changes["name"] = new_name
        changes.update(self._clean_bio(fields))
        return self._write(inv, changes)

    def set_characteristics(
        self, actor_id: object, investigator_id: int, values: Mapping[str, object]
    ) -> Investigator:
        """Partial update. Keys: STR..EDU, LUCK. Blank values clear the field."""
        inv = self.get(actor_id, investigator_id, include_archived=False)
        changes: Dict[str, object] = {}
        for key, raw in values.items():
            k = key.upper()
            if k == "LUCK":
                changes["luck"] = parse_int(raw, "Luck", generation.LUCK_MIN, generation.LUCK_MAX)
            elif k in CHAR_COLUMNS:
                changes[CHAR_COLUMNS[k]] = parse_int(
                    raw, k, generation.CHARACTERISTIC_MIN, generation.CHARACTERISTIC_MAX
                )
            else:
                raise ValidationError(f"Unknown characteristic {key!r}.")
        if inv.status == STATUS_READY and any(v is None for v in changes.values()):
            raise ValidationError("A finished investigator cannot have blank characteristics.")
        changes.update(_clamp_resources_for(inv, changes))
        return self._sync_allocations(self._write(inv, changes))

    def roll_characteristics(
        self, actor_id: object, investigator_id: int, rng: Optional[random.Random] = None
    ) -> Investigator:
        """Roll every characteristic and Luck with the standard RAW method.

        Only allowed while the investigator is a draft: re-rolling a finished
        investigator would be a Keeper decision, not a player one.
        """
        inv = self.get(actor_id, investigator_id, include_archived=False)
        if inv.status != STATUS_DRAFT:
            raise InvalidState("Characteristics can only be rolled while the investigator is a draft.")
        rolled = generation.roll_all(rng).values
        changes: Dict[str, object] = {CHAR_COLUMNS[k]: rolled[k] for k in CHARACTERISTICS}
        changes["luck"] = rolled["LUCK"]
        changes["creation_step"] = "occupation"
        changes.update(_clamp_resources_for(inv, changes))
        return self._sync_allocations(self._write(inv, changes))

    def set_skill(self, actor_id: object, investigator_id: int, skill: str, value: object) -> Investigator:
        """Set a trained skill value. ``value`` blank/None removes it (back to base)."""
        inv = self.get(actor_id, investigator_id, include_archived=False)
        sd = resolve_skill(skill, conn=self.conn)
        if sd is None:
            raise ValidationError(f"Unknown skill {skill!r}.")
        n = parse_int(value, sd.display_name, SKILL_MIN, SKILL_MAX)
        if inv.status == STATUS_DRAFT and inv.occupation_id:
            # Occupation-based creation: every skill value must come from points, so
            # hand-entered values would bypass the rules. Progression edits resume
            # once the investigator is finished.
            raise InvalidState(f"During occupation-based creation, skills are set with skill points "
                               f"(Occupation & skills, or /character allocate). {sd.display_name} was not changed.")
        if sd.key == "cthulhu_mythos" and n is not None and n > 99:
            raise ValidationError("Cthulhu Mythos cannot exceed 99 (Maximum SAN would be negative).")
        if n is None:
            repo.remove_skill(self.conn, inv.id, sd.key)
        else:
            repo.set_skill(self.conn, inv.id, sd.key, n)
        if sd.key == "cthulhu_mythos":
            # Maximum SAN = 99 - Cthulhu Mythos: a stored current SAN above the
            # new maximum is lowered to it (the implicit value is capped in the model).
            san_max = 99 - (n or 0)
            if inv.san_current is not None and inv.san_current > san_max:
                repo.update(self.conn, inv.id, {"san_current": san_max})
        self.conn.commit()
        return self.get(actor_id, inv.id)

    def set_resources(self, actor_id: object, investigator_id: int, **values: object) -> Investigator:
        """Current HP / MP / SAN / Luck. Blank resets HP/MP/SAN to the derived value."""
        inv = self.get(actor_id, investigator_id, include_archived=False)
        d = inv.derived()
        limits = {
            "hp": ("HP", "hp_current", d.hp_max),
            "mp": ("MP", "mp_current", d.mp_max),
            "san": ("SAN", "san_current", d.san_max),
        }
        changes: Dict[str, object] = {}
        for key, raw in values.items():
            if key == "luck":
                n = parse_int(raw, "Luck", generation.LUCK_MIN, generation.LUCK_MAX)
                if n is None:
                    raise ValidationError("Luck cannot be blank.")
                changes["luck"] = n
                continue
            if key not in limits:
                raise ValidationError(f"Unknown resource {key!r}.")
            label, column, maximum = limits[key]
            if maximum is None:
                raise InvalidState(f"Enter characteristics before tracking {label}.")
            changes[column] = parse_int(raw, label, 0, maximum)
        return self._write(inv, changes)

    def finalize(self, actor_id: object, investigator_id: int, confirm_unspent: bool = False) -> Investigator:
        inv = self.get(actor_id, investigator_id, include_archived=False)
        if inv.status == STATUS_READY:
            return inv
        if not inv.is_complete:
            raise InvalidState("Still missing: " + ", ".join(inv.missing_characteristics) + ".")
        if inv.occupation_id:
            # Occupation-based creation: the full skill-point rules must pass.
            from cocbot.occupations.service import CreationService
            check = CreationService(self.conn).check_for(inv)
            if not check.ok:
                raise InvalidState("Not finished yet:\n- " + "\n- ".join(check.errors))
            occ_left, int_left = check.unspent
            if (occ_left or int_left) and not confirm_unspent:
                raise UnspentPoints(occ_left, int_left)
            return self._write(inv, {"status": STATUS_READY, "creation_step": None, "creation_validated": 1})
        return self._write(inv, {"status": STATUS_READY, "creation_step": None})

    # --- lifecycle ---------------------------------------------------------------
    def archive(self, actor_id: object, investigator_id: int) -> Investigator:
        inv = self.get(actor_id, investigator_id)
        if inv.is_archived:
            return inv
        # trigger trg_investigators_archive_clears_active removes active slots
        return self._write(
            inv,
            {"status": STATUS_ARCHIVED, "status_before_archive": inv.status, "archived_at": _now(self.conn)},
        )

    def restore(self, actor_id: object, investigator_id: int) -> Investigator:
        inv = self.get(actor_id, investigator_id)
        if not inv.is_archived:
            return inv
        if repo.count_live(self.conn, inv.owner_user_id) >= MAX_LIVE_INVESTIGATORS:
            raise Conflict(f"You already have {MAX_LIVE_INVESTIGATORS} live investigators.")
        if repo.name_taken(self.conn, inv.owner_user_id, inv.name, exclude_id=inv.id):
            raise Conflict(f"Rename your other {inv.name!r} before restoring this one.")
        # Restore exactly the status it had; never infer "ready" from completeness.
        status = inv.status_before_archive if inv.status_before_archive in (STATUS_DRAFT, STATUS_READY) \
            else STATUS_DRAFT
        return self._write(inv, {"status": status, "status_before_archive": None, "archived_at": None})

    def delete(self, actor_id: object, investigator_id: int) -> None:
        """Permanent deletion. Only archived investigators can be deleted."""
        inv = self.get(actor_id, investigator_id)
        if not inv.is_archived:
            raise InvalidState("Archive the investigator first; only archived investigators can be deleted.")
        repo.delete(self.conn, inv.id)
        self.conn.commit()

    # --- active selection --------------------------------------------------------
    def select(self, actor_id: object, investigator_id: int, guild_id: Optional[object] = None,
               everywhere: bool = False) -> Investigator:
        """Make an investigator active in this server (default) or everywhere."""
        inv = self.get(actor_id, investigator_id, include_archived=False)
        scope = GLOBAL_SCOPE if everywhere or not guild_id else guild_scope(guild_id)
        repo.set_active(self.conn, inv.owner_user_id, scope, inv.id)
        self.conn.commit()
        return inv

    def clear_selection(self, actor_id: object, guild_id: Optional[object] = None,
                        everywhere: bool = False) -> bool:
        """Remove this server's selection (default) or the global default.

        Returns whether a selection existed. Clearing a server selection makes
        the global default apply here again.
        """
        scope = GLOBAL_SCOPE if everywhere or not guild_id else guild_scope(guild_id)
        existed = scope in repo.active_ids(self.conn, str(actor_id), [scope])
        repo.clear_active(self.conn, str(actor_id), scope)
        self.conn.commit()
        return existed

    # --- internals -----------------------------------------------------------------
    def _sync_allocations(self, inv: Investigator) -> Investigator:
        """Keep allocation-based skill values in step with characteristics (Dodge, Language (Own))."""
        if inv.occupation_id and inv.status == STATUS_DRAFT:
            from cocbot.occupations.service import sync_allocated_skills
            sync_allocated_skills(self.conn, inv)
            self.conn.commit()
            return self.get(inv.owner_user_id, inv.id)
        return inv

    def _write(self, inv: Investigator, changes: Mapping[str, object]) -> Investigator:
        try:
            repo.update(self.conn, inv.id, changes)
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise ValidationError(f"Rejected by the database: {e}") from None
        return self.get(inv.owner_user_id, inv.id)


def _clamp_resources_for(inv: Investigator, char_changes: Mapping[str, object]) -> Dict[str, object]:
    """Stored current HP/MP that would exceed the new maximum, lowered to it.

    Returned changes are written in the same UPDATE (one transaction) as the
    characteristic edit. Rules:
    * NULL current values mean "at maximum" and are left NULL, so they keep
      following the derived maximum automatically;
    * values at or below the new maximum are untouched (raising a maximum
      never heals or restores MP);
    * 0 is a real value (never treated as missing) and stays 0.
    If a maximum becomes unknown (a draft characteristic cleared) nothing is
    changed; the model still caps on read once it is known again.
    """
    merged = dict(inv.characteristics)
    for key, column in CHAR_COLUMNS.items():
        if column in char_changes:
            merged[key] = char_changes[column]  # type: ignore[assignment]
    d = compute_derived(merged, age=inv.age, cthulhu_mythos=inv.skills.get("cthulhu_mythos", 0))
    out: Dict[str, object] = {}
    for column, current, maximum in (("hp_current", inv.hp_current, d.hp_max),
                                     ("mp_current", inv.mp_current, d.mp_max)):
        if current is not None and maximum is not None and current > maximum:
            out[column] = maximum
    return out


def _now(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
