This folder contains the cleaned skill schema + seeds.

Apply order:
  001_core_schema.sql
  002_seed_skill_defs.sql
  003_seed_skill_bases.sql
  lang/seed_i18n_en.sql
  lang/seed_i18n_zh.sql

To add a new language later:
  - create ONE new file under lang/seed_i18n_<lang>.sql that only touches:
      * skill_def_i18n
      * skill_def_aliases
  - no other migrations needed.
