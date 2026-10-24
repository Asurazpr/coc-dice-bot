"""Creation UX follow-up: grouped occupation choices and personal-interest skill management."""
import asyncio
import os
import tempfile
import unittest

from cocbot.characters.service import InvestigatorService, ValidationError
from cocbot.occupations.service import CreationService, load_picks, load_points
from tests.helpers import fresh_db
from tests.test_discord_layer import HAVE_DISCORD, fake_interaction, set_input

ALICE = "111111111111111111"
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}
# Film Actor: fixed Acting, Disguise, Drive Auto, Psychology; slot 3 = two of Charm/Fast Talk/Intimidate/Persuade;
# slot 5 = any two other skills.


class GroupPickServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.svc = CreationService(self.conn)
        inv = self.svc.investigators.create(ALICE, "Ada")
        self.svc.investigators.set_characteristics(ALICE, inv.id, FULL)
        self.inv_id = inv.id
        self.svc.set_occupation(ALICE, inv.id, "actor_film_actor")

    def inv(self):
        return self.svc.investigators.get(ALICE, self.inv_id)

    def test_group_saved_atomically(self):
        self.svc.set_slot_picks(ALICE, self.inv_id, 3, ["charm", "persuade"])
        self.assertEqual(load_picks(self.conn, self.inv_id), {(3, 0): "charm", (3, 1): "persuade"})
        self.assertEqual(self.inv().creation_step, "choices")          # slot 5 still open
        self.svc.set_slot_picks(ALICE, self.inv_id, 5, ["dodge", "stealth"])
        self.assertEqual(self.inv().creation_step, "allocation")

    def test_slot_constraints_still_apply(self):
        bad = [(3, ["charm", "dodge"]),                  # Dodge is not one of slot 3's options
               (3, ["charm", "fast_talk", "persuade"]),  # three in a two-skill group
               (3, ["charm", "charm"]),                  # duplicate
               (5, ["psychology"]),                      # already a fixed occupation skill
               (0, ["acting"])]                          # fixed slot
        for slot, keys in bad:
            with self.subTest(slot=slot, keys=keys), self.assertRaises(ValidationError):
                self.svc.set_slot_picks(ALICE, self.inv_id, slot, keys)
        self.svc.set_slot_picks(ALICE, self.inv_id, 3, ["charm"])
        with self.assertRaises(ValidationError):                       # used by the other group
            self.svc.set_slot_picks(ALICE, self.inv_id, 5, ["charm"])
        self.assertEqual(load_picks(self.conn, self.inv_id), {(3, 0): "charm"})   # failed saves changed nothing

    def test_replacing_a_pick_refunds_its_occupation_points(self):
        self.svc.set_slot_picks(ALICE, self.inv_id, 3, ["charm", "persuade"])
        self.svc.set_slot_picks(ALICE, self.inv_id, 5, ["dodge", "stealth"])
        self.svc.allocate_batch(ALICE, self.inv_id, {"charm": (30, 10), "dodge": (20, None)})
        _, refunds = self.svc.preview_slot_picks(self.inv(), 3, ["intimidate", "persuade"])
        self.assertEqual(refunds, [("charm", 30)])
        before = self.svc.check_for(self.inv()).occupation_remaining
        self.svc.set_slot_picks(ALICE, self.inv_id, 3, ["intimidate", "persuade"])
        points = load_points(self.conn, self.inv_id)
        self.assertEqual(points["charm"], (0, 10))                     # interest points stay
        self.assertEqual(self.svc.check_for(self.inv()).occupation_remaining, before + 30)

    def test_partial_group_and_clearing(self):
        self.svc.set_slot_picks(ALICE, self.inv_id, 5, ["dodge"])
        self.assertEqual(load_picks(self.conn, self.inv_id), {(5, 0): "dodge"})
        self.svc.set_slot_picks(ALICE, self.inv_id, 5, [])
        self.assertEqual(load_picks(self.conn, self.inv_id), {})

    def test_selection_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bot.sqlite3")
            conn = fresh_db(path)
            svc = CreationService(conn)
            inv = svc.investigators.create(ALICE, "Ada")
            svc.investigators.set_characteristics(ALICE, inv.id, FULL)
            svc.set_occupation(ALICE, inv.id, "actor_film_actor")
            svc.set_slot_picks(ALICE, inv.id, 3, ["charm", "fast_talk"])
            conn.close()
            conn2 = fresh_db(path)
            self.assertEqual(load_picks(conn2, inv.id), {(3, 0): "charm", (3, 1): "fast_talk"})
            self.assertEqual(CreationService(conn2).next_step(InvestigatorService(conn2).get(ALICE, inv.id)),
                             "choices")
            conn2.close()


@unittest.skipUnless(HAVE_DISCORD, "discord.py not installed")
class CreationUXDiscordTests(unittest.TestCase):
    def setUp(self):
        from apps.discord_bot.views import Ctx
        from cocbot.db.bootstrap import bootstrap
        from cocbot.db.connection import get_conn
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "bot.sqlite3")
        with get_conn(self.db) as conn:
            bootstrap(conn)
            svc = CreationService(conn)
            inv = svc.investigators.create(int(ALICE), "Ada")
            svc.investigators.set_characteristics(int(ALICE), inv.id, FULL)
            svc.set_occupation(int(ALICE), inv.id, "actor_film_actor")
            self.inv_id = inv.id
        self.loop = asyncio.new_event_loop()
        self.ctx = Ctx(self.db, int(ALICE), 1)

    def tearDown(self):
        self.loop.close()
        self.tmp.cleanup()

    def arun(self, coro):
        return self.loop.run_until_complete(coro)

    def build(self, factory):
        async def _make():
            return factory()
        return self.arun(_make())

    def step(self, step, ctx=None, **state):
        from apps.discord_bot.creation_views import render_step
        return self.build(lambda: render_step(ctx or self.ctx, self.inv_id, step, **state))

    def press(self, coro_factory):
        it = fake_interaction(int(ALICE))
        self.arun(coro_factory(it))
        return it.response.calls[0]

    def conn(self):
        from cocbot.db.connection import get_conn
        return get_conn(self.db)

    def finish_choices(self):
        with self.conn() as conn:
            svc = CreationService(conn)
            svc.set_slot_picks(int(ALICE), self.inv_id, 3, ["charm", "persuade"])
            svc.set_slot_picks(int(ALICE), self.inv_id, 5, ["dodge", "stealth"])

    # --- grouped choices -------------------------------------------------------------------
    def test_short_list_group_in_one_panel(self):
        embed, view = self.step("choices")
        self.assertEqual(view.group, 0)                       # first open group: two of four
        self.assertFalse(hasattr(view, "category_select"))    # four options: shown directly
        self.assertEqual([o.value for o in view.skill_select.options],
                         ["charm", "fast_talk", "intimidate", "persuade"])
        self.assertEqual(view.skill_select.max_values, 2)
        self.assertIn("Group 1/2", embed.description)
        _, _, kw = self.press(lambda it: view.picker_selected_many(it, ["charm", "intimidate"]))
        self.assertIn("Selected (2/2): Charm, Intimidate", kw["embed"].description)
        self.assertFalse(kw["view"].save_button.disabled)
        _, _, kw = self.press(lambda it: kw["view"].save_button.callback(it))
        self.assertIn("Saved: Charm, Intimidate", kw["content"])
        self.assertEqual(kw["view"].group, 1)                 # moves on to the open "any two" group
        with self.conn() as conn:
            self.assertEqual(load_picks(conn, self.inv_id), {(3, 0): "charm", (3, 1): "intimidate"})

    def test_too_many_ticks_are_refused_without_losing_the_selection(self):
        _, view = self.step("choices", group=1)
        _, _, kw = self.press(lambda it: view.picker_navigate(it, category="physical", query="", page=0))
        _, _, kw = self.press(lambda it: kw["view"].picker_selected_many(it, ["climb", "dodge"]))
        view = kw["view"]
        _, _, kw2 = self.press(lambda it: view.picker_navigate(it, category="knowledge", query="", page=0))
        kind, args, _ = self.press(lambda it: kw2["view"].picker_selected_many(it, ["history"]))
        self.assertEqual(kind, "send_message")
        self.assertIn("allows 2 skills", args[0])
        self.assertEqual(kw2["view"].draft, ("climb", "dodge"))

    def test_unsaved_ticks_are_not_stored_and_switching_group_discards_them(self):
        _, view = self.step("choices")
        _, _, kw = self.press(lambda it: view.picker_selected_many(it, ["charm"]))
        _, _, kw = self.press(lambda it: kw["view"]._goto_group(1)(it))
        with self.conn() as conn:
            self.assertEqual(load_picks(conn, self.inv_id), {})
        _, _, kw = self.press(lambda it: kw["view"]._goto_group(0)(it))
        self.assertEqual(kw["view"].draft, ())

    def test_refund_preview_before_saving(self):
        self.finish_choices()
        with self.conn() as conn:
            CreationService(conn).allocate_batch(int(ALICE), self.inv_id, {"charm": (25, None)})
        _, view = self.step("choices", group=0)
        self.assertEqual(view.draft, ("charm", "persuade"))
        _, _, kw = self.press(lambda it: view.picker_selected_many(it, ["fast_talk", "persuade"]))
        self.assertIn("returns 25 occupation points from Charm", kw["embed"].description)
        _, _, kw = self.press(lambda it: kw["view"].save_button.callback(it))
        with self.conn() as conn:
            self.assertNotIn("charm", load_points(conn, self.inv_id))

    def test_clear_then_save_empties_the_group(self):
        self.finish_choices()
        _, view = self.step("choices", group=1)
        _, _, kw = self.press(lambda it: view.clear_button.callback(it))
        self.assertEqual(kw["view"].draft, ())
        self.press(lambda it: kw["view"].save_button.callback(it))
        with self.conn() as conn:
            self.assertEqual(load_picks(conn, self.inv_id), {(3, 0): "charm", (3, 1): "persuade"})

    # --- personal interest management --------------------------------------------------------
    def interest_screen(self, **state):
        from apps.discord_bot.creation_views import InterestScreenView
        embed, view = self.step("allocation", screen="interest", **state)
        self.assertIsInstance(view, InterestScreenView)
        return embed, view

    def give_interest(self, values):
        with self.conn() as conn:
            CreationService(conn).allocate_batch(int(ALICE), self.inv_id, {k: (None, v) for k, v in values.items()})

    def test_interest_screen_lists_selects_edits_and_returns(self):
        from apps.discord_bot.creation_views import InterestPointsModal, InterestScreenView
        self.finish_choices()
        self.give_interest({"climb": 20, "occult": 15})
        embed, view = self.interest_screen()
        self.assertIn("Climb: 20 interest", embed.fields[0].value)
        self.assertIn("Occult: 15 interest", embed.fields[0].value)
        self.assertTrue(view.edit_button.disabled)
        _, _, kw = self.press(lambda it: (setattr(view.skill_select, "_values", ["occult"]),
                                          view.skill_select.callback(it))[1])
        view = kw["view"]
        self.assertEqual(view.selected, "occult")
        kind, (modal,), _ = self.press(lambda it: view.edit_button.callback(it))
        self.assertIsInstance(modal, InterestPointsModal)
        set_input(modal.points_input, "25")
        _, _, kw = self.press(lambda it: modal.on_submit(it))
        self.assertIn("Occult: Personal interest 15 → 25 (+10)", kw["content"])
        self.assertIsInstance(kw["view"], InterestScreenView)            # same context, not page 1
        self.assertEqual(kw["view"].selected, "occult")
        # pick another skill, then "edit previous" goes back to Occult
        _, _, kw = self.press(lambda it: (setattr(kw["view"].skill_select, "_values", ["climb"]),
                                          kw["view"].skill_select.callback(it))[1])
        self.assertIn("Occult", kw["view"].previous_button.label)
        kind, (modal,), _ = self.press(lambda it: kw["view"].previous_button.callback(it))
        self.assertEqual(modal.key, "occult")

    def test_remove_needs_confirmation_and_refunds(self):
        self.finish_choices()
        self.give_interest({"climb": 20})
        _, view = self.interest_screen(selected="climb")
        _, _, kw = self.press(lambda it: view.remove_button.callback(it))
        self.assertIn("Confirm remove", kw["view"].remove_button.label)
        self.assertIn("go back to your budget", kw["embed"].fields[1].value)
        with self.conn() as conn:
            self.assertEqual(load_points(conn, self.inv_id)["climb"], (0, 20))     # not yet
            left_before = CreationService(conn).check_for(InvestigatorService(conn).get(int(ALICE),
                                                                                       self.inv_id)).interest_remaining
        _, _, kw = self.press(lambda it: kw["view"].remove_button.callback(it))
        self.assertIn("Climb: Personal interest 20 → 0", kw["content"])
        self.assertIsNone(kw["view"].selected)
        with self.conn() as conn:
            self.assertNotIn("climb", load_points(conn, self.inv_id))
            left_after = CreationService(conn).check_for(InvestigatorService(conn).get(int(ALICE),
                                                                                      self.inv_id)).interest_remaining
        self.assertEqual(left_after, left_before + 20)

    def test_add_skill_from_interest_screen_and_back(self):
        from apps.discord_bot.creation_views import InterestPickerView, InterestScreenView
        self.finish_choices()
        _, view = self.interest_screen()
        self.assertIn("No personal interest points yet", self.interest_screen()[0].fields[0].value)
        _, _, kw = self.press(lambda it: view.add_button.callback(it))
        picker = kw["view"]
        self.assertIsInstance(picker, InterestPickerView)
        _, _, kw = self.press(lambda it: picker._back(it))
        self.assertIsInstance(kw["view"], InterestScreenView)           # back to where it was opened
        _, _, kw = self.press(lambda it: view.add_button.callback(it))
        _, _, kw = self.press(lambda it: kw["view"].picker_navigate(it, category="knowledge", query="", page=0))
        kind, (modal,), _ = self.press(lambda it: kw["view"].picker_selected(it, "history"))
        set_input(modal.points_input, "10")
        _, _, kw = self.press(lambda it: modal.on_submit(it))
        self.assertIsInstance(kw["view"], InterestScreenView)
        self.assertEqual((kw["view"].selected, kw["view"].keys), ("history", ["history"]))

    def test_overview_button_and_bad_input(self):
        from apps.discord_bot.creation_views import AllocationStepView
        self.finish_choices()
        self.give_interest({"climb": 20})
        _, view = self.interest_screen(selected="climb", page=1)
        kind, (modal,), _ = self.press(lambda it: view.edit_button.callback(it))
        set_input(modal.points_input, "abc")
        _, _, kw = self.press(lambda it: modal.on_submit(it))
        self.assertIn("Nothing was saved", kw["content"])
        _, _, kw = self.press(lambda it: kw["view"].overview_button.callback(it))
        self.assertIsInstance(kw["view"], AllocationStepView)
        self.assertEqual(kw["view"].page, 1)                            # the page the player came from

    def test_add_points_mode_on_interest_screen(self):
        self.finish_choices()
        self.give_interest({"climb": 20})
        _, view = self.interest_screen(selected="climb", mode="add")
        kind, (modal,), _ = self.press(lambda it: view.edit_button.callback(it))
        set_input(modal.points_input, "+5")
        _, _, kw = self.press(lambda it: modal.on_submit(it))
        self.assertIn("20 → 25", kw["content"])
        self.assertEqual(kw["view"].mode, "add")

    # --- one-skill edits from the overview -----------------------------------------------------
    def test_edit_one_skill_from_overview(self):
        from apps.discord_bot.creation_views import AllocationStepView, SkillEditModal
        self.finish_choices()
        self.give_interest({"occult": 15})
        _, view = self.step("allocation", page=1)
        values = [o.value for o in view.edit_select.options]
        self.assertIn("acting", values)                                 # occupation skills
        self.assertIn("occult", values)                                 # interest skills
        self.assertLess(values.index("acting"), values.index("occult"))
        view.edit_select._values = ["acting"]
        kind, (modal,), _ = self.press(lambda it: view.edit_select.callback(it))
        self.assertIsInstance(modal, SkillEditModal)
        self.assertIsNotNone(modal.occ_input)
        set_input(modal.occ_input, "40")
        set_input(modal.int_input, "5")
        _, _, kw = self.press(lambda it: modal.on_submit(it))
        self.assertIn("Acting: Occupation 0 → 40 (+40)", kw["content"])
        self.assertIsInstance(kw["view"], AllocationStepView)
        self.assertEqual(kw["view"].page, 1)                            # stays on the same page
        view = kw["view"]
        view.edit_select._values = ["occult"]
        kind, (modal,), _ = self.press(lambda it: view.edit_select.callback(it))
        self.assertIsNone(modal.occ_input)                              # not an occupation skill
        with self.conn() as conn:
            self.assertEqual(load_points(conn, self.inv_id)["acting"], (40, 5))

    def test_edit_one_skill_is_atomic(self):
        self.finish_choices()
        _, view = self.step("allocation")
        view.edit_select._values = ["acting"]
        kind, (modal,), _ = self.press(lambda it: view.edit_select.callback(it))
        set_input(modal.occ_input, "40")
        set_input(modal.int_input, "999")
        _, _, kw = self.press(lambda it: modal.on_submit(it))
        self.assertIn("Nothing was saved", kw["content"])
        with self.conn() as conn:
            self.assertNotIn("acting", load_points(conn, self.inv_id))

    # --- languages -----------------------------------------------------------------------------
    def test_chinese_and_bilingual(self):
        from apps.discord_bot.views import Ctx
        self.finish_choices()
        self.give_interest({"climb": 20})
        zh = Ctx(self.db, int(ALICE), 1, "zh")
        embed, view = self.step("choices", ctx=zh, group=0)
        self.assertIn("第 1/2 组", embed.description)
        self.assertEqual(view.save_button.label, "保存选择")
        embed, view = self.step("allocation", ctx=zh, screen="interest", selected="climb")
        self.assertIn("你的兴趣技能", embed.fields[0].name)
        self.assertIn("攀爬", embed.fields[0].value)
        self.assertEqual(view.remove_button.label, "移除")
        both = Ctx(self.db, int(ALICE), 1, "both")
        _, view = self.step("allocation", ctx=both)
        self.assertIn("Edit one skill", view.edit_select.placeholder)
        self.assertIn("编辑单项技能", view.edit_select.placeholder)
        for item in view.children:
            if getattr(item, "label", None):
                self.assertLessEqual(len(item.label), 80)


if __name__ == "__main__":
    unittest.main()
