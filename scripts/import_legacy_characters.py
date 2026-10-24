"""Assign legacy (pre-006) characters that have no recorded owner.

Migration 007 imports legacy rows automatically when it can tell who owns
them. Rows it could not attribute can be given to a Discord user here:

    python scripts/import_legacy_characters.py --owner 123456789012345678

Legacy tables are only read, never modified.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cocbot.config import settings  # noqa: E402
from cocbot.db.connection import connect  # noqa: E402
from cocbot.db.legacy_import import import_legacy_characters  # noqa: E402
from cocbot.db.migrate import migrate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", required=True, help="Discord user ID that will own unattributed legacy characters")
    parser.add_argument("--db", default=str(settings.DB_PATH))
    args = parser.parse_args()
    if not args.owner.isdigit():
        parser.error("--owner must be a numeric Discord user ID")

    conn = connect(args.db)
    try:
        migrate(conn, log=print)
        report = import_legacy_characters(conn, default_owner=args.owner)
        conn.commit()
    finally:
        conn.close()
    print(f"imported={report.imported} already_imported={report.already_imported}")


if __name__ == "__main__":
    main()
