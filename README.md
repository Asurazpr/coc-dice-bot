# CoC Dice Bot (Discord)

A Discord bot for **Call of Cthulhu 7th Edition**, focused on rules accuracy, clarity, and extensibility.  
Built as a personal passion project to support real tabletop play rather than a novelty dice roller.

---

## Features

### Core Dice & Checks
- **/roll** – Roll arbitrary dice expressions (`d20`, `2d6+1`, `1d100`, etc.)
- **/check** – Call of Cthulhu 7e skill or target checks
  - Accurate d100 mechanics
  - **Bonus / Penalty dice** implemented per RAW
  - Transparent **candidate roll visualization** (shows all possible tens combinations)
  - Success tiers: Fail, Success, Hard, Extreme, Critical, Fumble

### Skill System
- Normalized **SQL / SQLite schema** for skills and categories
- Canonical skill keys (language-independent)
- **Multilingual support (EN / ZH)** via i18n tables and alias resolution
- Supports derived skills (e.g. **Dodge = DEX / 2**)
- Case-insensitive and alias-based skill lookup

### UI & Readability
- Restored **classic tabletop-style /check embed**
- Large, clear roll display
- Visible Hard / Extreme thresholds
- Bonus/Penalty candidate rolls marked explicitly

### Design Goals
- Rules correctness over shortcuts
- Transparent mechanics (no hidden rerolls)
- Clean separation between mechanics, UI, and app layer
- Designed to support future extensions:
  - Keeper-configurable house rules (critical/fumble ranges)
  - Luck spending
  - Additional languages

---

## Project Structure

```
coc-dice-bot/
├─ apps/
│  ├─ discord_bot/        # Discord slash-command app
│  │  └─ main.py
│  └─ dashboard/          # (Optional) web dashboard
├─ cocbot/                # Core library
│  ├─ mechanics/          # Dice, checks, rules logic
│  ├─ db/                 # SQLite repositories
│  └─ ui/                 # Embed builders
├─ data/
│  ├─ sql/                # Schema & seed SQL (clean, keyed)
│  └─ seed/               # Excel sources (skills, careers)
├─ scripts/
│  └─ apply_sql.py        # Apply SQL migrations
└─ README.md
```

---

## Setup & Installation

### 1. Prerequisites
- Python **3.10+**
- A Discord application + bot token
- SQLite (bundled with Python)

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
pip install -r requirements.txt
```

---

### 4. Configure Environment Variables

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_GUILD_ID=your_test_guild_id_here  # optional but recommended for dev
```

- **DISCORD_TOKEN**: Bot token from Discord Developer Portal
- **DISCORD_GUILD_ID**: Guild (server) ID for fast command sync during development
  - If omitted, commands are registered globally (slower to update)

---

### 5. Initialize Database

Apply schema and seed data:

```bash
python scripts/apply_sql.py
```

This will create and populate:
- Skill definitions
- Bases and derived rules
- EN / ZH language packs

---

### 6. Run the Bot

```bash
python -m apps.discord_bot.main
```

You should see the bot log in and sync slash commands.

---

## Usage Examples

```
/check spot hidden
/check 聆听
/check dodge
/check 60 bonus_penalty:1
```

```
/roll d20
/roll 2d6+1
```

---

## Adding a New Language

Language support is fully data-driven.

To add a new language:
1. Create a new SQL file in `data/sql/lang/`
2. Insert only into:
   - `skill_def_i18n`
   - `skill_def_aliases`
3. Apply with `apply_sql.py`

No code changes required.

---

## Roadmap

- Keeper-configurable house rules (critical / fumble ranges)
- Luck spending support
- Career templates
- Additional language packs
- Optional dashboard features

---

## License

MIT License

---

## Notes

This project is developed as a personal, long-term system rather than a quick demo.  
The focus is correctness, clarity, and maintainability over gimmicks.

