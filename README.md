\# 🎲 CoC Dice Bot



A Discord bot for \*\*Call of Cthulhu 7th Edition\*\* that replaces physical dice and manages character sheets — complete with sanity checks, skill rolls, and percentile bonus/penalty dice.



Built with \*\*Python 3 + discord.py (slash commands)\*\* and \*\*SQLite\*\* for persistent storage.



---



\## 🚀 Features



\- 🎯 `/roll` — Full dice expression parser (`1d6+1d4-2`, `d100b1`, etc.)

\- 🧠 `/coc` — CoC percentile success check (Regular / Hard / Extreme / Fumble)

\- 📜 `/sheet\_\*` — Manage character sheets (create, show, import/export)

\- 🎓 `/skill\_set` — Add or update skills

\- ❤️ `/hp` and `/san` — Apply HP or SAN changes

\- 🧩 `/sancheck` — Perform sanity checks and auto-apply loss

\- 💾 Persistent storage using SQLite (`coc\_bot.sqlite3`)



---



\## 🛠️ Installation



\### 1. Clone the repo

```bash

git clone https://github.com/yourusername/CoC-Dice-Bot.git

cd CoC-Dice-Bot



