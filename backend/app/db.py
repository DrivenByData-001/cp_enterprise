import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "career_nav.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

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


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("top_adjacent_roles",):
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
    if "embedding" in d:
        d.pop("embedding", None)  # never ship raw vectors to the client
    return d
