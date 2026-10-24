PRAGMA foreign_keys = ON;
-- 016: record of each spreadsheet import (docs/EXCEL_IMPORT.md §6). Additive only.
-- The uploaded file itself is never stored; only its SHA-256.

BEGIN;

CREATE TABLE IF NOT EXISTS investigator_imports (
  import_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  investigator_id INTEGER NOT NULL REFERENCES investigators(investigator_id) ON DELETE CASCADE,
  owner_user_id   TEXT NOT NULL,
  template        TEXT NOT NULL,
  file_sha256     TEXT NOT NULL CHECK (length(file_sha256) = 64),
  action          TEXT NOT NULL CHECK (action IN ('create', 'update')),
  completeness    TEXT NOT NULL CHECK (completeness IN ('complete', 'incomplete')),
  verification    TEXT NOT NULL DEFAULT 'unverified' CHECK (verification IN ('unverified')),
  issues_json     TEXT NOT NULL DEFAULT '[]',
  notes_json      TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_investigator_imports_inv ON investigator_imports(investigator_id, import_id);

-- The importer must be the investigator's owner.
CREATE TRIGGER IF NOT EXISTS trg_investigator_imports_owner
BEFORE INSERT ON investigator_imports
FOR EACH ROW
WHEN NOT EXISTS (SELECT 1 FROM investigators i
                 WHERE i.investigator_id = NEW.investigator_id AND i.owner_user_id = NEW.owner_user_id)
BEGIN
  SELECT RAISE(ABORT, 'imports can only be recorded by the investigator''s owner');
END;

CREATE TRIGGER IF NOT EXISTS trg_investigator_imports_no_update
BEFORE UPDATE ON investigator_imports
BEGIN
  SELECT RAISE(ABORT, 'import records are append-only');
END;

COMMIT;
