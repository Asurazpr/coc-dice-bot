"""Create or upgrade the bot database.

Usage:
    python scripts/apply_sql.py            # uses COC_DB_PATH or data/coc_bot.sqlite3
    python scripts/apply_sql.py --db PATH

Safe to run repeatedly: only pending migrations are applied. The bot also runs
this automatically at start-up.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cocbot.config import settings  # noqa: E402
from cocbot.db.connection import connect  # noqa: E402
from cocbot.db.migrate import applied_versions, migrate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(settings.DB_PATH), help="SQLite database path")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        applied = migrate(conn, log=print)
        versions = sorted(applied_versions(conn))
    finally:
        conn.close()

    if not applied:
        print("[DONE] Database already up to date.")
    else:
        print(f"[DONE] Applied {len(applied)} migration(s).")
    print(f"       Database: {args.db}  (schema versions: {versions[0]:03d}-{versions[-1]:03d})")


if __name__ == "__main__":
    main()
