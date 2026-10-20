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

        # occupation points through the page modal
        modal = self.build(lambda: PointsModal(alloc, "occupation"))
        set_input(modal.inputs["accounting"][0], "60")
        set_input(modal.inputs["law"][0], "200")     # invalid -> reported, others still saved
        for k in ("library_use", "listen", "persuade"):
            set_input(modal.inputs[k][0], "")
        it = fake_interaction(ALICE)
        self.arun(modal.on_submit(it))
        kw = it.response.calls[0][2]
        self.assertIn("Saved: Accounting", kw["content"])
        self.assertIn("Not saved", kw["content"])

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


if __name__ == "__main__":
    unittest.main()
