# Persistent rolls, required difficulty and Luck spending (M3B1)

Status: draft for review on `feature/roll-interactions` (based on `feature/keeper-console`).

## 1. What exists today

* `/check` (`apps/discord_bot/main.py`) calls `plan_check()` (`cocbot/campaigns/checks.py`), which picks the target, investigator view (personal or campaign), rule profile and visibility. It then rolls with `d100_check_details()`, builds an embed and posts it (ephemeral when the campaign's rolls are private).
* Nothing about the roll is stored, except a one-line `campaign_events` row of kind `roll` for private campaign rolls (`record_private_roll`). That row is free text and cannot drive later decisions.
* Campaign Luck lives in `campaign_investigators.luck` and is changed only through `CampaignService` (audited, Keeper-revertible). Personal Luck lives in `investigators.luck`.
* `success_level()` already takes a `CheckRules` (critical range, fumble rule).

Gaps: no roll identity, no record of dice/target/rules, no required difficulty, no way to attach later actions (Luck, pushing) to a roll.

## 2. Rules basis and how far it is verified

Luck spending is an **optional rule** in the 7th-edition Keeper Rulebook (p. 99, "Spending Luck"). The rows below separate what a Chaosium publication confirms from what is still our interpretation. I have not read the Keeper Rulebook itself.

### Publisher-confirmed

| Rule as implemented | Source |
|---|---|
| Luck may not be spent on Luck rolls, damage rolls or Sanity rolls | Keeper Rulebook p. 99, quoted on Chaosium's official forum (BRP Central, "A question re spending Luck") |
| A player may only spend Luck on their own dice rolls | same |
| Luck can upgrade an existing success: Regular → Hard, Hard → Extreme | Chaosium, *The Lightless Beacon*, p. 7 (identified by the project owner; I could not open the scenario from this environment) |
| A natural success that Luck upgrades still earns its skill improvement check | Chaosium staff ruling (Nick Brooke, Community Ambassador, Chaosium Inc.) on BRP Central, "Pass skill, but use luck to improve… check box or not?": "They'd have got the check for a normal success. So yes." |

### Unresolved rulebook interpretations (check against a licensed Keeper Rulebook before release)

| Rule as implemented | Basis | Why it is still open |
|---|---|---|
| 1 Luck point lowers the roll by 1 | Roll20 "Using Luck" (Pulp text), secondary summaries citing KRB p. 99 | no primary core-rules text read |
| A success can be upgraded by more than one step (Regular → Extreme) in one spend | follows from the 1:1 cost; *The Lightless Beacon* describes one-step upgrades | multi-step not explicitly confirmed |
| A failure can be bought up to any level at or above the required one | same 1:1 reasoning | not explicitly confirmed |
| A pushed roll cannot be changed with Luck | Roll20 "Using Luck"; secondary summaries | no primary core text read |
| Criticals and fumbles cannot be altered by Luck (natural fumbles, including 96–99 below skill 50) | secondary summaries; Roll20 says a natural 100 cannot be avoided on non-combat skills | no primary core text read |
| Luck lowered into the critical range is at most an Extreme success | Roll20 "Using Luck" (Pulp text) | Pulp source only — conservative reading |
| No improvement check when Luck turns a failure into a success | Keeper Rulebook p. 99 as quoted in the same BRP Central thread by a forum member: "no skill improvement check is earned if Luck points were used to alter the dice roll" | quote not checked against the book; read literally it would also deny the upgrade case, which the staff ruling above allows — we follow the staff ruling |
| Only skills earn improvement checks; characteristic, Luck, SAN and numeric rolls never do | the improvement rules apply to skills (the investigator sheet has check boxes only beside skills) | not a quoted source; Cthulhu Mythos and Credit Rating exceptions are not modelled yet |
| Pushed attempts are not judged for improvement | pushed rolls are not implemented | open |
| One Luck spend per roll, only on the latest roll, within 30 minutes | project choice ("spent right after the roll") | not a rulebook figure |

Every restriction lives in `cocbot/rolls/rules.py` (`upgrade_targets`, `luck_options`, `luck_eligibility`, `improvement_check_earned`) with a test, so a correction is a small change.

### Improvement checks

`Roll.improvement_eligible` is decided by the **natural** result: a skill roll earns a check when the dice alone met the required difficulty. So:

| Roll | Improvement check |
|---|---|
| Skill, natural success at the required level | ✔ |
| Same, then upgraded with Luck (Regular → Hard) | ✔ (staff ruling) |
| Skill, natural failure turned into a success with Luck | ✘ (KRB p. 99) |
| Skill, natural Regular success when Hard was required, raised to Hard with Luck | ✘ (the required level was bought) |
| Characteristic, Luck, SAN or numeric roll | ✘ (only skills improve through checks) |

`/rolls roll:N` shows the result for skill rolls ("Improvement check: earned / not earned"). Ticking the sheet and the development phase are not implemented yet.

### Target values

Stored targets range from 0 to 999 (migration 014; databases that applied its first 1–100 version are rebuilt by migration 015, see §4): skills and Luck can be 0, and improved skills can exceed 100. The special results are unchanged by the target: 01 is always a Critical (or 01–N under a campaign's critical range), 100 is always a Fumble, and 96–99 are Fumbles when the target is below 50 under the RAW fumble rule. A target of 0 has no reachable success level, so Luck is never offered for it.

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

**Upgrade path:** the first version of 014 limited `target` to 1–100. Migration `015_rolls_target_range.py` detects that constraint and rebuilds `rolls` with SQLite's documented table-rebuild procedure (foreign keys off, copy every row, drop, rename, recreate indexes and triggers, `foreign_key_check`) in one transaction. IDs, the AUTOINCREMENT counter, `roll_events` and pushed-attempt links are preserved; on any error nothing changes. It does nothing on fresh databases or when run again.

`roll_events` — append-only state changes of a roll (now: `luck_spent`), with actor, before/after effective result and Luck, and `request_id UNIQUE` (the Discord interaction ID), so a replayed interaction cannot apply twice.

## 5. Luck spending flow

1. After `/check`, if the roll is eligible (a failure that Luck can raise, or a Regular/Hard success that Luck can upgrade), the result carries a **🍀 Spend Luck…** button.
2. Clicking it (owner only) shows a private confirmation with each reachable level: "Spend 17 Luck: 62 → 45, Hard success · Luck 50 → 33". Only levels the current Luck can pay for are offered.
3. **Confirm** runs one `BEGIN IMMEDIATE` transaction that:
   * re-checks eligibility against the stored roll and the *current* state (ownership, investigator still active/participating, campaign rule still on, latest roll of that investigator, within 30 minutes);
   * updates the roll only `WHERE state_version = <seen> AND luck_spent = 0`;
   * deducts Luck only `WHERE luck = <value just read>`;
   * appends a `roll_events` row (unique `request_id`) and, in a campaign, a `campaign_events` row of kind `luck_spend` (not Keeper-revertible, since the roll depends on it).

   Any mismatch rolls back everything: no Luck lost, roll unchanged.
4. The original message is updated to show `62 → 45 (🍀 −17)` and the new level; the natural roll stays visible.

Eligibility (`luck_eligibility`): investigator-linked skill or characteristic check; not Luck, SAN or pushed; natural result not a fumble and not already Extreme or Critical; Luck spending allowed by the rule profile; one spend per roll; enough Luck for at least the cheapest level. The offered levels are those at or above the required difficulty **and** better than the natural result, so a failure can be bought up to the requirement and an existing success can be upgraded.

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
