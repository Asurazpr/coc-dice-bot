# Audit of the original implementation

Baseline: `main` at `b18c665`. Every finding below was verified by reading the
code and, where noted, by running it against a fresh database.

## What worked and was kept

| Area | State |
|---|---|
| d100 mechanics (`cocbot/mechanics/dice.py`) | Correct bonus/penalty implementation (one units die, N+1 tens dice, 00+0 = 100). Kept; only guards and an injectable RNG were added. |
| Skill catalogue (`data/sql/001`–`005`) | Clean, language-neutral schema: 85 skills, EN/ZH names and aliases, Dodge/Language (Own) as derived formulas. Kept unchanged. |
| `/check` embed (`cocbot/ui/check_embed_old.py`) | Readable classic layout with candidate rolls. Kept; its private success logic now delegates to the shared rules function. |
| Layering intent | `mechanics` / `db` / `ui` / `apps` split is sound and was extended rather than replaced. |

## Defects and broken assumptions

1. **Fresh setup could not succeed.** README says `pip install -r requirements.txt`, but no requirements file existed. `scripts/apply_sql.py` raised `FileNotFoundError` when the database did not already exist, so a new install could not create its own database.
2. **`.env` was never loaded.** `cocbot/config.py` reads `os.getenv` but nothing loads the `.env` file the README tells users to create, so `DISCORD_TOKEN` had to be exported by hand.
3. **Two different database paths.** `apps/discord_bot/main.py` opened the hard-coded relative path `data/coc_bot.sqlite3` (dependent on the working directory) while skill lookup used `settings.DB_PATH`. With `COC_DB_PATH` set, or when started from another directory, character and skill data came from different files.
4. **Character tables did not exist.** The code read `guild_settings` and `attributes` (and the dashboard templates expect `characters`), but no migration created them. `/setchar` failed with `OperationalError`, and derived skills (Dodge, Language (Own)) could never be rolled.
5. **Characters were assigned to a whole server.** The active character was `guild_settings.active_character_id`: one player's `/setchar` changed the character used by everyone in that server. It accepted any raw database ID with no ownership check, so any user could point the server at anyone's character.
6. **Rules bug: 100 was not always a fumble.** `success_level` returned *Fail* for a roll of 100 when the target was 50 or more. RAW (Keeper Rulebook, ch. 5): 100 is always a fumble; 96–99 also fumble when the skill is below 50. The embed had its own, correct copy of the rule, so the two implementations disagreed and `CheckResult.level` was wrong.
7. **"Ephemeral" errors were public.** `/check` deferred publicly and then sent error follow-ups with `ephemeral=True`; the first follow-up after a public defer inherits its visibility.
8. **Unbounded dice.** `/roll 1000000000d6` would block the event loop; bonus/penalty dice had no limit either.
9. **Skill lookup was brittle.** Exact-case alias match in one guessed language only (`SPOT HIDDEN` failed).
10. **Dead or duplicated code.** `cocbot/db/repo_skills.py` queried `skills_master`, a table only the Excel import script creates; `data/sql/lang/*.sql` duplicates `004`/`005` and `data/sql/README.md` described an apply order the script did not use.
11. **Dashboard scaffold is non-functional.** Only `/` is routed; `guild.html` and `detail.html` reference routes and tables that do not exist; its dependencies were undeclared. Not changed in this milestone (documented as optional).
12. **Repository hygiene.** `.env.example` contained a personal absolute Windows path and a placeholder guild ID that breaks command sync if copied unchanged; several files began with a UTF-8 BOM; there were no tests and no CI.
13. **Excel dependency for character data.** Occupation data exists only inside the Chinese Excel character sheet (`data/seed/COC七版人物卡v1.35.xlsx`); the import script writes to tables nothing at runtime reads.

## Technical debt carried forward (not fixed here)

* Occupation data still needs a proper, reviewed seed (see roadmap M2).
* The dashboard scaffold remains a placeholder.
* Bot reply strings are English only (skill names are already bilingual).
* SQLite is accessed synchronously from async handlers. Queries are small and indexed; revisit if the bot serves many guilds.
