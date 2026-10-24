"""Milestone 3B1 Discord layer: /check difficulty, the Spend Luck flow and /rolls (mocked interactions)."""
import asyncio
import itertools
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from tests.helpers import SeqRandom
from tests.test_discord_layer import HAVE_DISCORD, FakeFollowup, FakeResponse

KEEPER, ALICE, BOB = 300000000000000001, 111111111111111111, 222222222222222222
GUILD, TABLE, LOUNGE = 900000000000000001, 700000000000000001, 700000000000000099
FULL = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75, "LUCK": 50}
_ids = itertools.count(1)


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kw):
        self.edits.append(kw)


def it(user_id, channel_id=LOUNGE, message=None, interaction_id=None):
    return SimpleNamespace(id=interaction_id or next(_ids), user=SimpleNamespace(id=user_id, display_name="User"),
                           guild_id=GUILD, channel_id=channel_id, response=FakeResponse(), followup=FakeFollowup(),
                           locale=None, message=message)


def d100(*values):
    seq = []
    for v in values:
        v = 0 if v == 100 else v
        seq += [v % 10, v // 10]
    from cocbot.mechanics import dice
    dice.set_rng(SeqRandom(seq))


@unittest.skipUnless(HAVE_DISCORD, "discord.py not installed")
class RollDiscordTests(unittest.TestCase):
    def setUp(self):
        from apps.discord_bot.campaign_cog import CampaignCog, KeeperCog
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
        self.campaign_cog = CampaignCog(self.bot, self.db)
        self.keeper_cog = KeeperCog(self.bot, self.db)
        for cog in (self.campaign_cog, self.keeper_cog):
            self.arun(self.bot.add_cog(cog))
        register_commands(self.bot)

    def tearDown(self):
        from cocbot.mechanics import dice
        dice.set_rng(random.Random())
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

    def luck(self):
        with self.conn() as c:
            return c.execute("SELECT luck FROM investigators WHERE investigator_id=?", (self.ada_id,)).fetchone()[0]

    def check(self, uid, text, natural, channel=LOUNGE, difficulty=None):
        d100(natural)
        interaction = it(uid, channel)
        choice = SimpleNamespace(value=difficulty) if difficulty else None
        self.arun(self.bot.tree.get_command("check").callback(interaction, text, 0, choice))
        return interaction.response.calls[0][2]

    def rolls_cmd(self, uid, roll=None):
        interaction = it(uid)
        self.arun(self.bot.tree.get_command("rolls").callback(interaction, roll))
        return interaction.response.calls[0]

    # --- tests -----------------------------------------------------------------------------
    def test_difficulty_display_and_roll_id(self):
        kw = self.check(ALICE, "STR", 25, difficulty="extreme")
        desc = kw["embed"].description
        self.assertIn("Required: **Extreme**", desc)
        self.assertIn("❌ not met", desc)
        self.assertIn("HARD SUCCESS", kw["embed"].title)
        self.assertIn("Roll #1", kw["embed"].footer.text)
        self.assertIn("view", kw)                       # 13 Luck lowers 25 to 12: Extreme reachable

    def test_regular_checks_look_as_before(self):
        kw = self.check(ALICE, "STR", 10)
        self.assertNotIn("Required", kw["embed"].description)
        self.assertNotIn("view", kw)                    # Extreme success: nothing left to buy
        self.assertNotIn("ephemeral", kw)

    def test_spend_luck_on_public_roll(self):
        from apps.discord_bot.roll_views import LuckButtonView, LuckConfirmView
        kw = self.check(ALICE, "STR", 62)
        view = kw["view"]
        self.assertIsInstance(view, LuckButtonView)
        public = FakeMessage()

        # someone else presses the button
        other = it(BOB, message=public)
        self.arun(view.luck_button.callback(other))
        self.assertIn("Only the player who rolled", other.response.calls[0][1][0])

        # the roller opens a private prompt
        click = it(ALICE, message=public)
        self.arun(view.luck_button.callback(click))
        kind, _, pkw = click.response.calls[0]
        self.assertEqual(kind, "send_message")
        self.assertTrue(pkw["ephemeral"])
        confirm = pkw["view"]
        self.assertIsInstance(confirm, LuckConfirmView)
        self.assertIn("Your Luck: 50", pkw["embed"].description)
        self.assertEqual([b.label for b in confirm.option_buttons],
                         ["Regular: −2 Luck", "Hard: −32 Luck", "Extreme: −50 Luck"])
        self.assertFalse(self.arun(confirm.interaction_check(it(BOB))))

        press = it(ALICE)
        self.arun(confirm.option_buttons[0].callback(press))
        self.assertIn("Spent 2 Luck", press.response.calls[0][2]["content"])
        self.assertEqual(self.luck(), 48)
        edited = public.edits[0]["embed"]
        self.assertIn("62 → **60**", edited.description)
        self.assertIn("the original roll is kept", edited.description)
        self.assertIn("SUCCESS", edited.title)
        self.assertIsNone(public.edits[0]["view"])

        # replaying the same interaction charges nothing more; a new press is refused
        replay = it(ALICE, interaction_id=press.id)
        self.arun(confirm.option_buttons[0].callback(replay))
        self.assertIn("Spent 2 Luck", replay.response.calls[0][2]["content"])
        again = it(ALICE)
        self.arun(confirm.option_buttons[1].callback(again))
        self.assertIn("⚠️", again.response.calls[0][1][0])
        self.assertEqual(self.luck(), 48)

    def test_stale_button_after_a_newer_roll(self):
        kw = self.check(ALICE, "STR", 62)
        self.check(ALICE, "DEX", 90)
        click = it(ALICE, message=FakeMessage())
        self.arun(kw["view"].luck_button.callback(click))
        self.assertIn("latest roll", click.response.calls[0][1][0])

    def test_no_button_for_luck_san_numeric_or_fumble(self):
        for text, natural in (("luck", 80), ("SAN", 80), ("45", 80), ("STR", 100)):
            with self.subTest(text=text):
                self.assertNotIn("view", self.check(ALICE, text, natural))

    def test_private_campaign_roll_and_cancel(self):
        from apps.discord_bot.roll_views import LuckButtonView
        from cocbot.campaigns.service import CampaignService
        with self.conn() as c:
            svc = CampaignService(c)
            camp = svc.create(KEEPER, "Masks", channel_id=TABLE, guild_id=GUILD)
            svc.invite(KEEPER, camp.id, ALICE)
            svc.join(ALICE, camp.id, self.ada_id)
        interaction = it(KEEPER, TABLE)
        cmd = self.keeper_cog.app_command.get_command("rules")
        self.arun(cmd.callback(self.keeper_cog, interaction, None, None, SimpleNamespace(value="private"), None,
                               True))
        self.assertIn("Luck spending on", interaction.response.calls[0][1][0])

        kw = self.check(ALICE, "STR", 62, channel=TABLE)
        self.assertTrue(kw["ephemeral"])
        self.assertIn("Masks · rules v2", kw["embed"].footer.text)
        view = kw["view"]
        click = it(ALICE, TABLE)
        self.arun(view.luck_button.callback(click))
        kind, _, pkw = click.response.calls[0]
        self.assertEqual(kind, "edit_message")                  # the private message itself becomes the prompt
        self.assertIn("campaign Luck in **Masks**", pkw["embed"].description)
        cancel = it(ALICE, TABLE)
        self.arun(pkw["view"].cancel_button.callback(cancel))
        ckw = cancel.response.calls[0][2]
        self.assertIsInstance(ckw["view"], LuckButtonView)
        self.assertIn("Cancelled", ckw["content"])

        click = it(ALICE, TABLE)
        self.arun(ckw["view"].luck_button.callback(click))
        press = it(ALICE, TABLE)
        self.arun(click.response.calls[0][2]["view"].option_buttons[0].callback(press))
        kind, _, done = press.response.calls[0]
        self.assertEqual(kind, "edit_message")
        self.assertIn("62 → **60**", done["embed"].description)
        with self.conn() as c:
            svc = CampaignService(c)
            self.assertEqual(svc.participant_luck(camp.id, self.ada_id, ALICE), 48)
            kinds = [e.kind for e in svc.history(KEEPER, camp.id)]
        self.assertEqual(self.luck(), 50)                      # personal Luck untouched
        self.assertIn("luck_spend", kinds)
        self.assertEqual(kinds.count("roll"), 1)

    def test_rolls_detail_shows_improvement_check(self):
        from cocbot.characters.service import InvestigatorService
        with self.conn() as c:
            InvestigatorService(c).set_skill(ALICE, self.ada_id, "Spot Hidden", 60)
        self.check(ALICE, "Spot Hidden", 40)
        _, _, kw = self.rolls_cmd(ALICE, 1)
        imp = next(f.value for f in kw["embed"].fields if f.name == "📈")
        self.assertIn("Improvement check: earned", imp)

    def test_rolls_command_is_private_and_owner_only(self):
        self.check(ALICE, "STR", 62, difficulty="hard")
        kind, _, kw = self.rolls_cmd(ALICE)
        self.assertTrue(kw["ephemeral"])
        self.assertIn("#1", kw["embed"].description)
        self.assertIn("(Hard)", kw["embed"].description)
        kind, _, kw = self.rolls_cmd(ALICE, 1)
        names = [f.name for f in kw["embed"].fields]
        self.assertIn("Dice", names)
        self.assertIn("can be spent", kw["embed"].fields[-1].value)
        self.assertNotIn("📈", names)                          # STR is a characteristic: no improvement checks
        kind, args, kw = self.rolls_cmd(BOB, 1)
        self.assertIn("No such roll", args[0])
        kind, _, kw = self.rolls_cmd(BOB)
        self.assertIn("no recorded rolls", kw["embed"].description)


if __name__ == "__main__":
    unittest.main()
