import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "career_nav.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    node_type TEXT NOT NULL DEFAULT 'posting',

    title TEXT NOT NULL,
    organisation TEXT,
    location TEXT,
    country TEXT,
    remote_type TEXT,
    employment_type TEXT,
    seniority_level TEXT,

    posting_date TEXT,
    captured_at TEXT,
    source TEXT,
    url TEXT,

    salary_min REAL,
    salary_max REAL,
    salary_estimate_min REAL,
    salary_estimate_max REAL,
    currency TEXT,

    description TEXT,
    requirements TEXT,
    responsibilities TEXT,
    summary TEXT,
    key_skills_summary TEXT,
    notes TEXT,

    career_track TEXT,
    seniority_score REAL,
    complexity_score REAL,
    specialisation_score REAL,
    transferability_score REAL,
    market_demand_score REAL,
    rarity_score REAL,
    automation_risk_score REAL,
    top_adjacent_roles TEXT,

    extraction_status TEXT,
    extraction_notes TEXT,
    raw_json TEXT NOT NULL,

    embedding TEXT,
    embedding_model TEXT,
    embedded_at TEXT,

    -- target-role fields (node_type = 'target_real' | 'target_imagined')
    typical_tasks TEXT,
    skill_decomposition TEXT,
    technical_subjects TEXT,
    grounding_note TEXT,
    feasibility_note TEXT,
    is_plausible INTEGER,

    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_role_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_role_id INTEGER NOT NULL REFERENCES job_roles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT,
    importance INTEGER,
    requirement_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_role_skills_role ON job_role_skills(job_role_id);
CREATE INDEX IF NOT EXISTS idx_job_role_skills_name ON job_role_skills(name);

CREATE TABLE IF NOT EXISTS profile_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    narrative_text TEXT NOT NULL,
    embedding TEXT,
    embedding_model TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


# Columns added after the initial v1 schema. init_db() adds any that are missing
# from an existing database on disk, so upgrading doesn't require deleting local data.
_MIGRATIONS = {
    "job_roles": {
        "node_type": "TEXT NOT NULL DEFAULT 'posting'",
        "typical_tasks": "TEXT",
        "skill_decomposition": "TEXT",
        "technical_subjects": "TEXT",
        "grounding_note": "TEXT",
        "feasibility_note": "TEXT",
        "is_plausible": "INTEGER",
    }
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


def upsert_job_role(role_id: int | None, columns: dict, skills: list[dict]) -> int:
    """Insert a new job_roles row, or overwrite an existing one's columns + skills.

    Shared by postings and targets (same table, different column subsets) and by
    both import (role_id=None) and edit (role_id set) — editing an existing
    posting/target with a fresh paste is a full overwrite of these columns, not a
    merge, so re-pasting a more complete extraction always wins.
    """
    with db_cursor() as cur:
        if role_id is None:
            cols = list(columns.keys())
            placeholders = ", ".join(["?"] * len(cols))
            cur.execute(
                f"INSERT INTO job_roles ({', '.join(cols)}) VALUES ({placeholders})",
                [columns[c] for c in cols],
            )
            role_id = cur.lastrowid
        else:
            cur.execute("SELECT id FROM job_roles WHERE id = ?", (role_id,))
            if not cur.fetchone():
                raise ValueError("role not found")
            set_clause = ", ".join(f"{c} = ?" for c in columns)
            cur.execute(
                f"UPDATE job_roles SET {set_clause} WHERE id = ?",
                [*columns.values(), role_id],
            )
            cur.execute("DELETE FROM job_role_skills WHERE job_role_id = ?", (role_id,))

        for skill in skills:
            cur.execute(
                "INSERT INTO job_role_skills (job_role_id, name, category, importance, requirement_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    role_id,
                    skill["name"],
                    skill.get("category"),
                    skill.get("importance"),
                    skill.get("requirement_type"),
                ),
            )
    return role_id


_JSON_COLUMNS = ("top_adjacent_roles", "typical_tasks", "skill_decomposition", "technical_subjects")


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in _JSON_COLUMNS:
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, json.JSONDecodeError):
                pass
    if "raw_json" in d and d["raw_json"]:
        try:
            d["raw_json"] = json.loads(d["raw_json"])
        except (TypeError, json.JSONDecodeError):
            pass
    if "is_plausible" in d and d["is_plausible"] is not None:
        d["is_plausible"] = bool(d["is_plausible"])
    if "embedding" in d:
        d.pop("embedding", None)  # never ship raw vectors to the client
    return d
