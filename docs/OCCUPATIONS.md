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

* **The two occupation sources agree** on every Credit Rating range and skill list. The only conflict is **Musician**: the workbook says EDU×2 + (POW or DEX)×2, `career.xlsx` says (DEX or APP). The workbook value matches the 7e Musician as far as I know; **needs your verification**.
* **Provenance tags** in the Chinese names: `原作向` (11 rows, rulebook-oriented versions), `古典` (4, 1920s), `现代` (1, modern). Untagged rows mix rulebook occupations with community additions (e.g. 除魅师 Disenchanter, 业余技艺爱好者). The data does not say which untagged rows are official.
* **English names** had typos and duplicates, corrected in the importer: Anitique→Antique Dealer, Firefigher→Firefighter, Enthsiast→Enthusiast, a second "Artist" (艺人) → Entertainer, a second "Psychiatrist" (精神病医生（古典）) → Psychiatrist (Classic), and 驻外记者 "Writer / Journalist" → Foreign Correspondent. Other translations are kept as given and **have not been reviewed**.

## 2. Licensing

The catalogue contains only structured game data: names, numeric ranges, skill-point formulas and which skills an occupation offers. No descriptions or other rulebook prose are reproduced. Call of Cthulhu is a trademark of Chaosium Inc., whose fan-material policy allows free, non-commercial fan works with attribution. The community workbook's own licence is unknown; **confirm you are comfortable redistributing data derived from it** before publishing the repository widely. Every entry carries `verified: false` until someone checks it against a rulebook they own.

## 3. Parsing result

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

## 6. Rules as implemented (and decisions you should confirm)

Standard rules (7e, chapter 3):

1. Occupation skill points = the occupation's formula; Personal interest points = INT × 2. Tracked separately.
2. Occupation points may be spent only on the occupation's 8 skills (as chosen) and Credit Rating.
3. Personal interest points may be spent on any skill except Cthulhu Mythos.
4. Credit Rating must end within the occupation's range.
5. No skill may exceed 99 at creation; Cthulhu Mythos stays 0.
6. Dodge and Language (Own) bases come from DEX/2 and EDU and follow characteristic edits.

Decisions where the rules leave room (**please confirm or override**):

* **"EDU × 2 + (STR or DEX) × 2"**: the bot uses the higher of the alternatives. The rules let the player choose; nobody benefits from choosing the lower one.
* **Credit Rating with personal interest points**: allowed, as long as the final value stays in range.
* **Unspent points**: allowed, shown as a warning at review. They are not carried over.
* **Changing characteristics after allocating**: budgets are recomputed live; an overspent budget blocks finishing but never deletes allocations.
* **Changing occupation** clears that occupation's skill choices and occupation points (after confirmation); personal interest points are kept.
* **Skills entered manually before choosing an occupation** are kept but flagged at review as "not paid for by allocation".
* **Optional rules not implemented**: age adjustments (still manual), the 75%/80% creation cap some Keepers use, half-value Credit Rating adjustments, quick-fire skill arrays.

## 7. Creation flow in Discord

Biography → Characteristics → Occupation → Occupation skill choices → Skill-point allocation → Review & Finish. Every step saves immediately; the editor shows **Continue creation** to resume from `creation_step`. Details and manual checks: `docs/MANUAL_TESTING.md` (Milestone 2 section).
