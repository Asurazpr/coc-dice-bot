import re
import sqlite3
from pathlib import Path

import pandas as pd

# --- Paths ---
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "coc_bot.sqlite3"

SKILLS_XLSX = ROOT / "data" / "seed" / "skillset.xlsx"
COC_XLSX = ROOT / "data" / "seed" / "COC七版人物卡v1.35.xlsx"  # professions live here


# ---------- DB Helpers ----------

def open_db(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # Keep this minimal: create if missing, don't “redefine” your existing DB
    cur.execute("""
        CREATE TABLE IF NOT EXISTS skills_master (
            key TEXT PRIMARY KEY,
            zh TEXT,
            base INTEGER,
            category TEXT
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_skills_master_category
        ON skills_master(category);
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS professions (
            name_zh TEXT PRIMARY KEY,
            credit_min INTEGER,
            credit_max INTEGER,
            attrs_formula TEXT,
            skills_raw TEXT
        );
    """)

    conn.commit()


def debug_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA database_list;")
    print("DB files:", cur.fetchall())
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    print("Tables:", [r[0] for r in cur.fetchall()])


def to_int_or_none(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


# ---------- Upserts ----------

def upsert_skill(conn: sqlite3.Connection, *, key: str, zh: str, base: int, category: str | None) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO skills_master (key, zh, base, category)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            zh=excluded.zh,
            base=excluded.base,
            category=excluded.category;
    """, (key, zh, base, category))


def upsert_profession(
    conn: sqlite3.Connection,
    name_zh: str,
    credit_min: int | None,
    credit_max: int | None,
    attrs_formula: str | None,
    skills_raw: str | None,
) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO professions (name_zh, credit_min, credit_max, attrs_formula, skills_raw)
        VALUES (?,?,?,?,?)
        ON CONFLICT(name_zh) DO UPDATE SET
            credit_min=excluded.credit_min,
            credit_max=excluded.credit_max,
            attrs_formula=excluded.attrs_formula,
            skills_raw=excluded.skills_raw;
    """, (name_zh, credit_min, credit_max, attrs_formula, skills_raw))


# ---------- Importers ----------

def parse_credit_range(txt: str):
    if not isinstance(txt, str):
        return (None, None)
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", txt)
    if not m:
        return (None, None)
    return (int(m.group(1)), int(m.group(2)))


def import_skills_master_from_skillset(conn: sqlite3.Connection) -> None:
    if not SKILLS_XLSX.exists():
        raise FileNotFoundError(f"Missing skills seed: {SKILLS_XLSX}")

    df = pd.read_excel(SKILLS_XLSX)
    df.columns = [str(c).strip() for c in df.columns]

    required = {"key", "zh_key", "tag", "zh_tag"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"skillset.xlsx missing columns {missing}. Found: {list(df.columns)}")

    df["key"] = df["key"].astype(str).str.strip().str.lower()
    df["zh_key"] = df["zh_key"].astype(str).str.strip()
    df["tag"] = df["tag"].fillna("").astype(str).str.strip()

    imported = 0
    for _, r in df.iterrows():
        key = r["key"]
        zh = r["zh_key"]
        category = r["tag"] if r["tag"] else None

        if not key or key == "nan":
            continue

        # base values are not in skillset.xlsx -> default 0 (keep existing if already set)
        existing = conn.execute("SELECT base FROM skills_master WHERE key=?", (key,)).fetchone()
        base = int(existing[0]) if existing else 0

        upsert_skill(conn, key=key, zh=zh, base=base, category=category)
        imported += 1

    conn.commit()
    print(f"[OK] skills_master: imported/updated {imported} rows from {SKILLS_XLSX.name}")


def import_professions_from_coc_xlsx(conn: sqlite3.Connection) -> None:
    if not COC_XLSX.exists():
        print(f"[WARN] Missing CoC workbook: {COC_XLSX} — skipping professions import.")
        return

    try:
        df = pd.read_excel(COC_XLSX, sheet_name="职业列表")
    except ValueError:
        print("[WARN] Sheet '职业列表' not found in CoC workbook — skipping professions import.")
        return

    imported = 0
    for _, row in df.iterrows():
        name_zh = str(row.get("职业", "")).strip()
        if not name_zh or name_zh.startswith("选择职业序号为0"):
            continue

        cmin, cmax = parse_credit_range(str(row.get("信誉", "")).strip())
        attrs_formula = (str(row.get("职业属性", "")).strip() or None)
        skills_raw = (str(row.get("本职技能", "")).strip() or None)

        upsert_profession(conn, name_zh, cmin, cmax, attrs_formula, skills_raw)
        imported += 1

    conn.commit()
    print(f"[OK] professions: imported/updated {imported} rows from {COC_XLSX.name}")


# ---------- Main ----------

def main() -> None:
    conn = open_db(DB_PATH)
    ensure_schema(conn)
    debug_db(conn)

    import_skills_master_from_skillset(conn)
    n = conn.execute("SELECT COUNT(*) FROM skills_master").fetchone()[0]
    print("[INFO] skills_master rowcount:", n)

    import_professions_from_coc_xlsx(conn)
    p = conn.execute("SELECT COUNT(*) FROM professions").fetchone()[0]
    print("[INFO] professions rowcount:", p)

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
