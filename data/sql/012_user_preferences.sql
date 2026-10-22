PRAGMA foreign_keys = ON;
-- 012: per-user preferences (display language). Additive only.
-- A user with no row gets the default derived from their Discord client
-- locale (zh-* -> 'zh', otherwise 'en') until they choose explicitly.

BEGIN;
CREATE TABLE IF NOT EXISTS user_preferences (
  user_id    TEXT PRIMARY KEY,
  language   TEXT NOT NULL CHECK (language IN ('en', 'zh', 'both')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
COMMIT;
