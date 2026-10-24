PRAGMA foreign_keys = ON;
-- 014: persistent check records and their state changes (Luck spending; pushed attempts later).
-- Additive only. See docs/ROLLS.md.

BEGIN;

CREATE TABLE IF NOT EXISTS rolls (
  roll_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id         TEXT NOT NULL,
  investigator_id INTEGER REFERENCES investigators(investigator_id) ON DELETE SET NULL,
  investigator_name TEXT,
  campaign_id     INTEGER REFERENCES campaigns(campaign_id) ON DELETE SET NULL,
  channel_id      TEXT,
  guild_id        TEXT,
  luck_scope      TEXT NOT NULL CHECK (luck_scope IN ('campaign', 'personal', 'none')),
  subject_kind    TEXT NOT NULL CHECK (subject_kind IN ('skill', 'characteristic', 'numeric')),
  subject_key     TEXT,
  subject_label   TEXT NOT NULL,
  -- 0 (untrained Cthulhu Mythos, Luck 0) and above 100 (improved skills) are valid targets
  target          INTEGER NOT NULL CHECK (target BETWEEN 0 AND 999),
  base_value      INTEGER,
  difficulty      TEXT NOT NULL DEFAULT 'regular' CHECK (difficulty IN ('regular', 'hard', 'extreme')),
  bonus_penalty   INTEGER NOT NULL DEFAULT 0,
  dice_json       TEXT NOT NULL,
  natural         INTEGER NOT NULL CHECK (natural BETWEEN 1 AND 100),
  natural_level   TEXT NOT NULL,
  rules_json      TEXT NOT NULL,
  rules_version   INTEGER,
  visibility      TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
  attempt         TEXT NOT NULL DEFAULT 'original' CHECK (attempt IN ('original', 'pushed')),
  parent_roll_id  INTEGER REFERENCES rolls(roll_id),
  luck_spent      INTEGER NOT NULL DEFAULT 0 CHECK (luck_spent >= 0),
  effective       INTEGER NOT NULL CHECK (effective BETWEEN 1 AND 100),
  effective_level TEXT NOT NULL,
  state_version   INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (effective = natural - luck_spent),
  CHECK ((attempt = 'original' AND parent_roll_id IS NULL) OR (attempt = 'pushed' AND parent_roll_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_rolls_user ON rolls(user_id, roll_id);
CREATE INDEX IF NOT EXISTS idx_rolls_investigator ON rolls(investigator_id, roll_id);
CREATE INDEX IF NOT EXISTS idx_rolls_campaign ON rolls(campaign_id, roll_id);

-- A pushed attempt must follow an original roll.
CREATE TRIGGER IF NOT EXISTS trg_rolls_pushed_parent
BEFORE INSERT ON rolls
FOR EACH ROW
WHEN NEW.attempt = 'pushed'
 AND NOT EXISTS (SELECT 1 FROM rolls p WHERE p.roll_id = NEW.parent_roll_id AND p.attempt = 'original')
BEGIN
  SELECT RAISE(ABORT, 'a pushed attempt must reference an original roll');
END;

-- The original dice, target, rules and context are permanent.
CREATE TRIGGER IF NOT EXISTS trg_rolls_immutable
BEFORE UPDATE ON rolls
FOR EACH ROW
WHEN NEW.roll_id IS NOT OLD.roll_id OR NEW.user_id IS NOT OLD.user_id
  OR (NEW.campaign_id IS NOT OLD.campaign_id AND NEW.campaign_id IS NOT NULL) OR NEW.channel_id IS NOT OLD.channel_id
  OR NEW.guild_id IS NOT OLD.guild_id OR NEW.luck_scope IS NOT OLD.luck_scope
  OR NEW.subject_kind IS NOT OLD.subject_kind OR NEW.subject_key IS NOT OLD.subject_key
  OR NEW.subject_label IS NOT OLD.subject_label OR NEW.target IS NOT OLD.target
  OR NEW.base_value IS NOT OLD.base_value OR NEW.difficulty IS NOT OLD.difficulty
  OR NEW.bonus_penalty IS NOT OLD.bonus_penalty OR NEW.dice_json IS NOT OLD.dice_json
  OR NEW.natural IS NOT OLD.natural OR NEW.natural_level IS NOT OLD.natural_level
  OR NEW.rules_json IS NOT OLD.rules_json OR NEW.rules_version IS NOT OLD.rules_version
  OR NEW.visibility IS NOT OLD.visibility OR NEW.attempt IS NOT OLD.attempt
  OR NEW.parent_roll_id IS NOT OLD.parent_roll_id OR NEW.created_at IS NOT OLD.created_at
  OR NEW.investigator_name IS NOT OLD.investigator_name
  OR (NEW.investigator_id IS NOT OLD.investigator_id AND NEW.investigator_id IS NOT NULL)
  OR NEW.luck_spent < OLD.luck_spent OR NEW.state_version <= OLD.state_version AND
     (NEW.luck_spent IS NOT OLD.luck_spent OR NEW.effective IS NOT OLD.effective)
BEGIN
  SELECT RAISE(ABORT, 'the original roll is permanent');
END;

CREATE TRIGGER IF NOT EXISTS trg_rolls_no_delete
BEFORE DELETE ON rolls
BEGIN
  SELECT RAISE(ABORT, 'rolls are permanent');
END;

CREATE TABLE IF NOT EXISTS roll_events (
  event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  roll_id         INTEGER NOT NULL REFERENCES rolls(roll_id),
  actor_user_id   TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('luck_spent')),
  old_effective   INTEGER,
  new_effective   INTEGER,
  old_level       TEXT,
  new_level       TEXT,
  luck_before     INTEGER,
  luck_after      INTEGER,
  request_id      TEXT UNIQUE,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_roll_events_roll ON roll_events(roll_id, event_id);

CREATE TRIGGER IF NOT EXISTS trg_roll_events_no_update
BEFORE UPDATE ON roll_events
BEGIN
  SELECT RAISE(ABORT, 'roll events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_roll_events_no_delete
BEFORE DELETE ON roll_events
BEGIN
  SELECT RAISE(ABORT, 'roll events are append-only');
END;

COMMIT;
