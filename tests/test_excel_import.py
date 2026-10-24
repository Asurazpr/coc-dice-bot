"""M3C: Excel character import — parsing, mapping, safety, saving, re-import, Keeper access."""
import io
import os
import sqlite3
import tempfile
import unittest
import zipfile

from cocbot.campaigns.service import CampaignService
from cocbot.characters.service import Conflict, InvalidState, InvestigatorService, NotFound
from cocbot.imports.service import ImportRejected, ImportService
from cocbot.imports.workbook import MAX_UPLOAD_BYTES
from tests.helpers import fresh_db
from tests.xlsx_factory import build, sheet_bytes, to_bytes

ALICE, BOB, KEEPER = "1001", "1002", "100"
CH = 5001


def codes(preview):
    return [i.msg.code for i in preview.sheet.issues]


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.svc = ImportService(self.conn)
        self.inv = InvestigatorService(self.conn)

    def tearDown(self):
        self.conn.close()

    def preview(self, user=ALICE, **kw):
        return self.svc.preview(user, sheet_bytes(**kw))


class ParseTests(Base):
    def test_all_supported_fields(self):
        p = self.preview(hp=10, mp=7, san=40, points={("L", 15): (0, 40, 0), ("R", 34): (0, 30, 5)})
        s = p.sheet
        self.assertEqual((p.template, s.name, s.occupation, s.age, s.residence, s.birthplace),
                         ("coc7_zh_v1.35", "Ada Lovell", "会计师", 32, "Arkham", "Boston"))
        self.assertEqual(s.characteristics, {"STR": 60, "DEX": 70, "POW": 45, "CON": 55, "APP": 50, "EDU": 75,
                                             "SIZ": 65, "INT": 80})
        self.assertEqual((s.luck, s.hp, s.mp, s.san), (50, 10, 7, 40))
        self.assertEqual(p.skills, {"accounting": 45, "spot_hidden": 60})
        self.assertTrue(p.complete)
        self.assertEqual(p.occupation_match, "Accountant")
        self.assertEqual(p.advisory, {"occupation": (70, 300), "interest": (5, 160)})
        self.assertEqual(s.notes["player"], "Player One")
        self.assertEqual(s.notes["sex"], "女")
        self.assertEqual(s.notes["backstory"], {"信念/信仰": "Science explains everything"})
        self.assertEqual(s.notes["weapons"][0]["name"], ".38 Revolver")
        self.assertEqual(s.errors, [])

    def test_chinese_aliases_and_specializations_map_to_canonical_skills(self):
        p = self.preview(points={("R", 16): (0, 20, 0), ("R", 34): (0, 0, 10), ("L", 30): (0, 10, 0),
                                 ("L", 19): (0, 30, 0), ("L", 33): (0, 15, 0), ("L", 36): (0, 0, 20),
                                 ("R", 30): (0, 0, 20), ("R", 40): (0, 0, 10), ("R", 26): (0, 0, 20),
                                 ("L", 43): (0, 0, 30)},
                         specs={("L", 19): "摄影", ("R", 30): "化学", ("R", 40): "驯兽", ("R", 26): "飞机",
                                ("L", 43): "English"})
        self.assertEqual(p.skills, {"library_use": 40, "spot_hidden": 35, "electrical_repair": 20,
                                    "photography": 35, "brawl": 40, "handgun": 40, "chemistry": 21,
                                    "animal_handling": 15, "pilot": 21, "language_foreign": 31})
        self.assertEqual(p.sheet.notes["specializations"],
                         {"pilot": [{"specialization": "飞机", "value": 21}],
                          "language_foreign": [{"specialization": "English", "value": 31}]})

    def test_english_skill_names_in_free_rows(self):
        p = self.preview(specs={("R", 41): "Spot Hidden", ("R", 42): "Occult"},
                         points={("R", 41): (0, 0, 60), ("R", 42): (0, 0, 15)})
        # a blank initial in a free row means the skill's base: 25 + 60, 5 + 15
        self.assertEqual(p.skills["spot_hidden"], 85)
        self.assertEqual(p.skills["occult"], 20)
        self.assertNotIn("imp_duplicate_skill", codes(p))          # the 侦察 row is at its base

    def test_unknown_skills_and_specializations_are_kept_not_dropped(self):
        p = self.preview(specs={("L", 19): "木工", ("R", 41): "开锁术"},
                         points={("L", 19): (0, 20, 0), ("R", 41): (0, 0, 15), ("L", 20): (0, 10, 0)})
        unmapped = {u["label"]: u["value"] for u in p.sheet.notes["unmapped_skills"]}
        self.assertEqual(unmapped["技艺: 木工"], 25)
        self.assertEqual(unmapped["开锁术"], 15)
        self.assertIn("技艺", unmapped)                            # points but no specialization named
        self.assertGreaterEqual(codes(p).count("imp_unknown_skill"), 2)
        self.assertIn("imp_unnamed_specialization", codes(p))

    def test_several_foreign_languages_keep_the_highest_and_warn(self):
        p = self.preview(specs={("L", 43): "英语", ("L", 44): "日语"},
                         points={("L", 43): (0, 0, 40), ("L", 44): (0, 0, 20)})
        self.assertEqual(p.skills["language_foreign"], 41)
        self.assertIn("imp_duplicate_skill", codes(p))
        self.assertEqual(len(p.sheet.notes["specializations"]["language_foreign"]), 2)

    def test_formulas_are_recomputed_not_trusted(self):
        # Dodge's initial is =INT(DEX/2); totals are =SUM(): recomputed even when the saved value is stale
        p = self.preview(points={("L", 28): (0, 10, 0), ("L", 22): (0, 0, 20)},
                         cached={"I22": 99, "I28": 1, "E10": "/40", "Q10": "/9"})
        self.assertEqual(p.skills["dodge"], 45)
        self.assertEqual(p.skills["charm"], 35)
        self.assertIn("imp_stale_cached", codes(p))
        self.assertEqual(p.derived.hp_max, 12)
        recomputed = [i for i in p.sheet.issues if i.msg.code == "imp_recomputed"]
        self.assertEqual([i.msg.params["field"] for i in recomputed], ["hp_max"])   # MP 9 matched

    def test_derived_values_follow_the_bots_rules(self):
        p = self.preview(chars={"STR": 80, "SIZ": 85, "CON": 50, "DEX": 40, "POW": 60, "APP": 50, "EDU": 70,
                                "INT": 60}, age=45)
        d = p.derived
        self.assertEqual((d.hp_max, d.mp_max, d.san_start, d.build, d.damage_bonus, d.mov), (13, 12, 60, 2, "+1D6", 6))

    def test_typed_over_and_foreign_formulas(self):
        p = self.preview(overrides={"I22": 70, "S17": "=O17*3", "J3": "=属性掷骰!C3"},
                         cached={"S17": 60, "J3": 55})
        self.assertEqual(p.skills["charm"], 70)
        self.assertIn("imp_total_typed", codes(p))
        self.assertEqual(p.skills["listen"], 60)                    # other formula: saved value, warned
        self.assertEqual(p.sheet.characteristics["STR"], 55)
        self.assertGreaterEqual(codes(p).count("imp_formula_cached"), 2)

    def test_formula_without_saved_value_is_left_blank(self):
        p = self.preview(overrides={"J3": "=属性掷骰!C3"})
        self.assertIsNone(p.sheet.characteristics["STR"])
        self.assertIn("imp_formula_no_value", codes(p))
        self.assertFalse(p.complete)

    def test_missing_and_invalid_fields(self):
        p = self.preview(chars={"STR": 60, "DEX": 70}, luck=None, age=400, overrides={"M5": "abc"})
        self.assertFalse(p.complete)
        self.assertIn("imp_missing_characteristics", codes(p))
        self.assertIn("imp_out_of_range", codes(p))                  # age 400
        self.assertIn("imp_not_a_number", codes(p))                  # APP "abc"
        self.assertIsNone(p.sheet.age)
        self.assertTrue(p.can_save)
        no_name = self.preview(name=None)
        self.assertFalse(no_name.can_save)
        self.assertEqual(no_name.errors[0].msg.code, "imp_no_name")

    def test_resource_above_maximum_is_not_imported(self):
        p = self.preview(hp=30, san=100)
        self.assertIsNone(p.sheet.hp)
        self.assertIsNone(p.sheet.san)
        self.assertEqual(codes(p).count("imp_resource_above_max"), 2)

    def test_occupation_from_index_when_text_blank(self):
        p = self.preview(occupation=None, occupation_index=2)
        self.assertEqual(p.sheet.occupation, "会计师")
        self.assertIn("imp_occupation_from_index", codes(p))

    def test_era_dependent_skill_names(self):
        modern = self.preview(points={("L", 24): (0, 0, 30)})
        self.assertEqual(modern.skills["computer_use"], 35)
        classic = self.preview(era="1920s", points={("L", 24): (0, 0, 30)})
        self.assertNotIn("computer_use", classic.skills)


class SafetyTests(Base):
    def rejected(self, data, code):
        with self.assertRaises(ImportRejected) as cm:
            self.svc.preview(ALICE, data)
        self.assertEqual(cm.exception.args[0].code, code)

    def test_unsupported_layout_fails_without_guessing(self):
        self.rejected(sheet_bytes(signature=False), "import_unsupported")
        wb = build()
        wb["人物卡"]["B14"] = "Skill"
        self.rejected(to_bytes(wb), "import_unsupported")

    def test_not_a_workbook(self):
        self.rejected(b"hello", "import_not_xlsx")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "x")
        self.rejected(buf.getvalue(), "import_not_xlsx")

    def test_oversized_and_bomb_like_files(self):
        self.rejected(b"PK" + b"0" * (MAX_UPLOAD_BYTES + 1), "import_too_large")
        self.rejected(sheet_bytes(add_parts={"xl/media/huge.bin": b"\0" * (11 * 1024 * 1024)}), "import_too_complex")
        wb = build()
        wb["人物卡"]["A600"] = "x"
        self.rejected(to_bytes(wb), "import_too_complex")

    def test_macros_rejected_and_external_links_ignored(self):
        self.rejected(sheet_bytes(add_parts={"xl/vbaProject.bin": b"\0" * 10}), "import_macros")
        p = self.svc.preview(ALICE, sheet_bytes(add_parts={"xl/externalLinks/externalLink1.xml": b"<x/>"}))
        self.assertIn("import_external_links", codes(p))

    def test_corrupted_xml(self):
        data = sheet_bytes()
        src = zipfile.ZipFile(io.BytesIO(data))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dst:
            for info in src.infolist():
                content = src.read(info.filename)
                if info.filename == "xl/worksheets/sheet1.xml":
                    content = content[: len(content) // 2]
                dst.writestr(info, content)
        self.rejected(out.getvalue(), "import_unreadable")

    def test_error_messages_never_echo_contents(self):
        try:
            self.svc.preview(ALICE, sheet_bytes(signature=False, name="SECRET-NAME"))
        except ImportRejected as e:
            self.assertNotIn("SECRET-NAME", str(e))


class SaveTests(Base):
    def test_create_saves_everything_unverified(self):
        p = self.preview(hp=10, points={("R", 34): (0, 30, 5)})
        inv = self.svc.create(ALICE, p)
        self.assertEqual((inv.owner_user_id, inv.name, inv.status, inv.creation_validated, inv.occupation_id),
                         (ALICE, "Ada Lovell", "ready", False, None))
        self.assertEqual((inv.hp_current, inv.hp, inv.luck), (10, 10, 50))
        self.assertEqual(inv.skills, {"spot_hidden": 60})
        rec = self.svc.latest_for_owner(ALICE, inv.id)
        self.assertEqual((rec.action, rec.completeness, rec.verification, rec.template),
                         ("create", "complete", "unverified", "coc7_zh_v1.35"))
        self.assertEqual(rec.notes["player"], "Player One")
        self.assertEqual(len(self.conn.execute("SELECT file_sha256 FROM investigator_imports").fetchone()[0]), 64)

    def test_incomplete_sheet_becomes_a_draft(self):
        inv = self.svc.create(ALICE, self.preview(chars={"STR": 60}))
        self.assertEqual(inv.status, "draft")
        self.assertEqual(self.svc.latest_for_owner(ALICE, inv.id).completeness, "incomplete")

    def test_imported_investigator_works_for_checks(self):
        from cocbot.characters.check_target import resolve_check_target
        inv = self.svc.create(ALICE, self.preview(points={("R", 34): (0, 30, 5)}))
        self.inv.select(ALICE, inv.id, 42)
        t = resolve_check_target(self.conn, ALICE, 42, "侦查")
        self.assertEqual((t.value, t.investigator.id), (60, inv.id))
        self.assertEqual(resolve_check_target(self.conn, ALICE, 42, "dodge").value, 35)

    def test_blocking_errors_and_name_conflicts(self):
        with self.assertRaises(ImportRejected):
            self.svc.create(ALICE, self.preview(name=None))
        p = self.preview()
        self.svc.create(ALICE, p)
        with self.assertRaises(Conflict):
            self.svc.create(ALICE, p)
        self.assertEqual(self.svc.free_name(ALICE, "Ada Lovell"), "Ada Lovell (2)")
        self.assertEqual(self.svc.create(ALICE, p, name="Ada Lovell (2)").name, "Ada Lovell (2)")
        self.assertEqual(self.svc.create(BOB, p).owner_user_id, BOB)      # other users unaffected

    def test_duplicate_detection_in_preview(self):
        self.svc.create(ALICE, self.preview())
        self.assertEqual([i.name for i in self.preview().candidates], ["Ada Lovell"])
        self.assertEqual(self.preview(user=BOB).candidates, [])

    def test_atomic_rollback(self):
        p = self.preview(points={("R", 34): (0, 30, 5)})
        p.skills["not_a_skill"] = 50                                 # violates the skill foreign key
        with self.assertRaises(sqlite3.IntegrityError):
            self.svc.create(ALICE, p)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM investigators").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM investigator_imports").fetchone()[0], 0)

    def test_persistence_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            inv = ImportService(conn).create(ALICE, ImportService(conn).preview(
                ALICE, sheet_bytes(points={("R", 34): (0, 30, 5)})))
            conn.close()
            conn2 = fresh_db(path)
            again = InvestigatorService(conn2).get(ALICE, inv.id)
            self.assertEqual((again.name, again.skills), ("Ada Lovell", {"spot_hidden": 60}))
            self.assertEqual(ImportService(conn2).latest_for_owner(ALICE, inv.id).verification, "unverified")
            conn2.close()

    def test_import_records_are_append_only_and_owner_bound(self):
        inv = self.svc.create(ALICE, self.preview())
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE investigator_imports SET verification='unverified'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO investigator_imports (investigator_id, owner_user_id, template, "
                              "file_sha256, action, completeness) VALUES (?, ?, 't', ?, 'create', 'complete')",
                              (inv.id, BOB, "0" * 64))
        with self.assertRaises(NotFound):
            self.svc.latest_for_owner(BOB, inv.id)


class ReimportTests(Base):
    def setUp(self):
        super().setUp()
        self.ada = self.svc.create(ALICE, self.preview(points={("R", 34): (0, 30, 5), ("L", 22): (0, 0, 10)}))

    def test_diff_lists_every_change(self):
        p = self.preview(chars={**{"STR": 65, "DEX": 70, "POW": 45, "CON": 55, "APP": 50, "EDU": 75, "SIZ": 65,
                                   "INT": 80}}, luck=40, age=33,
                         points={("R", 34): (0, 40, 5), ("R", 17): (0, 0, 20)})
        plan = self.svc.plan_update(ALICE, self.ada.id, p)
        got = {(c.kind, c.name, c.old, c.new) for c in plan.changes}
        self.assertEqual(got, {("field", "age", 32, 33), ("characteristic", "STR", 60, 65),
                               ("characteristic", "LUCK", 50, 40), ("skill_changed", "spot_hidden", 60, 70),
                               ("skill_added", "listen", None, 40), ("skill_removed", "charm", 25, None)})
        self.assertIsNone(plan.blocked)
        self.assertFalse(plan.drops_validation)

    def test_update_replaces_permanent_sheet_without_duplicating(self):
        p = self.preview(luck=40, points={("R", 17): (0, 0, 20)})
        plan = self.svc.plan_update(ALICE, self.ada.id, p)
        inv = self.svc.update(ALICE, self.ada.id, p, expected_fingerprint=plan.fingerprint)
        self.assertEqual((inv.id, inv.luck, inv.skills), (self.ada.id, 40, {"listen": 40}))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM investigators").fetchone()[0], 1)
        self.assertEqual(self.svc.latest_for_owner(ALICE, inv.id).action, "update")

    def test_stale_preview_is_refused(self):
        p = self.preview()
        plan = self.svc.plan_update(ALICE, self.ada.id, p)
        self.inv.set_resources(ALICE, self.ada.id, luck=30)          # edited while the diff was on screen
        with self.assertRaises(InvalidState):
            self.svc.update(ALICE, self.ada.id, p, expected_fingerprint=plan.fingerprint)
        self.assertEqual(self.inv.get(ALICE, self.ada.id).luck, 30)

    def test_only_the_owner_can_update(self):
        with self.assertRaises(NotFound):
            self.svc.plan_update(BOB, self.ada.id, self.preview(user=BOB))
        with self.assertRaises(NotFound):
            self.svc.update(BOB, self.ada.id, self.preview(user=BOB))

    def test_incomplete_sheet_cannot_strip_a_finished_investigator(self):
        plan = self.svc.plan_update(ALICE, self.ada.id, self.preview(chars={"STR": 60}))
        self.assertEqual(plan.blocked.code, "import_update_incomplete")
        with self.assertRaises(InvalidState):
            self.svc.update(ALICE, self.ada.id, self.preview(chars={"STR": 60}))

    def test_archived_investigators_cannot_be_updated(self):
        self.inv.archive(ALICE, self.ada.id)
        with self.assertRaises(NotFound):
            self.svc.plan_update(ALICE, self.ada.id, self.preview())

    def test_update_of_wizard_investigator_drops_validation(self):
        from cocbot.occupations.service import CreationService
        cs = CreationService(self.conn)
        wiz = self.inv.create(ALICE, "Wizard")
        self.inv.set_characteristics(ALICE, wiz.id, {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50,
                                                      "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50})
        cs.set_occupation(ALICE, wiz.id, "accountant")
        cs.allocate(ALICE, wiz.id, "accounting", occupation_points=40)
        plan = self.svc.plan_update(ALICE, wiz.id, self.preview(name="Wizard"))
        self.assertTrue(plan.drops_validation)
        inv = self.svc.update(ALICE, wiz.id, self.preview(name="Wizard"))
        self.assertEqual((inv.occupation_id, inv.creation_validated), (None, False))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM investigator_skill_points WHERE "
                                           "investigator_id=?", (wiz.id,)).fetchone()[0], 0)


class CampaignIsolationTests(Base):
    def setUp(self):
        super().setUp()
        self.camps = CampaignService(self.conn)
        self.ada = self.svc.create(ALICE, self.preview(points={("R", 34): (0, 30, 5)}))
        self.other = self.svc.create(ALICE, self.preview(name="Secret Second"))
        self.camp = self.camps.create(KEEPER, "Masks", channel_id=CH)

    def test_imported_investigator_joins_and_keeper_inspects(self):
        self.assertEqual(self.camps.join(ALICE, self.camp.id, self.ada.id), "requested")
        inv, kind = self.camps.inspect(KEEPER, self.camp.id, self.ada.id)          # pending request
        self.assertEqual((inv.id, kind), (self.ada.id, "pending"))
        rec = self.svc.latest_for_keeper(KEEPER, self.camp.id, self.ada.id)
        self.assertEqual(rec.verification, "unverified")
        with self.assertRaises(NotFound):                                           # other investigators hidden
            self.camps.inspect(KEEPER, self.camp.id, self.other.id)
        with self.assertRaises(NotFound):
            self.svc.latest_for_keeper(KEEPER, self.camp.id, self.other.id)
        self.camps.approve(KEEPER, self.camp.id, ALICE)
        _, kind = self.camps.inspect(KEEPER, self.camp.id, self.ada.id)
        self.assertEqual(kind, "participant")
        with self.assertRaises(NotFound):                                           # Keeper never owns it
            self.inv.get(KEEPER, self.ada.id)
        with self.assertRaises(NotFound):
            self.camps.inspect(BOB, self.camp.id, self.ada.id)

    def test_players_cannot_use_keeper_inspection(self):
        self.camps.invite(KEEPER, self.camp.id, ALICE)
        self.camps.join(ALICE, self.camp.id, self.ada.id)
        from cocbot.campaigns.service import Forbidden
        with self.assertRaises(Forbidden):
            self.camps.inspect(ALICE, self.camp.id, self.ada.id)

    def test_reimport_leaves_campaign_state_alone_and_tells_the_keeper(self):
        self.camps.invite(KEEPER, self.camp.id, ALICE)
        self.camps.join(ALICE, self.camp.id, self.ada.id)
        self.camps.adjust(KEEPER, self.camp.id, self.ada.id, {"hp": 4, "luck": 20}, confirmed=True)
        self.camps.add_condition(KEEPER, self.camp.id, self.ada.id, "Shaken")
        history_before = len(self.camps.history(KEEPER, self.camp.id, limit=100))
        plan = self.svc.plan_update(ALICE, self.ada.id, self.preview(luck=70, hp=12))
        self.assertEqual(plan.campaigns, ["Masks"])
        self.svc.update(ALICE, self.ada.id, self.preview(luck=70, hp=12))
        entry = self.camps.entry(KEEPER, self.camp.id, self.ada.id)
        self.assertEqual((entry.investigator.hp, entry.investigator.luck), (4, 20))          # campaign values kept
        self.assertEqual([c.label for c in entry.conditions], ["Shaken"])
        self.assertEqual(self.inv.get(ALICE, self.ada.id).luck, 70)                          # permanent updated
        history = self.camps.history(KEEPER, self.camp.id, limit=100)
        self.assertEqual(len(history), history_before + 1)
        self.assertEqual(history[0].kind, "sheet_import")
        self.assertEqual(history[0].actor_user_id, ALICE)


if __name__ == "__main__":
    unittest.main()
