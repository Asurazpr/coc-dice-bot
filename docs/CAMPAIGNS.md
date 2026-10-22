# Campaigns and the Keeper Console (Milestone 3A)

Status: first vertical slice. Combat, Sanity rolls, Luck spending and progression are **not** part of it.

## 1. What exists today (inspected before designing)

* Investigators belong to a Discord user (`investigators.owner_user_id`) and hold permanent data: characteristics, skills, and "personal" current HP/MP/SAN/Luck (`hp_current`… NULL = at maximum).
* The investigator used for `/check` comes from `active_investigators` (scopes `guild:<id>`, then `global`). `ARCHITECTURE.md` already reserved a `campaign:<id>` scope ahead of the guild.
* `/check` resolves its target in `cocbot/characters/check_target.py`. Success levels come from `mechanics.checks.success_level(roll, target, rules)`. `CheckRules` already parameterizes the critical range (`critical_max`) and the low-skill fumble range, but nothing passes a non-default instance yet. The check embed computes its band with the default rules.
* All ownership checks live in `InvestigatorService`; Discord views re-check the clicking user. This slice keeps that pattern: **every campaign operation goes through `CampaignService`, which checks the acting user's role on every call**, including every button callback.

## 2. Data model (migration 013, additive)

```
campaigns                      campaign_id, name, status (active|archived), created_by, created_at
campaign_channels              channel_id PK, campaign_id, guild_id, added_by
                               (a channel belongs to at most one campaign; a server can host many)
campaign_members               (campaign_id, user_id) PK, role keeper|assistant|player,
                               status invited|requested|active|removed, invited_by, updated_at
                               (exactly one active primary keeper per campaign — partial unique index)
campaign_investigators         (campaign_id, investigator_id) PK, user_id, status active|left,
                               hp, mp, san, luck            ← campaign-side current values
                               (one active investigator per player per campaign — partial unique index)
campaign_conditions            id, campaign_id, investigator_id, label, added_by/at, removed_by/at
campaign_events                append-only audit log (see §6); database triggers forbid deletes and edits
campaign_rules                 (campaign_id, version) PK, rules_json, created_by, created_at
                               (the highest version is current; older versions are kept)
```

Campaigns are not bound to one server: they are linked to *channels*, which may be in different servers. The server a campaign was created in has no special rights.

## 3. Source of truth

| Data | Owner | Where it lives | Who can change it |
|---|---|---|---|
| Characteristics, skills, maximum HP/MP/SAN (derived) | player | `investigators`, `investigator_skills` | the player (as before) |
| Personal current HP/MP/SAN/Luck (outside any campaign) | player | `investigators.*_current`, `luck` | the player |
| Current HP/MP/SAN/Luck **in a campaign** | campaign | `campaign_investigators` | Keeper / assistant Keepers |
| Session conditions | campaign | `campaign_conditions` | Keeper / assistant Keepers |
| Campaign rules | campaign | `campaign_rules` | primary Keeper |

* **Joining** copies the investigator's personal current values into the campaign row once. Afterwards the two never sync. A Keeper's adjustment never touches the permanent collection or another campaign the same investigator is in.
* **Maxima are never copied.** They are always derived from the permanent characteristics, and campaign values are capped at the current maximum when read (same rule as M1).
* **One read path:** `CampaignService.context(user, channel)` decides whether a request is "in campaign C". If it is, `campaign_view(inv)` returns the same `Investigator` object with the campaign's current values substituted. `/check`, the player's sheet in that channel, and the Keeper Console all use this view, so they cannot disagree. Outside campaign channels nothing changes.
* In a campaign channel, the player's sheet shows the campaign values, labelled with the campaign name. The editor's **HP·MP·SAN·Luck** button is disabled there (campaign values are Keeper-managed). The player's personal values stay editable from any other channel or DM.
* An investigator that is active in a campaign cannot be archived or deleted until it leaves the campaign.

## 4. Roles and permissions (enforced in `CampaignService`)

| Action | Primary Keeper | Assistant Keeper | Player (member) | Anyone else |
|---|:-:|:-:|:-:|:-:|
| See the Keeper Console, roster, history | ✔ | ✔ | — | — |
| Adjust campaign resources, add/remove conditions, revert an event | ✔ | ✔ | — | — |
| Invite, approve, remove players | ✔ | ✔ | — | — |
| Link/unlink channels, change rules, add/remove assistants | ✔ | — | — | — |
| Join with own investigator (if invited, or by request), leave | — | — | ✔ | request only |
| Read another player's permanent collection | ✘ | ✘ | ✘ | ✘ |

* Sharing a Discord server grants nothing. A Keeper sees an investigator only while it participates in a campaign they administer, and only its campaign view.
* "Not found" and "not authorized" give the same answer, so other campaigns can't be discovered by guessing ids.
* Permissions are checked when an action runs, not when the panel was drawn. Removing an assistant takes effect on their next click, even on a panel that is already open.

## 5. Campaign rules (`campaign_rules`, versioned)

Only settings the engine actually supports are offered:

| Setting | Values | Engine hook |
|---|---|---|
| Critical range | 1 (RAW: 01 only) to 5 | `CheckRules.critical_max`; a roll ≤ N is a Critical (as the engine implements it, independent of the skill value) |
| Fumble rule | `raw` (96–100 when skill < 50, else 100) or `only_100` | `CheckRules.fumble_low_skill_threshold` |
| Roll visibility | `public` (posted in the channel) or `private` (only the roller sees the result; the roll is recorded in the campaign history for the Keepers) | `/check` response mode |

Every change creates a new version with author and time. Checks in a campaign channel by a participating player use the current version, and the result shows the campaign name and rules version. Checks anywhere else use RAW rules, as before. No toggle is shown for pushed rolls, Luck spending, Sanity or combat options; those don't exist yet.

## 6. Audit and corrections

`campaign_events` rows record: campaign, investigator (if any), acting user, kind, field, previous value, new value, reason, time.

* **Kinds:** resource changes, condition add/remove, joins/leaves/approvals/removals, role changes, channel links, rule changes, private rolls and reverts.
* **Append-only:** database triggers reject `DELETE`, and any `UPDATE` other than marking a row as reverted.
* **Revert:** a Keeper can revert a resource or condition event. Reverting creates a *new* event that restores the previous value, and it only works if the value is still what that event set. If someone changed it since, the revert is refused with an explanation instead of silently overwriting the later change. An event can be reverted only once.
* **Confirmation:** decreases, reaching 0, removing a player and reverts all need confirmation. Increases and adding a condition apply directly.

## 7. Discord interface

* `/campaign create`, `/campaign info`, `/campaign join`, `/campaign leave`. Joining in a channel linked to the campaign uses that campaign.
* `/keeper panel`, `/keeper invite`, `/keeper remove`, `/keeper assistant`, `/keeper channel`, `/keeper rules`.
  Join requests are approved or denied from the panel (**Review a join request…**); `/keeper invite` on a user with a pending request also approves it, so no separate `/keeper approve` command is needed.
* The panel is ephemeral and shows:
  * the roster: HP/MP/SAN/Luck and conditions per investigator, plus pending requests;
  * an investigator detail view with **Adjust** (form showing current values, accepts `7`, `+2` or `-3`, optional reason), **Add/Remove condition**, **History** (with Revert), **Refresh** and **Back**.
* All new text uses the existing `/language` preference (English / 简体中文 / bilingual).
* **Stale panels:** a confirmation stores the values the Keeper saw; if another Keeper changed them in the meantime, Confirm saves nothing and says so (`changed_while_confirming`).

### Implementation map

| Concern | Module |
|---|---|
| Schema | `data/sql/013_campaigns.sql` |
| Permissions, state, audit, rules | `cocbot/campaigns/service.py` (`CampaignService`) |
| Rule profile ↔ engine | `cocbot/campaigns/rules.py` (`CampaignRules.to_check_rules()`) |
| Campaign-aware `/check` | `cocbot/campaigns/checks.py` (`plan_check`) |
| Text (no Discord) | `cocbot/ui/keeper_text.py` |
| Commands | `apps/discord_bot/campaign_cog.py` (`/campaign`, `/keeper`) |
| Console views | `apps/discord_bot/keeper_views.py` |

## 8. Out of scope (later milestones)

* **Rules engine:** combat, damage/Major Wound states, Sanity rolls and insanity, Luck spending, pushed rolls, improvement checks.
* **Delivery:** delivering private rolls to Keepers by DM.
* **Sharing:** sharing homebrew occupations to a campaign.
* **Interface:** a web dashboard.
