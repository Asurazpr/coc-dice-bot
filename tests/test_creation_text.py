import unittest

from cocbot.characters.service import InvestigatorService
from cocbot.db.repo_skill_defs import skill_display_names
from cocbot.occupations import catalogue
from cocbot.occupations.service import CreationService, load_picks
from cocbot.ui.creation_text import (
    ALLOC_PAGE,
    allocation_pages,
    budget_line,
    choices_text,
    occupation_detail,
    review_text,
    rows_table,
)
from cocbot.ui.sheet_text import status_badge
from tests.helpers import fresh_db

ALICE = "1001"
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


class CreationTextTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.names = dict(self.conn.execute(
            "SELECT sd.key, i.name FROM skill_defs sd JOIN skill_def_i18n i ON i.skill_id=sd.skill_id AND i.lang='en'"
        ).fetchall())
        self.inv_svc, self.svc = InvestigatorService(self.conn), CreationService(self.conn)
        self.inv = self.inv_svc.create(ALICE, "Ada")
        self.inv_svc.set_characteristics(ALICE, self.inv.id, FULL)

    def tearDown(self):
        self.conn.close()

    def test_every_occupation_detail_fits_embed_fields(self):
        for occ in catalogue.search(self.conn, ALICE, "", limit=1000):
            with self.subTest(occ.key):
                for name, value in occupation_detail(occ, self.names):
                    self.assertLessEqual(len(name), 256)
                    self.assertLessEqual(len(value), 1024)
                    self.assertTrue(value)

    def test_detail_content(self):
        fields = dict(occupation_detail(catalogue.find(self.conn, ALICE, "accountant"), self.names))
        self.assertEqual(fields["Credit Rating"], "30–70")
        self.assertEqual(fields["Occupation skill points"], "EDU×4")
        self.assertIn("• Accounting", fields["Occupation skills"])
        self.assertIn("• any 2 other skills", fields["Occupation skills"])
        self.assertIn("not yet verified", fields["Notes"])
        fields = dict(occupation_detail(catalogue.find(self.conn, ALICE, "private_investigator"), self.names))
        self.assertIn("1 of: Charm, Fast Talk, Intimidate, Persuade", fields["Occupation skills"])

    def test_choices_and_allocation_layout(self):
        self.svc.set_occupation(ALICE, self.inv.id, "accountant")
        self.svc.set_pick(ALICE, self.inv.id, 6, 0, "charm")
        occ = catalogue.find(self.conn, ALICE, "accountant")
        text = choices_text(occ, load_picks(self.conn, self.inv.id), self.names, current=(6, 1))
        self.assertIn("✔ Charm", text)
        self.assertIn("👉 —", text)
        self.assertIn("Credit Rating", text)

        self.svc.set_pick(ALICE, self.inv.id, 6, 1, "dodge")
        self.svc.allocate(ALICE, self.inv.id, "accounting", occupation_points=40)
        self.svc.allocate(ALICE, self.inv.id, "climb", interest_points=10)
        check = self.svc.check(ALICE, self.inv.id)
        pages, other = allocation_pages(check)
        self.assertEqual([len(p) for p in pages], [5, 4])            # 8 skills + Credit Rating
        self.assertTrue(all(len(p) <= ALLOC_PAGE for p in pages))
        self.assertEqual([r.key for r in other], ["climb"])
        table = rows_table(pages[0], self.names)
        self.assertIn("Accounting", table)
        self.assertIn("   45", table)                                 # 5 base + 40
        self.assertIn("40/300 used · **260 left**", budget_line(check))
        review = review_text(check)
        self.assertIn("❌ Credit Rating is 0", review)
        self.assertLessEqual(len(review), 4000)

    def test_status_badges(self):
        self.assertEqual(status_badge(self.inv), "📝 Draft")
        self.svc.set_occupation(ALICE, self.inv.id, "accountant")
        self.assertIn("creation in progress", status_badge(self.inv_svc.get(ALICE, self.inv.id)))
        for n, k in enumerate(("charm", "dodge")):
            self.svc.set_pick(ALICE, self.inv.id, 6, n, k)
        self.svc.allocate(ALICE, self.inv.id, "credit_rating", occupation_points=40)
        inv = self.inv_svc.finalize(ALICE, self.inv.id, confirm_unspent=True)
        self.assertEqual(status_badge(inv), "✅ Created (occupation rules checked)")
        names = skill_display_names(self.conn, inv.skills.keys())
        self.assertEqual(names["credit_rating"], "Credit Rating")


if __name__ == "__main__":
    unittest.main()
