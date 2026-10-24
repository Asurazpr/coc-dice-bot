# Manual Discord testing checklist

Automated tests cover rules, persistence, ownership and the command callbacks
with mocked interactions. These steps cover what only a real Discord client can
show: modal rendering, ephemeral visibility, autocomplete and button behaviour.

**Setup:** a private test server, `DISCORD_GUILD_ID` set to it, two Discord
accounts (A and B), and a fresh database (`COC_DB_PATH=data/test.sqlite3`).

## Creation and editing (account A)
- [ ] `/character create` opens a form; submit name "Ada", occupation, age 31. The reply is visible only to A, shows a Draft sheet and the editor buttons.
- [ ] Submitting age `abc` shows a clear error and creates nothing.
- [ ] **STR·CON·SIZ·DEX·APP** and **INT·POW·EDU·Luck** forms pre-fill current values; saving updates the sheet; HP/MP/SAN/DB/Build/MOV appear once their inputs exist.
- [ ] **Roll characteristics** on a sheet with values asks for a second click, then fills all nine values (multiples of 5, SIZ/INT/EDU ≥ 40).
- [ ] **Skill**: "Spot Hidden" 70, then "侦查" blank → skill removed.
- [ ] **HP·MP·SAN·Luck**: HP above max is rejected; blank HP resets to max.
- [ ] Set HP below max, then lower CON: HP drops only if it is now above the new max. Raise CON again: HP does not heal. With HP blank (full), HP follows the new max.
- [ ] **Finish basic sheet** is disabled until all characteristics and Luck are set; afterwards the status reads "Basic sheet complete", the message says full CoC 7e creation is not checked, and Roll/Finish buttons disappear.
- [ ] `/character create` "Harvey" — second investigator; "ada" as a name is rejected as a duplicate.

## Selection and checks
- [ ] `/character list` shows both, ⭐ on Ada (auto-selected as the first one), and a line saying Ada is chosen for this server. Selecting Harvey from the menu opens his editor.
- [ ] `/character select` autocompletes only A's investigators; select Harvey → `/check listen` footer shows "Harvey · A".
- [ ] `/check spot hidden` uses the trained value; `/check dodge` uses DEX/2; `/check STR`, `/check luck`, `/check SAN`, `/check 力量` work.
- [ ] `/check` autocomplete lists the active investigator's trained skills first.
- [ ] `/check 60 bonus_penalty:2` and `-1` show candidate rolls; `/check 150` errors privately.
- [ ] In a second server (or DM) with no selection there, `/check dodge` asks A to select an investigator; `/character select … everywhere:True` sets a default that then works there.
- [ ] Back in the first server, `/character select Bram everywhere:True` says the server's own choice still applies; `/check listen` still uses the server choice; `/character unselect` then switches this server to the default.

## Two users
- [ ] Account B creates "Bram" and selects him. A's and B's `/check listen` show their own investigator names.
- [ ] B's `/character select` / `/character view` autocomplete never shows A's investigators, and typing `Ada` returns "no investigator named". (Panels are ephemeral, so cross-user clicks are covered by the automated tests.)

## Lifecycle and persistence
- [ ] Archive Harvey (two clicks) → disappears from list; `/check` falls back to the next scope or base values.
- [ ] `/character restore Harvey` works; `/character delete` on a live investigator is refused; on an archived one asks for confirmation.
- [ ] Restart the bot: investigators, skills and selections are unchanged; startup log reports "0 migration(s) applied".
- [ ] `/character view public:True` posts the sheet visibly to the channel.
- [ ] Archive a draft whose characteristics are all filled in but which was never finished; restore it: it is still a Draft.
- [ ] With 11+ investigators (archived count too), `/character list show_archived:True` shows page buttons; Next/Previous work and nothing is cut off.

## Milestone 2 — occupations and skill points

Use a fresh draft with characteristics entered (account A).

### Occupation
- [ ] Editor shows **🧭 Occupation & skills**; on a draft without characteristics it explains they come first.
- [ ] The Occupation step lists 25 occupations per page; ◀ ▶ page through all 113; each option shows Credit Rating and formula.
- [ ] **Search** finds by English (`doctor`) and Chinese (`医生`) names.
- [ ] Choosing **Accountant** shows Credit Rating 30–70, EDU×4, its skills, and the "not yet verified" note.
- [ ] `/character occupation` autocompletes in English and Chinese and opens the next step.
- [ ] **Custom occupation**: name "Ghost Hunter", range `10-40`, formula `EDU*2+POW*2` → 8 free choices. A bad range (`40-10`) or formula (`EDU*3`) is rejected with a clear message. Account B cannot find A's custom occupation.

### Choices
- [ ] Each choice shows only allowed skills (e.g. Soldier: Climb or Swim); skills already used elsewhere are not offered; "any skill" choices use the category picker (see the M2 UX section).
- [ ] After the last choice the Points step opens automatically.

### Points
- [ ] Budgets show "used / total / left" for occupation (formula) and personal interest (INT×2) separately.
- [ ] **Occupation points** opens a form for the 5 skills on the page; page 2 holds the rest and Credit Rating.
- [ ] Occupation points on a non-occupation skill (via **Other skill** is interest only; `/character allocate skill:Climb occupation_points:10`) are refused.
- [ ] A skill above 99, Cthulhu Mythos, overspending either budget, or Credit Rating above the range are refused with a clear reason, and **nothing in that form is saved** (see the M2 UX section).
- [ ] `/check accounting` uses base + points immediately.
- [ ] **Private Investigator** (EDU×2 + DEX or STR ×2): the Points step offers a characteristic choice with the higher one marked *suggested*; picking the other changes the budget, and the choice is still there after reopening.

### Review and resume
- [ ] Review lists blocking problems (missing choices, Credit Rating out of range, overspending) and notes (unspent points, unverified data, age adjustments not automated). **Finish creation** stays disabled until there are no problems.
- [ ] With unspent points the button reads **Finish (N points unspent)…**; the first click asks for confirmation, the second finishes.
- [ ] During occupation creation, `/character skill` is refused and the editor's **Skill** button is disabled; **Review & finish** opens the Review step.
- [ ] On a draft with hand-entered skills from before choosing an occupation (e.g. Cthulhu Mythos 30, Listen 60): Review blocks finishing and lists them; **Discard** Cthulhu Mythos (convert is refused), **Convert** Listen (points are spent, value kept); then finishing works.
- [ ] Close the panel mid-way, run `/character edit` later (or restart the bot): **Occupation & skills** resumes at the right step with everything kept.
- [ ] Picking a different occupation after spending points asks for confirmation; interest points survive the change.
- [ ] After finishing: badge "Created (occupation rules checked)", creation steps disappear, `/character skill` edits work again.
- [ ] An M1 investigator (no occupation) still finishes with **Finish basic sheet** and its message says the occupation rules were not checked.

## Milestone 2 UX polish — point forms, skill picker, language

### Point forms (atomic)
- [ ] **Occupation points** form: title shows the points left; each field shows the skill, its current points and base, pre-filled with the current allocation.
- [ ] Spend the occupation budget to exactly 0 left. In one form, lower one skill by 30 and raise another by 30: saved; the reply lists both changes (old → new, ±) and "Left: occupation 0".
- [ ] In one form, make one field valid and another over the limit (e.g. 200): the reply starts "Nothing was saved", names the problem field, and the valid field is **not** saved either.
- [ ] Over-budget form: the reply says exactly how many points it exceeds the budget by.
- [ ] **Mode: set totals** ↔ **Mode: add points** toggle: in add mode fields are empty and accept `+10` / `-5`; removing more points than a skill has is refused with the number it has. Text such as `abc` is refused without saving.
- [ ] **Other skill** opens the skill picker for personal interest; choosing a skill opens a one-field form showing its current interest points and the interest points left.

### Skill picker
- [ ] For a choice with a short list (Soldier: Climb or Swim) the skills are shown directly.
- [ ] For "any N skills" choices, a **category** menu appears first (Social, Perception & research, Knowledge, Physical & outdoors, Technical & vehicles, Art & Craft, Science, Fighting, Firearms) with counts; picking one shows its skills with base values.
- [ ] **Search** finds skills by English (`spot`) or Chinese (`侦查`) name in any display language; skills already chosen are not offered; **All categories** clears the filter.
- [ ] After picking, the next unfilled choice opens; the embed shows ✔ chosen skills, 👉 the current choice and "N remaining". **◀ Choice** goes back; picking another skill replaces the earlier choice.

### Language
- [ ] `/language` without an option shows the current language; `English`, `简体中文`, `Bilingual / 双语` change it, and the choice survives a bot restart.
- [ ] A user who never chose and whose Discord client is Chinese sees Chinese by default.
- [ ] In 简体中文: the creation steps, buttons, picker, point forms, and validation errors (e.g. occupation points on a non-occupation skill) are Chinese; skill and occupation names are Chinese.
- [ ] In Bilingual: titles and messages show both languages; short labels that would not fit fall back to English.
- [ ] Another user's language is unaffected.

## Milestone 3A — campaigns and Keeper Console

Use three accounts: **K** (Keeper), **P** (player with a finished investigator), **A** (assistant), plus **X** (unrelated member). Two text channels: **#table** and **#lounge**.

### Setup and membership
- [ ] K: `/campaign create name:Masks` in #table → private confirmation; `/campaign info` there shows K as Keeper and "rules v1".
- [ ] K: `/campaign create name:Second` in #table → refused (channel already linked). With `link_channel:False` it succeeds.
- [ ] K: `/keeper invite user:@P`; P: `/campaign join investigator:<theirs>` in #table → "joined"; autocomplete only lists P's own finished investigators.
- [ ] X: `/campaign join` in #table without an invite → "Request sent"; K's panel shows "Join requests: 1"; review → **Deny** asks for confirmation; denying removes the request.
- [ ] P: try to join with a draft investigator → refused; try to join twice → refused.

### Keeper Console
- [ ] K: `/keeper panel` → private roster with P's investigator and HP/MP/SAN/Luck `x/max`.
- [ ] Pick the investigator → detail with resources, conditions (none), recent changes.
- [ ] **Adjust**: HP `+0`/blank keeps; Luck `+5` applies immediately ("Saved"); HP `-3` with a reason shows a confirmation with `old → new`; **Cancel** leaves HP unchanged; **Confirm** saves.
- [ ] Set HP `0` → the confirmation warns "reaches 0". Values over the maximum, negative results or text are refused with nothing saved.
- [ ] **Add condition** `Shaken` → shown in roster and detail; duplicate (case-insensitive) is refused; **Remove condition…** asks for confirmation.
- [ ] **History** shows actor, time, old → new and reason; **Revert a change…** + Confirm restores the old value. Change HP again, then try to revert the older change → refused with an explanation, nothing overwritten. A change can be reverted only once.
- [ ] X runs `/keeper panel` in #table → refused; X never sees the roster or anyone's campaign values.
- [ ] P runs `/keeper panel` → refused.
- [ ] Open two panels (K and A), change HP in one, then Confirm a decrease in the other → "changed while you were confirming", nothing saved.

### Assistant Keeper
- [ ] K: `/keeper assistant user:@A enabled:True`; A can open the panel, adjust and revert, but `/keeper rules critical_max:3`, `/keeper channel` and `/keeper assistant` are refused for A.
- [ ] While A has a panel open, K revokes A → A's next click is refused.

### Single source of truth
- [ ] After K sets P's HP to 5 in Masks: P's `/character view` in #table shows HP 5 and a "Campaign: Masks" note; in #lounge it shows P's personal HP.
- [ ] In #table, P's sheet editor shows **HP·MP·SAN·Luck** disabled (Keeper-managed); in #lounge it is enabled and editing personal values does not change the campaign values.
- [ ] P's `/check luck` in #table uses the campaign Luck and the footer names "Masks · rules vN"; in #lounge it uses the personal Luck and has no campaign footer.
- [ ] Join the same investigator to a second campaign in another channel: changes in one campaign never appear in the other.
- [ ] P cannot archive or delete the investigator while it is in a campaign; after `/campaign leave` (with confirmation) they can.

### Rules
- [ ] K: `/keeper rules` (no options) shows the current rules; `critical_max:5` → "v2"; a roll of 01–05 in #table is a Critical; outside campaign channels 02–05 are not.
- [ ] `fumble:Only 100` → a 97 on a skill under 50 is a Failure in #table and a Fumble elsewhere.
- [ ] `roll_visibility:Private` → P's `/check` in #table is visible only to P, and appears as "private roll" in K's campaign history. Public again restores normal posts.
- [ ] Setting the same rule twice does not create a new version.

### Language and persistence
- [ ] With `/language` 简体中文, the console, confirmations and errors are Chinese; Bilingual shows both.
- [ ] Restart the bot: campaigns, members, assistants, campaign values, conditions, history and rule versions are unchanged.
- [ ] Existing personal `/check`, `/character` and creation flows outside campaign channels behave as before.

## Milestone 3B1 — roll records and Luck spending

### Single account, no Keeper (any channel not linked to a campaign)
Needs one finished investigator with Luck ≥ 40 selected in the server (e.g. STR 60).

- [ ] `/check STR` → result as before, footer ends with `Roll #N`; no "Required" line for a Regular check.
- [ ] `/check STR difficulty:Hard` and `difficulty:Extreme` → the title shows the rolled level, a line shows "Required: Hard — ✅ met / ❌ not met". A Critical meets Extreme; a Fumble meets nothing.
- [ ] Roll until a check misses (or use a low skill): a **🍀 Spend Luck…** button appears. Click → private prompt listing each reachable level with its cost and Luck after.
- [ ] **Cancel** → nothing spent; `/character view` shows the same Luck.
- [ ] Pick a level → private "Spent N Luck"; the public result now shows `natural → effective`, the new level, "the original roll is kept"; the button is gone. `/character view` shows Luck reduced by exactly N.
- [ ] Press the old prompt's button again → refused ("already spent" / "changed"); Luck unchanged.
- [ ] Roll again, then press **Spend Luck…** on the *previous* result → refused ("latest roll").
- [ ] `/check luck`, `/check SAN`, `/check 45`, and a roll of 100 (or 96–99 on a skill under 50) → no Luck button.
- [ ] A Regular success (e.g. 45 vs STR 60) shows **Spend Luck…** offering Hard (15) and Extreme (33), not Regular; a Hard success offers only Extreme; an Extreme success or a Critical shows no button. Upgrading keeps "the original roll is kept" and shows the new level.
- [ ] Set a skill to 0 (e.g. Cthulhu Mythos) and to 150 with `/character skill`, then `/check` each: both post normally; 01 is still a Critical, 100 still a Fumble; the 0 skill never offers Luck.
- [ ] Lower Luck to 1 in the sheet editor, fail a check → no button (not enough Luck).
- [ ] Wait 30 minutes after a failed check, then press its button → refused ("too old").
- [ ] `/rolls` → private list with your rolls (number, time, subject, difficulty, natural → effective, 🍀, ✅/❌). `/rolls roll:N` → dice (bonus/penalty candidates), context, rules version, changes, Luck status.
- [ ] `/check STR bonus_penalty:2` then `/rolls roll:N` → all three candidates and the units die are listed.
- [ ] `/language 简体中文` → difficulty line, Luck prompt, confirmation and `/rolls` are Chinese.
- [ ] Restart the bot → `/rolls` still lists earlier rolls with the same results and Luck changes.

### Multiplayer (later; needs a Keeper K, players P and Q, observer X)
- [ ] K: `/keeper rules luck_spending:True` → rules v(N+1) and the summary says "Luck spending on"; an assistant Keeper cannot change it.
- [ ] Before K enables it, P's failed check in the campaign channel shows no Luck button.
- [ ] After enabling: P spends Luck → the campaign Luck drops (K's panel shows it at once), P's personal sheet outside the campaign is unchanged, and K's campaign history shows "spent Luck old → new" by P with the roll. That entry has no Revert option.
- [ ] Q or X presses P's **Spend Luck…** button → "Only the player who rolled…"; nothing changes.
- [ ] P opens the prompt; K then lowers P's campaign Luck with **Adjust**; P confirms → the spend uses the *current* Luck (Luck after = new value − cost), or is refused if that is no longer enough.
- [ ] K turns `luck_spending` off while P has a prompt open → P's confirm is refused.
- [ ] P leaves the campaign with a prompt open → confirm refused.
- [ ] Same investigator in two campaigns: spending in one leaves the other's Luck unchanged.
- [ ] With `roll_visibility:Private`: P's result, prompt and updated result are only visible to P; K sees one "private roll" history entry (with the roll number) plus the Luck spend; Q and X see nothing.
- [ ] Two clients (P on phone and desktop) press Confirm at the same moment → Luck deducted once.
- [ ] K (not a participant) rolls in the campaign channel with a personal investigator → no Luck option.
