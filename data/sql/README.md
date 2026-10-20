Versioned migrations, applied in numeric order by `cocbot/db/migrate.py`
(at bot start-up, or `python scripts/apply_sql.py`). Each is recorded in
`schema_migrations` and runs once.

  001_core_schema.sql            skill catalogue schema
  002_seed_skill_defs.sql        canonical skills
  003_seed_skill_bases.sql       base values + derived formulas
  004_seed_i18n_en.sql           English names/aliases
  005_seed_i18n_zh.sql           Chinese names/aliases
  006_investigator_collection.sql  per-user investigators, skills, active selection
  007_import_legacy_characters.py  non-destructive import of pre-006 data
  008_status_before_archive.sql  remember draft/ready status across archive

Rules:
  - Never edit a migration that has shipped; add a new NNN_ file instead.
  - `.sql` files manage their own BEGIN/COMMIT; `.py` files define upgrade(conn).
  - Never DROP or rewrite user tables.

`lang/` holds the original copies of the 004/005 language packs and is not
applied automatically. To add a language, create a new numbered migration.
