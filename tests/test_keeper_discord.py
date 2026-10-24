"""Milestone 3A Discord layer: /campaign, /keeper, Keeper Console views, campaign-aware /check and sheets.

Mocked interactions against a real temporary database (skipped without discord.py).
"""
import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from tests.test_discord_layer import HAVE_DISCORD, FakeFollowup, FakeResponse, set_input

KEEPER, ASSIST, ALICE, BOB, EVE = 300000000000000001, 300000000000000002, 111111111111111111, \
    222222222222222222, 666666666666666666
GUILD, TABLE, LOUNGE = 900000000000000001, 700000000000000001, 700000000000000099
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}


def it(user_id, channel_id=TABLE, guild_id=GUILD, name="User"):
    return SimpleNamespace(user=SimpleNamespace(id=user_id, display_name=name), guild_id=guild_id,
                           channel_id=channel_id, response=FakeResponse(), followup=FakeFollowup(), locale=None)


def user(uid):
    return SimpleNamespace(id=uid, mention=f"<@{uid}>")


@unittest.skipUnless(HAVE_DISCORD, "discord.py not installed")
class KeeperDiscordTests(unittest.TestCase):
    def setUp(self):
        from apps.discord_bot.campaign_cog import CampaignCog, KeeperCog
        from apps.discord_bot.characters_cog import CharacterCog
        from apps.discord_bot.main import CocBot, register_commands
        from cocbot.characters.service import InvestigatorService
        from cocbot.db.bootstrap import bootstrap
        from cocbot.db.connection import get_conn

        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "bot.sqlite3")
        with get_conn(self.db) as conn:
            bootstrap(conn)
            svc = InvestigatorService(conn)
            ada = svc.create(ALICE, "Ada")
            svc.set_characteristics(ALICE, ada.id, FULL)
            self.ada_id = svc.finalize(ALICE, ada.id).id
            svc.select(ALICE, self.ada_id, GUILD)
        self.loop = asyncio.new_event_loop()
        self.bot = CocBot(db_path=self.db)
        self.characters = CharacterCog(self.bot, self.db)
        self.campaign_cog = CampaignCog(self.bot, self.db)
        self.keeper_cog = KeeperCog(self.bot, self.db)
        for cog in (self.characters, self.campaign_cog, self.keeper_cog):
            self.arun(self.bot.add_cog(cog))
        register_commands(self.bot)

    def tearDown(self):
        try:
            self.loop.run_until_complete(self.bot.close())
        except Exception:
            pass
        self.loop.close()
        self.tmp.cleanup()

    # --- helpers -------------------------------------------------------------------------
    def arun(self, coro):
        return self.loop.run_until_complete(coro)

    def call(self, cog, name, interaction, *args, **kwargs):
        cmd = cog.app_command.get_command(name)
        self.arun(cmd.callback(cog, interaction, *args, **kwargs))
        return interaction.response.calls[0]

    def text_of(self, call):
        kind, args, kw = call
        return args[0] if args else kw.get("content") or ""

    def check(self, uid, text, channel=TABLE):
        interaction = it(uid, channel)
        self.arun(self.bot.tree.get_command("check").callback(interaction, text, 0))
        return interaction.response.calls[0]

    def setup_campaign(self):
        self.call(self.campaign_cog, "create", it(KEEPER), "Masks")
        self.call(self.keeper_cog, "invite", it(KEEPER), user(ALICE))
        kind, args, _ = self.call(self.campaign_cog, "join", it(ALICE), str(self.ada_id))
        self.assertIn("joined", args[0])
        with self.conn() as conn:
            return conn.execute("SELECT campaign_id FROM campaigns").fetchone()[0]

    def conn(self):
        from cocbot.db.connection import get_conn
        return get_conn(self.db)

    def campaign_hp(self, cid):
        with self.conn() as conn:
            return conn.execute("SELECT hp FROM campaign_investigators WHERE campaign_id=? AND investigator_id=?",
                                (cid, self.ada_id)).fetchone()[0]

    def panel(self, uid=KEEPER):
        kind, args, kw = self.call(self.keeper_cog, "panel", it(uid))
        return kw.get("embed"), kw.get("view"), kw

    # --- tests -------------------------------------------------------------------------------
    def test_create_invite_join_and_panel_roster(self):
        cid = self.setup_campaign()
        embed, view, kw = self.panel()
        self.assertTrue(kw["ephemeral"])
        self.assertIn("Masks", embed.title)
        self.assertIn("Ada", embed.description)
        self.assertIn("HP 12/12", embed.description)
        self.assertIsNotNone(cid)

    def test_non_keepers_cannot_open_the_console(self):
        self.setup_campaign()
        for uid in (ALICE, EVE):
            kind, args, kw = self.call(self.keeper_cog, "panel", it(uid))
            self.assertTrue(kw.get("ephemeral"))
            self.assertNotIn("embed", kw)
            self.assertIn("⚠️", args[0])

    def test_adjust_decrease_needs_confirmation_and_leaves_personal_sheet_alone(self):
        from apps.discord_bot.keeper_views import AdjustModal, ConfirmView, DetailView
        cid = self.setup_campaign()
        _, roster, _ = self.panel()
        roster.pick._values = [str(self.ada_id)]
        click = it(KEEPER)
        self.arun(roster.pick.callback(click))
        detail = click.response.calls[0][2]["view"]
        self.assertIsInstance(detail, DetailView)

        click = it(KEEPER)
        self.arun(detail._adjust(click))
        modal = click.response.calls[0][1][0]
        self.assertIsInstance(modal, AdjustModal)
        for f, v in (("hp", "-5"), ("mp", ""), ("san", ""), ("luck", "")):
            set_input(modal.inputs[f], v)
        set_input(modal.reason, "Ghoul bite")
        submit = it(KEEPER)
        self.arun(modal.on_submit(submit))
        kind, _, kw = submit.response.calls[0]
        self.assertIsInstance(kw["view"], ConfirmView)
        self.assertIn("12 → 7", kw["embed"].description)
        self.assertEqual(self.campaign_hp(cid), 12)                       # nothing saved before Confirm

        confirm = it(KEEPER)
        self.arun(kw["view"].confirm_button.callback(confirm))
        self.assertEqual(self.campaign_hp(cid), 7)
        self.assertIn("Saved", confirm.response.calls[0][2]["content"])
        with self.conn() as conn:
            self.assertEqual(conn.execute("SELECT hp_current FROM investigators WHERE investigator_id=?",
                                          (self.ada_id,)).fetchone()[0] or 12, 12)

    def test_cancel_changes_nothing(self):
        from apps.discord_bot.keeper_views import AdjustModal, render_detail
        from apps.discord_bot.views import Ctx
        cid = self.setup_campaign()
        _, detail = self.build(lambda: render_detail(Ctx(self.db, KEEPER, GUILD), cid, self.ada_id))
        click = it(KEEPER)
        self.arun(detail._adjust(click))
        modal: AdjustModal = click.response.calls[0][1][0]
        for f, v in (("hp", "0"), ("mp", ""), ("san", ""), ("luck", "")):
            set_input(modal.inputs[f], v)
        set_input(modal.reason, "")
        submit = it(KEEPER)
        self.arun(modal.on_submit(submit))
        view = submit.response.calls[0][2]["view"]
        self.assertIn("reaches 0", submit.response.calls[0][2]["embed"].description)
        cancel = it(KEEPER)
        self.arun(view.cancel_button.callback(cancel))
        self.assertEqual(self.campaign_hp(cid), 12)

    def build(self, factory):
        async def _make():
            return factory()
        return self.arun(_make())

    def test_other_users_cannot_press_a_keeper_panel(self):
        self.setup_campaign()
        _, roster, _ = self.panel()
        intruder = it(EVE)
        self.assertFalse(self.arun(roster.interaction_check(intruder)))

    def test_revoked_assistant_is_refused_on_the_next_click(self):
        from apps.discord_bot.keeper_views import render_detail
        from apps.discord_bot.views import Ctx
        cid = self.setup_campaign()
        self.call(self.keeper_cog, "assistant", it(KEEPER), user(ASSIST), True)
        _, detail = self.build(lambda: render_detail(Ctx(self.db, ASSIST, GUILD), cid, self.ada_id))
        self.call(self.keeper_cog, "assistant", it(KEEPER), user(ASSIST), False)
        click = it(ASSIST)
        self.arun(detail._adjust(click))
        kind, args, kw = click.response.calls[0]
        self.assertEqual(kind, "send_message")
        self.assertTrue(kw["ephemeral"])
        self.assertIn("⚠️", args[0])

    def test_campaign_check_uses_campaign_values_and_personal_check_does_not(self):
        from cocbot.campaigns.service import CampaignService
        cid = self.setup_campaign()
        with self.conn() as conn:
            CampaignService(conn).adjust(KEEPER, cid, self.ada_id, {"luck": 20}, confirmed=True)
        _, _, kw = self.check(ALICE, "luck")
        self.assertIn("`20`", kw["embed"].description)
        self.assertIn("Masks · rules v1", kw["embed"].footer.text)
        _, _, kw = self.check(ALICE, "luck", channel=LOUNGE)
        self.assertIn("`50`", kw["embed"].description)
        self.assertNotIn("Masks", kw["embed"].footer.text)

    def test_private_rules_make_checks_ephemeral_and_logged(self):
        from cocbot.campaigns.service import CampaignService
        from cocbot.campaigns.rules import CampaignRules
        cid = self.setup_campaign()
        _, args, _ = self.call(self.keeper_cog, "rules", it(KEEPER), 5, None,
                               SimpleNamespace(value="private"))
        self.assertIn("v2", args[0])
        _, _, kw = self.check(ALICE, "spot hidden")
        self.assertTrue(kw.get("ephemeral"))
        self.assertIn("critical 01-05", kw["embed"].footer.text)
        with self.conn() as conn:
            svc = CampaignService(conn)
            self.assertEqual(svc.rules(KEEPER, cid)[1], CampaignRules(critical_max=5, roll_visibility="private"))
            self.assertIn("roll", [e.kind for e in svc.history(KEEPER, cid)])

    def test_players_cannot_change_rules(self):
        self.setup_campaign()
        _, args, kw = self.call(self.keeper_cog, "rules", it(ALICE), 5, None, None)
        self.assertIn("⚠️", args[0])

    def test_history_revert_with_confirmation(self):
        from apps.discord_bot.keeper_views import render_history
        from apps.discord_bot.views import Ctx
        from cocbot.campaigns.service import CampaignService
        cid = self.setup_campaign()
        with self.conn() as conn:
            CampaignService(conn).adjust(KEEPER, cid, self.ada_id, {"hp": 3}, confirmed=True)
        embed, view = self.build(lambda: render_history(Ctx(self.db, KEEPER, GUILD), cid, self.ada_id))
        self.assertIn("HP 12 → 3", embed.description)
        resource_event = next(o.value for o in view.revert_select.options if "12 → 3" in o.label)
        view.revert_select._values = [resource_event]
        click = it(KEEPER)
        self.arun(view.revert_select.callback(click))
        confirm_view = click.response.calls[0][2]["view"]
        self.assertEqual(self.campaign_hp(cid), 3)
        confirm = it(KEEPER)
        self.arun(confirm_view.confirm_button.callback(confirm))
        self.assertEqual(self.campaign_hp(cid), 12)

    def test_conditions_add_and_remove(self):
        from apps.discord_bot.keeper_views import ConditionModal, render_detail
        from apps.discord_bot.views import Ctx
        cid = self.setup_campaign()
        ctx = Ctx(self.db, KEEPER, GUILD)
        _, detail = self.build(lambda: render_detail(ctx, cid, self.ada_id))
        click = it(KEEPER)
        self.arun(detail._add_condition(click))
        modal: ConditionModal = click.response.calls[0][1][0]
        set_input(modal.label_input, "Shaken")
        set_input(modal.reason, "")
        submit = it(KEEPER)
        self.arun(modal.on_submit(submit))
        detail = submit.response.calls[0][2]["view"]
        self.assertIn("Shaken", submit.response.calls[0][2]["embed"].fields[1].value)
        detail.remove_select._values = [detail.remove_select.options[0].value]
        click = it(KEEPER)
        self.arun(detail.remove_select.callback(click))
        confirm = it(KEEPER)
        self.arun(click.response.calls[0][2]["view"].confirm_button.callback(confirm))
        self.assertIn("none", confirm.response.calls[0][2]["embed"].fields[1].value)

    def test_join_request_flow_through_panel(self):
        self.call(self.campaign_cog, "create", it(KEEPER), "Masks")
        _, args, _ = self.call(self.campaign_cog, "join", it(ALICE), str(self.ada_id))
        self.assertIn("Request sent", args[0])
        _, roster, _ = self.panel()
        roster.requests._values = [str(ALICE)]
        click = it(KEEPER)
        self.arun(roster.requests.callback(click))
        request_view = click.response.calls[0][2]["view"]
        approve = it(KEEPER)
        self.arun(request_view._approve(approve))
        self.assertIn("Ada", approve.response.calls[0][2]["embed"].description)

    def test_sheet_in_campaign_channel_shows_campaign_values(self):
        from apps.discord_bot.views import Ctx, render_panel
        from cocbot.campaigns.service import CampaignService
        cid = self.setup_campaign()
        with self.conn() as conn:
            CampaignService(conn).adjust(KEEPER, cid, self.ada_id, {"hp": 4}, confirmed=True)
        _, _, kw = self.call(self.characters, "view", it(ALICE))
        names = [f.name for f in kw["embed"].fields]
        self.assertTrue(any("Masks" in n for n in names))
        _, _, kw = self.call(self.characters, "view", it(ALICE, channel_id=LOUNGE))
        self.assertFalse(any("Masks" in f.name for f in kw["embed"].fields))
        _, editor = self.build(lambda: render_panel(Ctx(self.db, ALICE, GUILD, channel_id=TABLE), self.ada_id))
        self.assertTrue(editor.resources_button.disabled)
        _, editor = self.build(lambda: render_panel(Ctx(self.db, ALICE, GUILD, channel_id=LOUNGE), self.ada_id))
        self.assertFalse(editor.resources_button.disabled)

    def test_remove_player_requires_confirmation(self):
        self.setup_campaign()
        kind, args, kw = self.call(self.keeper_cog, "remove", it(KEEPER), user(ALICE), "left the group")
        self.assertIn("Remove", args[0])
        _, roster, _ = self.panel()
        self.assertIn("Ada", self.panel()[0].description)        # still there until confirmed
        confirm = it(KEEPER)
        self.arun(kw["view"].confirm_button.callback(confirm))
        self.assertIn("removed", confirm.response.calls[0][2]["content"])
        self.assertNotIn("Ada", self.panel()[0].description)

    def test_campaign_autocomplete_lists_only_my_campaigns(self):
        self.setup_campaign()
        self.call(self.campaign_cog, "create", it(BOB, channel_id=LOUNGE), "Bob's game")
        cmd = self.keeper_cog.app_command.get_command("panel")
        choices = self.arun(cmd._params["campaign"].autocomplete(self.keeper_cog, it(KEEPER), ""))
        self.assertEqual([c.name for c in choices], ["Masks (#1)"])
        self.assertEqual(self.arun(cmd._params["campaign"].autocomplete(self.keeper_cog, it(ALICE), "")), [])

    def test_info_and_leave(self):
        from cocbot.campaigns.service import CampaignService
        cid = self.setup_campaign()
        with self.conn() as conn:
            CampaignService(conn).adjust(KEEPER, cid, self.ada_id, {"luck": 20}, confirmed=True)
        _, _, kw = self.call(self.campaign_cog, "info", it(ALICE))
        self.assertIn("Ada", kw["embed"].description)
        _, args, kw = self.call(self.campaign_cog, "leave", it(ALICE))
        confirm = it(ALICE)
        self.arun(kw["view"].confirm_button.callback(confirm))
        self.assertIn("left", confirm.response.calls[0][2]["content"])
        _, _, kw = self.check(ALICE, "luck")
        self.assertIn("`50`", kw["embed"].description)          # personal value once she has left

    def test_chinese_console(self):
        from cocbot.preferences import set_language
        self.setup_campaign()
        with self.conn() as conn:
            set_language(conn, KEEPER, "zh")
        embed, view, _ = self.panel()
        self.assertIn("守秘人控制台", embed.title)
        self.assertIn("规则", embed.footer.text)


if __name__ == "__main__":
    unittest.main()
