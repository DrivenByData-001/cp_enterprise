"""Postgres persistence layer.

Phase 2 replaces SQLite with Postgres (Supabase in production) as the only
runtime persistence mechanism — see docs/14-phase2-postgres-architecture.md.
This module deliberately stays a thin wrapper (a connection pool + a
dict-row cursor context manager + a small file-based migration runner), not an
ORM: every route still writes its own SQL, schema-qualified against `jobber`.

The historical SQLite schema/scripts (`backend/scripts/migrate_phase0.py`,
`migrate_phase1.py`) are kept, untouched, for reproducibility (per the Phase 2
brief §3) — they operate on a standalone `.db` file and are irrelevant to the
running app from Phase 2 onward.
"""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from .config import database_url

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=database_url(),
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
    return _pool


def reset_pool() -> None:
    """Close and drop the pool so the next get_pool() rebuilds it against
    whatever DATABASE_URL currently resolves to. Used by tests, which point
    DATABASE_URL at a fresh throwaway database per session."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def db_cursor():
    """Checkout a pooled connection, yield a dict-row cursor, commit on
    success / rollback on exception, return the connection to the pool."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur


def run_migrations() -> list[str]:
    """Apply every backend/migrations/*.sql file not yet recorded in
    jobber.migration_history, in filename order. Each file is a plain SQL
    script (DDL, no bind parameters) executed as a single statement batch —
    Postgres runs semicolon-separated statements, including PL/pgSQL DO
    blocks, in one round trip when psycopg sends them as a simple query
    (i.e. with no parameters), so no manual statement-splitting is needed.
    `migrations/manual/` is NOT scanned — see its own file headers for why.

    Returns the list of filenames actually applied (empty if the database was
    already up to date) — callers/tests use this to confirm migrations ran.
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS jobber")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS jobber.migration_history "
                "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT filename FROM jobber.migration_history")
            applied = {row["filename"] for row in cur.fetchall()}

        pending = sorted(
            p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file() and p.name not in applied
        )
        newly_applied = []
        for path in pending:
            sql_text = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql_text)
                cur.execute(
                    "INSERT INTO jobber.migration_history (filename) VALUES (%s)",
                    (path.name,),
                )
            conn.commit()
            newly_applied.append(path.name)
        return newly_applied


def row_to_dict(row: dict, json_columns: tuple[str, ...] = ()) -> dict:
    """dict_row already returns a plain dict; this exists for the handful of
    columns still round-tripped as JSON text at the app boundary rather than
    relying on driver-level jsonb auto-adaptation (kept explicit and
    predictable — see docs/14). `embedding` is never present here: embeddings
    live only in jobber.d_embedding (see app/embeddings.py), never on the
    primary row, so there is nothing to strip."""
    d = dict(row)
    for key in json_columns:
        if isinstance(d.get(key), str):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, ValueError):
                pass
    return d


def to_json_param(value) -> Json | None:
    """Wrap a Python value for a jsonb column parameter. None stays None
    (NULL), not the JSON literal "null"."""
    return None if value is None else Json(value)


# --- document ---------------------------------------------------------------
#
# jobber.document is immutable once written (doc 11 §4.1) — `body_sha256` makes
# re-ingesting the same text idempotent. `provenance` must always be passed
# explicitly (docs/14 §4: the column's ADD COLUMN default was dropped on
# purpose) so every caller states, in code, whether this is a genuine capture
# or a reconstruction.

_VALID_PROVENANCE = {"original_capture", "legacy_extracted", "user_paste", "unspecified"}


def get_or_create_document(
    cur,
    *,
    kind: str,
    body: str,
    provenance: str,
    title: str | None = None,
    source: str | None = None,
    url: str | None = None,
    document_date: str | None = None,
    notes: str | None = None,
) -> tuple[int, bool]:
    """Returns (document_id, created). created=False means a document with
    this exact body already existed (re-ingestion of identical text) — the
    existing row's id is returned rather than a duplicate being made."""
    if provenance not in _VALID_PROVENANCE:
        raise ValueError(f"invalid provenance {provenance!r}, must be one of {_VALID_PROVENANCE}")
    body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()

    cur.execute("SELECT id FROM jobber.document WHERE body_sha256 = %s", (body_sha256,))
    existing = cur.fetchone()
    if existing:
        return existing["id"], False

    cur.execute(
        """
        INSERT INTO jobber.document (kind, title, body, body_sha256, source, url, document_date, provenance, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (body_sha256) DO NOTHING
        RETURNING id
        """,
        (kind, title, body, body_sha256, source, url, document_date, provenance, notes),
    )
    row = cur.fetchone()
    if row:
        return row["id"], True

    # Lost a race with a concurrent insert of the same content — fetch what won.
    cur.execute("SELECT id FROM jobber.document WHERE body_sha256 = %s", (body_sha256,))
    return cur.fetchone()["id"], False


# --- role_instance / role_skill_observation --------------------------------
#
# jobber.role_instance carries only canonical/provenance fields (doc 11 §4.3).
# Model-judgment scores and target-only decomposition fields that predate the
# capability model live in the sibling jobber.legacy_role_analysis (doc 11
# §10.2) — kept for compatibility, wired into nothing new. Callers still pass
# one flat dict (as the pre-Phase-2 app did); this function routes each key to
# its table so route code doesn't need to know about the split.

_ROLE_INSTANCE_COLUMNS = {
    "kind", "document_id", "archetype_concept_id", "title", "organisation", "location",
    "country", "remote_type", "employment_type", "seniority_level", "posting_date",
    "captured_at", "url", "summary", "career_track",
}
_LEGACY_ANALYSIS_JSON_COLUMNS = (
    "top_adjacent_roles", "typical_tasks", "skill_decomposition", "technical_subjects", "raw_json",
)
_LEGACY_ANALYSIS_COLUMNS = {
    "seniority_score", "complexity_score", "specialisation_score", "transferability_score",
    "market_demand_score", "rarity_score", "automation_risk_score", "top_adjacent_roles",
    "salary_min", "salary_max", "salary_estimate_min", "salary_estimate_max", "currency",
    "key_skills_summary", "description", "requirements", "responsibilities", "notes",
    "extraction_status", "extraction_notes", "raw_json",
    "typical_tasks", "skill_decomposition", "technical_subjects", "grounding_note",
    "feasibility_note", "is_plausible",
}


def upsert_role_instance(cur, role_id: int | None, columns: dict, skills: list[dict]) -> int:
    """Insert a new role_instance (+ legacy_role_analysis) row, or overwrite an
    existing one's columns + skill observations. `columns` is a flat dict
    covering both tables — see the split above. Skill observations are a full
    delete+reinsert on edit, same as the pre-Phase-2 behaviour: they are
    explicitly the legacy flat model (jobber.role_skill_observation), not the
    append-only requirement_claim model, so this is not the doc 11 §3.4
    violation that append-only claims must avoid.

    Takes a cursor rather than opening its own — callers that also create the
    role's document (get_or_create_document) or set its embedding need that
    write visible in the *same* transaction, not a separate pooled connection
    that can't see the other's uncommitted rows.
    """
    role_cols = {k: v for k, v in columns.items() if k in _ROLE_INSTANCE_COLUMNS}
    legacy_cols = {k: v for k, v in columns.items() if k in _LEGACY_ANALYSIS_COLUMNS}
    for key in _LEGACY_ANALYSIS_JSON_COLUMNS:
        if key in legacy_cols:
            legacy_cols[key] = to_json_param(legacy_cols[key])

    if role_id is None:
        cols = list(role_cols.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cur.execute(
            f"INSERT INTO jobber.role_instance ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
            [role_cols[c] for c in cols],
        )
        role_id = cur.fetchone()["id"]
    else:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise ValueError("role not found")
        if role_cols:
            set_clause = ", ".join(f"{c} = %s" for c in role_cols)
            cur.execute(
                f"UPDATE jobber.role_instance SET {set_clause} WHERE id = %s",
                [*role_cols.values(), role_id],
            )
        cur.execute("DELETE FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_id,))

    if legacy_cols:
        cols = list(legacy_cols.keys())
        insert_placeholders = ", ".join(["%s"] * len(cols))
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        cur.execute(
            f"""
            INSERT INTO jobber.legacy_role_analysis (role_instance_id, {', '.join(cols)})
            VALUES (%s, {insert_placeholders})
            ON CONFLICT (role_instance_id) DO UPDATE SET {update_clause}
            """,
            [role_id, *legacy_cols.values()],
        )

    # Lazy import: keeps db.py free of a module-load-time dependency on
    # embeddings.py, same reasoning as the pre-Phase-2 module.
    from .concept_linking import exact_match_concept_id, normalize_name

    for skill in skills:
        resolved_concept_id = exact_match_concept_id(cur, normalize_name(skill["name"]))
        cur.execute(
            "INSERT INTO jobber.role_skill_observation "
            "(role_instance_id, name, category, importance, requirement_type, resolved_concept_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
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


# Phase 0 is (still) single-person: docs/11 §4.3 models `person` for a future
# multi-subject extension; nothing creates more than one row. See docs/14 §6
# for why this table is not extended with a claims layer in Phase 2 — that is
# profile360's role now.
DEFAULT_PERSON_DISPLAY_NAME = "Ranga"


def get_or_create_person(cur, display_name: str = DEFAULT_PERSON_DISPLAY_NAME) -> int:
    cur.execute("SELECT id FROM jobber.person ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        "INSERT INTO jobber.person (display_name) VALUES (%s) RETURNING id",
        (display_name,),
    )
    return cur.fetchone()["id"]
