PRAGMA foreign_keys = ON;

-- Clean, canonical schema for skills + i18n + aliases.
-- Key principle: mechanics use skill_defs.key; languages live only in skill_def_i18n / skill_def_aliases.

CREATE TABLE IF NOT EXISTS skill_categories (
  category_key TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS skill_defs (
  skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL UNIQUE,           -- canonical key like "spot_hidden"
  category_key TEXT,
  base INTEGER NOT NULL DEFAULT 0,
  is_derived INTEGER NOT NULL DEFAULT 0,
  derived_formula TEXT,
  FOREIGN KEY (category_key) REFERENCES skill_categories(category_key)
);

CREATE TABLE IF NOT EXISTS skill_def_i18n (
  skill_id INTEGER NOT NULL,
  lang TEXT NOT NULL,                 -- 'en', 'zh', later 'ja', 'ko', ...
  name TEXT NOT NULL,
  PRIMARY KEY (skill_id, lang),
  FOREIGN KEY (skill_id) REFERENCES skill_defs(skill_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_skill_def_i18n_name
ON skill_def_i18n(lang, name);

CREATE TABLE IF NOT EXISTS skill_def_aliases (
  lang TEXT NOT NULL,
  alias TEXT NOT NULL,
  skill_id INTEGER NOT NULL,
  PRIMARY KEY (lang, alias),
  FOREIGN KEY (skill_id) REFERENCES skill_defs(skill_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_skill_def_aliases_alias
ON skill_def_aliases(lang, alias);
