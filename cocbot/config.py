from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    # cocbot/config.py -> cocbot -> repo root
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    # Paths
    ROOT: Path = repo_root()
    DATA_DIR: Path = ROOT / "data"
    DB_PATH: Path = Path(os.getenv("COC_DB_PATH", str(DATA_DIR / "coc_bot.sqlite3")))

    # Dashboard
    DASHBOARD_HOST: str = os.getenv("COC_DASH_HOST", "127.0.0.1")
    DASHBOARD_PORT: int = int(os.getenv("COC_DASH_PORT", "8000"))

    # Discord
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    DISCORD_GUILD_ID: int | None = int(os.getenv("DISCORD_GUILD_ID", "0")) or None


settings = Settings()
