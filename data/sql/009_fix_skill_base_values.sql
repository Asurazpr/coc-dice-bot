PRAGMA foreign_keys = ON;
-- 009: correct skill base values that 003 seeded incorrectly.
-- Values agree with CoC 7e and with both base-value sheets of the community
-- workbook (人物卡, 分支技能). Only skill_defs.base changes; investigators'
-- trained values are untouched. See docs/OCCUPATIONS.md §4.

BEGIN;
UPDATE skill_defs SET base = 20 WHERE key = 'library_use';
UPDATE skill_defs SET base = 20 WHERE key = 'stealth';
UPDATE skill_defs SET base = 10 WHERE key = 'chainsaw';
UPDATE skill_defs SET base = 10 WHERE key = 'flail';
UPDATE skill_defs SET base = 20 WHERE key = 'spear';
UPDATE skill_defs SET base = 20 WHERE key = 'sword';
UPDATE skill_defs SET base = 5  WHERE key = 'whip';
COMMIT;
