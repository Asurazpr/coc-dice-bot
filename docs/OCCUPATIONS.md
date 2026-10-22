# Occupations and skill allocation (Milestone 2)

## 1. What the existing spreadsheets contain

| File | Content | Verdict |
|---|---|---|
| `data/seed/COC七版人物卡v1.35.xlsx`, sheet `职业列表` | 114 rows: number, Chinese name, Credit Rating range, skill-point formula (Chinese), occupation skills (Chinese free text). Row 0 is a "clear template" switch, row 1 a custom-occupation placeholder. | **Primary source.** Usable after parsing. |
| `data/seed/career.xlsx` | Same 114 rows with English names; formulas in `EDU*2+STR/DEX*2` notation; English skill text only for rows 1–9. | English names only, after correcting typos/duplicates. Formulas used as a cross-check. |
| `data/seed/skillset.xlsx` | The 85 skills with EN/ZH names and specialization groups. | Already matches our `skill_defs`. |
| Workbook sheets `人物卡`, `分支技能` | Base value for every skill. | Used to verify our base values (see §4). |
| `scripts/import_master_from_excel.py` | Writes `professions` / `skills_master` tables that nothing at runtime reads. | Superseded by `scripts/import_occupations.py`; left in place. |

Findings:

* **The two occupation sources agree** on every Credit Rating range and skill list. The only conflict is **Musician**: the workbook says EDU×2 + (POW or DEX)×2, `career.xlsx` says (DEX or APP). **Decision (maintainer, citing the Keeper Rulebook):** keep EDU×2 + (DEX or POW)×2. The discrepancy stays recorded on the entry, which remains `verified: false` pending a review against an authorized copy of the rulebook.
* **Provenance tags** in the Chinese names: `原作向` (11 rows, rulebook-oriented versions), `古典` (4, 1920s), `现代` (1, modern). Untagged rows mix rulebook occupations with community additions (e.g. 除魅师 Disenchanter, 业余技艺爱好者). The data does not say which untagged rows are official.
* **English names** had typos and duplicates, corrected in the importer: Anitique→Antique Dealer, Firefigher→Firefighter, Enthsiast→Enthusiast, a second "Artist" (艺人) → Entertainer, a second "Psychiatrist" (精神病医生（古典）) → Psychiatrist (Classic), and 驻外记者 "Writer / Journalist" → Foreign Correspondent. Other translations are kept as given and **have not been reviewed**.

## 2. Licensing status — unresolved release blockers

**No licensing clearance has been obtained for either the dataset or the bot.** Until the items below are resolved, the repository and the bot must not be distributed publicly (public repository, public bot invite, or hosted instance for others).

1. **Chaosium Fan Material Policy does not cover this project.** That policy explicitly excludes software, apps and virtual tabletops, so a Discord bot implementing Call of Cthulhu rules cannot rely on it, regardless of being free and non-commercial. Public release needs a different basis, such as a licence or written permission from Chaosium (e.g. through their licensing programmes), or limiting the bot to content and mechanics that need no permission. *Status: unresolved.*
2. **The community workbook's redistribution rights are unknown.** `data/seed/COC七版人物卡v1.35.xlsx` and `career.xlsx` have no stated author or licence in the repository, and `data/occupations/occupations.json` is derived from them. *Status: unresolved;* identify the author and terms, or replace the data.
3. **Rule-derived occupation data.** Even without prose, a full catalogue of a published game's occupations (names, Credit Rating ranges, skill lists, formulas) may need permission as part of item 1. *Status: unresolved.*
4. **Existing seed data.** The skill names, base values and Chinese translations seeded in `data/sql/002`–`005` have the same open questions as items 1 and 3. *Status: unresolved.*

What the code does to keep this resolvable:

* The catalogue stores only structured fields (names, numbers, skill structure), no descriptions or rulebook prose.
* The built-in catalogue is **one replaceable file**. `COC_OCCUPATIONS_PATH` points the bot at a different JSON in the same schema (e.g. a licensed or self-authored dataset), or `COC_OCCUPATIONS_PATH=none` runs with homebrew occupations only. Removing or replacing the file never deletes investigators; entries absent from a new file are only marked retired.
* The Excel files are used only by the developer import script, never at runtime.
* Every entry is `verified: false` and labelled as unverified community data in the bot.

## 3. Parsing result## 3. Parsing result

`scripts/import_occupations.py` regenerates `data/occupations/occupations.json` (committed, reviewed) from the workbook. The bot only reads the JSON.

* **113 occupations** imported, **0 unmapped phrases**, every one has **8 occupation skills** plus Credit Rating.
* **24 need review** (full list printed by the importer). None were guessed silently; each is flagged on the entry and shown to players:
  * **Specializations the skill catalogue does not have** → any Art/Craft is offered: Technical Drawing (Architect, Engineer), Performance types (Entertainer), Farming (Farmer), Instrument (Musician), Typing/Shorthand (Secretary), Carpentry/Welding/Plumbing (Technician).
  * **Specialization kept generic** because we have one skill for it: Language (Latin) → Language (Other) (Doctor, Forensic Doctor, Pharmacist); Pilot (Aircraft/Boat) → Pilot (6 occupations); Survival (Alpine/Sea) → Survival.
  * **"Art" in "Art/Craft (Art or Photography)"** approximated as Fine Art (Investigative Reporter).
  * **"Academic"/"study" restrictions** on free choices are not enforced (Doctor, Librarian, Professor, Psychologist, Researcher, Students).
  * **Unspecified "Language"** offered as Own or Other (White Collar Clerk).

## 4. Existing data bug found: skill base values

Migration `003` seeded seven base values that disagree with both 7e and the workbook. Points are added on top of the base, so this matters for allocation and also for current `/check` results:

| Skill | Seeded | 7e / workbook |
|---|---|---|
| Library Use | 10 | 20 |
| Stealth | 10 | 20 |
| Chainsaw | 15 | 10 |
| Flail | 15 | 10 |
| Spear | 15 | 20 |
| Sword | 15 | 20 |
| Whip | 15 | 5 |

Fixed by migration `009` (updates `skill_defs.base` only; investigators' trained values are untouched).

## 5. Data model

```
occupations                         catalogue (built-in synced from JSON at start-up; homebrew per user)
  occupation_id, key UNIQUE, source ('builtin'|'homebrew'), owner_user_id (homebrew only)
  name_en, name_zh, era, rulebook_variant, credit_min, credit_max
  skill_points_json   [{"stats":["EDU"],"mult":2},{"stats":["DEX","STR"],"mult":2}]
  slots_json          [{"kind":"fixed|choose|any","count":n,"options":[skill keys]}, …]
  keeper_note_zh, verified, issues_json, retired

investigators (+ columns)
  occupation_id       → occupations
  creation_validated  1 when finished under the occupation rules

investigator_occupation_picks       which skills fill each choose/any slot
  (investigator_id, slot_index, pick_index) -> skill_key

investigator_skill_points           creation bookkeeping
  (investigator_id, skill_key) -> occupation_points, interest_points

investigator_skills (+ column)
  source              'manual' | 'allocation'; allocation rows hold base + points
```

* Built-in occupations are **upserted by key** at start-up from the JSON, so catalogue fixes need no migration. Entries removed from the JSON are marked `retired`, never deleted, because investigators may reference them.
* Homebrew occupations are owned by the user who created them and visible only to them (Keeper-shared homebrew belongs to the campaign milestone).
* M1 investigators keep working unchanged: no occupation means the M1 "basic sheet" path.

## 6. Rules as implemented

Standard rules (7e, chapter 3):

1. Occupation skill points = the occupation's formula; Personal interest points = INT × 2. Tracked separately.
2. Occupation points may be spent only on the occupation's 8 skills (as chosen) and Credit Rating.
3. Personal interest points may be spent on any skill except Cthulhu Mythos.
4. Credit Rating must end within the occupation's range.
5. No skill may exceed 99 at creation; Cthulhu Mythos stays 0.
6. Dodge and Language (Own) bases come from DEX/2 and EDU and follow characteristic edits.

Decisions (confirmed by the maintainer in the PR #2 review):

* **"EDU × 2 + (STR or DEX) × 2"**: the player chooses the characteristic on the Points step; the choice is saved (`investigators.skill_point_stat`). Until they choose, the higher value is suggested and used. Choosing a lower value can overspend the budget, which blocks finishing but never deletes points.
* **Credit Rating with personal interest points**: allowed, as long as the final value stays in range.
* **Unspent points**: allowed, but finishing with unspent points needs an explicit second confirmation. They are not carried over.
* **Changing characteristics after allocating**: budgets are recomputed live; an overspent budget blocks finishing but never deletes allocations.
* **Changing occupation** clears that occupation's skill choices and occupation points (after confirmation); personal interest points are kept.
* **Unverified data stays visible**: unverified occupations and approximated specializations are flagged in the occupation summary and at review.

Hand-entered skill values (closes a validation bypass found in review):

* While a draft has an occupation, `/character skill` and the editor's **Skill** button refuse changes; skills get their values only from points.
* Values typed in **before** an occupation was chosen (Milestone 1 behaviour) are kept, but any that differ from the skill's base are a **blocking error** at review: they were not paid for. The player reconciles each one explicitly on the Review step: **convert** (spend occupation points first if it is an occupation skill, then personal interest, only if the budgets and rules allow) or **discard** (back to base). Nothing is deleted without that choice. Cthulhu Mythos cannot be converted; values above 99 cannot be converted.
* `creation_validated` is set only when every trained value equals base + points and all rules pass.
* Finished investigators (and Milestone 1 investigators without an occupation) keep free manual editing for progression; this check applies only to occupation-based creation.
* **Optional rules not implemented**: age adjustments (still manual), the 75%/80% creation cap some Keepers use, half-value Credit Rating adjustments, quick-fire skill arrays.

## 7. Creation flow in Discord

Biography → Characteristics → Occupation → Occupation skill choices → Skill-point allocation → Review & Finish. Every step saves immediately; **Occupation & skills** in the sheet editor resumes at the right step. Details and manual checks: `docs/MANUAL_TESTING.md` (Milestone 2 sections).

### Point forms are atomic

A points form (up to 5 skills, one budget) is validated as one batch against the **final** totals (`rules.evaluate_batch`) and written in one transaction, or not at all:

* Order does not matter, and lowering one skill while raising another works even when the budget was fully spent.
* If the batch would exceed a budget, nothing is saved and the reply states the exact excess (e.g. "exceed the budget by 15 (315 of 300; 60 occupation points were left)").
* Per-skill rules (occupation-only spending, 99 cap, Credit Rating maximum, Cthulhu Mythos, unreconciled hand-entered values) apply to each changed skill in the batch.
* A budget that is already over its limit (e.g. after EDU was lowered) may be reduced in steps; a batch is refused only if it *increases* spending in a budget that ends over the limit.
* Two modes, switched with a button: **set totals** (each field is the skill's total from that budget; blank = 0) and **add points** (signed changes such as `+10` / `-5`; blank = no change).

### Skill picker

Discord select menus hold at most 25 options, so all 85 skills are never put into one dropdown. Choices with ≤ 25 options are shown directly; larger sets (e.g. "any two skills", or personal interest) first offer nine categories, each ≤ 25 skills (`cocbot/ui/skill_picker.py`), plus search by English/Chinese name, canonical key or alias in any display language. The same picker serves occupation choices and personal-interest skills.

*Modal select menus* (`discord.ui.Label` with a `Select`) exist since discord.py 2.6. They were not used: options are still capped at 25, a modal cannot change its options after opening (so no category drill-down inside it), and it would raise the minimum discord.py version from 2.4. The picker uses ordinary message components instead.

### Display language

`/language` stores a per-user preference (`user_preferences`, migration 012): English, 简体中文 or bilingual. Without a stored choice, Chinese Discord clients default to Chinese. Localized: the occupation wizard, skill picker, point forms and the creation-related validation errors (`cocbot/i18n.py`). Skill keys and occupation keys stay language-independent; search accepts both languages regardless of the preference. Not yet localized: Milestone 1 screens (sheet, list, basic editor), `/check` and `/roll`.
