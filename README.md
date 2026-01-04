# 🐙 Call of Cthulhu 7e Discord Dice Bot (WIP)

> ⚠️ **Work in Progress**  
> This project is under active development. Core features work, but commands, schema, and behavior may change.

A Discord bot for **Call of Cthulhu 7th Edition** that replaces physical dice and helps manage character sheets — including percentile rolls, difficulty checks, sanity mechanics, and bonus/penalty dice.

Built with **Python 3**, **discord.py (slash commands)**, and **SQLite** for persistent storage.

---

## ✨ Features

- 🎯 **`/roll`** — Dice expression parser  
  Supports standard and CoC-style rolls  
  (`1d6+1d4-2`, `d100`, `d100b1`, etc.)

- 🧠 **`/coc`** — CoC percentile success checks  
  Automatically evaluates **Regular / Hard / Extreme / Fumble**

- 📜 **`/sheet_*`** — Character sheet management  
  Create, view, import, and export investigator sheets

- 🎓 **`/skill_set`** — Add or update investigator skills

- ❤️ **`/hp`** & 🧠 **`/san`** — Apply HP or Sanity changes

- 🧩 **`/sancheck`** — Perform SAN checks  
  Rolls automatically and applies sanity loss

- 💾 **Persistent storage** via SQLite  
  (`coc_bot.sqlite3`)

---

## 🛠 Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/CoC-Dice-Bot.git
cd CoC-Dice-Bot

Further setup instructions coming soon as the project stabilizes.
