import json
import unittest
from pathlib import Path

from cocbot.occupations.zh_parser import CATEGORY_OPTIONS, parse_formula_zh, parse_skill_text

CATALOGUE = Path(__file__).resolve().parents[1] / "data" / "occupations" / "occupations.json"


def shape(text):
    return [(s.kind, s.count, s.options if len(s.options) <= 4 else len(s.options)) for s in parse_skill_text(text).slots]


class ParserTests(unittest.TestCase):
    def test_fixed_and_any(self):
        p = parse_skill_text("会计，法律，图书馆，聆听，说服，侦查，任意其他两项个人或时代特长。")
        self.assertEqual(p.skill_count, 8)
        self.assertEqual(p.issues, [])
        self.assertEqual(p.slots[-1].kind, "any")
        self.assertEqual(p.slots[-1].count, 2)

    def test_social_group_and_alternatives(self):
        self.assertEqual(shape("两项社交技能（魅惑、话术、恐吓、说服）")[0], ("choose", 2, CATEGORY_OPTIONS["social"]))
        self.assertEqual(shape("聆听或侦查")[0], ("choose", 1, ("listen", "spot_hidden")))
        self.assertEqual(shape("格斗（斗殴）或射击（手枪）")[0], ("choose", 1, ("brawl", "handgun")))

    def test_category_inside_alternative(self):
        kind, count, n = shape("导航或科学（任一：如化学、物理、地理等）")[0]
        self.assertEqual((kind, count, n), ("choose", 1, 1 + len(CATEGORY_OPTIONS["science"])))

    def test_science_lists(self):
        self.assertEqual(shape("科学（生物学，化学）"), [("fixed", 1, ("biology",)), ("fixed", 1, ("chemistry",))])
        self.assertEqual(shape("科学（生物学或植物学）"), [("choose", 1, ("biology", "botany"))])
        self.assertEqual(shape("科学（化学和任意两项）"), [("fixed", 1, ("chemistry",)), ("choose", 2, 13)])

    def test_pick_from_list_below(self):
        p = parse_skill_text("潜行，生存，下面任选两项：急救、机械维修、外语。")
        self.assertEqual(p.slots[-1].kind, "choose")
        self.assertEqual(p.slots[-1].count, 2)
        self.assertEqual(p.slots[-1].options, ("first_aid", "mechanical_repair", "language_foreign"))
        p = parse_skill_text("侦查和下面的一种个人特长：汽车驾驶或骑乘。")
        self.assertEqual([(s.kind, s.options) for s in p.slots],
                         [("fixed", ("spot_hidden",)), ("choose", ("drive_auto", "ride"))])

    def test_category_phrase_not_split(self):
        p = parse_skill_text("图书馆，外语，母语，心理学，任意四项其他学术、时代或个人特长。")
        self.assertEqual(p.skill_count, 8)
        self.assertFalse([i for i in p.issues if "unmapped" in i])

    def test_unmapped_is_reported_not_guessed(self):
        p = parse_skill_text("会计，飞天遁地，聆听。")
        self.assertTrue(any("unmapped phrase" in i and "飞天遁地" in i for i in p.issues))
        self.assertTrue(any("2 occupation skills" in i for i in p.issues))

    def test_keeper_note_split_off(self):
        p = parse_skill_text("会计。※经KP允许 可用催眠替换其中一项。")
        self.assertEqual(p.keeper_note_zh, "经KP允许 可用催眠替换其中一项。")

    def test_formulas(self):
        self.assertEqual(parse_formula_zh("教育×4"), [{"stats": ["EDU"], "mult": 4}])
        self.assertEqual(parse_formula_zh("教育×2＋外貌或敏捷或力量×2"),
                         [{"stats": ["EDU"], "mult": 2}, {"stats": ["APP", "DEX", "STR"], "mult": 2}])


class CommittedCatalogueTests(unittest.TestCase):
    def test_catalogue_is_consistent(self):
        data = json.loads(CATALOGUE.read_text(encoding="utf-8"))
        occs = data["occupations"]
        self.assertEqual(len(occs), 113)
        keys = [o["key"] for o in occs]
        self.assertEqual(len(keys), len(set(keys)))
        for o in occs:
            with self.subTest(o["key"]):
                self.assertEqual(sum(s["count"] for s in o["slots"]), 8)
                lo, hi = o["credit_rating"]
                self.assertTrue(0 <= lo <= hi <= 99)
                self.assertTrue(o["skill_points"])
                self.assertFalse([i for i in o["issues"] if "unmapped" in i])


if __name__ == "__main__":
    unittest.main()
