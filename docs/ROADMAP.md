# Roadmap

Ordered by player value and by dependency. Each milestone should ship as a
usable vertical slice with tests, like M1.

## M1 — Character collection (this PR) ✅

Per-user investigator library; create / list / view / edit / select / archive /
restore / delete inside Discord; characteristics by entry or standard RAW roll;
derived stats; trained skills; tracked HP/MP/SAN/Luck; `/check` against the
active investigator (skills, derived skills, characteristics, Luck, SAN);
versioned migrations with legacy import; tests and CI.

## M2 — Complete RAW character creation

1. **Occupation catalogue** as a reviewed SQL seed (EN/ZH names, occupation skill list incl. "any two" choices, Credit Rating range, skill-point formula such as EDU×4 or EDU×2+DEX×2). Source the data from the Keeper/Investigator Handbook rather than the Excel sheet; keep Excel import as an optional developer tool.
2. **Skill-point allocation step**: occupation points and personal-interest points (INT×2), Credit Rating within the occupation range, running totals and validation (no skill above 99 at creation, Cthulhu Mythos not allowed).
3. **Specializations**: Art/Craft, Science, Language (Other), Pilot, Fighting, Firearms, Survival — using the reserved `specialization` column.
4. **Age step** (RAW table): STR/CON/DEX/APP deductions with player choice where the rules allow it, EDU improvement checks, Luck re-roll for 15–19.
5. **Wizard resume** using `creation_step`, with a "Continue creating" button in `/character list`.
6. **Optional generation methods** behind explicit house-rule flags: quick-fire array, point-buy. Never silently applied.

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
