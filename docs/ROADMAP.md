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

1. **Verify the catalogue** against the rulebooks and mark entries `verified`; resolve the flagged items in `docs/OCCUPATIONS.md` (Musician formula, unreviewed English names, approximated specializations).
2. **Specializations**: Art/Craft (Technical Drawing, Instrument, Farming, …), Language (Other) per language, Pilot (Aircraft/Boat), Survival (environment), using the reserved `specialization` column, so the approximations can be removed.
3. **Age step** (RAW table): STR/CON/DEX/APP deductions with player choice where the rules allow it, EDU improvement checks, Luck re-roll for 15–19.
4. **Keeper-shared homebrew** occupations (needs the campaign/Keeper roles of M4).
5. **Optional rules** behind explicit house-rule flags: creation skill cap (75/80), quick-fire array, point-buy. Never silently applied.

## M3 — Play at the table

1. Pushed rolls (one per check, recorded) and Luck spending after a roll, decrementing current Luck.
2. Sanity rolls with loss expressions (`/san 1/1D6`), current SAN updates, temporary/indefinite insanity flags.
3. Damage and healing: current HP, Major Wound, dying/unconscious states.
4. Improvement marks on successful skill checks; development-phase improvement rolls.
5. Opposed and combined rolls.
6. Roll history per investigator.

## M4 — Campaigns and Keeper tools

Campaign tables (see `ARCHITECTURE.md`), Keeper role, players joining with an investigator from their library, campaign-scoped active selection, Keeper view of the party, Keeper-approved edits, per-campaign house rules (`CheckRules`), hidden rolls to the Keeper.

## M5 — Portability

JSON export/import of an investigator; optional import of the existing Excel character sheet (opt-in, never required); printable sheet; read-only dashboard behind Discord OAuth (replacing the current scaffold).

## M6 — Operations and localisation

Chinese UI strings (skill names already bilingual), structured logging, database backups, deployment guide, optional PostgreSQL if usage outgrows SQLite.
