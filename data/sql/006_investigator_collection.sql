PRAGMA foreign_keys = ON;
-- 006: Per-user investigator collection.
--
-- Investigators belong to a Discord *user* (owner_user_id), never to a guild.
-- Table names deliberately avoid the generic `characters` / `attributes` /
-- `guild_settings` names that the previous code read from, so that any such
-- hand-made tables in an existing database are left untouched (see 007 for
-- the optional, non-destructive import).
--
-- Discord snowflakes are stored as TEXT to avoid any integer-precision issues.

BEGIN;

CREATE TABLE IF NOT EXISTS investigators (
  investigator_id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id   TEXT    NOT NULL,
  name            TEXT    NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 64),
  status          TEXT    NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'ready', 'archived')),
  ruleset         TEXT    NOT NULL DEFAULT 'coc7e',

  -- Biography
  occupation      TEXT,
  age             INTEGER CHECK (age IS NULL OR age BETWEEN 1 AND 150),
  pronouns        TEXT,
  residence       TEXT,
  birthplace      TEXT,

  -- Characteristics (NULL = not yet entered; drafts may be partial)
  char_str        INTEGER CHECK (char_str IS NULL OR char_str BETWEEN 0 AND 999),
  char_con        INTEGER CHECK (char_con IS NULL OR char_con BETWEEN 0 AND 999),
  char_siz        INTEGER CHECK (char_siz IS NULL OR char_siz BETWEEN 0 AND 999),
  char_dex        INTEGER CHECK (char_dex IS NULL OR char_dex BETWEEN 0 AND 999),
  char_app        INTEGER CHECK (char_app IS NULL OR char_app BETWEEN 0 AND 999),
  char_int        INTEGER CHECK (char_int IS NULL OR char_int BETWEEN 0 AND 999),
  char_pow        INTEGER CHECK (char_pow IS NULL OR char_pow BETWEEN 0 AND 999),
  char_edu        INTEGER CHECK (char_edu IS NULL OR char_edu BETWEEN 0 AND 999),
  luck            INTEGER CHECK (luck IS NULL OR luck BETWEEN 0 AND 999),

  -- Tracked resources. NULL means "at the rules-derived maximum/starting value",
  -- so they follow characteristic edits until the character takes damage etc.
  hp_current      INTEGER,
  mp_current      INTEGER,
  san_current     INTEGER,

  creation_step   TEXT,               -- resumable creation wizard position
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  archived_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_investigators_owner
  ON investigators(owner_user_id, status);

-- A user cannot have two live investigators with the same name
-- (case-insensitive). Archived ones are exempt so names can be reused.
CREATE UNIQUE INDEX IF NOT EXISTS uq_investigators_owner_name_live
  ON investigators(owner_user_id, name COLLATE NOCASE)
  WHERE status <> 'archived';

CREATE TABLE IF NOT EXISTS investigator_skills (
  investigator_id  INTEGER NOT NULL
                   REFERENCES investigators(investigator_id) ON DELETE CASCADE,
  skill_key        TEXT    NOT NULL
                   REFERENCES skill_defs(key) ON UPDATE CASCADE,
  specialization   TEXT    NOT NULL DEFAULT '',   -- reserved: Language (French), Pilot (Aircraft), ...
  value            INTEGER NOT NULL CHECK (value BETWEEN 0 AND 999),
  improvement_mark INTEGER NOT NULL DEFAULT 0 CHECK (improvement_mark IN (0, 1)),
  updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (investigator_id, skill_key, specialization)
);

-- Active investigator per user per context.
-- scope: 'global' (default everywhere) | 'guild:<guild_id>' | later 'campaign:<id>'.
-- Resolution order is most specific first (see cocbot/characters/service.py).
CREATE TABLE IF NOT EXISTS active_investigators (
  user_id         TEXT    NOT NULL,
  scope           TEXT    NOT NULL CHECK (scope = 'global' OR scope LIKE 'guild:%' OR scope LIKE 'campaign:%'),
  investigator_id INTEGER NOT NULL
                  REFERENCES investigators(investigator_id) ON DELETE CASCADE,
  updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (user_id, scope)
);

-- Defence in depth: the database itself refuses to make a user's active
-- investigator one they do not own, or an archived one.
CREATE TRIGGER IF NOT EXISTS trg_active_investigators_owner_ins
BEFORE INSERT ON active_investigators
FOR EACH ROW
WHEN NOT EXISTS (
  SELECT 1 FROM investigators i
  WHERE i.investigator_id = NEW.investigator_id
    AND i.owner_user_id = NEW.user_id
    AND i.status <> 'archived'
)
BEGIN
  SELECT RAISE(ABORT, 'active investigator must be owned by the user and not archived');
END;

CREATE TRIGGER IF NOT EXISTS trg_active_investigators_owner_upd
BEFORE UPDATE ON active_investigators
FOR EACH ROW
WHEN NOT EXISTS (
  SELECT 1 FROM investigators i
  WHERE i.investigator_id = NEW.investigator_id
    AND i.owner_user_id = NEW.user_id
    AND i.status <> 'archived'
)
BEGIN
  SELECT RAISE(ABORT, 'active investigator must be owned by the user and not archived');
END;

-- Archiving an investigator clears it from every active slot.
CREATE TRIGGER IF NOT EXISTS trg_investigators_archive_clears_active
AFTER UPDATE OF status ON investigators
FOR EACH ROW
WHEN NEW.status = 'archived'
BEGIN
  DELETE FROM active_investigators WHERE investigator_id = NEW.investigator_id;
END;

-- Records rows imported from pre-006 ad-hoc tables so the import is idempotent.
CREATE TABLE IF NOT EXISTS legacy_character_map (
  legacy_character_id INTEGER PRIMARY KEY,
  investigator_id     INTEGER REFERENCES investigators(investigator_id) ON DELETE SET NULL,
  imported_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

COMMIT;
