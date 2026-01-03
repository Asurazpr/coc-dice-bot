# import_master_from_excel.py
import sqlite3, re
from pathlib import Path
import pandas as pd

# --- Config (use project-relative, normalized paths) ---
DB_PATH = Path("data/coc_bot.sqlite3")
XLSX = Path("COC七版人物卡v1.35.xlsx")  # adjust if your file lives elsewhere

ZH_TO_EN = {
    "侦查":"spot hidden","聆听":"listen","图书馆使用":"library use","心理学":"psychology","潜行":"stealth",
    "神秘学":"occult","信誉":"credit rating","话术":"fast talk","恐吓":"intimidate","说服":"persuade","魅惑":"charm",
    "闪避":"dodge","急救":"first aid","医学":"medicine","克苏鲁神话":"cthulhu mythos","历史":"history",
    "人类学":"anthropology","考古学":"archaeology","博物学":"natural world","导航":"navigate","机械维修":"mechanical repair",
    "电器维修":"electrical repair","开锁":"locksmith","妙手":"sleight of hand","伪装":"disguise","法律":"law","会计":"accounting",
    "攀爬":"climb","跳跃":"jump","骑术":"ride","游泳":"swim","投掷":"throw","追踪":"track",
    "格斗（斗殴）":"fighting (brawl)","射击（手枪）":"firearms (handgun)","射击（步/霰）":"firearms (rifle/shotgun)",
    "驾驶（飞机）":"pilot (aircraft)","操作重型机械":"operate heavy machinery","母语":"language (own)","外语":"language (other)","科学":"science"
}

# ---------- DB Helpers ----------

def open_db(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def ensure_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    # skills_master
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
    # professions (needed by upsert_profession)
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

def debug_db(conn: sqlite3.Connection):
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

def upsert_skill(conn, *, key, zh, base, category):
    cur = conn.cursor
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO skills_master (key, zh, base, category)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            zh=excluded.zh,
            base=excluded.base,
            category=excluded.category;
    """, (key, zh, base, category))

def upsert_profession(conn, name_zh, credit_min, credit_max, attrs_formula, skills_raw):
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

def import_branch_skills(conn):
    """
    If your skills are in the Excel file, point to the correct sheet & columns here.
    This example expects columns: 技能Key, 中文名, Base, 类别
    """
    if not XLSX.exists():
        print(f"[WARN] Excel not found at: {XLSX.resolve()} — skipping skills import.")
        return
    try:
        df = pd.read_excel(XLSX, sheet_name="技能总表")  # <-- adjust to your actual sheet name
    except ValueError:
        print("[WARN] Sheet '技能总表' not found — skipping skills import.")
        return

    for _, r in df.iterrows():
        key = str(r.get("技能Key", "")).strip()
        if not key:
            continue
        zh = str(r.get("中文名", "")).strip()
        base = to_int_or_none(r.get("Base"))
        category = (str(r.get("类别")).strip() or None) if r.get("类别") is not None else None
        upsert_skill(conn, key=key, zh=zh, base=base, category=category)
    conn.commit()
    print(f"Imported {len(df)} rows into skills_master.")

def parse_credit_range(txt):
    if not isinstance(txt, str):
        return (None, None)
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", txt)
    if not m:
        return (None, None)
    return (int(m.group(1)), int(m.group(2)))

def import_professions(conn):
    if not XLSX.exists():
        print(f"[WARN] Excel not found at: {XLSX.resolve()} — skipping professions import.")
        return
    try:
        df = pd.read_excel(XLSX, sheet_name="职业列表")
    except ValueError:
        print("[WARN] Sheet '职业列表' not found — skipping professions import.")
        return

    count = 0
    for _, row in df.iterrows():
        name_zh = str(row.get("职业", "")).strip()
        if not name_zh or name_zh.startswith("选择职业序号为0"):
            continue
        cmin, cmax = parse_credit_range(str(row.get("信誉", "")).strip())
        attrs_formula = (str(row.get("职业属性", "")).strip() or None)
        skills_raw = (str(row.get("本职技能", "")).strip() or None)
        upsert_profession(conn, name_zh, cmin, cmax, attrs_formula, skills_raw)
        count += 1
    conn.commit()
    print(f"Imported/updated {count} professions.")

# ---------- Main ----------

def main():
    conn = open_db(DB_PATH)
    ensure_schema(conn)
    debug_db(conn)

    import_branch_skills(conn)   # reads from Excel if present
    import_professions(conn)     # reads from Excel if present

    conn.close()
    print("Done: imported skills_master and professions (where available).")

if __name__ == "__main__":
    main()
