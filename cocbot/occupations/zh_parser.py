"""Parse the Chinese occupation skill lists of the community character sheet
(``data/seed/COC七版人物卡v1.35.xlsx`` sheet ``职业列表``) into structured slots.

Development-time only: the bot never runs this. ``scripts/import_occupations.py``
uses it to regenerate ``data/occupations/occupations.json``, which is reviewed
and committed. Anything the parser cannot map exactly is recorded as an
*issue* on the occupation instead of being guessed silently.

Slot kinds
----------
``fixed``   one specific skill (options has exactly one key)
``choose``  pick ``count`` distinct skills from ``options``
``any``     pick ``count`` distinct skills from all skills (except Credit
            Rating and Cthulhu Mythos), i.e. "personal or era specialties"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

CATEGORY_OPTIONS: Dict[str, Tuple[str, ...]] = {
    "craft": ("acting", "calligraphy", "cuisine", "fine_art", "forgery", "literature", "music_theory", "photography"),
    "combat": ("axe", "brawl", "chainsaw", "flail", "garrote", "spear", "sword", "whip"),
    "shooting": ("bow", "flamethrower", "handgun", "heavy_weapon", "machine_gun", "rifle_shotgun", "submachine_gun"),
    "science": ("astronomy", "biology", "botany", "chemistry", "cryptography", "engineering", "forensic",
                "geology", "mathematics", "meteorology", "pharmacy", "physics", "zoology"),
    "social": ("charm", "fast_talk", "intimidate", "persuade"),
    "language": ("language_foreign", "language_own"),
}

# Exact phrases -> canonical skill keys.
_SKILLS: Dict[str, str] = {
    "会计": "accounting", "法律": "law", "图书馆": "library_use", "图书馆使用": "library_use",
    "聆听": "listen", "说服": "persuade", "侦查": "spot_hidden", "侦察": "spot_hidden",
    "攀爬": "climb", "闪避": "dodge", "跳跃": "jump", "投掷": "throw", "游泳": "swim",
    "乔装": "disguise", "历史": "history", "心理学": "psychology", "汽车驾驶": "drive_auto",
    "医学": "medicine", "母语": "language_own", "精神分析": "psychoanalysis", "自然": "natural_world",
    "潜行": "stealth", "追踪": "track", "估价": "appraise", "考古": "archaeology",
    "导航": "navigate", "计算机": "computer_use", "急救": "first_aid", "电工维修": "electrical_repair",
    "电气维修": "electrical_repair", "电工": "electrical_repair", "电子学": "electronics", "骑乘": "ride",
    "机械维修": "mechanical_repair", "锁匠": "locksmith", "妙手": "sleight_of_hand", "恐吓": "intimidate",
    "操作重型机械": "operate_heavy_machinery", "神秘学": "occult", "话术": "fast_talk", "魅惑": "charm",
    "人类学": "anthropology", "潜水": "diving", "动物驯养": "animal_handling", "催眠": "hypnosis",
    "外语": "language_foreign", "生存": "survival", "驾驶": "pilot",
}

_CRAFT: Dict[str, str] = {"表演": "acting", "摄影": "photography", "伪造": "forgery", "文学": "literature"}
_FIGHTING: Dict[str, str] = {"斗殴": "brawl", "链锯": "chainsaw"}
_FIREARMS: Dict[str, str] = {"手枪": "handgun", "步枪/霰弹枪": "rifle_shotgun"}
_SCIENCE: Dict[str, str] = {
    "地质": "geology", "天文": "astronomy", "动物学": "zoology", "数学": "mathematics", "生物": "biology",
    "生物学": "biology", "化学": "chemistry", "制药": "pharmacy", "鉴证": "forensic", "工程学": "engineering",
    "物理": "physics", "植物学": "botany",
}
_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5}


class Unmapped(ValueError):
    pass


@dataclass
class Slot:
    kind: str                      # fixed | choose | any
    count: int
    options: Tuple[str, ...]       # empty for "any"
    source: str                    # the original phrase, for review
    note: Optional[str] = None     # approximation explanation

    def as_dict(self) -> dict:
        d = {"kind": self.kind, "count": self.count, "options": list(self.options), "source_zh": self.source}
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class Parsed:
    slots: List[Slot] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    keeper_note_zh: Optional[str] = None

    @property
    def skill_count(self) -> int:
        return sum(s.count for s in self.slots)


def _split_top(text: str, seps: str) -> List[str]:
    """Split on separator characters that are outside （…） / (…)."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if ch in seps and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [p.strip() for p in out if p.strip()]


def _outside_parens(text: str) -> str:
    out, depth = "", 0
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif depth == 0:
            out += ch
    return out


def _inner(text: str) -> Tuple[str, Optional[str]]:
    m = re.fullmatch(r"([^（(]+)[（(](.*)[）)]", text)
    return (m.group(1).strip(), m.group(2).strip()) if m else (text.strip(), None)


def _atom(text: str, notes: List[str]) -> Tuple[str, ...]:
    """One skill phrase (no top-level 或) -> option keys."""
    head, inner = _inner(text)
    if head in ("汽车驾驶",):                       # 汽车驾驶（或运货马车）
        return ("drive_auto",)
    if head in ("外语", "生存", "驾驶"):
        if inner and inner not in ("任一",):
            notes.append(f"{text}: specialization '{inner}' kept as generic {_SKILLS[head]}")
        return (_SKILLS[head],)
    if head == "语言":
        if inner is None:
            notes.append(f"{text}: unspecified language, offered as Own or Foreign")
        return CATEGORY_OPTIONS["language"]
    if head == "技艺":
        if inner is None or inner.startswith("任一") or inner == "任意":
            return CATEGORY_OPTIONS["craft"]
        if inner in _CRAFT:
            return (_CRAFT[inner],)
        if inner == "技艺或摄影":
            notes.append(f"{text}: 'Art' approximated as Fine Art")
            return ("fine_art", "photography")
        notes.append(f"{text}: specialization '{inner}' not in the skill catalogue; any Art/Craft offered")
        return CATEGORY_OPTIONS["craft"]
    if head == "格斗":
        if inner is None:
            return CATEGORY_OPTIONS["combat"]
        if inner in _FIGHTING:
            return (_FIGHTING[inner],)
        raise Unmapped(text)
    if head == "射击":
        if inner is None:
            return CATEGORY_OPTIONS["shooting"]
        if inner in _FIREARMS:
            return (_FIREARMS[inner],)
        raise Unmapped(text)
    if head == "科学":
        if inner is None or inner.startswith("任一"):
            return CATEGORY_OPTIONS["science"]
        alts = [a.strip() for a in inner.split("或")]
        if all(a in _SCIENCE for a in alts):
            return tuple(_SCIENCE[a] for a in alts)
        raise Unmapped(text)
    if inner is None and head in _SKILLS:
        return (_SKILLS[head],)
    raise Unmapped(text)


def _alternatives(text: str, notes: List[str]) -> Tuple[str, ...]:
    keys: List[str] = []
    for part in _split_top(text, "或"):
        for k in _atom(part, notes):
            if k not in keys:
                keys.append(k)
    return tuple(keys)


_COUNT_RE = re.compile(r"^(?:任意|任)?(?:其他)?([一两二三四五])(?:项|个|种)(.*)$")


def _token(tok: str, out: Parsed) -> None:
    notes: List[str] = []
    m = _COUNT_RE.match(tok)
    if m:
        n, rest = _NUM[m.group(1)], m.group(2)
        if "社交技能" in rest:
            out.slots.append(Slot("choose", n, CATEGORY_OPTIONS["social"], tok))
        elif "科学" in rest:
            out.slots.append(Slot("choose", n, CATEGORY_OPTIONS["science"], tok))
        else:
            note = None
            if "学术" in rest or "专业" in rest:
                note = "restriction to academic/study skills is not enforced; agree with your Keeper"
            out.slots.append(Slot("any", n, (), tok, note))
        return

    # Science with a list inside: 科学（生物学，化学） = each one; （化学和任意两项）
    head, inner = _inner(tok)
    if head == "科学" and inner and "，" in inner:
        for part in inner.split("，"):
            out.slots.append(Slot("fixed", 1, (_SCIENCE[part.strip()],), tok))
        return
    if head == "科学" and inner and "和任意" in inner:
        first, rest = inner.split("和任意")
        out.slots.append(Slot("fixed", 1, (_SCIENCE[first.strip()],), tok))
        out.slots.append(Slot("choose", _NUM[rest[0]], CATEGORY_OPTIONS["science"], tok))
        return
    if head == "技艺" and inner == "任二":
        out.slots.append(Slot("choose", 2, CATEGORY_OPTIONS["craft"], tok))
        return

    opts = _alternatives(tok, notes)
    kind = "fixed" if len(opts) == 1 else "choose"
    out.slots.append(Slot(kind, 1, opts, tok, "; ".join(notes) or None))


def parse_skill_text(text: str) -> Parsed:
    out = Parsed()
    text = (text or "").strip()
    if "※" in text:
        text, note = text.split("※", 1)
        out.keeper_note_zh = note.strip()
    text = text.strip().rstrip("。.")
    # "学术、时代或个人特长" is one category phrase, not two list items
    text = re.sub(r"(学术|时代|个人)、", r"\1或", text)
    tokens = _split_top(text, "，、,")

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        try:
            if "：" in _outside_parens(tok):
                lead, first = tok.split("：", 1)
                pool = [first] + tokens[i + 1:]
                i = len(tokens)
                m = re.search(r"任选([一两二三四])项", lead)
                if "和下面的一种" in lead:
                    _token(lead.split("和")[0], out)
                    n = 1
                elif m:
                    n = _NUM[m.group(1)]
                else:
                    raise Unmapped(tok)
                notes: List[str] = []
                opts: List[str] = []
                for p in pool:
                    for k in _alternatives(p, notes):
                        if k not in opts:
                            opts.append(k)
                out.slots.append(Slot("choose", n, tuple(opts), "：".join([lead, "、".join(pool)]),
                                      "; ".join(notes) or None))
                continue
            _token(tok, out)
        except (Unmapped, KeyError) as e:
            out.issues.append(f"unmapped phrase: {tok!r} ({e})")
        i += 1

    for s in out.slots:
        if s.note:
            out.issues.append(f"approximation: {s.note}")
    if out.skill_count != 8:
        out.issues.append(f"{out.skill_count} occupation skills parsed (7e occupations list 8 plus Credit Rating)")
    return out


# --- skill-point formulas ----------------------------------------------------

_STAT_ZH = {"教育": "EDU", "敏捷": "DEX", "力量": "STR", "外貌": "APP", "意志": "POW", "体质": "CON", "智力": "INT"}


def parse_formula_zh(text: Optional[str]) -> List[dict]:
    """'教育×2＋外貌或敏捷×2' -> [{"stats": ["EDU"], "mult": 2}, {"stats": ["APP", "DEX"], "mult": 2}]"""
    if not text:
        raise Unmapped("empty formula")
    terms = []
    for part in re.split(r"[＋+]", text.replace(" ", "")):
        m = re.fullmatch(r"(.+)[×x*](\d+)", part)
        if not m:
            raise Unmapped(part)
        stats = [_STAT_ZH[s] for s in m.group(1).split("或")]
        terms.append({"stats": sorted(stats), "mult": int(m.group(2))})
    return terms


def formula_from_career_xlsx(text: Optional[str]) -> Optional[List[dict]]:
    """'EDU*2+STR/DEX*2' -> same structure; used only to cross-check sources."""
    if not text:
        return None
    terms = []
    for part in text.replace(" ", "").split("+"):
        stats, mult = part.rsplit("*", 1)
        terms.append({"stats": sorted(stats.split("/")), "mult": int(mult)})
    return terms


def all_keys(slots: Sequence[Slot]) -> List[str]:
    return [k for s in slots for k in s.options]
