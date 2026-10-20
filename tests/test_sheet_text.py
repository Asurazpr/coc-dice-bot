import unittest

from cocbot.characters.service import InvestigatorService
from cocbot.db.repo_skill_defs import resolve_skill, search_skills, skill_display_names
from cocbot.ui.sheet_text import build_list_text, build_sheet_text
from tests.helpers import fresh_db

FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


class SheetTextTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.svc = InvestigatorService(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_draft_sheet_lists_missing(self):
        inv = self.svc.create("1", "Ada", occupation="Librarian", age=31)
        text = build_sheet_text(inv, {})
        self.assertEqual(text.title, "Ada")
        self.assertIn("Draft", text.description)
        self.assertIn("Librarian · age 31", text.description)
        fields = dict((n, v) for n, v, _ in text.fields)
        self.assertIn("STR", fields["To finish the basic sheet"])
        self.assertIn("**HP** —/—", fields["Status"])

    def test_complete_sheet(self):
        inv = self.svc.create("1", "Ada")
        self.svc.set_characteristics("1", inv.id, FULL)
        inv = self.svc.set_skill("1", inv.id, "listen", 55)
        names = skill_display_names(self.conn, inv.skills.keys())
        text = build_sheet_text(inv, names, active_here=True)
        fields = dict((n, v) for n, v, _ in text.fields)
        self.assertIn("**STR** 60 (30/12)", fields["Characteristics"])
        self.assertIn("**HP** 12/12", fields["Status"])
        self.assertIn("**DB** +1D4", fields["Derived"])
        self.assertIn("**Dodge** 35", fields["Derived"])
        self.assertIn("Listen **55**", fields["Trained skills (1)"])
        self.assertNotIn("To finish the basic sheet", fields)
        self.assertIn("Active here", text.description)

    def test_list_text(self):
        a = self.svc.create("1", "Ada")
        b = self.svc.create("1", "Harvey", occupation="Professor")
        out = build_list_text(self.svc.list("1"), active_id=b.id)
        self.assertIn("**Ada**", out)
        self.assertIn("⭐ **Harvey** — Professor", out)
        self.assertIn("/character create", build_list_text([], None))
        self.assertNotIn("⭐ **Ada**", out)
        self.assertTrue(a.id)


class SkillLookupTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def tearDown(self):
        self.conn.close()

    def test_resolution_variants(self):
        for q in ("Spot Hidden", "spot hidden", "SPOT HIDDEN", "spot_hidden", "侦查"):
            with self.subTest(q=q):
                self.assertEqual(resolve_skill(q, conn=self.conn).key, "spot_hidden")
        self.assertEqual(resolve_skill("侦查", conn=self.conn).display_name, "侦查")
        self.assertEqual(resolve_skill("spot hidden", conn=self.conn).display_name, "Spot Hidden")
        self.assertIsNone(resolve_skill("nonsense", conn=self.conn))

    def test_search(self):
        keys = [k for k, _ in search_skills(self.conn, "spot")]
        self.assertIn("spot_hidden", keys)
        self.assertIn("spot_hidden", [k for k, _ in search_skills(self.conn, "侦")])
        self.assertEqual(len(search_skills(self.conn, "")), 25)


class ListPaginationRegressionTests(unittest.TestCase):
    """Review item 3: /character list stays within Discord embed limits."""

    def setUp(self):
        self.conn = fresh_db()
        self.svc = InvestigatorService(self.conn)

    def tearDown(self):
        self.conn.close()

    def _many(self, n):
        # 20 live + the rest archived, all with maximum-length names/occupations
        invs = []
        for i in range(n):
            inv = self.svc.create("1", f"{i:03d}" + "N" * 61, occupation="O" * 100)
            if i >= 20:
                self.svc.archive("1", inv.id)
            invs.append(inv)
        return self.svc.list("1", include_archived=True)

    def test_every_page_within_limits(self):
        from cocbot.ui.sheet_text import EMBED_DESCRIPTION_LIMIT, LIST_PAGE_SIZE, build_list_page, selection_summary
        invs = self._many(60)
        summary = selection_summary(invs[0], invs[1], in_guild=True)
        first = build_list_page(invs, invs[0].id, 0, summary)
        self.assertEqual(first.pages, 6)
        seen = []
        for page in range(first.pages):
            p = build_list_page(invs, invs[0].id, page, summary)
            self.assertLessEqual(len(p.description), EMBED_DESCRIPTION_LIMIT)
            self.assertLessEqual(len(p.footer), 2048)
            self.assertLessEqual(p.description.count("`#"), LIST_PAGE_SIZE)
            seen += [i.id for i in invs if f"`#{i.id}`" in p.description]
        self.assertEqual(sorted(seen), sorted(i.id for i in invs))   # nothing lost, nothing repeated
        self.assertIn("…", first.description)                         # long occupation truncated

    def test_page_is_clamped(self):
        from cocbot.ui.sheet_text import build_list_page
        invs = self._many(12)
        self.assertEqual(build_list_page(invs, None, 99).page, 1)
        self.assertEqual(build_list_page(invs, None, -5).page, 0)
        self.assertEqual(build_list_page([], None, 3).pages, 1)


class SelectionSummaryTests(unittest.TestCase):
    """Review item 4: the list explains server choice vs. default."""

    def test_wording(self):
        from cocbot.characters.models import Investigator
        from cocbot.ui.sheet_text import selection_summary
        ada = Investigator(id=1, owner_user_id="1", name="Ada", status="draft")
        bram = Investigator(id=2, owner_user_id="1", name="Bram", status="draft")
        s = selection_summary(ada, bram, in_guild=True)
        self.assertIn("Ada — chosen for this server (overrides your default)", s)
        self.assertIn("Default elsewhere: Bram", s)
        self.assertIn("/character unselect", s)
        self.assertIn("Bram — your default", selection_summary(None, bram, in_guild=True))
        self.assertIn("none", selection_summary(None, None, in_guild=True))
        self.assertIn("Default (everywhere):** Bram", selection_summary(None, bram, in_guild=False))


if __name__ == "__main__":
    unittest.main()
