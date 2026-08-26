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

-- Phase 0 (docs/11-capability-model-design.md §11) — episodes and documents.
-- Additive only: nothing above this line is altered or dropped by Phase 0.

CREATE TABLE IF NOT EXISTS document (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT,
    body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL UNIQUE,
    source TEXT,
    url TEXT,
    document_date TEXT,
    ingested_at TEXT NOT NULL,
    superseded_by INTEGER REFERENCES document(id),
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_document_kind ON document(kind);

CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES person(id),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    organisation TEXT,
    start_date TEXT,
    end_date TEXT,
    date_precision TEXT NOT NULL DEFAULT 'month',
    parent_episode_id INTEGER REFERENCES episode(id),
    domain_hint TEXT,
    context_note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episode_person ON episode(person_id, start_date);

CREATE TABLE IF NOT EXISTS episode_document (
    episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES document(id),
    PRIMARY KEY (episode_id, document_id)
);

-- Not used by any Phase 0 logic; created now (dormant) so Phase 1's extraction
-- pipeline needs no further schema migration. See docs/11 §11 Phase 0 build list.
CREATE TABLE IF NOT EXISTS vocabulary_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    concept_count INTEGER NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS extraction_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES document(id),
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    vocabulary_version_id INTEGER NOT NULL REFERENCES vocabulary_version(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    notes TEXT
);

-- Phase 1 (docs/11-capability-model-design.md §11) — vocabulary.
-- Additive only: nothing above this line is altered or dropped by Phase 1.

CREATE TABLE IF NOT EXISTS concept_type (
    code       TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    definition TEXT NOT NULL,
    is_atom    INTEGER NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS concept (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type_code      TEXT NOT NULL REFERENCES concept_type(code),
    canonical_name TEXT NOT NULL,
    definition     TEXT,
    status         TEXT NOT NULL DEFAULT 'proposed',
    merged_into    INTEGER REFERENCES concept(id),
    origin         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    reviewed_at    TEXT,
    UNIQUE (type_code, canonical_name)
);
CREATE INDEX IF NOT EXISTS idx_concept_type ON concept(type_code, status);

CREATE TABLE IF NOT EXISTS concept_alias (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    origin     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (alias, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_concept_alias_alias ON concept_alias(alias);

CREATE TABLE IF NOT EXISTS concept_xref (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    scheme     TEXT NOT NULL,
    code       TEXT NOT NULL,
    label      TEXT,
    UNIQUE (concept_id, scheme, code)
);

CREATE TABLE IF NOT EXISTS concept_edge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_concept_id INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    to_concept_id   INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL,
    necessity       TEXT,
    weight          REAL,
    note            TEXT,
    origin          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'proposed',
    created_at      TEXT NOT NULL,
    UNIQUE (from_concept_id, to_concept_id, relation)
);

CREATE TABLE IF NOT EXISTS concept_edge_rule (
    relation  TEXT NOT NULL,
    from_type TEXT NOT NULL REFERENCES concept_type(code),
    to_type   TEXT NOT NULL REFERENCES concept_type(code),
    PRIMARY KEY (relation, from_type, to_type)
);

-- status: pending | accepted_new | accepted_alias | rejected | deferred
-- ('deferred' added beyond docs/11 §8.1's DDL comment, which omitted it despite
-- the prose requiring a fourth "defer" action — see Phase 1 build notes in §11.)
CREATE TABLE IF NOT EXISTS concept_proposal (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    surface_form         TEXT NOT NULL,
    suggested_type       TEXT REFERENCES concept_type(code),
    suggested_definition TEXT,
    nearest_concept_id   INTEGER REFERENCES concept(id),
    nearest_similarity   REAL,
    occurrence_count     INTEGER NOT NULL DEFAULT 1,
    document_id          INTEGER REFERENCES document(id),
    evidence_span        TEXT,
    extraction_run_id    INTEGER REFERENCES extraction_run(id),
    status               TEXT NOT NULL DEFAULT 'pending',
    resolved_concept_id  INTEGER REFERENCES concept(id),
    resolved_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_concept_proposal_surface ON concept_proposal(surface_form, status);

CREATE TABLE IF NOT EXISTS d_embedding (
    owner_kind  TEXT NOT NULL,
    owner_id    INTEGER NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (owner_kind, owner_id, model)
);

-- Gold-set scaffolding (§9.2) — dormant in Phase 1, same precedent as
-- vocabulary_version/extraction_run in Phase 0 (no labelling UI yet; this just
-- means Phase 2 needs no further schema migration for evaluation).
CREATE TABLE IF NOT EXISTS gold_document (
    document_id INTEGER PRIMARY KEY REFERENCES document(id),
    split       TEXT NOT NULL,
    stratum     TEXT NOT NULL,
    labelled_at TEXT NOT NULL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS gold_claim (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id      INTEGER NOT NULL REFERENCES gold_document(document_id),
    subject_hint     TEXT,
    concept_id       INTEGER NOT NULL REFERENCES concept(id),
    relation         TEXT NOT NULL,
    depth            TEXT,
    autonomy         TEXT,
    requirement_type TEXT,
    evidence_span    TEXT NOT NULL,
    is_core          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS eval_run (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    split                 TEXT NOT NULL,
    task                  TEXT NOT NULL,
    model                 TEXT NOT NULL,
    prompt_version        TEXT NOT NULL,
    vocabulary_version_id INTEGER NOT NULL REFERENCES vocabulary_version(id),
    precision_micro       REAL,
    recall_micro          REAL,
    f1_micro              REAL,
    span_validity         REAL,
    proposals_per_doc     REAL,
    modifier_accuracy     REAL,
    run_at                TEXT NOT NULL,
    notes                 TEXT
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
    },
    "job_role_skills": {
        "resolved_concept_id": "INTEGER REFERENCES concept(id)",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


# Phase 1 seed vocabulary (docs/11 §2.3 concept types, §4.2.4 edge grammar). Both
# tables use natural keys (concept_type.code, the concept_edge_rule triple), so
# seeding is a plain INSERT OR IGNORE — no SELECT-then-INSERT dance like
# get_or_create_person needs for a surrogate-keyed singleton.
_CONCEPT_TYPES = [
    ("knowledge", "Knowledge", "A body of theory one can know", 1, 1),
    ("method", "Method", "A named technique one can apply", 1, 2),
    ("tool", "Tool", "A named artefact one operates", 1, 3),
    ("function", "Function", "A business activity", 1, 4),
    ("domain", "Domain", "A sector or market context", 1, 5),
    ("product", "Product", "A thing sold or managed", 1, 6),
    ("regulation", "Regulation", "A named regulatory or reporting regime", 1, 7),
    ("credential", "Credential", "An externally-issued, verifiable qualification", 1, 8),
    ("capability", "Capability", "Something a person can do, at economic scale", 0, 9),
    ("role_archetype", "Role archetype", "A recurring role shape across many postings", 0, 10),
]

_ATOMIC_TYPES = [t[0] for t in _CONCEPT_TYPES if t[3] == 1]

# docs/11 §4.2.4 "Seed grammar (Phase 1-3 subset)". broader_than is seeded within
# each atomic type only — not applied to the two composite types, which are
# differentiated by their edges rather than a taxonomy of their own.
_CONCEPT_EDGE_RULES = (
    [("component_of", t, "capability") for t in _ATOMIC_TYPES]
    + [("demands", "role_archetype", "capability")]
    + [("broader_than", t, t) for t in _ATOMIC_TYPES]
    + [("governs", "regulation", "function")]
    + [("applies_in", "method", "domain"), ("applies_in", "method", "product")]
    + [("senior_to", "role_archetype", "role_archetype")]
)


def seed_vocabulary(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO concept_type (code, label, definition, is_atom, sort_order) "
        "VALUES (?, ?, ?, ?, ?)",
        _CONCEPT_TYPES,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO concept_edge_rule (relation, from_type, to_type) VALUES (?, ?, ?)",
        _CONCEPT_EDGE_RULES,
    )


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    seed_vocabulary(conn)
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

        # Lazy import: keeps db.py free of a module-load-time dependency on
        # embeddings.py (see concept_linking.py's docstring). Exact-match only —
        # cheap enough to run inline on every skill insert. Without this, editing
        # a posting (which deletes+reinserts job_role_skills, above) would
        # silently revert already-resolved skills back to unresolved.
        from .concept_linking import exact_match_concept_id, normalize_name

        for skill in skills:
            resolved_concept_id = exact_match_concept_id(cur, normalize_name(skill["name"]))
            cur.execute(
                "INSERT INTO job_role_skills (job_role_id, name, category, importance, requirement_type, resolved_concept_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    role_id,
                    skill["name"],
                    skill.get("category"),
                    skill.get("importance"),
                    skill.get("requirement_type"),
                    resolved_concept_id,
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


# Phase 0 is single-person: docs/11 §4.3 models `person` for a future multi-subject
# extension, but nothing today creates more than one row. This is the one seam
# both the migration script and the episodes routes use to find/seed it.
DEFAULT_PERSON_DISPLAY_NAME = "Ranga"


def get_or_create_person(cur: sqlite3.Cursor, display_name: str = DEFAULT_PERSON_DISPLAY_NAME) -> int:
    cur.execute("SELECT id FROM person ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        "INSERT INTO person (display_name, created_at) VALUES (?, datetime('now'))",
        (display_name,),
    )
    return cur.lastrowid
