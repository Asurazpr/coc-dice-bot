"""Per-user preferences (currently: display language)."""
from __future__ import annotations

import sqlite3
from typing import Optional

from cocbot.i18n import DEFAULT_LANG, LANGS


def language_from_locale(locale: Optional[object]) -> str:
    """Default for users who never chose: Chinese for zh-* Discord clients, else English."""
    value = str(getattr(locale, "value", locale) or "").lower()
    return "zh" if value.startswith("zh") else DEFAULT_LANG


def get_language(conn: sqlite3.Connection, user_id: object, locale: Optional[object] = None) -> str:
    row = conn.execute("SELECT language FROM user_preferences WHERE user_id=?", (str(user_id),)).fetchone()
    if row and row[0] in LANGS:
        return row[0]
    return language_from_locale(locale)


def has_language(conn: sqlite3.Connection, user_id: object) -> bool:
    return conn.execute("SELECT 1 FROM user_preferences WHERE user_id=?", (str(user_id),)).fetchone() is not None


def set_language(conn: sqlite3.Connection, user_id: object, language: str) -> str:
    language = str(language).lower()
    if language not in LANGS:
        raise ValueError(f"language must be one of {', '.join(LANGS)}")
    conn.execute(
        """
        INSERT INTO user_preferences (user_id, language) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET language=excluded.language,
                                           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (str(user_id), language),
    )
    conn.commit()
    return language
