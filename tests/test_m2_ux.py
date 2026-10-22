"""M2 UX polish: atomic batch allocation, skill picker, language preference."""
import os
import tempfile
import unittest

from cocbot.characters.service import InvestigatorService
from cocbot.i18n import CATALOG, Msg, label, localize, localized_names, occupation_name, render, skill_names
from cocbot.occupations import catalogue
from cocbot.occupations.rules import MODE_ADD, MODE_SET
from cocbot.occupations.service import BatchRejected, CreationService, load_points
from cocbot.preferences import get_language, language_from_locale, set_language
from cocbot.ui.creation_text import batch_result_text, budget_line, parse_points, rows_table
from cocbot.ui.skill_picker import GROUPS, build_search_index, group_of, picker_page, search
from tests.helpers import fresh_db

ALICE, BOB = "1001", "1002"
# EDU 75 -> Accountant EDU×4 = 300 occupation points; INT 80 -> 160 interest points
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.inv_svc, self.svc = InvestigatorService(self.conn), CreationService(self.conn)
        self.inv = self.inv_svc.create(ALICE, "Ada")
        self.inv_svc.set_characteristics(ALICE, self.inv.id, FULL)
        self.svc.set_occupation(ALICE, self.inv.id, "accountant")
        self.svc.set_pick(ALICE, self.inv.id, 6, 0, "psychology")
        self.svc.set_pick(ALICE, self.inv.id, 6, 1, "charm")

    def tearDown(self):
        self.conn.close()

    def points(self):
        return load_points(self.conn, self.inv.id)

    def batch(self, proposals, mode=MODE_SET):
        return self.svc.allocate_batch(ALICE, self.inv.id, proposals, mode)


class AtomicBatchTests(Base):
    def test_whole_batch_saved_together(self):
        inv, result = self.batch({"accounting": (60, None), "law": (40, None), "climb": (None, 30)})
        self.assertEqual(self.points(), {"accounting": (60, 0), "law": (40, 0), "climb": (0, 30)})
        self.assertEqual(inv.skills["accounting"], 65)
        self.assertEqual(result.remaining_after, (200, 130))
        self.assertEqual(sorted(c.key for c in result.changes), ["accounting", "climb", "law"])

    def test_one_invalid_field_saves_nothing(self):
        self.batch({"accounting": (10, None)})
        with self.assertRaises(BatchRejected) as cm:
            self.batch({"accounting": (60, None), "listen": (85, None)})       # listen 20 + 85 > 99
        self.assertEqual(self.points(), {"accounting": (10, 0)})               # unchanged
        self.assertEqual([e.code for e in cm.exception.result.errors], ["batch_over_max"])
        self.assertIn("Listen would be 105", str(cm.exception.result.errors[0]))

    def test_budget_overflow_reports_exact_amount(self):
        self.batch({"accounting": (60, None), "law": (60, None), "listen": (60, None), "persuade": (60, None)})
        with self.assertRaises(BatchRejected) as cm:                          # 240 + 45 + 30 = 315 > 300
            self.batch({"spot_hidden": (45, None), "library_use": (30, None)})
        errors = cm.exception.result.errors
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "batch_occ_over")
        self.assertEqual((errors[0].params["over"], errors[0].params["after"], errors[0].params["total"],
                          errors[0].params["left"]), (15, 315, 300, 60))
        self.assertIn("exceed the budget by 15", str(errors[0]))
        self.assertNotIn("spot_hidden", self.points())

    def test_refund_and_reallocate_in_one_batch_when_fully_spent(self):
        self.batch({"accounting": (70, None), "law": (70, None), "listen": (70, None), "persuade": (70, None),
                    "spot_hidden": (20, None)})                                # exactly 300
        self.assertEqual(self.svc.check(ALICE, self.inv.id).occupation_remaining, 0)
        # increasing alone overflows ...
        with self.assertRaises(BatchRejected):
            self.batch({"psychology": (30, None)})
        # ... but decreasing one and increasing another together is fine
        _, result = self.batch({"accounting": (40, None), "psychology": (30, None)})
        self.assertEqual(result.remaining_after[0], 0)
        self.assertEqual(self.points()["accounting"], (40, 0))
        self.assertEqual(self.points()["psychology"], (30, 0))

    def test_order_independence(self):
        self.batch({"accounting": (70, None), "law": (70, None), "listen": (70, None), "persuade": (70, None),
                    "spot_hidden": (20, None)})
        # same edits listed in the opposite order give the same result
        _, r1 = self.svc.allocate_batch(ALICE, self.inv.id, {"psychology": (30, None), "accounting": (40, None)})
        self.assertEqual(r1.remaining_after[0], 0)

    def test_add_mode_deltas_and_refunds(self):
        self.batch({"accounting": (50, None), "climb": (None, 20)})
        _, result = self.batch({"accounting": (-20, None), "law": (+20, None), "climb": (None, +5)}, MODE_ADD)
        self.assertEqual(self.points(), {"accounting": (30, 0), "law": (20, 0), "climb": (0, 25)})
        self.assertEqual(result.spent_before, (50, 20))
        self.assertEqual(result.spent_after, (50, 25))
        with self.assertRaises(BatchRejected) as cm:
            self.batch({"law": (-30, None)}, MODE_ADD)
        self.assertEqual(cm.exception.result.errors[0].code, "batch_remove_too_many")
        self.assertIn("only 20 are allocated", str(cm.exception.result.errors[0]))

    def test_rules_still_enforced_per_skill(self):
        cases = {
            "occupation points on a non-occupation skill": {"climb": (10, None)},
            "Cthulhu Mythos": {"cthulhu_mythos": (None, 5)},
            "Credit Rating above range": {"credit_rating": (71, None)},
            "interest budget": {"climb": (None, 79), "swim": (None, 79), "jump": (None, 3)},
        }
        for what, proposal in cases.items():
            with self.subTest(what), self.assertRaises(BatchRejected):
                self.batch(proposal)
        self.assertEqual(self.points(), {})
        # interest points on Credit Rating within range are allowed
        self.batch({"credit_rating": (30, 10)})
        self.assertEqual(self.inv_svc.get(ALICE, self.inv.id).skills["credit_rating"], 40)

    def test_overspent_budget_can_be_reduced(self):
        for k in ("accounting", "law", "library_use", "listen"):
            self.batch({k: (70, None)})                                        # 280 of 300
        self.inv_svc.set_characteristics(ALICE, self.inv.id, {"EDU": 60})      # budget 240: 40 over
        _, result = self.batch({"accounting": (50, None)})                     # still 20 over, but lower
        self.assertEqual(result.spent_after[0], 260)
        with self.assertRaises(BatchRejected):
            self.batch({"law": (75, None)})                                    # increasing while over

    def test_unchanged_batch_is_a_no_op(self):
        self.batch({"accounting": (40, None)})
        _, result = self.batch({"accounting": (40, None), "law": (0, None)})
        self.assertEqual(result.changes, [])

    def test_ownership_and_draft_rules(self):
        from cocbot.characters.service import NotFound
        with self.assertRaises(NotFound):
            self.svc.allocate_batch(BOB, self.inv.id, {"accounting": (10, None)})
        self.assertEqual(self.points(), {})

    def test_batch_persists_after_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            inv_svc, svc = InvestigatorService(conn), CreationService(conn)
            inv = inv_svc.create(ALICE, "Ada")
            inv_svc.set_characteristics(ALICE, inv.id, FULL)
            svc.set_occupation(ALICE, inv.id, "accountant")
            svc.allocate_batch(ALICE, inv.id, {"accounting": (60, None), "law": (40, 10)})
            conn.close()
            conn2 = fresh_db(path)
            self.assertEqual(load_points(conn2, inv.id), {"accounting": (60, 0), "law": (40, 10)})
            self.assertEqual(InvestigatorService(conn2).get(ALICE, inv.id).skills["law"], 55)
            conn2.close()


class ParsePointsTests(unittest.TestCase):
    def test_set_mode(self):
        self.assertEqual(parse_points("40", "set", 0), 40)
        self.assertIsNone(parse_points("40", "set", 40))     # unchanged
        self.assertEqual(parse_points("", "set", 10), 0)     # blank clears
        self.assertIsNone(parse_points("", "set", 0))
        for bad in ("abc", "1.5", "4 0 0 0"):
            with self.subTest(bad), self.assertRaises(ValueError):
                parse_points(bad, "set", 0)

    def test_add_mode(self):
        self.assertEqual(parse_points("+10", "add", 40), 10)
        self.assertEqual(parse_points("-5", "add", 40), -5)
        self.assertEqual(parse_points("7", "add", 40), 7)
        self.assertIsNone(parse_points("", "add", 40))
        with self.assertRaises(ValueError):
            parse_points("ten", "add", 0)


class BatchFeedbackTextTests(Base):
    def test_feedback_lists_changes_and_remaining(self):
        names = skill_names(self.conn, "en")
        _, result = self.batch({"accounting": (60, None), "climb": (None, 20)})
        text = batch_result_text(result, names, "en", localized_names(self.conn))
        self.assertIn("Saved.", text)
        self.assertIn("Accounting: Occupation 0 → 60 (+60)", text)
        self.assertIn("Climb: Personal interest 0 → 20 (+20)", text)
        self.assertIn("Left: occupation 240 · personal interest 140", text)

    def test_feedback_for_rejected_batch_in_chinese(self):
        try:
            self.batch({"accounting": (99, None), "law": (99, None), "listen": (79, None), "persuade": (40, None)})
        except BatchRejected as e:
            text = batch_result_text(e.result, skill_names(self.conn, "zh"), "zh", localized_names(self.conn))
        self.assertIn("未保存任何更改", text)
        self.assertIn("会计", text)                 # skill name localized inside the error
        self.assertIn("上限为 99", text)
        self.assertIn("超出预算", text)


class SkillPickerTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.index = build_search_index(self.conn)
        self.all = [r[0] for r in self.conn.execute("SELECT key FROM skill_defs")]

    def tearDown(self):
        self.conn.close()

    def test_groups_cover_all_skills_within_limits(self):
        grouped = [k for _, _, _, keys in GROUPS for k in keys]
        self.assertEqual(sorted(grouped), sorted(self.all))
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertTrue(all(len(keys) <= 25 for _, _, _, keys in GROUPS))
        self.assertEqual(group_of("not_a_skill"), "other")

    def test_large_sets_require_a_category_or_search(self):
        pp = picker_page(self.all, self.index)
        self.assertEqual((pp.mode, pp.keys, pp.available), ("categories", [], 85))
        self.assertEqual(sum(n for _, n in pp.categories), 85)
        pp = picker_page(self.all, self.index, category="science")
        self.assertEqual(pp.mode, "category")
        self.assertEqual(len(pp.keys), 13)
        self.assertIn("chemistry", pp.keys)

    def test_small_sets_shown_directly_in_given_order(self):
        pp = picker_page(["swim", "climb"], self.index, keep_order=True)
        self.assertEqual((pp.mode, pp.keys), ("all", ["swim", "climb"]))

    def test_bilingual_search(self):
        for query, expected in (("spot", "spot_hidden"), ("侦查", "spot_hidden"), ("图书馆", "library_use"),
                                ("library use", "library_use"), ("会计", "accounting"), ("Fast", "fast_talk"),
                                ("话术", "fast_talk"), ("spot_hidden", "spot_hidden")):
            with self.subTest(query=query):
                self.assertEqual(search(self.index, self.all, query)[0], expected)
        self.assertEqual(search(self.index, self.all, "nonexistent"), [])
        # search respects what is available (e.g. skills already chosen are excluded)
        self.assertEqual(search(self.index, ["listen"], "侦查"), [])
        pp = picker_page(self.all, self.index, category="science", query="手枪")
        self.assertEqual((pp.mode, pp.keys), ("search", ["handgun"]))   # search overrides the category filter

    def test_search_does_not_depend_on_display_language(self):
        # the index is built from both languages regardless of any preference
        terms = self.index["spot_hidden"]
        self.assertIn("spot hidden", terms)
        self.assertIn("侦查", terms)

    def test_pagination(self):
        keys = [f"k{n}" for n in range(60)]
        pp = picker_page(keys, {k: [k] for k in keys}, query="k", page=2)
        self.assertEqual((pp.page, pp.pages, len(pp.keys), pp.total), (2, 3, 10, 60))
        self.assertEqual(picker_page(keys, {k: [k] for k in keys}, query="k", page=99).page, 2)


class ChoiceNavigationServiceTests(Base):
    """Changing an earlier choice replaces it; options exclude skills chosen elsewhere."""

    def test_go_back_and_change_a_choice(self):
        inv = self.inv_svc.get(ALICE, self.inv.id)
        self.assertNotIn("charm", self.svc.pick_options(inv, 6, 0))       # used by choice 2
        self.assertIn("psychology", self.svc.pick_options(inv, 6, 0))     # its own current value
        self.svc.set_pick(ALICE, self.inv.id, 6, 0, "dodge")
        inv = self.inv_svc.get(ALICE, self.inv.id)
        self.assertIn("psychology", self.svc.pick_options(inv, 6, 1))     # freed again
        from cocbot.occupations.service import load_picks
        self.assertEqual(load_picks(self.conn, self.inv.id), {(6, 0): "dodge", (6, 1): "charm"})


class LanguagePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def tearDown(self):
        self.conn.close()

    def test_default_from_locale_then_explicit_choice(self):
        self.assertEqual(get_language(self.conn, ALICE), "en")
        self.assertEqual(get_language(self.conn, ALICE, "zh-CN"), "zh")
        self.assertEqual(get_language(self.conn, ALICE, "zh-TW"), "zh")
        self.assertEqual(get_language(self.conn, ALICE, "en-GB"), "en")
        set_language(self.conn, ALICE, "both")
        self.assertEqual(get_language(self.conn, ALICE, "zh-CN"), "both")   # explicit choice wins
        self.assertEqual(get_language(self.conn, BOB), "en")                # per user
        with self.assertRaises(ValueError):
            set_language(self.conn, ALICE, "fr")

    def test_locale_objects(self):
        class FakeLocale:
            value = "zh-CN"
        self.assertEqual(language_from_locale(FakeLocale()), "zh")
        self.assertEqual(language_from_locale(None), "en")

    def test_persists_after_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            set_language(conn, ALICE, "zh")
            conn.close()
            conn2 = fresh_db(path)
            self.assertEqual(get_language(conn2, ALICE), "zh")
            conn2.close()


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.nbl = localized_names(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_catalog_complete(self):
        for code, pair in CATALOG.items():
            with self.subTest(code=code):
                self.assertEqual(len(pair), 2)
                self.assertTrue(pair[0] and pair[1])

    def test_msg_is_english_string_and_localizes(self):
        m = Msg("over_max", {"listen": "Listen"}, skill="listen", total=105, max=99)
        self.assertEqual(m, "Listen would be 105; the maximum is 99.")
        self.assertEqual(localize(m, "zh", self.nbl), "聆听 将达到 105，上限为 99。")
        both = localize(m, "both", self.nbl)
        self.assertIn("Listen would be 105", both)
        self.assertIn("聆听", both)
        self.assertEqual(localize("plain text", "zh"), "plain text")

    def test_nested_messages(self):
        m = Msg("not_finished", errors=[Msg("picks_missing", n=2), Msg("credit_range", value=0, min=30, max=70)])
        self.assertIn("- 2 occupation skill choice(s) still to make.", m)
        zh = localize(m, "zh", self.nbl)
        self.assertIn("尚未完成", zh)
        self.assertIn("还有 2 项本职技能尚未选择", zh)
        self.assertIn("信誉为 0", zh)

    def test_skill_and_occupation_names(self):
        self.assertEqual(skill_names(self.conn, "en")["spot_hidden"], "Spot Hidden")
        self.assertEqual(skill_names(self.conn, "zh")["spot_hidden"], "侦查")
        self.assertEqual(skill_names(self.conn, "both")["spot_hidden"], "Spot Hidden · 侦查")
        occ = catalogue.find(self.conn, ALICE, "accountant")
        self.assertEqual(occupation_name(occ, "zh"), "会计师")
        self.assertEqual(occupation_name(occ, "both"), "Accountant / 会计师")

    def test_label_limits(self):
        for lang in ("en", "zh", "both"):
            for code in ("btn_finish_confirm", "modal_set_int", "picker_category_ph"):
                with self.subTest(lang=lang, code=code):
                    self.assertLessEqual(len(label(code, lang, 45, n=999, left=999)), 45)

    def test_wizard_text_in_chinese(self):
        conn = self.conn
        inv_svc, svc = InvestigatorService(conn), CreationService(conn)
        inv = inv_svc.create(ALICE, "Ada")
        inv_svc.set_characteristics(ALICE, inv.id, FULL)
        svc.set_occupation(ALICE, inv.id, "private_investigator")
        check = svc.check(ALICE, inv.id)
        line = budget_line(check, "zh")
        self.assertIn("**职业:**", line)
        self.assertIn("建议", line)
        self.assertIn("**剩余", line)
        rows = rows_table(check.rows[:2], skill_names(conn, "zh"), "zh")
        self.assertNotIn("```", rows)                 # list layout for CJK
        self.assertEqual(render("step_allocation", "zh"), "4 · 技能点数")


if __name__ == "__main__":
    unittest.main()
