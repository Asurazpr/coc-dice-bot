# Roadmap

Ordered by player value and by dependency. Each milestone should ship as a
usable vertical slice with tests, like M1.

## M1 — Character collection (this PR) ✅

Per-user investigator library; create / list / view / edit / select / archive /
restore / delete inside Discord; characteristics by entry or standard RAW roll;
derived stats; trained skills; tracked HP/MP/SAN/Luck; `/check` against the
active investigator (skills, derived skills, characteristics, Luck, SAN);
versioned migrations with legacy import; tests and CI.

## M2 — Complete RAW character creation (in progress: PR for `feature/occupations`)

Done in the first M2 slice: occupation catalogue (113, EN/ZH, searchable, homebrew), occupation skill
choices, occupation and personal-interest budgets, Credit Rating range, 99 cap, review and resumable
creation. Details and open rule questions: `docs/OCCUPATIONS.md`.

Remaining:

0. **Release blockers (licensing)**: no clearance for public distribution of the bot or its game data; Chaosium's Fan Material Policy excludes software, and the community spreadsheet's rights are unknown. See `docs/OCCUPATIONS.md` §2. Required before any public release.
1. **Verify the catalogue** against the rulebooks and mark entries `verified`; resolve the flagged items in `docs/OCCUPATIONS.md` (Musician formula, unreviewed English names, approximated specializations).
2. **Specializations**: Art/Craft (Technical Drawing, Instrument, Farming, …), Language (Other) per language, Pilot (Aircraft/Boat), Survival (environment), using the reserved `specialization` column, so the approximations can be removed.
3. **Age step** (RAW table): STR/CON/DEX/APP deductions with player choice where the rules allow it, EDU improvement checks, Luck re-roll for 15–19.
4. **Keeper-shared homebrew** occupations (needs the campaign/Keeper roles of M4).
5. **Optional rules** behind explicit house-rule flags: creation skill cap (75/80), quick-fire array, point-buy. Never silently applied.

## M3B1 — Persistent rolls and Luck spending (draft PR for `feature/roll-interactions`)

Stored check records, required difficulty, optional Luck spending (campaign rule, default off; personal outside campaigns), `/rolls`. Prepared: pushed attempts as linked rows. See `ROLLS.md`; Luck rule rows not marked "verified quote" there need checking against a licensed Keeper Rulebook.

## M3 — Play at the table

1. Pushed rolls (one per check, recorded, linked to the failed original — schema ready since M3B1); Keeper approval and consequences.
2. Sanity rolls with loss expressions (`/san 1/1D6`), current SAN updates, temporary/indefinite insanity flags.
3. Damage and healing: current HP, Major Wound, dying/unconscious states.
4. Improvement marks on successful skill checks; development-phase improvement rolls.
5. Opposed and combined rolls.
6. Improvement marks from stored rolls (successes without Luck).

## M3A — Campaigns and Keeper Console (draft PR for `feature/keeper-console`)

Done in this slice (see `CAMPAIGNS.md`): persistent campaigns linked to channels, primary and assistant Keepers, invite/request/approve, players joining with an investigator from their collection, campaign-side HP/MP/SAN/Luck separate from the permanent sheet, condition labels, the private `/keeper panel` (roster, detail, adjust with confirmation, conditions, history, revert), versioned per-campaign rules (critical range, fumble rule, public/private rolls), campaign-aware `/check` and sheets, and an append-only audit log.

Still open for later campaign work: delivering private rolls to Keepers by DM, Keeper-approved edits to permanent sheets, sharing homebrew occupations to a campaign, archiving campaigns from Discord, and the play-at-the-table mechanics of M3 (which will write through `CampaignService` so they are audited).

## M4 — Further Keeper tools

NPC sheets, handouts, session notes, spectators.

## M5 — Portability

JSON export/import of an investigator; optional import of the existing Excel character sheet (opt-in, never required); printable sheet; read-only dashboard behind Discord OAuth (replacing the current scaffold).

## M6 — Operations and localisation

Chinese UI strings for the remaining screens (the creation wizard, skill picker and point forms are already localized via `/language`), structured logging, database backups, deployment guide, optional PostgreSQL if usage outgrows SQLite.
