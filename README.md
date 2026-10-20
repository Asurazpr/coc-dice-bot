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
  trained skills, current HP / MP / SAN / Luck, and finishing the sheet
* Derived stats calculated automatically: HP, MP, SAN, Damage Bonus, Build, MOV (incl. age), Dodge
* Each Discord user has a **personal collection** of investigators (drafts, ready, archived)
* **Active investigator per user**, per server or everywhere — one player's choice never affects another's
* **/check** uses the invoking player's active investigator: trained skill values, derived bases,
  characteristics, Luck and SAN. Numeric checks still work with no character at all
* No spreadsheets: Excel files in `data/seed/` are optional developer resources only

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
/character select Ada               use Ada for your rolls in this server (everywhere:True for all)
/character skill skill:Spot Hidden value:70   set a trained skill (active investigator)
/character view public:True         show your sheet to the table
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

## License

MIT License

---

## Notes

This project is developed as a personal, long-term system rather than a quick demo.
The focus is correctness, clarity, and maintainability over gimmicks.
