from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def repo_root() -> Path:
    # cocbot/config.py -> cocbot -> repo root
    return Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Load ``<repo>/.env`` if python-dotenv is installed.

    Real environment variables always win over the file.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency
        return
    load_dotenv(repo_root() / ".env", override=False)


_load_dotenv()


def _optional_int(name: str) -> Optional[int]:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) != 0 else None


@dataclass(frozen=True)
class Settings:
    # Paths
    ROOT: Path = field(default_factory=repo_root)
    DATA_DIR: Path = field(default_factory=lambda: repo_root() / "data")
    DB_PATH: Path = field(
        default_factory=lambda: Path(os.getenv("COC_DB_PATH") or (repo_root() / "data" / "coc_bot.sqlite3"))
    )

    # Dashboard
    DASHBOARD_HOST: str = field(default_factory=lambda: os.getenv("COC_DASH_HOST", "127.0.0.1"))
    DASHBOARD_PORT: int = field(default_factory=lambda: int(os.getenv("COC_DASH_PORT", "8000")))

    # Discord
    DISCORD_TOKEN: str = field(default_factory=lambda: os.getenv("DISCORD_TOKEN", ""))
    # Optional: a development guild for instant slash-command sync. Never required.
    DISCORD_GUILD_ID: Optional[int] = field(default_factory=lambda: _optional_int("DISCORD_GUILD_ID"))


settings = Settings()
