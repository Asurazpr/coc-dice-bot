# CoC Dice Bot (Discord)

A Discord bot for **Call of Cthulhu 7th Edition**, focused on rules accuracy, clarity, and extensibility.
Built as a personal passion project to support real tabletop play rather than a novelty dice roller.

---

## Features

### Core Dice & Checks

* **/roll** – Roll arbitrary dice expressions (`d20`, `2d6+1`, `1d100`, etc.)
* **/check** – Call of Cthulhu 7e skill or target checks

  * Accurate d100 mechanics
  * **Bonus / Penalty dice** implemented per RAW
  * Transparent **candidate roll visualization** (shows all possible tens combinations)
  * Success tiers: Fail, Success, Hard, Extreme, Critical, Fumble

### Investigators (characters) — entirely inside Discord

* **/character create** opens a form; a step-by-step editor (buttons + forms) handles
  characteristics (enter them, or roll with the standard 3D6×5 / (2D6+6)×5 method),
  trained skills, current HP / MP / SAN / Luck, and finishing the basic sheet
* **Occupation & skills**: occupation skill choices are made one group at a time — tick several skills in
  one menu (with category filter and EN/中文 search), check the selection, then **Save choices**. A
  dedicated **Interest skills** screen lists your personal-interest skills to edit, remove or add, and
  **Edit one skill…** changes a single skill without paging through forms
* Choose from 113 occupations (searchable in English and Chinese) or a
  custom one, make the occupation's skill choices, then spend occupation points (from the
  occupation's formula) and personal interest points (INT×2) with both budgets tracked. The bot
  enforces occupation-only spending, the Credit Rating range and the 99 cap, and saves every step
  so creation can be resumed later. Age adjustments are not automated yet
* **"Finish basic sheet"** (no occupation) only means every characteristic and Luck is filled in;
  the occupation rules are checked only when an occupation is chosen
* Derived stats calculated automatically: HP, MP, SAN, Damage Bonus, Build, MOV (incl. age), Dodge
* Each Discord user has a **personal collection** of investigators (draft, basic sheet complete, archived)
* **Active investigator per user** — one player's choice never affects another's. You can choose one
  for a specific server, and set a **default** (`everywhere:True`) used wherever you haven't chosen
  one. A server-specific choice always overrides the default; `/character unselect` clears it
* **/check** uses the invoking player's active investigator: trained skill values, derived bases,
  characteristics, Luck and SAN. Numeric checks still work with no character at all
* No spreadsheets: Excel files in `data/seed/` are optional developer resources only

### Roll records and Luck

* Every `/check` is stored with its dice, target, required difficulty, rule profile and visibility;
  the footer shows its number (`Roll #12`). The original roll is never rerolled or overwritten
* `/check … difficulty:Hard|Extreme` shows the rolled level and whether the requirement was met
* **Spend Luck** (optional rule, Keeper Rulebook p. 99): after a check, the roller can lower the roll
  1 point per Luck point after a private confirmation — to turn a failure into the required level, or
  to upgrade a success (Regular → Hard → Extreme). Not for Luck or SAN rolls, fumbles, numbers without
  an investigator, or pushed rolls; a Luck-bought result is at most an Extreme success. Outside
  campaigns it spends personal Luck; in a campaign it spends campaign Luck and only if the Keeper
  enabled it (`/keeper rules luck_spending:True`)
* Targets of 0 and above 100 (untrained Mythos, Luck 0, improved skills) are recorded normally
* `/rolls` (private) lists your recent checks; `/rolls roll:12` shows one with its dice and changes

### Campaigns and the Keeper Console

* **/campaign create** makes a campaign and links the channel; a server can host several campaigns
* Keepers invite players (**/keeper invite**) or approve their requests; players join with one of
  their own ready investigators (**/campaign join**). The investigator stays owned by the player
* In a campaign channel, `/check` and the player's sheet use the campaign's HP / MP / SAN / Luck;
  the personal sheet and other campaigns are never changed
* **/keeper panel** (private): roster, investigator detail, adjust HP/MP/SAN/Luck (`7`, `+2`, `-3`,
  with an optional reason; decreases and 0 need confirmation), condition labels, history with revert
* Assistant Keepers (granted by the primary Keeper) can use the console; rules, channels and
  assistants stay with the primary Keeper
* **/keeper rules**: versioned per-campaign rules — critical range 01–05, fumble rule (RAW or 100
  only), public or private rolls. Only rules the engine supports are offered
* Every change records who did it, when, the old and new value and the reason

### Skill System

* Normalized **SQL / SQLite schema** for skills and categories
* Canonical skill keys (language-independent)
* **Multilingual support (EN / ZH)** via i18n tables and alias resolution
* Derived skills (e.g. **Dodge = DEX / 2**) computed from the active investigator
* Case-insensitive lookup across languages, aliases and canonical keys

### UI & Readability

* Restored **classic tabletop-style /check embed**
* Large, clear roll display
* Visible Hard / Extreme thresholds
* Bonus/Penalty candidate rolls marked explicitly

### Design Goals

* Rules correctness over shortcuts
* Transparent mechanics (no hidden rerolls)
* Clean separation between mechanics, UI, and app layer
* Designed to support future extensions:

  * Keeper-configurable house rules (critical/fumble ranges)
  * Luck spending
  * Additional languages

---

## Project Structure

```
coc-dice-bot/
├─ apps/
│  ├─ discord_bot/        # Discord layer: main.py, characters_cog.py, views.py
│  └─ dashboard/          # (Optional, unfinished) web dashboard scaffold
├─ cocbot/                # Core library (no Discord imports outside ui/)
│  ├─ mechanics/          # Dice, checks, derived stats, characteristic generation
│  ├─ characters/         # Investigator model, ownership service, /check resolution
│  ├─ db/                 # Connection, migrations, repositories
│  └─ ui/                 # Embed builders
├─ data/
│  ├─ sql/                # Versioned migrations (NNN_*.sql / NNN_*.py)
│  └─ seed/               # Excel sources (optional, developer-only)
├─ docs/                  # Audit, architecture, roadmap, manual test checklist
├─ scripts/               # apply_sql.py (migrate), legacy import, Excel import
└─ tests/                 # Automated tests
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data model and design decisions.

---

## Invite the Bot

You can invite **CoC Dice Bot** to your own Discord server using the official OAuth2 link below:

👉 [https://discord.com/oauth2/authorize?client_id=1429217082297548812&permissions=2147502080&integration_type=0&scope=bot+applications.commands](https://discord.com/oauth2/authorize?client_id=1429217082297548812&permissions=2147502080&integration_type=0&scope=bot+applications.commands)

> You must have **Manage Server** permission to add the bot.
> The bot requests only the minimum permissions required for slash commands and embeds.

---

## Setup & Installation

### 1. Prerequisites

* Python **3.10+**
* A Discord application + bot token
* SQLite (bundled with Python)

---

### 2. Clone the Repository

```bash
git clone https://github.com/Asurazpr/coc-dice-bot.git
cd coc-dice-bot
```

---

### 3. Create Virtual Environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt        # bot
pip install -r requirements-dev.txt    # + test tools
```

---

### 4. Configure Environment Variables

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

Then edit `.env` (it is git-ignored — never commit it):

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_GUILD_ID=          # optional: your test server ID for instant command sync
COC_DB_PATH=               # optional: database path (default data/coc_bot.sqlite3)
```

* **DISCORD_TOKEN**: Bot token from the Discord Developer Portal
* **DISCORD_GUILD_ID**: if omitted, commands are registered globally (can take a while to appear)

---

### 5. Database

The bot creates and upgrades its SQLite database automatically at start-up.
To do it by hand (for example before a deploy):

```bash
python scripts/apply_sql.py              # or: --db path/to/file.sqlite3
```

Migrations are versioned (`data/sql/NNN_*.sql|py`), recorded in `schema_migrations`,
applied once each, and never drop existing data. Databases created by the old
`apply_sql.py` upgrade in place. If an old database contains hand-made
`characters` / `attributes` tables, migration 007 imports rows whose owner is known
and leaves the originals untouched; assign the rest with:

```bash
python scripts/import_legacy_characters.py --owner <discord_user_id>
```

---

### 6. Run the Bot

```bash
python -m apps.discord_bot.main
```

You should see the bot log in and sync slash commands.

---

### 7. Run the Tests

```bash
python -m pytest            # or, with no extra packages: python -m unittest discover -s tests -t .
```

Core tests need only Python. Discord-layer tests run when `discord.py` is installed
and are skipped otherwise. Manual checks: [docs/MANUAL_TESTING.md](docs/MANUAL_TESTING.md).

---

## Usage Examples

```
/character create                   form → editor with buttons for each part of the sheet
/character list                     your collection (⭐ = active here)
/character select Ada               use Ada for your rolls in this server
/character select Ada everywhere:True   make Ada your default (servers with their own choice keep it)
/character unselect                 clear this server's choice so your default applies here
/character skill skill:Spot Hidden value:70   set a trained skill (active investigator)
/character view public:True         show your sheet to the table
/character creation                 occupation → choices → skill points → review (resumable)
/character occupation occupation:医生   choose an occupation (autocomplete, EN/中文)
/character allocate skill:Law occupation_points:40   spend creation points on one skill
/language language:简体中文          display language for the creation steps (English / 简体中文 / both)
/character edit | archive | restore | delete
```

```
/check spot hidden        → your investigator's Spot Hidden
/check 聆听
/check dodge              → DEX/2 of your investigator
/check STR | luck | SAN
/check 60 bonus_penalty:1 → plain target, no character needed
```

```
/campaign create name:Masks         create a campaign in this channel; you are its Keeper
/keeper invite user:@Alice          invite a player (or approve their request)
/campaign join investigator:Ada     join this channel's campaign
/keeper panel                       private Keeper Console
/keeper assistant user:@Bob enabled:True
/keeper rules critical_max:5 roll_visibility:Private
/campaign info | /campaign leave | /keeper remove | /keeper channel link:True
```

```
/check spot hidden difficulty:Hard     → shows "Required: Hard — met / not met"
/rolls                                 → your recent checks (private)
/rolls roll:12                         → one roll: dice, rules, Luck spent
```

```
/roll d20
/roll 2d6+1
```

---

## Adding a New Language

Language support is data-driven. Add a new numbered migration, e.g.
`data/sql/008_seed_i18n_ja.sql`, that inserts only into `skill_def_i18n` and
`skill_def_aliases` (copy the shape of `004_seed_i18n_en.sql`). It is applied
automatically on the next start or `python scripts/apply_sql.py`.

---

## Roadmap

Next: occupations and skill-point allocation, age rules, pushed rolls and Luck
spending, Sanity rolls, campaigns with Keeper permissions. Details in
[docs/ROADMAP.md](docs/ROADMAP.md).

---

## Licensing status — not cleared for public release

This is a personal development project. **No licensing clearance has been obtained** for the bot or
its game data. Chaosium's Fan Material Policy excludes software and apps, so it does not cover this
bot, and the redistribution rights of the community spreadsheet the occupation data comes from are
unknown. These are open release blockers; see [docs/OCCUPATIONS.md §2](docs/OCCUPATIONS.md). The
occupation catalogue is a single replaceable file (`COC_OCCUPATIONS_PATH`, or `none` to disable it).

Call of Cthulhu is a trademark of Chaosium Inc. This project is not affiliated with or endorsed by Chaosium.

## License

MIT License (applies to this project's own code only; it grants no rights to third-party game
content or data).

---

## Notes

This project is developed as a personal, long-term system rather than a quick demo.
The focus is correctness, clarity, and maintainability over gimmicks.
