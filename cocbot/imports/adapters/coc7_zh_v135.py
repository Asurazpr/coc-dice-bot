"""Adapter for the community CoC 7e Chinese character sheet, v1.35 layout (sheet 人物卡).

Cell map and formula policy: docs/EXCEL_IMPORT.md §2-3. Detection checks fixed
labels at fixed cells; a workbook that does not match exactly is not parsed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from cocbot.imports.model import ERROR, INFO, WARNING, ParsedSheet, ParsedSkill, SkillCatalog
from cocbot.imports.workbook import Book, as_int, as_text, is_formula
from cocbot.mechanics.generation import CHARACTERISTIC_MAX, CHARACTERISTIC_MIN, LUCK_MAX, LUCK_MIN

SHEET = "人物卡"
OCC_SHEET = "职业列表"

# (cell, expected text fragment) — all must match
SIGNATURE = (("B3", "姓名"), ("I3", "STR"), ("L3", "DEX"), ("O3", "POW"), ("I5", "CON"), ("I7", "SIZ"),
             ("J10", "Luck"), ("B14", "技能名称"), ("I14", "成功率"), ("L14", "技能名称"), ("S14", "成功率"))

CHARACTERISTIC_CELLS = {"STR": "J3", "DEX": "M3", "POW": "P3", "CON": "J5", "APP": "M5", "EDU": "P5",
                        "SIZ": "J7", "INT": "M7"}
LUCK_CELL = "L10"
RESOURCE_CELLS = {"hp": "D10", "san": "H10", "mp": "P10"}
CACHED_DERIVED = {"hp_max": "E10", "san_max": "I10", "mp_max": "Q10", "mov": "P7", "damage_bonus": "T49",
                  "build": "T51", "dodge": "T53"}

# Skill blocks: name column, specialization column, initial, growth, occupation, interest, total
BLOCKS = (("B", "C", "E", "F", "G", "H", "I"), ("L", "M", "O", "P", "Q", "R", "S"))
FIRST_ROW, LAST_ROW = 15, 46
# rows whose name cell is merged over several rows: (block, first row) -> last row
GROUPS = {(0, 19): 21, (0, 33): 35, (0, 36): 38, (0, 43): 45, (1, 30): 32}
# rows where the player writes a specialization next to the generic name
SPECIALIZED = {(0, r) for r in (*range(19, 22), *range(33, 39), *range(43, 47))} | \
              {(1, r) for r in (26, 30, 31, 32, 36, 40)}
FREE_ROWS = {(1, r) for r in range(41, 47)}          # player-typed skill names
ERA_NAME_ROWS = {(0, 24): "电脑使用", (0, 31): "电子学"}   # =IF(F4="现代",...,"[不可用]")

# generic label -> how its specialization maps
SPEC_AS_SKILL = {"技艺", "科学", "格斗", "射击", "罕见"}            # specialization is a skill of its own
SPEC_ON_KEY = {"外语": "language_foreign", "母语": "language_own", "驾驶": "pilot", "生存": "survival"}

# template spellings that differ from the catalogue's Chinese names
ALIASES = {"侦察": "spot_hidden", "图书馆利用": "library_use", "电气维修": "electrical_repair",
           "电脑使用": "computer_use", "电子学": "electronics", "汽车驾驶": "drive_auto"}

NOTE_CELLS = {
    "player": "C4", "era": "F4", "sex": "F6", "cash": "C59", "spending_level": "G58", "background": "K65",
    "investigator_notes": "B73",
}
BACKSTORY_ROWS = range(58, 65)                        # K label, M value
WEAPON_ROWS = range(50, 55)
WEAPON_COLS = (("name", "B"), ("skill", "F"), ("damage", "I"), ("range", "K"), ("attacks", "M"), ("ammo", "N"),
               ("malfunction", "P"))

_SUM_RE = re.compile(r"^=SUM\(\s*([A-Z]{1,2})(\d+)\s*:\s*([A-Z]{1,2})(\d+)\s*\)$", re.IGNORECASE)


class Coc7ZhV135Adapter:
    key = "coc7_zh_v1.35"
    label = "CoC 7e 人物卡 v1.35 (Chinese)"

    def detect(self, book: Book) -> bool:
        if not book.has(SHEET):
            return False
        for ref, fragment in SIGNATURE:
            value = book.raw(SHEET, ref)
            if not isinstance(value, str) or fragment not in value:
                return False
        return True

    # --- helpers ---------------------------------------------------------------------------
    def _number(self, sheet: ParsedSheet, book: Book, ref: str, label: str, lo: int, hi: int) -> Optional[int]:
        raw = book.raw(SHEET, ref)
        where = f"{SHEET}!{ref}"
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        if is_formula(raw):
            value = as_int(book.cached(SHEET, ref))
            if value is None:
                sheet.add(WARNING, "imp_formula_no_value", where, field=label)
                return None
            sheet.add(WARNING, "imp_formula_cached", where, field=label, value=value)
        else:
            value = as_int(raw)
            if value is None:
                sheet.add(WARNING, "imp_not_a_number", where, field=label)
                return None
        if not lo <= value <= hi:
            sheet.add(WARNING, "imp_out_of_range", where, field=label, value=value, lo=lo, hi=hi)
            return None
        return value

    def _text(self, book: Book, ref: str, limit: int = 200) -> Optional[str]:
        raw = book.raw(SHEET, ref)
        if is_formula(raw):
            return as_text(book.cached(SHEET, ref), limit)
        return as_text(raw, limit)

    # --- parse ---------------------------------------------------------------------------------
    def parse(self, book: Book, catalog: SkillCatalog) -> ParsedSheet:
        s = ParsedSheet(template=self.key)
        s.name = self._text(book, "C3", 64)
        if not s.name:
            s.add(ERROR, "imp_no_name", f"{SHEET}!C3")
        s.occupation = self._text(book, "C5", 100)
        s.age = self._number(s, book, "C6", "age", 1, 150)
        s.residence = self._text(book, "C7", 100)
        s.birthplace = self._text(book, "C8", 100)
        for stat, ref in CHARACTERISTIC_CELLS.items():
            s.characteristics[stat] = self._number(s, book, ref, stat, CHARACTERISTIC_MIN, CHARACTERISTIC_MAX)
        s.luck = self._number(s, book, LUCK_CELL, "Luck", LUCK_MIN, LUCK_MAX)
        for res, ref in RESOURCE_CELLS.items():
            setattr(s, res, self._number(s, book, ref, res.upper(), 0, 999))
        if s.missing_characteristics:
            s.add(WARNING, "imp_missing_characteristics", fields=", ".join(s.missing_characteristics))
        self._occupation_index(s, book)
        for name, ref in CACHED_DERIVED.items():
            v = book.cached(SHEET, ref)
            if isinstance(v, str):
                v = v.strip().lstrip("/")
                v = as_int(v) if v.lstrip("+-").isdigit() else v
            s.cached_derived[name] = v
        era = self._text(book, "F4")
        self._skills(s, book, catalog, era)
        self._notes(s, book)
        return s

    def _occupation_index(self, s: ParsedSheet, book: Book) -> None:
        idx = as_int(book.raw(SHEET, "D12"))
        if not idx:
            return
        s.occupation_index = idx
        if not book.has(OCC_SHEET):
            return
        ws = book.formulas[OCC_SHEET]
        for row in range(2, min(ws.max_row or 0, 300) + 1):
            if as_int(ws.cell(row, 1).value) == idx:
                s.occupation_zh = as_text(ws.cell(row, 2).value, 100)
                break
        if not s.occupation and s.occupation_zh:
            s.occupation = s.occupation_zh
            s.add(INFO, "imp_occupation_from_index", f"{SHEET}!D12", occupation=s.occupation_zh)

    def _row_name(self, book: Book, block: int, row: int, era: Optional[str],
                  group_names: Dict[Tuple[int, int], str]) -> Optional[str]:
        name_col = BLOCKS[block][0]
        raw = book.raw(SHEET, f"{name_col}{row}")
        if (block, row) in ERA_NAME_ROWS:
            return ERA_NAME_ROWS[(block, row)] if (era or "现代") == "现代" else None
        if is_formula(raw):
            return as_text(book.cached(SHEET, f"{name_col}{row}"))
        text = as_text(raw)
        if text:
            return text.replace("\n", "")
        for (b, first), last in GROUPS.items():       # inside a merged group: inherit its label
            if b == block and first < row <= last:
                return group_names.get((b, first))
        return None

    def _part(self, s: ParsedSheet, book: Book, ref: str) -> int:
        raw = book.raw(SHEET, ref)
        if raw is None or (isinstance(raw, str) and not is_formula(raw)):
            return as_int(raw) or 0                   # blanks and '——' count as 0
        if is_formula(raw):
            return as_int(book.cached(SHEET, ref)) or 0
        return as_int(raw) or 0

    def _skills(self, s: ParsedSheet, book: Book, catalog: SkillCatalog, era: Optional[str]) -> None:
        group_names: Dict[Tuple[int, int], str] = {}
        occ_points = interest_points = 0
        for block, (_name_c, spec_c, init_c, grow_c, occ_c, int_c, total_c) in enumerate(BLOCKS):
            for row in range(FIRST_ROW, LAST_ROW + 1):
                name = self._row_name(book, block, row, era, group_names)
                if (block, row) in GROUPS and name:
                    group_names[(block, row)] = name
                if not name:
                    continue
                spec = as_text(book.raw(SHEET, f"{spec_c}{row}")) if (block, row) in SPECIALIZED else None
                if is_formula(book.raw(SHEET, f"{spec_c}{row}")):
                    spec = as_text(book.cached(SHEET, f"{spec_c}{row}"))
                where = f"{SHEET}!{total_c}{row}"
                growth, occ, interest = (self._part(s, book, f"{c}{row}") for c in (grow_c, occ_c, int_c))
                occ_points += occ
                interest_points += interest
                key, spec_key = self._map(name, spec, catalog)
                if (block, row) in SPECIALIZED and not spec and not (growth or occ or interest):
                    continue                                  # an unused specialization row
                if (block, row) in SPECIALIZED and name in SPEC_AS_SKILL and not spec:
                    if growth or occ or interest:
                        s.add(WARNING, "imp_unnamed_specialization", where, label=name)
                        s.notes.setdefault("unmapped_skills", []).append(
                            {"label": name, "value": None, "where": where})
                    continue
                initial_raw = book.raw(SHEET, f"{init_c}{row}")
                if is_formula(initial_raw) or initial_raw is None:
                    initial = catalog.base(key, s.characteristics) if key else as_int(book.cached(SHEET,
                                                                                                  f"{init_c}{row}"))
                    if initial is None and not (growth or occ or interest):
                        continue                              # base unknown (e.g. DEX missing), nothing added
                    initial = initial or 0
                else:
                    initial = as_int(initial_raw) or 0
                total = self._total(s, book, total_c, init_c, int_c, row, initial + growth + occ + interest, name)
                label = f"{name}: {spec}" if spec else name
                parts = {"initial": initial, "growth": growth, "occupation": occ, "interest": interest}
                if total is None:
                    continue
                if not 0 <= total <= 999:
                    s.add(WARNING, "imp_out_of_range", where, field=label, value=total, lo=0, hi=999)
                    continue
                if key is None:
                    if (block, row) in FREE_ROWS and total == 0:
                        continue
                    s.add(WARNING, "imp_unknown_skill", where, label=label, value=total)
                    s.notes.setdefault("unmapped_skills", []).append({"label": label, "value": total,
                                                                       "where": where})
                    continue
                s.skills.append(ParsedSkill(label, key, total, where, spec_key, parts))
        s.notes["points_on_sheet"] = {"occupation": occ_points, "interest": interest_points}

    def _total(self, s: ParsedSheet, book: Book, total_c: str, init_c: str, int_c: str, row: int,
               parts_sum: int, label: str) -> Optional[int]:
        ref = f"{total_c}{row}"
        raw = book.raw(SHEET, ref)
        cached = as_int(book.cached(SHEET, ref))
        where = f"{SHEET}!{ref}"
        if is_formula(raw):
            m = _SUM_RE.match(raw.replace(" ", ""))
            if m and m.group(1).upper() == init_c and m.group(3).upper() == int_c and \
                    int(m.group(2)) == row == int(m.group(4)):
                if cached is not None and cached != parts_sum:
                    s.add(INFO, "imp_stale_cached", where, field=label, cached=cached, value=parts_sum)
                return parts_sum
            if cached is None:
                s.add(WARNING, "imp_formula_no_value", where, field=label)
                return None
            s.add(WARNING, "imp_formula_cached", where, field=label, value=cached)
            return cached
        value = as_int(raw)
        if value is None:
            return parts_sum if raw is None and parts_sum else None
        if value != parts_sum:
            s.add(WARNING, "imp_total_typed", where, field=label, value=value, parts=parts_sum)
        return value

    def _map(self, name: str, spec: Optional[str], catalog: SkillCatalog) -> Tuple[Optional[str], Optional[str]]:
        """(skill key, specialization stored alongside it)."""
        base = name.split("(")[0].split("（")[0].strip()
        if spec and base in SPEC_AS_SKILL:
            return ALIASES.get(spec) or catalog.resolve(spec), None
        if base in SPEC_ON_KEY:
            return SPEC_ON_KEY[base], spec
        return ALIASES.get(base) or catalog.resolve(base), None

    def _notes(self, s: ParsedSheet, book: Book) -> None:
        for name, ref in NOTE_CELLS.items():
            text = self._text(book, ref, 1000)
            if text:
                s.notes[name] = text
        backstory = {}
        for row in BACKSTORY_ROWS:
            label, value = self._text(book, f"K{row}", 40), self._text(book, f"M{row}", 500)
            if label and value:
                backstory[label] = value
        if backstory:
            s.notes["backstory"] = backstory
        weapons = []
        for row in WEAPON_ROWS:
            w = {k: self._text(book, f"{col}{row}", 60) for k, col in WEAPON_COLS}
            if w.get("name") and w["name"] != "空手战斗":
                weapons.append({k: v for k, v in w.items() if v})
        if weapons:
            s.notes["weapons"] = weapons
        items = [t for t in (self._text(book, f"B{r}", 200) for r in range(64, 71)) if t]
        if items:
            s.notes["possessions"] = items
        mythos = [t for t in (self._text(book, f"P{r}", 200) for r in range(77, 91)) if t]
        if mythos:
            s.notes["mythos"] = mythos


__all__: List[Any] = ["Coc7ZhV135Adapter"]
