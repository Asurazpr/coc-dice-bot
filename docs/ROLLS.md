# Persistent rolls, required difficulty and Luck spending (M3B1)

Status: draft for review on `feature/roll-interactions` (based on `feature/keeper-console`).

## 1. What exists today

* `/check` (`apps/discord_bot/main.py`) calls `plan_check()` (`cocbot/campaigns/checks.py`), which picks the target, investigator view (personal or campaign), rule profile and visibility. It then rolls with `d100_check_details()`, builds an embed and posts it (ephemeral when the campaign's rolls are private).
* Nothing about the roll is stored, except a one-line `campaign_events` row of kind `roll` for private campaign rolls (`record_private_roll`). That row is free text and cannot drive later decisions.
* Campaign Luck lives in `campaign_investigators.luck` and is changed only through `CampaignService` (audited, Keeper-revertible). Personal Luck lives in `investigators.luck`.
* `success_level()` already takes a `CheckRules` (critical range, fumble rule).

Gaps: no roll identity, no record of dice/target/rules, no required difficulty, no way to attach later actions (Luck, pushing) to a roll.

## 2. Rules basis and how far it is verified

Luck spending is an **optional rule** in the 7th-edition Keeper Rulebook (p. 99, "Spending Luck"). I could not read the rulebook or Chaosium's free Quick-Start PDF from this environment (the PDF link fails here). What I could verify:

| Rule as implemented | Source | Status |
|---|---|---|
| Luck may not be spent on Luck rolls, damage rolls or Sanity rolls | Keeper Rulebook p. 99, quoted on Chaosium's official forum (BRP Central, "A question re spending Luck") | **verified quote** |
| A player may only spend Luck on their own dice rolls | same | **verified quote** |
| 1 Luck point lowers the roll by 1 | Roll20 compendium "Using Luck" (Pulp-flavoured text; core and Pulp agree here), secondary summaries | corroborated, not primary |
| A pushed roll cannot be changed with Luck | Roll20 "Using Luck"; secondary summaries citing KRB p. 99 | corroborated, not primary |
| Criticals, fumbles (and firearm malfunctions) cannot be altered by Luck | secondary summaries citing KRB p. 99; Roll20 says a natural 100 cannot be avoided on non-combat skills | corroborated, not primary |
| Luck lowered to 01 (or into a house-rule critical range) is at most an Extreme success | Roll20 "Using Luck" (Pulp text) | **Pulp source only** — applied as the conservative reading |
| No skill improvement check for a success bought with Luck | Roll20 "Using Luck"; secondary summaries citing KRB p. 99 | corroborated, not primary |

**Needs checking against a licensed copy of the Keeper Rulebook before release:** every row not marked "verified quote". The code keeps each restriction in one function (`cocbot/rolls/rules.py: luck_eligibility`) so a correction is a one-line change with a test.

Out of scope here: Luck to halve Sanity loss, Luck in combat (fumble/malfunction ignore), Luck recovery, Luck rolls themselves.

## 3. Required difficulty

`/check … difficulty:Regular|Hard|Extreme` (default Regular, so existing commands are unchanged).

* The rolled level is computed as today (critical/extreme/hard/regular/fail/fumble) under the roll's rule profile.
* "Met" means the level is at least the required one. A Critical meets every difficulty; a Fumble meets none.
* The embed shows both: "Hard success — required **Extreme**: not met".

## 4. Data model (migration 014, additive)

`rolls` — one row per check, never rerolled:

| column | meaning |
|---|---|
| `roll_id` | identity shown in the embed footer (`Roll #12`) |
| `user_id`, `investigator_id` (nullable, `SET NULL` on delete) | who rolled, with which investigator |
| `campaign_id` (nullable), `channel_id`, `guild_id` | context |
| `luck_scope` | `campaign` (participant: campaign Luck) / `personal` / `none` |
| `subject_kind`, `subject_key`, `subject_label` | skill / characteristic / number |
| `target`, `base_value`, `difficulty` | original target and required difficulty |
| `bonus_penalty`, `dice_json` | bonus/penalty count, tens candidates, units die, all candidate results |
| `natural`, `natural_level` | the chosen d100 result and its level — **immutable** |
| `rules_json`, `rules_version` | the exact rule profile used (campaign version, or RAW outside campaigns) |
| `visibility` | `public` / `private` |
| `attempt`, `parent_roll_id` | `original` now; `pushed` later, pointing at the failed original |
| `luck_spent`, `effective`, `effective_level` | the result after Luck (`effective = natural − luck_spent`) |
| `state_version` | bumped on every state change (optimistic concurrency) |
| `created_at`, `updated_at` | timestamps |

Triggers make `natural`, `natural_level`, `target`, `difficulty`, dice, subject, rules, visibility, context, `attempt`, `parent_roll_id` and `created_at` immutable, forbid deleting rows, and require a `pushed` row to reference an `original` one.

`roll_events` — append-only state changes of a roll (now: `luck_spent`), with actor, before/after effective result and Luck, and `request_id UNIQUE` (the Discord interaction ID), so a replayed interaction cannot apply twice.

## 5. Luck spending flow

1. After `/check`, if the roll is eligible and the required difficulty was missed, the result carries a **🍀 Spend Luck…** button.
2. Clicking it (owner only) shows a private confirmation with each reachable level: "Spend 17 Luck: 62 → 45, Hard success · Luck 50 → 33". Only levels the current Luck can pay for are offered.
3. **Confirm** runs one `BEGIN IMMEDIATE` transaction that:
   * re-checks eligibility against the stored roll and the *current* state (ownership, investigator still active/participating, campaign rule still on, latest roll of that investigator, within 30 minutes);
   * updates the roll only `WHERE state_version = <seen> AND luck_spent = 0`;
   * deducts Luck only `WHERE luck = <value just read>`;
   * appends a `roll_events` row (unique `request_id`) and, in a campaign, a `campaign_events` row of kind `luck_spend` (not Keeper-revertible, since the roll depends on it).

   Any mismatch rolls back everything: no Luck lost, roll unchanged.
4. The original message is updated to show `62 → 45 (🍀 −17)` and the new level; the natural roll stays visible.

Eligibility (`luck_eligibility`): investigator-linked skill or characteristic check; not Luck, SAN or pushed; natural result not a fumble and not already meeting the requirement; Luck spending allowed by the rule profile; one spend per roll; enough Luck for at least the cheapest level.

**Where is Luck spending allowed?** Campaign rule `luck_spending` (new, default **off**, primary Keeper sets it with `/keeper rules luck_spending:True`). Outside campaigns there is no Keeper, so it is on and spends the player's personal Luck — the player's own sheet, their own decision. In a campaign channel only participants spend Luck (their campaign Luck); anyone else rolling there (a Keeper, a non-member) gets no Luck option, so a campaign table never touches someone's personal sheet.

### Implementation map

| Concern | Module |
|---|---|
| Schema | `data/sql/014_rolls.sql` |
| Difficulty, eligibility, costs (pure) | `cocbot/rolls/rules.py` |
| Records, reads, atomic spend | `cocbot/rolls/service.py` (`RollService`) |
| Campaign Luck deduction + audit | `CampaignService.spend_luck_in_tx` |
| Text (no Discord) | `cocbot/ui/roll_text.py` |
| `/check`, `/rolls` | `apps/discord_bot/main.py` |
| Luck button and confirmation, `/rolls` embeds | `apps/discord_bot/roll_views.py` |

## 6. Privacy and history

* `/rolls` (ephemeral) lists the caller's own recent rolls; `/rolls roll:<id>` shows one with dice and state changes. Nobody can read another user's rolls through it.
* Private rolls: the result, the Luck prompt and the updated result are all ephemeral to the roller. Keepers of that campaign see them in the campaign history (as designed in M3A) — the `roll` campaign event now references the roll ID instead of free text.
* Public rolls: the Luck confirmation is private; only the updated public result is visible to others.

## 7. Single source of truth

* Campaign Luck is deducted inside `CampaignService.spend_luck_in_tx()` — the same table and event log as the Keeper Console, so the panel, sheet and `/check` agree immediately. The roll stores the Luck before/after only as history.
* A Keeper revert of a later Luck adjustment works as before; `luck_spend` events themselves are not revertible (Keepers correct Luck with **Adjust**, which is logged).

## 8. Pushed rolls (prepared, not implemented)

`attempt='pushed'` + `parent_roll_id` already exist with triggers: a pushed attempt must reference an original roll. A pushed attempt will be its own row with its own dice; Luck spending refuses it. Keeper approval and consequences are not part of this PR.

## 9. Out of scope

Pushed-roll workflow, Sanity rolls/loss, combat, NPCs, opposed/combined rolls, improvement marks (the data needed — `luck_spent` — is recorded).
