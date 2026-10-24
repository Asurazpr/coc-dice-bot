"""M3C Discord layer: /character import preview, create/update, and Keeper inspection (mocked interactions)."""
import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from tests.test_discord_layer import HAVE_DISCORD, FakeFollowup, FakeResponse
from tests.xlsx_factory import sheet_bytes

ALICE, BOB, KEEPER = 111111111111111111, 222222222222222222, 300000000000000001
GUILD, TABLE = 900000000000000001, 700000000000000001


class FakeAttachment:
    def __init__(self, data: bytes, filename: str = "ada.xlsx", size=None):
        self.data, self.filename = data, filename
        self.size = len(data) if size is None else size
        self.reads = 0

    async def read(self):
        self.reads += 1
        return self.data


def it(user_id, channel_id=TABLE):
    return SimpleNamespace(user=SimpleNamespace(id=user_id, display_name="User"), guild_id=GUILD,
                           channel_id=channel_id, response=FakeResponse(), followup=FakeFollowup(), locale=None)


@unittest.skipUnless(HAVE_DISCORD, "discord.py not installed")
class ImportDiscordTests(unittest.TestCase):
    def setUp(self):
        from apps.discord_bot.campaign_cog import CampaignCog, KeeperCog
        from apps.discord_bot.characters_cog import CharacterCog
        from apps.discord_bot.main import CocBot, register_commands
        from cocbot.db.bootstrap import bootstrap
        from cocbot.db.connection import get_conn
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "bot.sqlite3")
        with get_conn(self.db) as conn:
            bootstrap(conn)
        self.loop = asyncio.new_event_loop()
        self.bot = CocBot(db_path=self.db)
        self.chars = CharacterCog(self.bot, self.db)
        self.campaign_cog = CampaignCog(self.bot, self.db)
        self.keeper_cog = KeeperCog(self.bot, self.db)
        for cog in (self.chars, self.campaign_cog, self.keeper_cog):
            self.arun(self.bot.add_cog(cog))
        register_commands(self.bot)

    def tearDown(self):
        try:
            self.loop.run_until_complete(self.bot.close())
        except Exception:
            pass
        self.loop.close()
        self.tmp.cleanup()

    def arun(self, coro):
        return self.loop.run_until_complete(coro)

    def conn(self):
        from cocbot.db.connection import get_conn
        return get_conn(self.db)

    def do_import(self, user=ALICE, update=None, attachment=None, **kw):
        interaction = it(user)
        attachment = attachment or FakeAttachment(sheet_bytes(**kw))
        cmd = self.chars.app_command.get_command("import")
        self.arun(cmd.callback(self.chars, interaction, attachment, update))
        return interaction

    def preview(self, **kw):
        interaction = self.do_import(**kw)
        self.assertEqual(interaction.response.calls[0][0], "defer")
        self.assertTrue(interaction.response.calls[0][2]["ephemeral"])
        args, kwargs = interaction.followup.calls[0]
        self.assertTrue(kwargs["ephemeral"])
        return kwargs

    def press(self, button, user=ALICE):
        interaction = it(user)
        self.arun(button.callback(interaction))
        return interaction.response.calls[0]

    def investigators(self, user=ALICE):
        with self.conn() as c:
            return c.execute("SELECT investigator_id, name, status FROM investigators WHERE owner_user_id=?",
                             (str(user),)).fetchall()

    # --- upload and preview -----------------------------------------------------------------
    def test_preview_shows_parsed_data_and_statuses_and_saves_nothing(self):
        kw = self.preview(points={("R", 34): (0, 30, 5)}, hp=10)
        e = kw["embed"]
        self.assertIn("Import preview — Ada Lovell", e.title)
        fields = {f.name: f.value for f in e.fields}
        self.assertIn("Creation rules not verified", fields["Status"])
        self.assertIn("Complete", fields["Status"])
        self.assertIn("STR 60", fields["Characteristics"])
        self.assertIn("HP 10/12", fields["Derived (recomputed)"])
        self.assertIn("Spot Hidden 60", fields["Skills (1)"])
        self.assertTrue(any("kept" in n for n in fields))
        self.assertEqual(self.investigators(), [])
        self.assertLessEqual(len(e), 6000)

    def test_wrong_file_type_and_oversized_attachments_are_refused_before_reading(self):
        att = FakeAttachment(b"x", filename="ada.xls")
        interaction = self.do_import(attachment=att)
        self.assertIn(".xlsx", interaction.response.calls[0][1][0])
        big = FakeAttachment(b"PK", size=50 * 1024 * 1024)
        interaction = self.do_import(attachment=big)
        self.assertIn("larger than", interaction.response.calls[0][1][0])
        self.assertEqual((att.reads, big.reads), (0, 0))

    def test_unsupported_layout_and_garbage(self):
        interaction = self.do_import(signature=False, name="SECRET")
        text = interaction.followup.calls[0][0][0]
        self.assertIn("does not match a supported character-sheet template", text)
        self.assertNotIn("SECRET", text)
        interaction = self.do_import(attachment=FakeAttachment(b"PK\x03\x04garbage"))
        self.assertIn("not a readable .xlsx", interaction.followup.calls[0][0][0])

    # --- create -------------------------------------------------------------------------------
    def test_create_then_use_for_checks_and_view(self):
        kw = self.preview(points={("R", 34): (0, 30, 5)})
        view = kw["view"]
        self.assertFalse(self.arun(view.interaction_check(it(BOB))))      # not someone else's preview
        kind, _, ekw = self.press(view.create_button)
        self.assertIn("imported into your collection", ekw["content"])
        rows = self.investigators()
        self.assertEqual([(r[1], r[2]) for r in rows], [("Ada Lovell", "ready")])
        inv_id = rows[0][0]
        it_sel = it(ALICE)
        self.arun(self.chars.app_command.get_command("select").callback(self.chars, it_sel, str(inv_id), False))
        check = it(ALICE, channel_id=None)
        self.arun(self.bot.tree.get_command("check").callback(check, "侦查", 0, None))
        self.assertIn("`60`", check.response.calls[0][2]["embed"].description)
        view_it = it(ALICE, channel_id=None)
        self.arun(self.chars.app_command.get_command("view").callback(self.chars, view_it, None, False))
        imported = next(f.value for f in view_it.response.calls[0][2]["embed"].fields if f.name == "📥")
        self.assertIn("Imported from Excel", imported)
        self.assertIn("not verified", imported)

    def test_cancel_discards(self):
        kw = self.preview()
        kind, _, ekw = self.press(kw["view"].cancel_button)
        self.assertIn("Import cancelled", ekw["content"])
        self.assertEqual(self.investigators(), [])

    def test_incomplete_sheet_becomes_a_draft(self):
        kw = self.preview(chars={"STR": 60})
        self.assertIn("Incomplete", next(f.value for f in kw["embed"].fields if f.name == "Status"))
        self.press(kw["view"].create_button)
        self.assertEqual(self.investigators()[0][2], "draft")

    def test_sheet_without_name_cannot_be_saved(self):
        kw = self.preview(name=None)
        self.assertTrue(kw["view"].create_button.disabled)

    # --- duplicates and re-import ------------------------------------------------------------
    def test_reimport_offers_update_with_diff_instead_of_duplicate(self):
        self.press(self.preview(points={("R", 34): (0, 30, 5)})["view"].create_button)
        kw = self.preview(luck=40, points={("R", 34): (0, 40, 5)})
        view = kw["view"]
        self.assertEqual(view.create_button.label, "Create as Ada Lovell (2)")
        self.assertEqual(view.update_buttons[0].label, "Update Ada Lovell…")
        self.assertIn("Already in your collection", [f.name for f in kw["embed"].fields])
        kind, _, dkw = self.press(view.update_buttons[0])
        diff = dkw["embed"].fields[0].value
        self.assertIn("LUCK: 50 → 40", diff)
        self.assertIn("Spot Hidden: 60 → 70", diff)
        kind, _, ukw = self.press(dkw["view"].confirm_button)
        self.assertIn("updated from the sheet (2 change(s))", ukw["content"])
        self.assertEqual(len(self.investigators()), 1)

    def test_stale_diff_is_refused(self):
        self.press(self.preview()["view"].create_button)
        kw = self.preview(luck=40)
        _, _, dkw = self.press(kw["view"].update_buttons[0])
        from cocbot.characters.service import InvestigatorService
        with self.conn() as c:
            InvestigatorService(c).set_resources(ALICE, self.investigators()[0][0], luck=10)
        kind, args, _ = self.press(dkw["view"].confirm_button)
        self.assertEqual(kind, "send_message")
        self.assertIn("changed since this preview", args[0])
        with self.conn() as c:
            self.assertEqual(c.execute("SELECT luck FROM investigators").fetchone()[0], 10)

    def test_update_option_names_a_specific_investigator(self):
        self.press(self.preview()["view"].create_button)
        inv_id = self.investigators()[0][0]
        kw = self.preview(name="Ada Renamed", update=str(inv_id))
        self.assertEqual(kw["view"].update_buttons[0].label, "Update Ada Lovell…")
        _, _, dkw = self.press(kw["view"].update_buttons[0])
        self.assertIn("Ada Lovell → Ada Renamed", dkw["embed"].fields[0].value)

    def test_other_users_investigator_cannot_be_targeted(self):
        self.press(self.preview()["view"].create_button)
        inv_id = self.investigators()[0][0]
        interaction = self.do_import(user=BOB, update=str(inv_id))
        self.assertIn("⚠️", interaction.followup.calls[0][0][0])

    # --- campaign submission and Keeper inspection ----------------------------------------------
    def setup_request(self):
        self.press(self.preview(points={("R", 34): (0, 30, 5)})["view"].create_button)
        self.press(self.preview(name="Hidden Other")["view"].create_button)
        inv_id = self.investigators()[0][0]
        self.arun(self.campaign_cog.app_command.get_command("create").callback(self.campaign_cog, it(KEEPER),
                                                                              "Masks", True))
        join = it(ALICE)
        self.arun(self.campaign_cog.app_command.get_command("join").callback(self.campaign_cog, join,
                                                                            str(inv_id), None))
        self.assertIn("Request sent", join.response.calls[0][1][0])
        return inv_id

    def keeper_panel(self):
        interaction = it(KEEPER)
        self.arun(self.keeper_cog.app_command.get_command("panel").callback(self.keeper_cog, interaction, None))
        return interaction.response.calls[0][2]["view"]

    def test_keeper_inspects_the_imported_applicant_then_approves(self):
        from apps.discord_bot.keeper_views import RequestView, SheetView
        inv_id = self.setup_request()
        roster = self.keeper_panel()
        roster.requests._values = [str(ALICE)]
        _, _, rkw = self.press(roster.requests, KEEPER)
        req = rkw["view"]
        self.assertIsInstance(req, RequestView)
        _, _, skw = self.press(req.sheet_button, KEEPER)
        e = skw["embed"]
        self.assertIn("Ada Lovell — join request", e.title)
        values = "\n".join(f.value for f in e.fields)
        self.assertIn("60", values)                                   # Spot Hidden on the full sheet
        self.assertIn("Imported from Excel", values)
        self.assertIn("not verified", values)
        self.assertNotIn("Hidden Other", e.title + values)
        self.assertIsInstance(skw["view"], SheetView)
        _, _, bkw = self.press(skw["view"].back_button, KEEPER)
        self.assertIsInstance(bkw["view"], RequestView)
        approve = it(KEEPER)
        self.arun(bkw["view"]._approve(approve))
        self.assertIn("Request approved", approve.response.calls[0][2]["content"])
        with self.conn() as c:
            self.assertEqual(c.execute("SELECT owner_user_id FROM investigators WHERE investigator_id=?",
                                       (inv_id,)).fetchone()[0], str(ALICE))
            self.assertEqual(c.execute("SELECT status FROM campaign_investigators").fetchone()[0], "active")

    def test_full_sheet_from_roster_detail(self):
        inv_id = self.setup_request()
        from cocbot.campaigns.service import CampaignService
        with self.conn() as c:
            camp_id = c.execute("SELECT campaign_id FROM campaigns").fetchone()[0]
            CampaignService(c).approve(str(KEEPER), camp_id, ALICE)
            CampaignService(c).adjust(str(KEEPER), camp_id, inv_id, {"hp": 5}, confirmed=True)
        roster = self.keeper_panel()
        roster.pick._values = [str(inv_id)]
        _, _, dkw = self.press(roster.pick, KEEPER)
        _, _, skw = self.press(dkw["view"].sheet_button, KEEPER)
        self.assertIn("campaign values", skw["embed"].title)
        self.assertIn("5", "\n".join(f.value for f in skw["embed"].fields))     # campaign HP shown
        from apps.discord_bot.keeper_views import render_history
        from apps.discord_bot.views import Ctx
        _, _, dkw = self.press(self.preview(luck=40)["view"].update_buttons[0])

        async def history():
            return render_history(Ctx(self.db, KEEPER, GUILD), camp_id, None)[0].description
        self.assertNotIn("sheet from Excel", self.arun(history()))           # nothing before confirming
        self.assertIn("Masks", dkw["embed"].fields[1].value)                 # the diff names the campaign
        self.press(dkw["view"].confirm_button)
        self.assertIn("updated the permanent sheet from Excel", self.arun(history()))
        with self.conn() as c:
            self.assertEqual(c.execute("SELECT hp, luck FROM campaign_investigators").fetchone()[:], (5, 50))

    def test_chinese_preview(self):
        from cocbot.preferences import set_language
        with self.conn() as c:
            set_language(c, ALICE, "zh")
        kw = self.preview(points={("R", 34): (0, 30, 5)})
        self.assertIn("导入预览", kw["embed"].title)
        self.assertIn("侦查 60", "\n".join(f.value for f in kw["embed"].fields))
        self.assertEqual(kw["view"].create_button.label, "创建调查员")


if __name__ == "__main__":
    unittest.main()
