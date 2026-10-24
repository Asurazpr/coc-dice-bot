# Architecture

```
apps/discord_bot/           Discord presentation only
  main.py                   bot start-up, /roll, /check, error funnel
  characters_cog.py         /character … slash commands
  views.py                  modals, buttons, select menus (ownership re-checked)
cocbot/
  mechanics/                pure rules: dice, checks, derived stats, generation
  characters/               domain
    models.py               Investigator dataclass + derived accessors
    service.py              InvestigatorService — the only ownership gate
    check_target.py         "what number does /check roll against?"
  db/                       SQLite access
    connection.py           one connection factory (FKs on, Row factory)
    migrate.py              versioned migration runner
    repo_investigators.py   SQL for investigators (no permission logic)
    repo_skill_defs.py      skill lookup (EN/ZH, aliases, keys)
    legacy_import.py        non-destructive import of pre-006 data
  occupations/              catalogue (JSON-synced + homebrew), rules.py (pure), service.py,
                            zh_parser.py (development-time import from the Excel sheet)
  ui/                       embed builders; sheet_text.py and creation_text.py are Discord-free
data/sql/NNN_*.sql|py       migrations (001–005 original, 006–007 new)
tests/                      stdlib unittest, runnable with pytest
```

Dependencies point downwards only: `apps` → `cocbot.characters` → `cocbot.db` / `cocbot.mechanics`. Nothing in `cocbot.mechanics` or `cocbot.characters` imports `discord`.

## Data model (migration 006)

```
investigators            one row per investigator, owned by a Discord user
  owner_user_id TEXT     Discord snowflake (TEXT avoids precision issues)
  status                 draft | ready | archived  (ready = basic sheet complete, not
                         a check of full CoC 7e creation)
  status_before_archive  restored exactly on un-archive (migration 008)
  bio columns            occupation, age, pronouns, residence, birthplace
  char_str … char_edu    NULL until entered (drafts may be partial)
  luck
  hp/mp/san_current      NULL = "at the rules-derived value"
  creation_step          where the creation wizard should resume
investigator_skills      (investigator_id, skill_key, specialization) -> value
  skill_key → skill_defs.key; specialization reserved for Language/Pilot/…
  improvement_mark       reserved for improvement checks
active_investigators     (user_id, scope) -> investigator_id
  scope = 'global' | 'guild:<id>' | later 'campaign:<id>'
legacy_character_map     makes the legacy import idempotent
schema_migrations        applied migration versions
```

Database triggers refuse an active selection whose investigator is not owned by that user or is archived, and archiving clears all of an investigator's active slots.

## Significant decisions

1. **Investigators belong to users, not servers.** The personal library is independent of any server; servers only influence which investigator is *active*.
2. **Scoped active selection.** Lookup order is most specific first: `guild:<id>` then `global`. `global` is a *default*: `/character select … everywhere:True` sets it, but any server where the player chose someone keeps that choice. `/character unselect` removes the server choice (or the default with `everywhere:True`); `/character list` shows which layer is in effect. In DMs only the default exists. A campaign scope slots in ahead of the guild without schema changes.
3. **New table names.** The old code read `characters`/`attributes`/`guild_settings`, which no migration created, so real databases may contain hand-made versions of unknown shape. Using `investigators` etc. guarantees `CREATE TABLE IF NOT EXISTS` never silently adopts a legacy table. Migration 007 imports legacy rows when an owner can be determined and never modifies the originals.
4. **Ownership enforced in one place.** `InvestigatorService` takes the acting user's ID on every call; "not found" and "not yours" are indistinguishable. Views re-check the clicking user, and triggers back this up in the database.
5. **Characteristics as columns, skills as rows.** The eight characteristics are fixed by the rules and benefit from typed columns and CHECKs; skills are open-ended (85 now, specializations later).
6. **Resources default to derived values.** `NULL` current HP/MP/SAN follow characteristic edits, so drafts never show stale maxima.
7. **Archive before delete.** Deletion is permanent and only allowed for archived investigators, behind a confirmation button.
8. **Rules are data-driven where house rules are expected.** `CheckRules` holds critical/fumble ranges; RAW is the default instance, so Keeper configuration later means passing a different instance, not editing logic.
9. **Migrations run at bot start-up.** `scripts/apply_sql.py` remains for operators; both use the same runner. SQL migrations manage their own transaction; Python migrations run inside one committed by the runner.
10. **Tests use the standard library.** Core tests (mechanics, persistence, ownership, check resolution) need nothing beyond Python; Discord-layer tests use mocked interactions and skip if discord.py is absent.

## Extension path: campaigns and Keepers

Planned tables (not created yet):

```
campaigns(campaign_id, guild_id, name, keeper_user_id, ruleset, house_rules_json, created_at)
campaign_members(campaign_id, user_id, role)          -- keeper | player | spectator
campaign_investigators(campaign_id, investigator_id,  -- an investigator *joins* a campaign
                       joined_at, state_json)        -- session state: temp. insanity, conditions
```

* Joining a campaign links a user-owned investigator; it never transfers ownership.
* Keeper permissions are checked in a new `CampaignService`; `InvestigatorService` stays the owner gate and gains an explicit "keeper may edit tracked resources of investigators in my campaign" path.
* `resolution_scopes()` becomes `[campaign:<id>, guild:<id>, global]` when the channel is bound to a campaign.
* Per-campaign `CheckRules` come from `house_rules_json`.
