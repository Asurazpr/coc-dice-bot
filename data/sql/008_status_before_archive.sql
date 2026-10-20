PRAGMA foreign_keys = ON;
-- 008: remember an investigator's status when it is archived, so restoring
-- returns it to exactly that status (a draft stays a draft even if its
-- characteristics happen to be complete).
--
-- Additive only. Rows archived before this migration have NULL here and are
-- restored as 'draft' (the conservative choice: finishing again is one click,
-- whereas promoting an unfinished draft to 'ready' would be wrong).

BEGIN;

ALTER TABLE investigators
  ADD COLUMN status_before_archive TEXT
  CHECK (status_before_archive IS NULL OR status_before_archive IN ('draft', 'ready'));

COMMIT;
