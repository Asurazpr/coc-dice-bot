"""Discord-layer integration tests with mocked interactions.

These exercise the real slash-command callbacks, modals and views against a
real (temporary) database, replacing only the Discord network objects.
They are skipped automatically if discord.py is not installed.
"""
import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

try:
    import discord  # noqa: F401
    HAVE_DISCORD = True
except ImportError:  # pragma: no cover
    HAVE_DISCORD = False

ALICE, BOB = 111111111111111111, 222222222222222222
GUILD = 900000000000000001


class FakeResponse:
    def __init__(self):
        self.calls = []
        self._done = False

    def is_done(self):
        return self._done

    async def _record(self, kind, *args, **kwargs):
        assert not self._done, "interaction responded to twice"
        self._done = True
        self.calls.append((kind, args, kwargs))

    async def send_message(self, *a, **k):
        await self._record("send_message", *a, **k)

    async def send_modal(self, modal):
        await self._record("send_modal", modal)

    async def edit_message(self, *a, **k):
        await self._record("edit_message", *a, **k)

    async def defer(self, *a, **k):
        await self._record("defer", *a, **k)


class FakeFollowup:
    def __init__(self):
        self.calls = []

    async def send(self, *a, **k):
        self.calls.append((a, k))


def fake_interaction(user_id, guild_id=GUILD, name="Player"):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name=name),
        guild_id=guild_id,
        response=FakeResponse(),
        followup=FakeFollowup(),
    )


def set_input(text_input, value):
    # TextInput.value is read-only in discord.py; tests set the backing field.
    text_input._value = value


@unittest.skipUnless(HAVE_DISCORD, "discord.py not installed")
class DiscordLayerTests(unittest.TestCase):
    def setUp(self):
        from apps.discord_bot.main import CocBot, register_commands
        from apps.discord_bot.characters_cog import CharacterCog
        from cocbot.db.bootstrap import bootstrap
        from cocbot.db.connection import get_conn

        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "bot.sqlite3")
        with get_conn(self.db) as conn:
            bootstrap(conn)
        self.loop = asyncio.new_event_loop()
        self.bot = CocBot(db_path=self.db)
        self.cog = CharacterCog(self.bot, self.db)
        self.arun(self.bot.add_cog(self.cog))
        register_commands(self.bot)

    def tearDown(self):
        try:
            self.loop.run_until_complete(self.bot.close())
        except Exception:
            pass  # never logged in; nothing to close
        self.loop.close()
        self.tmp.cleanup()

    def arun(self, coro):
        return self.loop.run_until_complete(coro)

    def build(self, factory):
        """discord.ui views/modals need a running event loop at construction."""
        async def _make():
            return factory()
        return self.arun(_make())

    def sub(self, name):
        return self.cog.app_command.get_command(name)

    def create(self, user, name, guild=GUILD):
        from apps.discord_bot.views import BioModal
        it = fake_interaction(user, guild)
        self.arun(self.sub("create").callback(self.cog, it))
        kind, (modal,), _ = it.response.calls[0]
        self.assertEqual(kind, "send_modal")
        self.assertIsInstance(modal, BioModal)
        set_input(modal.name_input, name)
        for ti in (modal.occupation_input, modal.age_input, modal.residence_input, modal.birthplace_input):
            set_input(ti, "")
        submit = fake_interaction(user, guild)
        self.arun(modal.on_submit(submit))
        kind, _, kwargs = submit.response.calls[0]
        self.assertEqual(kind, "send_message")
        self.assertTrue(kwargs["ephemeral"])
        return kwargs["view"]  # EditorView

    def check(self, user, text, bp=0, guild=GUILD):
        it = fake_interaction(user, guild)
        cmd = self.bot.tree.get_command("check")
        self.arun(cmd.callback(it, text, bp))
        return it.response.calls[0]

    def test_create_edit_and_check_flow(self):
        from apps.discord_bot.views import CharacteristicsModal, SkillModal

        panel = self.create(ALICE, "Ada")
        # first investigator auto-selected here
        # fill characteristics through the modal
        modal = self.build(lambda: CharacteristicsModal(panel.ctx, panel.inv, ("STR", "CON", "SIZ", "DEX", "APP")))
        for k, v in zip(("STR", "CON", "SIZ", "DEX", "APP"), ("60", "55", "65", "70", "50")):
            set_input(modal.inputs[k], v)
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        self.assertEqual(it.response.calls[0][0], "edit_message")

        skill = self.build(lambda: SkillModal(panel.ctx, panel.inv))
        set_input(skill.skill_input, "Spot Hidden")
        set_input(skill.value_input, "80")
        it = fake_interaction(ALICE)
        self.arun(skill.on_submit(it))

        kind, _, kwargs = self.check(ALICE, "spot hidden")
        self.assertEqual(kind, "send_message")
        embed = kwargs["embed"]
        self.assertIn("Spot Hidden (trained)", embed.title)
        self.assertIn("`80`", embed.description)
        self.assertIn("Ada", embed.footer.text)

        kind, _, kwargs = self.check(ALICE, "dodge")
        self.assertIn("`35`", kwargs["embed"].description)

    def test_two_users_do_not_interfere(self):
        self.create(ALICE, "Ada")
        self.create(BOB, "Bram")
        _, _, a = self.check(ALICE, "listen")
        _, _, b = self.check(BOB, "listen")
        self.assertIn("Ada", a["embed"].footer.text)
        self.assertIn("Bram", b["embed"].footer.text)

    def test_numeric_and_bonus_penalty_checks_still_work(self):
        kind, _, kwargs = self.check(BOB, "60", bp=2)
        self.assertEqual(kind, "send_message")
        self.assertIn("Bonus Dice (2)", kwargs["embed"].description)
        kind, _, kwargs = self.check(BOB, "60", bp=-1)
        self.assertIn("Penalty Dice (1)", kwargs["embed"].description)
        kind, _, kwargs = self.check(BOB, "150")
        self.assertTrue(kwargs["ephemeral"])

    def test_panel_rejects_other_users(self):
        panel = self.create(ALICE, "Ada")
        it = fake_interaction(BOB)
        allowed = self.arun(panel.interaction_check(it))
        self.assertFalse(allowed)
        self.assertTrue(it.response.calls[0][2]["ephemeral"])

    def test_select_command_and_list(self):
        self.create(ALICE, "Ada")
        self.create(ALICE, "Harvey")
        it = fake_interaction(ALICE)
        self.arun(self.sub("select").callback(self.cog, it, "Harvey", False))
        self.assertIn("Harvey", it.response.calls[0][1][0])
        _, _, kwargs = self.check(ALICE, "listen")
        self.assertIn("Harvey", kwargs["embed"].footer.text)

        it = fake_interaction(ALICE)
        self.arun(self.sub("list").callback(self.cog, it, False))
        embed = it.response.calls[0][2]["embed"]
        self.assertIn("⭐ **Harvey**", embed.description)

        # Bob cannot select Alice's investigator by name
        it = fake_interaction(BOB)
        self.arun(self.sub("select").callback(self.cog, it, "Harvey", False))
        self.assertIn("no investigator named", it.response.calls[0][1][0])

    def test_list_paginates_many_archived(self):
        from cocbot.characters.service import InvestigatorService
        from cocbot.db.connection import get_conn
        with get_conn(self.db) as conn:
            svc = InvestigatorService(conn)
            for n in range(35):
                inv = svc.create(ALICE, f"Inv {n:02d} " + "x" * 50, occupation="y" * 100)
                if n >= 5:
                    svc.archive(ALICE, inv.id)
        it = fake_interaction(ALICE)
        self.arun(self.sub("list").callback(self.cog, it, True))
        _, _, kwargs = it.response.calls[0]
        embed, view = kwargs["embed"], kwargs["view"]
        self.assertLessEqual(len(embed.description), 4096)
        self.assertLessEqual(len(embed), 6000)
        self.assertIn("Page 1/4", embed.footer.text)
        nxt = [c for c in view.children if getattr(c, "label", "") == "Next ▶"][0]
        it2 = fake_interaction(ALICE)
        self.arun(nxt.callback(it2))
        embed2 = it2.response.calls[0][2]["embed"]
        self.assertIn("Page 2/4", embed2.footer.text)
        self.assertLessEqual(len(embed2), 6000)

    def test_select_everywhere_reports_server_override_and_unselect(self):
        self.create(ALICE, "Ada")      # auto-selected for this server
        self.create(ALICE, "Bram")
        it = fake_interaction(ALICE)
        self.arun(self.sub("select").callback(self.cog, it, "Bram", True))
        msg = it.response.calls[0][1][0]
        self.assertIn("default everywhere", msg)
        self.assertIn("still applies here", msg)
        _, _, kwargs = self.check(ALICE, "listen")
        self.assertIn("Ada", kwargs["embed"].footer.text)

        it = fake_interaction(ALICE)
        self.arun(self.sub("unselect").callback(self.cog, it, False))
        self.assertIn("now uses **Bram**", it.response.calls[0][1][0])
        _, _, kwargs = self.check(ALICE, "listen")
        self.assertIn("Bram", kwargs["embed"].footer.text)

    # --- Milestone 2: occupation creation through the Discord UI ------------------

    def _draft_with_characteristics(self, user=ALICE, name="Ada"):
        from cocbot.characters.service import InvestigatorService
        from cocbot.db.connection import get_conn
        panel = self.create(user, name)
        with get_conn(self.db) as conn:
            InvestigatorService(conn).set_characteristics(user, panel.inv.id, {
                "STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50})
        return panel.inv.id

    def _occupation_id(self, key):
        from cocbot.db.connection import get_conn
        with get_conn(self.db) as conn:
            return conn.execute("SELECT occupation_id FROM occupations WHERE key=?", (key,)).fetchone()[0]

    def test_creation_requires_characteristics(self):
        panel = self.create(ALICE, "Ada")
        it = fake_interaction(ALICE)
        self.arun(panel.creation_button.callback(it))
        kind, args, kwargs = it.response.calls[0]
        self.assertEqual(kind, "send_message")
        self.assertIn("characteristics", args[0])
        self.assertTrue(kwargs["ephemeral"])

    def test_full_occupation_creation_flow(self):
        from apps.discord_bot.creation_views import (
            AllocationStepView, ChoicesStepView, OccupationStepView, PointsModal, ReviewStepView, render_step,
        )
        from apps.discord_bot.views import Ctx, EditorView, render_panel

        inv_id = self._draft_with_characteristics()
        ctx = Ctx(self.db, ALICE, GUILD)
        panel = self.build(lambda: render_panel(ctx, inv_id)[1])
        it = fake_interaction(ALICE)
        self.arun(panel.creation_button.callback(it))
        kind, _, kw = it.response.calls[0]
        self.assertEqual(kind, "edit_message")
        self.assertIsInstance(kw["view"], OccupationStepView)
        self.assertIn("Occupation", kw["embed"].title)
        self.assertLessEqual(len(kw["embed"]), 6000)

        # occupation -> choices
        it = fake_interaction(ALICE)
        self.arun(kw["view"].choose(it, self._occupation_id("accountant"), force=False))
        kw = it.response.calls[0][2]
        self.assertIsInstance(kw["view"], ChoicesStepView)
        self.assertIn("Accountant", kw["content"])

        # two "any" picks -> allocation
        it = fake_interaction(ALICE)
        self.arun(kw["view"].apply_pick(it, "psychology"))
        kw = it.response.calls[0][2]
        it = fake_interaction(ALICE)
        self.arun(kw["view"].apply_pick(it, "charm"))
        kw = it.response.calls[0][2]
        alloc = kw["view"]
        self.assertIsInstance(alloc, AllocationStepView)
        self.assertEqual(alloc.keys, ["accounting", "law", "library_use", "listen", "persuade"])

        # occupation points through the page modal: one bad field -> the whole form is refused
        modal = self.build(lambda: PointsModal(alloc, "occupation"))
        self.assertIn("300 left", modal.title)
        set_input(modal.inputs["accounting"][0], "60")
        set_input(modal.inputs["law"][0], "200")
        for k in ("library_use", "listen", "persuade"):
            set_input(modal.inputs[k][0], "")
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        kw = it.response.calls[0][2]
        self.assertIn("Nothing was saved", kw["content"])
        self.assertIn("Law would be 205", kw["content"])
        # corrected form: saved as one batch, with changes and remaining points listed
        modal = self.build(lambda: PointsModal(kw["view"], "occupation"))
        set_input(modal.inputs["accounting"][0], "60")
        set_input(modal.inputs["law"][0], "20")
        for k in ("library_use", "listen", "persuade"):
            set_input(modal.inputs[k][0], "")
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        content = it.response.calls[0][2]["content"]
        self.assertIn("Saved.", content)
        self.assertIn("Accounting: Occupation 0 → 60 (+60)", content)
        self.assertIn("Left: occupation 220", content)

        # Credit Rating via the slash command, then review and finish
        it = fake_interaction(ALICE)
        self.arun(self.sub("allocate").callback(self.cog, it, "credit_rating", 40, None, str(inv_id)))
        self.assertIn("Credit Rating = **40**", it.response.calls[0][1][0])
        embed, review = self.build(lambda: render_step(ctx, inv_id, "review"))
        self.assertIsInstance(review, ReviewStepView)
        self.assertIn("Meets the occupation", embed.description)
        # unspent points: first click asks for confirmation, second click finishes
        self.assertIn("unspent", review.finish_btn.label)
        it = fake_interaction(ALICE)
        self.arun(review._finish(it))
        kw = it.response.calls[0][2]
        self.assertIn("Click again", kw["content"])
        self.assertTrue(kw["view"].confirm)
        it = fake_interaction(ALICE)
        self.arun(kw["view"]._finish(it))
        kind, _, kw = it.response.calls[0]
        self.assertIn("is created", kw["content"])
        self.assertIsInstance(kw["view"], EditorView)
        self.assertIn("Created (occupation rules checked)", kw["embed"].description)

        # /check uses the allocated value (5 base + 60)
        _, _, kw = self.check(ALICE, "accounting")
        self.assertIn("`65`", kw["embed"].description)

    def test_occupation_command_autocomplete_and_confirmation(self):
        inv_id = self._draft_with_characteristics()
        it = fake_interaction(ALICE)
        choices = self.arun(self.cog.occupation_autocomplete(it, "会计"))
        self.assertTrue(any("Accountant" in c.name for c in choices))
        self.assertLessEqual(len(choices), 25)

        it = fake_interaction(ALICE)
        self.arun(self.sub("occupation").callback(self.cog, it, "accountant", str(inv_id)))
        self.assertIn("Occupation set to **Accountant**", it.response.calls[0][2]["content"])
        it = fake_interaction(ALICE)
        self.arun(self.sub("allocate").callback(self.cog, it, "law", 30, None, str(inv_id)))
        # changing now needs confirmation and shows the pending change
        it = fake_interaction(ALICE)
        self.arun(self.sub("occupation").callback(self.cog, it, "antiquarian", str(inv_id)))
        kw = it.response.calls[0][2]
        self.assertIn("Confirm", kw["content"])
        self.assertTrue(any(f.name == "Confirm change" for f in kw["embed"].fields))
        # Bob cannot use Alice's investigator
        it = fake_interaction(BOB)
        self.arun(self.sub("occupation").callback(self.cog, it, "antiquarian", str(inv_id)))
        self.assertIn("not found", it.response.calls[0][1][0].lower())

    def _accountant_ready_but_for(self, inv_id):
        from apps.discord_bot.creation_views import creation
        from apps.discord_bot.views import Ctx
        with creation(Ctx(self.db, ALICE, GUILD)) as (_, svc):
            svc.set_occupation(ALICE, inv_id, "accountant")
            svc.set_pick(ALICE, inv_id, 6, 0, "psychology")
            svc.set_pick(ALICE, inv_id, 6, 1, "charm")
            svc.allocate(ALICE, inv_id, "credit_rating", occupation_points=40)

    def test_skill_command_cannot_bypass_creation_rules(self):
        inv_id = self._draft_with_characteristics()
        self._accountant_ready_but_for(inv_id)
        for skill, value in (("cthulhu_mythos", 30), ("occult", 150), ("listen", 60)):
            it = fake_interaction(ALICE)
            self.arun(self.sub("skill").callback(self.cog, it, skill, value, str(inv_id)))
            kind, args, kw = it.response.calls[0]
            self.assertIn("skill points", args[0])
            self.assertTrue(kw["ephemeral"])
        # the editor routes Finish to Review and disables manual skill entry
        from apps.discord_bot.views import Ctx, render_panel
        panel = self.build(lambda: render_panel(Ctx(self.db, ALICE, GUILD), inv_id)[1])
        self.assertTrue(panel.skill_button.disabled)
        self.assertEqual(panel.finish_button.label, "Review & finish")

    def test_hand_entered_values_must_be_reconciled_in_review(self):
        from apps.discord_bot.creation_views import ReviewStepView, render_step
        from apps.discord_bot.views import Ctx
        inv_id = self._draft_with_characteristics()
        # M1-style entries made before choosing an occupation
        for skill, value in (("cthulhu_mythos", 30), ("listen", 60)):
            it = fake_interaction(ALICE)
            self.arun(self.sub("skill").callback(self.cog, it, skill, value, str(inv_id)))
        self._accountant_ready_but_for(inv_id)
        ctx = Ctx(self.db, ALICE, GUILD)
        embed, review = self.build(lambda: render_step(ctx, inv_id, "review"))
        self.assertIsInstance(review, ReviewStepView)
        self.assertTrue(review.finish_btn.disabled)
        self.assertTrue(any(f.name == "Hand-entered values to reconcile" for f in embed.fields))
        self.assertIn("Cthulhu Mythos is 30", embed.description)
        values = [o.value for o in review.reconcile_select.options]
        self.assertIn("discard:cthulhu_mythos", values)
        self.assertIn("convert:listen", values)

        it = fake_interaction(ALICE)
        self.arun(review.apply_reconcile(it, "convert:cthulhu_mythos"))          # refused
        self.assertIn("discard", it.response.calls[0][1][0])
        it = fake_interaction(ALICE)
        self.arun(review.apply_reconcile(it, "discard:cthulhu_mythos"))
        review2 = it.response.calls[0][2]["view"]
        it = fake_interaction(ALICE)
        self.arun(review2.apply_reconcile(it, "convert:listen"))
        kw = it.response.calls[0][2]
        self.assertFalse(kw["view"].finish_btn.disabled)
        self.assertFalse(any(f.name == "Hand-entered values to reconcile" for f in kw["embed"].fields))
        # /check now uses the converted value
        _, _, ck = self.check(ALICE, "listen")
        self.assertIn("`60`", ck["embed"].description)

    def test_formula_choice_select(self):
        from apps.discord_bot.creation_views import AllocationStepView, render_step
        from apps.discord_bot.views import Ctx
        inv_id = self._draft_with_characteristics()
        it = fake_interaction(ALICE)
        self.arun(self.sub("occupation").callback(self.cog, it, "private_investigator", str(inv_id)))
        ctx = Ctx(self.db, ALICE, GUILD)
        embed, view = self.build(lambda: render_step(ctx, inv_id, "allocation"))
        self.assertIsInstance(view, AllocationStepView)
        labels = {o.value: o.label for o in view.stat_select.options}
        self.assertEqual(labels["DEX"], "DEX×2 = 140 (suggested)")
        self.assertEqual(labels["STR"], "STR×2 = 120")
        it = fake_interaction(ALICE)
        self.arun(view.choose_stat(it, "STR"))
        kw = it.response.calls[0][2]
        self.assertIn("STR", kw["content"])
        self.assertIn("270", kw["embed"].description)      # EDU 75×2 + STR 60×2

    # --- M2 UX polish ---------------------------------------------------------------------------

    def _alloc_view(self, inv_id, ctx=None, **state):
        from apps.discord_bot.creation_views import render_step
        from apps.discord_bot.views import Ctx
        return self.build(lambda: render_step(ctx or Ctx(self.db, ALICE, GUILD), inv_id, "allocation", **state))

    def _submit(self, modal, values):
        for key, (ti, _) in modal.inputs.items():
            set_input(ti, values.get(key, ""))
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        return it.response.calls[0][2]

    def test_points_form_refund_in_one_submission(self):
        from apps.discord_bot.creation_views import PointsModal
        inv_id = self._draft_with_characteristics()
        self._accountant_ready_but_for(inv_id)          # Credit Rating 40 occupation points
        # spend the rest: 300 - 40 = 260 on page 1
        _, view = self._alloc_view(inv_id)
        kw = self._submit(self.build(lambda: PointsModal(view, "occupation")),
                          {"accounting": "70", "law": "70", "library_use": "70", "listen": "50", "persuade": ""})
        self.assertIn("Left: occupation 0", kw["content"])
        # fully spent: move 30 from Accounting to Persuade in one form
        modal = self.build(lambda: PointsModal(kw["view"], "occupation"))
        self.assertEqual(modal.inputs["accounting"][0].default, "70")       # current allocation shown
        self.assertIn("now 70", modal.inputs["accounting"][0].label)
        self.assertIn("0 left", modal.title)
        kw = self._submit(modal, {"accounting": "40", "law": "70", "library_use": "70", "listen": "50",
                                  "persuade": "30"})
        self.assertIn("Saved.", kw["content"])
        self.assertIn("Accounting: Occupation 70 → 40 (-30)", kw["content"])
        self.assertIn("Persuade: Occupation 0 → 30 (+30)", kw["content"])
        self.assertIn("Left: occupation 0", kw["content"])

    def test_points_form_add_mode_and_overflow(self):
        from apps.discord_bot.creation_views import PointsModal
        inv_id = self._draft_with_characteristics()
        self._accountant_ready_but_for(inv_id)
        _, view = self._alloc_view(inv_id)
        it = fake_interaction(ALICE)
        self.arun(view._toggle_mode(it))
        view = it.response.calls[0][2]["view"]
        self.assertEqual(view.mode, "add")
        modal = self.build(lambda: PointsModal(view, "occupation"))
        self.assertIn("Add occupation points", modal.title)
        self.assertIsNone(modal.inputs["accounting"][0].default)
        kw = self._submit(modal, {"accounting": "+99", "law": "+99", "library_use": "+79", "listen": "+79"})
        self.assertIn("Nothing was saved", kw["content"])
        self.assertIn("exceed the budget by 96", kw["content"])         # 40 + 356 - 300
        kw = self._submit(self.build(lambda: PointsModal(kw["view"], "occupation")), {"accounting": "abc"})
        self.assertIn("is not a whole number", kw["content"])
        kw = self._submit(self.build(lambda: PointsModal(kw["view"], "occupation")), {"accounting": "+25"})
        self.assertIn("Accounting: Occupation 0 → 25 (+25)", kw["content"])

    def test_choice_picker_categories_search_and_navigation(self):
        """Grouped choices: tick skills across categories and search, then save the group once."""
        from apps.discord_bot.creation_views import ChoicesStepView, SkillSearchModal, render_step
        from apps.discord_bot.views import Ctx
        from cocbot.db.connection import get_conn
        from cocbot.occupations.service import load_picks
        inv_id = self._draft_with_characteristics()
        it = fake_interaction(ALICE)
        self.arun(self.sub("occupation").callback(self.cog, it, "accountant", str(inv_id)))
        view = it.response.calls[0][2]["view"]
        self.assertIsInstance(view, ChoicesStepView)
        # "any 2 skills": too many for one menu -> category select first, no skill menu yet
        self.assertTrue(hasattr(view, "category_select"))
        self.assertFalse(hasattr(view, "skill_select"))
        self.assertLessEqual(len(view.category_select.options), 25)
        self.assertTrue(view.save_button.disabled)                 # nothing to save yet
        # pick a category: a multi-select allowing up to 2
        it = fake_interaction(ALICE)
        self.arun(view.picker_navigate(it, category="social", query="", page=0))
        kw = it.response.calls[0][2]
        view = kw["view"]
        keys = [o.value for o in view.skill_select.options]
        self.assertIn("charm", keys)
        self.assertNotIn("credit_rating", keys)                    # never selectable as a choice
        self.assertEqual(view.skill_select.max_values, 2)
        self.assertIn("Category: **Social**", kw["embed"].description)
        self.assertIn("0/2 choices made · 2 remaining", kw["embed"].description)
        # tick Charm: shown as selected but not saved
        it = fake_interaction(ALICE)
        self.arun(view.picker_selected_many(it, ["charm"]))
        kw = it.response.calls[0][2]
        self.assertIn("Selected (1/2): Charm", kw["embed"].description)
        self.assertIn("Not saved yet", kw["embed"].description)
        with get_conn(self.db) as conn:
            self.assertEqual(load_picks(conn, inv_id), {})
        # search (Chinese) keeps the selection; Charm is shown ticked
        modal = self.build(lambda: SkillSearchModal(kw["view"]))
        set_input(modal.query_input, "魅惑")
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        kw = it.response.calls[0][2]
        self.assertEqual([(o.value, o.default) for o in kw["view"].skill_select.options], [("charm", True)])
        modal = self.build(lambda: SkillSearchModal(kw["view"]))
        set_input(modal.query_input, "psych")
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        view = it.response.calls[0][2]["view"]
        self.assertEqual([o.value for o in view.skill_select.options][:1], ["psychology"])
        it = fake_interaction(ALICE)
        self.arun(view.picker_selected_many(it, ["psychology"]))
        kw = it.response.calls[0][2]
        self.assertIn("Selected (2/2): Charm, Psychology", kw["embed"].description)
        # save the group -> allocation
        it = fake_interaction(ALICE)
        self.arun(kw["view"].save_button.callback(it))
        self.assertIn("All occupation skills chosen", it.response.calls[0][2]["content"])
        with get_conn(self.db) as conn:
            self.assertEqual(load_picks(conn, inv_id), {(6, 0): "charm", (6, 1): "psychology"})
        # come back: saved choices are pre-ticked; swap Charm for Dodge
        _, view = self.build(lambda: render_step(Ctx(self.db, ALICE, GUILD), inv_id, "choices", group=0))
        self.assertEqual(view.draft, ("charm", "psychology"))
        it = fake_interaction(ALICE)
        self.arun(view.picker_navigate(it, category="social", query="", page=0))
        social = it.response.calls[0][2]["view"]
        still_ticked = [o.value for o in social.skill_select.options if o.default and o.value != "charm"]
        it2 = fake_interaction(ALICE)
        self.arun(social.picker_selected_many(it2, still_ticked))                  # untick Charm only
        it3 = fake_interaction(ALICE)
        self.arun(it2.response.calls[0][2]["view"].picker_navigate(it3, category="physical", query="", page=0))
        it4 = fake_interaction(ALICE)
        self.arun(it3.response.calls[0][2]["view"].picker_selected_many(it4, ["dodge"]))
        it5 = fake_interaction(ALICE)
        self.arun(it4.response.calls[0][2]["view"].save_button.callback(it5))
        with get_conn(self.db) as conn:
            self.assertEqual(load_picks(conn, inv_id), {(6, 0): "psychology", (6, 1): "dodge"})

    def test_interest_picker_opens_points_modal(self):
        from apps.discord_bot.creation_views import InterestPickerView, InterestPointsModal
        inv_id = self._draft_with_characteristics()
        self._accountant_ready_but_for(inv_id)
        _, view = self._alloc_view(inv_id)
        it = fake_interaction(ALICE)
        self.arun(view._other(it))
        picker = it.response.calls[0][2]["view"]
        self.assertIsInstance(picker, InterestPickerView)
        it = fake_interaction(ALICE)
        self.arun(picker.picker_navigate(it, category="physical", query="", page=0))
        picker = it.response.calls[0][2]["view"]
        self.assertIn("climb", [o.value for o in picker.skill_select.options])
        it = fake_interaction(ALICE)
        self.arun(picker.picker_selected(it, "climb"))
        kind, (modal,), _ = it.response.calls[0]
        self.assertEqual(kind, "send_modal")
        self.assertIsInstance(modal, InterestPointsModal)
        self.assertIn("160 left", modal.points_input.label)
        set_input(modal.points_input, "30")
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        kw = it.response.calls[0][2]
        content = kw["content"]
        self.assertIn("Climb: Personal interest 0 → 30 (+30)", content)
        self.assertIn("personal interest 130", content)
        # back in personal-interest management, with Climb selected
        from apps.discord_bot.creation_views import InterestScreenView
        self.assertIsInstance(kw["view"], InterestScreenView)
        self.assertEqual(kw["view"].selected, "climb")

    def test_language_command_and_chinese_wizard(self):
        from apps.discord_bot.views import Ctx
        inv_id = self._draft_with_characteristics()
        cmd = self.bot.tree.get_command("language")
        it = fake_interaction(ALICE)
        self.arun(cmd.callback(it, None))
        self.assertIn("English", it.response.calls[0][1][0])
        it = fake_interaction(ALICE)
        self.arun(cmd.callback(it, SimpleNamespace(value="zh")))
        self.assertIn("简体中文", it.response.calls[0][1][0])
        # the cog now builds a Chinese context for Alice, but not for Bob
        self.assertEqual(self.cog.ctx(fake_interaction(ALICE)).lang, "zh")
        self.assertEqual(self.cog.ctx(fake_interaction(BOB)).lang, "en")
        it = fake_interaction(ALICE)
        self.arun(self.sub("occupation").callback(self.cog, it, "会计师", str(inv_id)))
        kw = it.response.calls[0][2]
        self.assertIn("职业已设为 **会计师**", kw["content"])
        self.assertIn("本职技能选择", kw["embed"].title)
        self.assertIn("选择类别", kw["view"].category_select.placeholder)
        # Chinese errors
        it = fake_interaction(ALICE)
        self.arun(self.sub("allocate").callback(self.cog, it, "climb", 10, None, str(inv_id)))
        self.assertIn("职业点数只能分配给本职技能和信誉", it.response.calls[0][1][0])
        # bilingual
        it = fake_interaction(ALICE)
        self.arun(cmd.callback(it, SimpleNamespace(value="both")))
        embed, _ = self._alloc_view(inv_id, Ctx(self.db, ALICE, GUILD, "both"))
        self.assertIn("Skill points", embed.title)
        self.assertIn("技能点数", embed.title)


if __name__ == "__main__":
    unittest.main()
