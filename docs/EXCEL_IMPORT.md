# Excel character import (M3C) — audit and mapping

Status: implemented on `feature/excel-character-import` (draft PR). The design below was written before the implementation; §10 records where the implementation refined it.

## 1. What already exists

| Resource | What it is | Relevance |
|---|---|---|
| `scripts/import_master_from_excel.py` | Developer script loading **reference data** (skills from `skillset.xlsx`, professions from the character sheet's 职业列表) into old `skills_master` / `professions` tables with pandas | **Not** a player-character importer; nothing reused at runtime |
| `data/seed/skillset.xlsx` | 85 rows `key, zh_key, tag, zh_tag` (EN/ZH skill names) | Same names as `skill_defs` + i18n seeds (002–005); cross-checked, no runtime use |
| `data/seed/career.xlsx` | 115 occupations `Number, Career, zh_Career, Credit Rating, Total Skill Value, Career Skillset, zh_…` | Source of the committed occupation catalogue (M2); its numbering equals the character sheet's 职业序号 |
| `data/seed/COC七版人物卡v1.35.xlsx` | The community CoC 7e Chinese character sheet players actually fill in | **The template this milestone imports** |
| `Investigator`, `InvestigatorService` | Permanent sheet: bio, 8 characteristics, Luck, current HP/MP/SAN, skills by canonical key (`specialization=''`), status draft/ready/archived, `creation_validated` | Import target |
| `CreationService` | Occupation-based creation with point budgets; sets `creation_validated` | Imports never set `creation_validated` |
| `CampaignService` | Campaign participation, campaign-side HP/MP/SAN/Luck, events, join requests | Import must not touch campaign state; Keepers inspect via it |

Licensing: the template and the occupation data are third-party; their redistribution status is still **unresolved**. Tests use synthetic workbooks built in code (`tests/xlsx_factory.py`), not the seed file.

## 2. The v1.35 workbook

Four sheets: **人物卡** (the sheet, 90×21, 224 merged regions), 属性掷骰 (dice helper: `RANDBETWEEN` formulas), 分支技能 (specialization lists with base values, used by `LOOKUP`), 职业列表 (occupations: index, name, credit range, attribute text, skill-point formula referencing 人物卡, skill text). No macros, no external links, no defined names in the published template.

### 2.1 Cells read from 人物卡 (top-left cell of each merged region)

| Data | Cell | Notes |
|---|---|---|
| Name | C3 (C3:G3) | required |
| Player | C4 | kept as import note (not stored on the investigator) |
| Era | F4 | `现代` enables Computer Use / Electronics labels; kept |
| Occupation (text) | C5 (C5:G5) | → `occupation` text |
| Occupation index | D12 | 职业序号 → catalogue occupation by Chinese name of 职业列表 row; informational only |
| Age / Sex | C6 / F6 | age → `age` (1–150); sex kept as note (no field, no guess at pronouns) |
| Residence / Hometown | C7 / C8 | → `residence` / `birthplace` |
| STR DEX POW | J3 M3 P3 | values; half/fifth K/N/Q are formulas, ignored |
| CON APP EDU | J5 M5 P5 | |
| SIZ INT | J7 M7 | |
| MOV | P7 | formula (age table); **recomputed**, cached value only compared |
| HP current / max | D10 / E10 (`="/"&INT((SIZ+CON)/10)`) | max recomputed |
| SAN current / max | H10 / I10 (`MIN(POW,99-Mythos)`) | max recomputed |
| Luck | L10 | the sheet has one Luck cell → `luck` |
| MP current / max | P10 / Q10 (`INT(POW/5)`) | max recomputed |
| Skills | rows 15–46, two blocks (§2.2) | |
| Weapons | rows 50–54 B,F,I,K,M,N,P | kept as notes |
| DB / Build / Dodge | T49 / T51 / T53 | recomputed; cached compared |
| Assets, backstory, notes | B58–B70, M58–M64, K65, B73, P77–P90 | kept as notes |

### 2.2 Skill table

Each row: **name** (merged B:D or L:N), optional **specialization** (C or M), **initial** E/O, **growth** F/P, **occupation** G/Q, **interest** H/R, **total** I/S = `SUM(initial:interest)`, half/fifth J,K/T,U.

* Grouped rows with a specialization column: 技艺 (Art/Craft, B19:B21 → C19–C21), 格斗 (Fighting, B33:B35 → C33–C35), 射击 (Firearms, B36:B38 → C36–C38), 外语 (Language Other, B43:B45 → C43–C45), 母语 (Own, B46 → C46), 驾驶 (Pilot, L26 → M26), 科学 (Science, L30:L32 → M30–M32), 生存 (Survival, L36 → M36), 罕见 (rare skills, L40 → M40).
* Free rows L41–L46: player-typed skill names.
* Name formulas: B24 / B31 `=IF(F4="现代","电脑使用","[不可用]")` (Computer Use, Electronics): evaluated by this rule only.
* Initial formulas: Dodge `=INT(DEX/2)`, Own language `=EDU`, specializations `LOOKUP(...,分支技能!…)`: **not evaluated**; the bot's own base for the mapped skill is used.

### 2.3 Specializations → canonical skills

The catalogue models Art/Craft, Science, Fighting, Firearms and rare skills as separate keys (摄影 → `photography`, 化学 → `chemistry`, 斗殴 → `brawl`, 手枪 → `handgun`, 驯兽 → `animal_handling`, …), so a recognised specialization maps to its own key. Language (Other), Pilot and Survival are single keys:

* one specialization → that key, with the specialization kept in the import notes;
* several (e.g. two foreign languages) → the key takes the **highest** value and every language is listed in the warnings and import notes — never dropped silently;
* an unknown specialization (e.g. 技艺: 木工) or unknown free-row name → **not mapped**, kept with its value in the import notes, warned.

Template spellings that differ from the catalogue (`侦察` vs `侦查`, `图书馆利用`, `电气维修`) are handled by the adapter's alias table.

## 3. Values, formulas and what is trusted

Workbooks are opened twice, read-only: formulas, and Excel's cached values. Nothing is evaluated except the fixed rules below.

| Cell | Rule |
|---|---|
| Plain number | used |
| Characteristic / Luck / current resource that is a formula | cached value used **with a warning**; no cached value → missing |
| Skill total `=SUM(E:H)` (the template's own formula) | **recomputed** from initial + growth + occupation + interest (initial = bot base when the template's initial is a formula) |
| Skill total that is a plain number (player typed over the formula) | used, warned if it differs from the parts |
| Skill total with any other formula | cached value used with a warning; none → skill not imported, warned |
| Derived values (HP/MP/SAN max, MOV, DB, Build, Dodge) | always **recomputed** by `compute_derived`; a different cached value is reported (stale cache) |

Workbooks saved by tools other than Excel often have no cached values at all; recomputing keeps them usable.

A skill is stored only when its total differs from the bot's base for that investigator (so base-valued rows stay "untrained"). Values outside 0–999 are rejected per skill; current HP/MP/SAN above the recomputed maximum are left at the default (full) and warned.

## 4. Three separate statuses

1. **Parsed** — which fields were recognised; issues are `error` (blocks saving: no name, unsupported layout), `warning` (saved, needs attention), `info`.
2. **Complete** — all eight characteristics and Luck present → status `ready` (usable for `/check`, joining campaigns); otherwise `draft`, finished in the sheet editor.
3. **Rules verification** — always **"not verified"** for imports. `creation_validated` stays false. When the sheet's occupation/interest columns are filled, an *advisory* comparison with the occupation formula (matched catalogue occupation) and INT×2 is shown, but it never marks the investigator as validated.

The import status is recorded per import (`investigator_imports`) and shown to the player (preview, `/character view`) and to Keepers.

## 5. Safety

* Discord attachment: `.xlsx` only, ≤ 2 MB, read into memory; **never written to disk or stored**. Only a SHA-256 and the template key are recorded.
* Before openpyxl: the ZIP is checked (≤ 200 entries, ≤ 25 MB uncompressed in total, ≤ 10 MB per entry, compression ratio ≤ 100, `[Content_Types].xml` and `xl/workbook.xml` present). `xl/vbaProject.bin` (macro content) → rejected. External link parts → ignored (`keep_links=False`) and reported.
* openpyxl never executes formulas or macros. Parsing runs in a worker thread with the size limits above; sheets larger than 500 rows × 40 columns are refused.
* Errors shown to the player are fixed messages; logs contain the exception type only, never cell contents or file names.

## 6. Data model (migration 016, additive)

`investigator_imports`: `import_id`, `investigator_id` (CASCADE), `owner_user_id`, `template`, `file_sha256`, `action` (`create`/`update`), `completeness` (`complete`/`incomplete`), `verification` (`unverified`), `issues_json` (codes + parameters), `notes_json` (unmapped data: player, era, sex, specializations, unknown skills, weapons, backstory, point columns, cached-vs-recomputed differences), `created_at`. Append-only (triggers).

## 7. Flows

**Import (new):** `/character import file:` → private preview (recognised data, characteristics, resources, skills, not-mapped items, issues, the three statuses) → **Create investigator** → saved to the player's collection in one transaction (investigator + skills + import record) → shown with the normal sheet; select / view / `/check` as usual.

**Re-import:** if the player owns an investigator with the same name, or names one with `update:`, the preview offers **Update *name*** next to **Create new**. Update shows a **diff** (bio, characteristics, Luck, resources, skills added/changed/removed) and needs a second confirmation. It replaces the permanent sheet values in one transaction; if the investigator came from the occupation wizard, its point allocations are removed and it is no longer marked rules-validated (stated in the diff). Archived investigators cannot be updated.

**Campaigns:** re-import changes only the permanent sheet. Campaign HP/MP/SAN/Luck, conditions and history are untouched (maxima follow the new characteristics). Each active campaign gets a `sheet_import` event so its Keepers see that the sheet changed.

**Keeper:** players join with `/campaign join` as before (the imported investigator is just a ready investigator). In `/keeper panel`, the join-request review and the investigator detail gain **📄 Full sheet**: characteristics, derived values, skills, occupation, and the latest import status/warnings. `CampaignService.inspect` allows this only for the campaign's active participants and pending requests; Keepers never see other investigators of the player.

## 8. Adapters

`cocbot/imports/adapters/`: each adapter has a `key`, a human `label`, `detect(book) -> bool` (exact label checks at known cells, never fuzzy) and `parse(book) -> ParsedSheet`. `ImportService` iterates the registry; no match → "unsupported template" with the templates that are supported. Adding a template = adding an adapter + tests.

## 9. Out of scope

Exporting to Excel, other templates, importing weapons/possessions as structured data, the 属性掷骰 helper sheet, age-adjustment checks, a web dashboard.

## 10. Implementation notes

| Concern | Module |
|---|---|
| ZIP checks, in-memory load (formulas + cached values) | `cocbot/imports/workbook.py` |
| Parsed data model, adapter protocol | `cocbot/imports/model.py` |
| Template registry / detection | `cocbot/imports/adapters/__init__.py` |
| v1.35 cell map | `cocbot/imports/adapters/coc7_zh_v135.py` |
| Preview, create, re-import, records | `cocbot/imports/service.py` (`ImportService`) |
| Keeper access | `CampaignService.inspect`, `ImportService.latest_for_keeper` |
| Text (no Discord) | `cocbot/ui/import_text.py` |
| `/character import`, preview/update views | `apps/discord_bot/import_views.py`, `characters_cog.py` |
| Keeper **Full sheet** | `apps/discord_bot/keeper_views.py` (`render_full_sheet`) |
| Synthetic test workbooks | `tests/xlsx_factory.py` |

Refinements:

* **Stale previews** are detected with a fingerprint of everything an import can change (name, bio, characteristics, Luck, resources, skills, wizard state), not the `updated_at` timestamp, which can repeat within a millisecond.
* **Skill values** above the bot's skill limit (200) are not stored; they are warned and kept in the import notes.
* **Characteristics and Luck** use the bot's 0–99 limits (the same as the sheet editor).
* **Rows left at the template default** (an unused specialization row, a derived base whose characteristic is missing) are skipped instead of producing noise.
* **"Create new" with a name already used** offers `Name (2)` instead of refusing, so a player can keep both.
* **The player's sheet** (`/character view`, the editor) shows the latest import status; Keepers see it under **Full sheet**.
* Dependencies: `openpyxl` (and `defusedxml`, which openpyxl uses for hardened XML parsing when installed) are now runtime requirements.
