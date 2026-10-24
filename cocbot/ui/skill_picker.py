"""Discord-independent logic for the skill picker (categories, search, paging).

The picker never puts every skill into one dropdown: Discord select menus hold
at most 25 options. Instead, when more than 25 skills are available the player
picks a category first (each category has ≤ 25 skills), or searches by English
or Chinese name/alias. Small option sets (e.g. "Climb or Swim") are shown
directly. Search always covers both languages, whatever the display language.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

PAGE_SIZE = 25

# (group key, English, Chinese, skills in display order). Every group has <= 25 skills.
GROUPS: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    ("social", "Social", "社交", ("charm", "fast_talk", "intimidate", "persuade", "psychology", "psychoanalysis",
                                 "disguise", "hypnosis", "credit_rating")),
    ("perception", "Perception & research", "感知与调查", ("spot_hidden", "listen", "library_use", "track",
                                                        "read_lips", "appraise", "navigate")),
    ("knowledge", "Knowledge", "学识", ("accounting", "anthropology", "archaeology", "history", "law", "medicine",
                                       "natural_world", "occult", "language_own", "language_foreign",
                                       "cthulhu_mythos")),
    ("physical", "Physical & outdoors", "体能与野外", ("climb", "dodge", "jump", "swim", "throw", "stealth", "ride",
                                                    "diving", "survival", "first_aid", "animal_handling")),
    ("technical", "Technical & vehicles", "技术与载具", ("computer_use", "electrical_repair", "electronics",
                                                      "mechanical_repair", "operate_heavy_machinery", "locksmith",
                                                      "sleight_of_hand", "drive_auto", "pilot", "demolitions",
                                                      "artillery")),
    ("craft", "Art & Craft", "技艺", ("acting", "fine_art", "photography", "forgery", "literature", "calligraphy",
                                     "music_theory", "cuisine")),
    ("science", "Science", "科学", ("astronomy", "biology", "botany", "chemistry", "cryptography", "engineering",
                                   "forensic", "geology", "mathematics", "meteorology", "pharmacy", "physics",
                                   "zoology")),
    ("fighting", "Fighting", "格斗", ("brawl", "axe", "chainsaw", "flail", "garrote", "spear", "sword", "whip")),
    ("firearms", "Firearms", "射击", ("handgun", "rifle_shotgun", "submachine_gun", "machine_gun", "bow",
                                     "flamethrower", "heavy_weapon")),
)
OTHER_GROUP = ("other", "Other", "其他")
_GROUP_OF: Dict[str, str] = {k: g for g, _, _, keys in GROUPS for k in keys}
_ORDER: Dict[str, int] = {k: n for n, (_, _, _, keys) in enumerate(GROUPS) for k in keys}
_ORDER_IN_GROUP: Dict[str, int] = {k: n for _, _, _, keys in GROUPS for n, k in enumerate(keys)}


def group_of(key: str) -> str:
    return _GROUP_OF.get(key, OTHER_GROUP[0])


_GROUP_LABELS: Dict[str, Tuple[str, str]] = {g: (en, zh) for g, en, zh, _ in GROUPS}


def group_label(group: str, lang: str) -> str:
    en, zh = _GROUP_LABELS.get(group, OTHER_GROUP[1:])
    if lang == "zh":
        return zh
    return f"{en} · {zh}" if lang == "both" else en


def sort_key(key: str) -> Tuple[int, int, str]:
    return (_ORDER.get(key, len(GROUPS)), _ORDER_IN_GROUP.get(key, 0), key)


# --- search -------------------------------------------------------------------------------

def build_search_index(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """skill key -> lowercased search terms: key, EN/ZH names and every alias."""
    index: Dict[str, List[str]] = {}
    for (key,) in conn.execute("SELECT key FROM skill_defs"):
        index[key] = [key.lower(), key.replace("_", " ").lower()]
    for key, name in conn.execute(
        "SELECT sd.key, i.name FROM skill_def_i18n i JOIN skill_defs sd ON sd.skill_id = i.skill_id"
    ):
        index.setdefault(key, []).append(str(name).lower())
    for key, alias in conn.execute(
        "SELECT sd.key, a.alias FROM skill_def_aliases a JOIN skill_defs sd ON sd.skill_id = a.skill_id"
    ):
        index.setdefault(key, []).append(str(alias).lower())
    return index


def search(index: Mapping[str, Sequence[str]], available: Sequence[str], query: str) -> List[str]:
    """Available skills whose key/name/alias contains the query (any language).

    Exact matches come first, then prefix matches, then other substring matches.
    """
    q = " ".join((query or "").lower().split())
    if not q:
        return list(available)
    scored = []
    for k in available:
        terms = index.get(k, [k])
        if any(t == q for t in terms):
            rank = 0
        elif any(t.startswith(q) for t in terms):
            rank = 1
        elif any(q in t for t in terms):
            rank = 2
        else:
            continue
        scored.append((rank, sort_key(k), k))
    return [k for _, _, k in sorted(scored)]


# --- one screen of the picker ------------------------------------------------------------------

@dataclass
class PickerPage:
    mode: str                                   # "all" | "categories" | "category" | "search"
    keys: List[str]                             # skills on this page (<= PAGE_SIZE)
    page: int
    pages: int
    total: int                                  # skills matching the current filter
    available: int                              # all skills selectable right now
    category: Optional[str] = None
    query: str = ""
    categories: List[Tuple[str, int]] = field(default_factory=list)   # (group, count) with count > 0

    @property
    def needs_category(self) -> bool:
        return self.mode == "categories"


def picker_page(available: Sequence[str], index: Mapping[str, Sequence[str]], category: Optional[str] = None,
                query: str = "", page: int = 0, page_size: int = PAGE_SIZE,
                keep_order: bool = False) -> PickerPage:
    """Which skills to show for the current filter.

    * a query searches all available skills (both languages);
    * <= page_size available skills: show them all, no category step;
    * otherwise a category must be chosen (or a search made) first.
    ``keep_order`` preserves the caller's order (e.g. an occupation's option list).
    """
    avail = list(available) if keep_order else sorted(available, key=sort_key)
    counts: Dict[str, int] = {}
    for k in avail:
        counts[group_of(k)] = counts.get(group_of(k), 0) + 1
    order = [g for g, _, _, _ in GROUPS] + [OTHER_GROUP[0]]
    cats = [(g, counts[g]) for g in order if counts.get(g)]

    if query.strip():
        keys, mode = search(index, avail, query), "search"
    elif len(avail) <= page_size:
        keys, mode = avail, "all"
    elif category and counts.get(category):
        keys, mode = [k for k in avail if group_of(k) == category], "category"
    else:
        keys, mode, category = [], "categories", None

    pages = max(1, -(-len(keys) // page_size))
    page = min(max(0, page), pages - 1)
    return PickerPage(mode, keys[page * page_size:(page + 1) * page_size], page, pages, len(keys), len(avail),
                      category if mode == "category" else None, query.strip() if mode == "search" else "", cats)
