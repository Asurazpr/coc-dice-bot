"""`/campaign` (players and Keepers) and `/keeper` (Keeper Console) commands.

Command handlers resolve which campaign is meant and hand everything else to
``CampaignService`` with the invoking user's ID. All replies are ephemeral
except none: campaign administration is private to whoever runs it.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import discord
from discord import app_commands
from discord.ext import commands

from apps.discord_bot.keeper_views import ask_confirmation, render_roster
from apps.discord_bot.views import Ctx, reply_error
from cocbot.campaigns.service import KEEPER_ROLES, ROLE_KEEPER, Campaign, CampaignService
from cocbot.characters.service import CharacterError, InvestigatorService, NotFound
from cocbot.db.connection import get_conn
from cocbot.i18n import Msg, render
from cocbot.ui.keeper_text import mention, role_name, rules_summary

CAMPAIGN_DESC = "Campaign (defaults to this channel's campaign)"


def _ctx(db_path: str, interaction: discord.Interaction) -> Ctx:
    from cocbot.preferences import get_language
    with get_conn(db_path) as conn:
        lang = get_language(conn, interaction.user.id, getattr(interaction, "locale", None))
    return Ctx(db_path=db_path, owner_id=interaction.user.id, guild_id=interaction.guild_id, lang=lang,
               channel_id=getattr(interaction, "channel_id", None))


def resolve_campaign(svc: CampaignService, user_id: object, channel_id: Optional[object], ref: Optional[str],
                     roles: Sequence[str]) -> Campaign:
    """Explicit campaign, else this channel's campaign, else the only one the user has that role in."""
    if ref:
        if not str(ref).strip().isdigit():
            raise NotFound(Msg("campaign_not_found"))
        camp, _ = svc.get(user_id, int(ref))           # NotFound for non-members
        return camp
    here = svc.campaign_for_channel(channel_id)
    if here is not None and svc.role_of(here.id, user_id) in roles:
        return here
    mine = svc.list_for(user_id, roles)
    if len(mine) == 1:
        return mine[0][0]
    if not mine:
        if here is not None:
            return here         # the service will answer with the right refusal
        raise NotFound(Msg("kp_no_campaigns"))
    raise NotFound(Msg("kp_choose_campaign"))


async def _send(interaction: discord.Interaction, text: str, **kw) -> None:
    await interaction.response.send_message(text, ephemeral=True, **kw)


class _Base(commands.GroupCog):
    def __init__(self, bot: commands.Bot, db_path: str) -> None:
        self.bot = bot
        self.db_path = db_path
        super().__init__()

    def ctx(self, interaction: discord.Interaction) -> Ctx:
        return _ctx(self.db_path, interaction)

    async def _campaign_choices(self, interaction: discord.Interaction, current: str, roles: Sequence[str]):
        with get_conn(self.db_path) as conn:
            mine = CampaignService(conn).list_for(interaction.user.id, roles)
        cur = current.lower().strip()
        return [app_commands.Choice(name=f"{c.name} (#{c.id})"[:100], value=str(c.id))
                for c, _ in mine if not cur or cur in c.name.lower() or cur == str(c.id)][:25]

    async def keeper_campaigns(self, interaction: discord.Interaction, current: str):
        return await self._campaign_choices(interaction, current, KEEPER_ROLES)

    async def primary_campaigns(self, interaction: discord.Interaction, current: str):
        return await self._campaign_choices(interaction, current, (ROLE_KEEPER,))

    async def any_campaigns(self, interaction: discord.Interaction, current: str):
        return await self._campaign_choices(interaction, current, (*KEEPER_ROLES, "player"))


class CampaignCog(_Base, group_name="campaign", group_description="Campaigns: create, join, leave"):
    @app_commands.command(name="create", description="Create a campaign; you become its Keeper")
    @app_commands.describe(name="Campaign name", link_channel="Link this channel to the campaign (default: yes)")
    async def create(self, interaction: discord.Interaction, name: str, link_channel: bool = True) -> None:
        ctx = self.ctx(interaction)
        try:
            with get_conn(self.db_path) as conn:
                camp = CampaignService(conn).create(
                    interaction.user.id, name,
                    channel_id=ctx.channel_id if link_channel and interaction.guild_id else None,
                    guild_id=interaction.guild_id)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        linked = render("camp_created_channel", ctx.lang) if link_channel and interaction.guild_id else ""
        await _send(interaction, render("camp_created", ctx.lang, name=camp.name, id=camp.id, channel=linked))

    @app_commands.command(name="info", description="Show this channel's campaign, its Keeper and its rules")
    async def info(self, interaction: discord.Interaction) -> None:
        ctx = self.ctx(interaction)
        with get_conn(self.db_path) as conn:
            svc = CampaignService(conn)
            camp = svc.campaign_for_channel(ctx.channel_id)
            if camp is None:
                await _send(interaction, render("camp_info_none", ctx.lang))
                return
            version, rules = svc.channel_rules(camp.id)
            keeper = conn.execute("SELECT user_id FROM campaign_members WHERE campaign_id=? AND role='keeper' "
                                  "AND status='active'", (camp.id,)).fetchone()
            role = svc.role_of(camp.id, interaction.user.id)
            cctx = svc.context(interaction.user.id, ctx.channel_id)
        body = render("camp_info_body", ctx.lang, keeper=mention(keeper[0]) if keeper else "—",
                      role=role_name(role, ctx.lang) if role else "—", version=version,
                      rules=rules_summary(rules, ctx.lang))
        if cctx is not None:
            body += "\n" + render("camp_info_playing", ctx.lang, name=cctx.investigator.name)
        embed = discord.Embed(title=render("camp_info_title", ctx.lang, name=camp.name)[:256], description=body,
                              color=discord.Color.dark_purple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="join", description="Join a campaign with one of your ready investigators")
    @app_commands.describe(investigator="Your investigator (name or #id)", campaign=CAMPAIGN_DESC)
    async def join(self, interaction: discord.Interaction, investigator: str, campaign: Optional[str] = None) -> None:
        ctx = self.ctx(interaction)
        try:
            with get_conn(self.db_path) as conn:
                svc = CampaignService(conn)
                if campaign:
                    if not campaign.strip().isdigit():
                        raise NotFound(Msg("campaign_not_found"))
                    cid = int(campaign)
                else:
                    here = svc.campaign_for_channel(ctx.channel_id)
                    if here is None:
                        raise NotFound(Msg("camp_info_none"))
                    cid = here.id
                inv = InvestigatorService(conn).find(interaction.user.id, investigator)
                status = svc.join(interaction.user.id, cid, inv.id)
                camp = svc._campaign(cid)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        code = "camp_joined" if status == "active" else "camp_requested"
        await _send(interaction, render(code, ctx.lang, name=inv.name, campaign=camp.name))

    @join.autocomplete("investigator")
    async def _join_investigator(self, interaction: discord.Interaction, current: str):
        with get_conn(self.db_path) as conn:
            invs = InvestigatorService(conn).list(interaction.user.id)
        cur = current.lower().strip()
        return [app_commands.Choice(name=f"{i.name} (#{i.id})"[:100], value=str(i.id))
                for i in invs if i.status == "ready" and (not cur or cur in i.name.lower())][:25]

    @app_commands.command(name="leave", description="Leave a campaign (its campaign values are kept)")
    @app_commands.describe(campaign=CAMPAIGN_DESC)
    async def leave(self, interaction: discord.Interaction, campaign: Optional[str] = None) -> None:
        ctx = self.ctx(interaction)
        try:
            with get_conn(self.db_path) as conn:
                svc = CampaignService(conn)
                camp = resolve_campaign(svc, interaction.user.id, ctx.channel_id, campaign,
                                        (*KEEPER_ROLES, "player"))
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return

        def action() -> str:
            with get_conn(self.db_path) as conn:
                CampaignService(conn).leave(interaction.user.id, camp.id)
            return render("camp_left", ctx.lang, campaign=camp.name)
        await _confirm_command(interaction, ctx, render("camp_left", ctx.lang, campaign=camp.name).split(".")[0] + "?",
                               action)

    leave.autocomplete("campaign")(_Base.any_campaigns)


async def _confirm_command(interaction: discord.Interaction, ctx: Ctx, prompt: str, action) -> None:
    """Slash-command confirmation: post a private prompt whose Confirm runs ``action``."""
    from apps.discord_bot.keeper_views import ConfirmView

    async def confirm(it: discord.Interaction) -> None:
        try:
            text = action()
        except CharacterError as e:
            await reply_error(it, e, ctx)
            return
        await it.response.edit_message(content=text, embed=None, view=None)

    async def cancel(it: discord.Interaction) -> None:
        await it.response.edit_message(content=render("kp_cancelled", ctx.lang), embed=None, view=None)

    await interaction.response.send_message(prompt, view=ConfirmView(ctx, confirm, cancel), ephemeral=True)


FUMBLE_CHOICES = [app_commands.Choice(name="RAW: 96-100 below 50, else 100", value="raw"),
                  app_commands.Choice(name="Only 100", value="only_100")]
VISIBILITY_CHOICES = [app_commands.Choice(name="Public", value="public"),
                      app_commands.Choice(name="Private (roller + Keeper log)", value="private")]


class KeeperCog(_Base, group_name="keeper", group_description="Keeper Console for campaigns you run"):
    def _resolve(self, interaction: discord.Interaction, campaign: Optional[str], roles=KEEPER_ROLES) -> Campaign:
        with get_conn(self.db_path) as conn:
            return resolve_campaign(CampaignService(conn), interaction.user.id,
                                    getattr(interaction, "channel_id", None), campaign, roles)

    @app_commands.command(name="panel", description="Open the private Keeper Console")
    @app_commands.describe(campaign=CAMPAIGN_DESC)
    async def panel(self, interaction: discord.Interaction, campaign: Optional[str] = None) -> None:
        ctx = self.ctx(interaction)
        try:
            camp = self._resolve(interaction, campaign)
            embed, view = render_roster(ctx, camp.id)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="invite", description="Invite a player (or approve their pending request)")
    @app_commands.describe(user="Player to invite", campaign=CAMPAIGN_DESC)
    async def invite(self, interaction: discord.Interaction, user: discord.User,
                     campaign: Optional[str] = None) -> None:
        ctx = self.ctx(interaction)
        try:
            camp = self._resolve(interaction, campaign)
            with get_conn(self.db_path) as conn:
                status = CampaignService(conn).invite(interaction.user.id, camp.id, user.id)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        code = "kp_invited" if status == "invited" else "kp_invite_approved"
        await _send(interaction, render(code, ctx.lang, user=mention(user.id), campaign=camp.name))

    @app_commands.command(name="remove", description="Remove a player from a campaign")
    @app_commands.describe(user="Player to remove", reason="Reason (kept in the campaign history)",
                           campaign=CAMPAIGN_DESC)
    async def remove(self, interaction: discord.Interaction, user: discord.User, reason: Optional[str] = None,
                     campaign: Optional[str] = None) -> None:
        ctx = self.ctx(interaction)
        try:
            camp = self._resolve(interaction, campaign)
            with get_conn(self.db_path) as conn:
                CampaignService(conn).members(interaction.user.id, camp.id)   # Keeper roles, before asking
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return

        def action() -> str:
            with get_conn(self.db_path) as conn:
                CampaignService(conn).remove_player(interaction.user.id, camp.id, user.id, reason=reason)
            return render("kp_removed", ctx.lang, user=mention(user.id), campaign=camp.name)
        await _confirm_command(interaction, ctx, render("kp_confirm_remove_player", ctx.lang, user=mention(user.id),
                                                        campaign=camp.name), action)

    @app_commands.command(name="assistant", description="Grant or revoke assistant Keeper (primary Keeper only)")
    @app_commands.describe(user="User", enabled="True to grant, False to revoke", campaign=CAMPAIGN_DESC)
    async def assistant(self, interaction: discord.Interaction, user: discord.User, enabled: bool,
                        campaign: Optional[str] = None) -> None:
        ctx = self.ctx(interaction)
        try:
            camp = self._resolve(interaction, campaign, (ROLE_KEEPER,))
            with get_conn(self.db_path) as conn:
                CampaignService(conn).set_assistant(interaction.user.id, camp.id, user.id, enabled)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        code = "kp_assistant_added" if enabled else "kp_assistant_removed"
        await _send(interaction, render(code, ctx.lang, user=mention(user.id), campaign=camp.name))

    @app_commands.command(name="channel", description="Link or unlink this channel (primary Keeper only)")
    @app_commands.describe(link="True to link this channel, False to unlink it", campaign=CAMPAIGN_DESC)
    async def channel(self, interaction: discord.Interaction, link: bool, campaign: Optional[str] = None) -> None:
        ctx = self.ctx(interaction)
        try:
            camp = self._resolve(interaction, campaign, (ROLE_KEEPER,))
            with get_conn(self.db_path) as conn:
                svc = CampaignService(conn)
                if link:
                    svc.link_channel(interaction.user.id, camp.id, ctx.channel_id, guild_id=interaction.guild_id)
                else:
                    svc.unlink_channel(interaction.user.id, camp.id, ctx.channel_id)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        code = "kp_channel_linked" if link else "kp_channel_unlinked"
        await _send(interaction, render(code, ctx.lang, campaign=camp.name))

    @app_commands.command(name="rules", description="Show or change campaign rules (changes: primary Keeper only)")
    @app_commands.describe(critical_max="Rolls at or under this are criticals (1 = RAW, up to 5)",
                           fumble="Fumble rule", roll_visibility="Who sees /check results in campaign channels",
                           luck_spending="Optional rule: players may spend campaign Luck to lower a roll",
                           campaign=CAMPAIGN_DESC)
    @app_commands.choices(fumble=FUMBLE_CHOICES, roll_visibility=VISIBILITY_CHOICES)
    async def rules(self, interaction: discord.Interaction,
                    critical_max: Optional[app_commands.Range[int, 1, 5]] = None,
                    fumble: Optional[app_commands.Choice[str]] = None,
                    roll_visibility: Optional[app_commands.Choice[str]] = None,
                    campaign: Optional[str] = None,
                    luck_spending: Optional[bool] = None) -> None:
        ctx = self.ctx(interaction)
        changing = (critical_max is not None or fumble is not None or roll_visibility is not None
                    or luck_spending is not None)
        try:
            camp = self._resolve(interaction, campaign, (ROLE_KEEPER,) if changing else KEEPER_ROLES)
            with get_conn(self.db_path) as conn:
                svc = CampaignService(conn)
                if changing:
                    version, current = svc.set_rules(
                        interaction.user.id, camp.id, critical_max=critical_max,
                        fumble=fumble.value if fumble else None,
                        roll_visibility=roll_visibility.value if roll_visibility else None,
                        luck_spending=luck_spending)
                else:
                    version, current = svc.rules(interaction.user.id, camp.id)
        except CharacterError as e:
            await reply_error(interaction, e, ctx)
            return
        await _send(interaction, render("kp_rules_now", ctx.lang, campaign=camp.name, version=version,
                                        rules=rules_summary(current, ctx.lang)))

    for _cmd in (panel, invite, remove):
        _cmd.autocomplete("campaign")(_Base.keeper_campaigns)
    for _cmd in (assistant, channel):
        _cmd.autocomplete("campaign")(_Base.primary_campaigns)
    rules.autocomplete("campaign")(_Base.keeper_campaigns)
    del _cmd


__all__: List[str] = ["CampaignCog", "KeeperCog", "resolve_campaign", "ask_confirmation"]
