PRAGMA foreign_keys = ON;
-- 011: persist the player's choice for occupation formulas with alternatives,
-- e.g. EDU×2 + (STR or DEX)×2. NULL means "not chosen": the bot suggests and
-- uses the higher value. Additive only.

BEGIN;
ALTER TABLE investigators ADD COLUMN skill_point_stat TEXT
  CHECK (skill_point_stat IS NULL OR skill_point_stat IN ('STR','CON','SIZ','DEX','APP','INT','POW','EDU'));
COMMIT;
