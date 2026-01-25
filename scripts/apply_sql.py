from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "coc_bot.sqlite3"
SQL_DIR = ROOT / "data" / "sql"


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found: {DB_PATH}")

    sql_files = sorted(
        p for p in SQL_DIR.iterdir()
        if p.suffix == ".sql" and p.name[:3].isdigit() and p.name[3] == "_"
    )

    if not sql_files:
        raise FileNotFoundError(f"No .sql files found in: {SQL_DIR}")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        for path in sql_files:
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.commit()
            print(f"[OK] Applied {path.name}")
    finally:
        conn.close()

    print("[DONE] All migrations applied.")


if __name__ == "__main__":
    main()
