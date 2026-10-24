PRAGMA foreign_keys = ON;
-- 010: occupation catalogue and occupation-based skill allocation.
-- Additive only. Investigators created before this migration have no
-- occupation and keep the Milestone 1 "basic sheet" behaviour.

BEGIN;

CREATE TABLE IF NOT EXISTS occupations (
  occupation_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  key               TEXT    NOT NULL UNIQUE,
  source            TEXT    NOT NULL CHECK (source IN ('builtin', 'homebrew')),
  owner_user_id     TEXT,                       -- homebrew only
  name_en           TEXT    NOT NULL,
  name_zh           TEXT,
  era               TEXT,                       -- NULL | 'classic' | 'modern'
  rulebook_variant  INTEGER NOT NULL DEFAULT 0,
  credit_min        INTEGER NOT NULL CHECK (credit_min BETWEEN 0 AND 99),
  credit_max        INTEGER NOT NULL CHECK (credit_max BETWEEN 0 AND 99),
  skill_points_json TEXT    NOT NULL,           -- [{"stats": [...], "mult": n}, ...]
  slots_json        TEXT    NOT NULL,           -- [{"kind", "count", "options"}, ...]
  keeper_note_zh    TEXT,
  verified          INTEGER NOT NULL DEFAULT 0,
  issues_json       TEXT    NOT NULL DEFAULT '[]',
  retired           INTEGER NOT NULL DEFAULT 0, -- removed from the catalogue; kept for references
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (credit_min <= credit_max),
  CHECK ((source = 'homebrew') = (owner_user_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_occupations_owner ON occupations(owner_user_id);

ALTER TABLE investigators ADD COLUMN occupation_id INTEGER REFERENCES occupations(occupation_id);
ALTER TABLE investigators ADD COLUMN creation_validated INTEGER NOT NULL DEFAULT 0
  CHECK (creation_validated IN (0, 1));

-- Which skill fills each choose/any slot of the investigator's occupation.
CREATE TABLE IF NOT EXISTS investigator_occupation_picks (
  investigator_id INTEGER NOT NULL REFERENCES investigators(investigator_id) ON DELETE CASCADE,
  slot_index      INTEGER NOT NULL,
  pick_index      INTEGER NOT NULL,
  skill_key       TEXT    NOT NULL REFERENCES skill_defs(key) ON UPDATE CASCADE,
  PRIMARY KEY (investigator_id, slot_index, pick_index),
  UNIQUE (investigator_id, skill_key)          -- one skill fills at most one slot
);

-- Creation bookkeeping: points spent per skill from each budget.
CREATE TABLE IF NOT EXISTS investigator_skill_points (
  investigator_id   INTEGER NOT NULL REFERENCES investigators(investigator_id) ON DELETE CASCADE,
  skill_key         TEXT    NOT NULL REFERENCES skill_defs(key) ON UPDATE CASCADE,
  occupation_points INTEGER NOT NULL DEFAULT 0 CHECK (occupation_points BETWEEN 0 AND 999),
  interest_points   INTEGER NOT NULL DEFAULT 0 CHECK (interest_points BETWEEN 0 AND 999),
  PRIMARY KEY (investigator_id, skill_key)
);

-- 'allocation' rows are maintained from investigator_skill_points (base + points);
-- 'manual' rows are values a player typed in directly (Milestone 1 behaviour).
ALTER TABLE investigator_skills ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'
  CHECK (source IN ('manual', 'allocation'));

COMMIT;
