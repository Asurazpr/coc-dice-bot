PRAGMA foreign_keys = ON;
-- 013: campaigns, Keeper roles, campaign-side investigator state, audit log, rules.
-- Additive only: no existing table is changed. See docs/CAMPAIGNS.md.

BEGIN;

CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- A channel belongs to at most one campaign; a server may host many campaigns.
CREATE TABLE IF NOT EXISTS campaign_channels (
  channel_id  TEXT PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  guild_id    TEXT,
  added_by    TEXT NOT NULL,
  added_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_channels_campaign ON campaign_channels(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_members (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  user_id     TEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('keeper', 'assistant', 'player')),
  status      TEXT NOT NULL CHECK (status IN ('invited', 'requested', 'active', 'removed')),
  invited_by  TEXT,
  -- investigator a player asked to join with, until a Keeper approves the request
  pending_investigator_id INTEGER REFERENCES investigators(investigator_id) ON DELETE SET NULL,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (campaign_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_campaign_members_user ON campaign_members(user_id, status);
-- Exactly one active primary Keeper per campaign.
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_primary_keeper
  ON campaign_members(campaign_id) WHERE role = 'keeper' AND status = 'active';

-- Participation of a player-owned investigator, with the campaign-side current values.
-- The investigator stays owned by its player; maxima are always derived from it.
CREATE TABLE IF NOT EXISTS campaign_investigators (
  campaign_id     INTEGER NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  investigator_id INTEGER NOT NULL REFERENCES investigators(investigator_id) ON DELETE CASCADE,
  user_id         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'left')),
  hp              INTEGER CHECK (hp IS NULL OR hp >= 0),
  mp              INTEGER CHECK (mp IS NULL OR mp >= 0),
  san             INTEGER CHECK (san IS NULL OR san >= 0),
  luck            INTEGER CHECK (luck IS NULL OR luck >= 0),
  joined_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (campaign_id, investigator_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_one_active_investigator_per_player
  ON campaign_investigators(campaign_id, user_id) WHERE status = 'active';

-- The participant must be the investigator's owner.
CREATE TRIGGER IF NOT EXISTS trg_campaign_investigators_owner
BEFORE INSERT ON campaign_investigators
FOR EACH ROW
WHEN NOT EXISTS (SELECT 1 FROM investigators i
                 WHERE i.investigator_id = NEW.investigator_id AND i.owner_user_id = NEW.user_id)
BEGIN
  SELECT RAISE(ABORT, 'campaign participant must own the investigator');
END;

CREATE TABLE IF NOT EXISTS campaign_conditions (
  condition_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id     INTEGER NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  investigator_id INTEGER NOT NULL REFERENCES investigators(investigator_id) ON DELETE CASCADE,
  label           TEXT NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 40),
  added_by        TEXT NOT NULL,
  added_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  removed_by      TEXT,
  removed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_campaign_conditions_active
  ON campaign_conditions(campaign_id, investigator_id) WHERE removed_at IS NULL;

-- Append-only audit log.
CREATE TABLE IF NOT EXISTS campaign_events (
  event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id     INTEGER NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  investigator_id INTEGER REFERENCES investigators(investigator_id) ON DELETE SET NULL,
  actor_user_id   TEXT NOT NULL,
  kind            TEXT NOT NULL,
  field           TEXT,
  old_value       TEXT,
  new_value       TEXT,
  reason          TEXT CHECK (reason IS NULL OR length(reason) <= 200),
  ref_id          INTEGER,                 -- e.g. condition_id
  reverts_event_id     INTEGER REFERENCES campaign_events(event_id),
  reverted_by_event_id INTEGER REFERENCES campaign_events(event_id),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_events_campaign ON campaign_events(campaign_id, event_id);
CREATE INDEX IF NOT EXISTS idx_campaign_events_investigator ON campaign_events(campaign_id, investigator_id, event_id);

CREATE TRIGGER IF NOT EXISTS trg_campaign_events_no_delete
BEFORE DELETE ON campaign_events
FOR EACH ROW
WHEN (SELECT COUNT(*) FROM campaigns WHERE campaign_id = OLD.campaign_id) > 0
BEGIN
  SELECT RAISE(ABORT, 'campaign events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_campaign_events_no_edit
BEFORE UPDATE ON campaign_events
FOR EACH ROW
WHEN NEW.event_id IS NOT OLD.event_id OR NEW.campaign_id IS NOT OLD.campaign_id
  OR NEW.actor_user_id IS NOT OLD.actor_user_id OR NEW.kind IS NOT OLD.kind OR NEW.field IS NOT OLD.field
  OR NEW.old_value IS NOT OLD.old_value OR NEW.new_value IS NOT OLD.new_value OR NEW.reason IS NOT OLD.reason
  OR NEW.created_at IS NOT OLD.created_at OR NEW.reverts_event_id IS NOT OLD.reverts_event_id
  OR NEW.ref_id IS NOT OLD.ref_id
  OR (OLD.reverted_by_event_id IS NOT NULL AND NEW.reverted_by_event_id IS NOT OLD.reverted_by_event_id)
  OR (NEW.investigator_id IS NOT OLD.investigator_id AND NEW.investigator_id IS NOT NULL)
BEGIN
  SELECT RAISE(ABORT, 'campaign events are append-only (only marking as reverted is allowed)');
END;

CREATE TABLE IF NOT EXISTS campaign_rules (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
  version     INTEGER NOT NULL CHECK (version >= 1),
  rules_json  TEXT NOT NULL,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (campaign_id, version)
);

COMMIT;
