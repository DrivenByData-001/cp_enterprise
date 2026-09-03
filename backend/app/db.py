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

Every id in this module is a UUID (as a `str`), matching the live production
`jobber` schema confirmed by direct inspection on 2026-09-03 — see docs/14 §3.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

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

    0001_live_schema_preflight.sql asserts the live jobber/profile360 schema
    matches what every later migration and every runtime query depends on
    (UUID entity ids, specific column names) and raises a clear error if not
    — see that file and docs/14 §2/§3. Against a from-scratch Postgres with
    none of that baseline, run backend/scripts/local_baseline.sql first
    (backend/tests/conftest.py does this automatically for tests).

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
# jobber.document's real identity column is `source_key` (unique), not a
# content hash: production intentionally has no uniqueness constraint on
# `content_sha256` — two distinct real postings were found, during the
# original SQLite migration, to share identical reconstructed text. So this
# is `create_document`, not `get_or_create_document`: every call inserts a
# new, immutable row. `content_sha256` is still computed and checked, but
# only to report a possible duplicate — never to collapse two captures into
# one. See docs/14 §4.
#
# `provenance_quality` must always be passed explicitly (no server-side
# default is relied on here) so every caller states, in code, whether this is
# a genuine capture or a reconstruction.

VALID_PROVENANCE_QUALITY = {"original", "legacy_extracted", "reconstructed", "unknown"}


def create_document(
    cur,
    *,
    kind: str,
    content_text: str,
    provenance_quality: str,
    title: str | None = None,
    source: str | None = None,
    url: str | None = None,
    source_date: str | None = None,
    content_kind: str = "source",
    source_payload: dict | None = None,
    notes: str | None = None,
    source_key: str | None = None,
) -> tuple[str, str | None]:
    """Returns (new_document_id, duplicate_of_document_id). The second is
    populated, informationally only, when an existing document already has
    the same content_sha256 — callers may surface a duplicate warning, but
    the insert always happens regardless (see module docstring above)."""
    if provenance_quality not in VALID_PROVENANCE_QUALITY:
        raise ValueError(
            f"invalid provenance_quality {provenance_quality!r}, must be one of {VALID_PROVENANCE_QUALITY}"
        )
    content_sha256 = hashlib.sha256(content_text.encode("utf-8")).hexdigest() if content_text else None
    source_key = source_key or f"{kind}:{uuid.uuid4()}"

    duplicate_of = None
    if content_sha256:
        cur.execute("SELECT id FROM jobber.document WHERE content_sha256 = %s LIMIT 1", (content_sha256,))
        existing = cur.fetchone()
        if existing:
            duplicate_of = str(existing["id"])

    cur.execute(
        """
        INSERT INTO jobber.document
            (source_key, kind, title, source, url, source_date, captured_at,
             content_text, content_sha256, content_kind, provenance_quality, source_payload, notes)
        VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            source_key, kind, title, source, url, source_date,
            content_text, content_sha256, content_kind, provenance_quality,
            to_json_param(source_payload or {}), notes,
        ),
    )
    new_id = str(cur.fetchone()["id"])
    return new_id, duplicate_of


# --- role_instance / role_skill_observation --------------------------------
#
# jobber.role_instance already carries the full migrated legacy role detail
# directly (description/requirements/responsibilities/summary/career_track/
# salary fields/legacy_scores/legacy_analysis/extraction_status/
# extraction_notes) — there is no separate compatibility table to split
# columns across; every one of these is a plain column on the live table.
# `instance_type` replaces this app's old `kind` three/four-way enum
# (posting -> observed_posting, target_real/target_imagined ->
# user_defined_target with `target_basis` distinguishing them,
# synthetic_reference unchanged).

INSTANCE_TYPE_MAP = {
    "posting": "observed_posting",
    "target_real": "user_defined_target",
    "target_imagined": "user_defined_target",
    "synthetic_reference": "synthetic_reference",
}
_TARGET_BASIS_MAP = {"target_real": "real_role", "target_imagined": "imagined"}

_ROLE_INSTANCE_COLUMNS = {
    "instance_type", "target_basis", "document_id", "archetype_concept_id",
    "title", "organisation", "location", "country", "remote_type", "employment_type",
    "seniority_level", "posting_date", "salary_min", "salary_max",
    "salary_estimate_min", "salary_estimate_max", "currency",
    "description", "requirements", "responsibilities", "summary", "career_track",
    "legacy_scores", "legacy_analysis", "extraction_status", "extraction_notes", "status",
}
_ROLE_INSTANCE_JSON_COLUMNS = ("legacy_scores", "legacy_analysis")


def app_kind_to_instance_type(kind: str) -> tuple[str, str | None]:
    """Maps this app's kind vocabulary (posting | target_real |
    target_imagined | synthetic_reference) onto production's
    (instance_type, target_basis) pair. Raises on an unrecognised kind rather
    than silently defaulting."""
    if kind not in INSTANCE_TYPE_MAP:
        raise ValueError(f"unknown role kind {kind!r}, must be one of {sorted(INSTANCE_TYPE_MAP)}")
    return INSTANCE_TYPE_MAP[kind], _TARGET_BASIS_MAP.get(kind)


def instance_type_to_app_kind(instance_type: str, target_basis: str | None) -> str:
    """Inverse of `app_kind_to_instance_type` — what the frontend still
    calls `node_type` (unchanged there; only the backend's storage model
    changed). Defaults an unrecognised/missing target_basis on a
    user_defined_target to 'target_real' rather than raising, since existing
    production rows predate `target_basis` and legitimately have it NULL."""
    if instance_type == "observed_posting":
        return "posting"
    if instance_type == "synthetic_reference":
        return "synthetic_reference"
    if instance_type == "user_defined_target":
        return "target_imagined" if target_basis == "imagined" else "target_real"
    raise ValueError(f"unknown instance_type {instance_type!r}")


def flatten_role_instance(role: dict) -> dict:
    """A `SELECT *` off jobber.role_instance packs everything this app used
    to keep as individual flat columns (scores, top_adjacent_roles,
    typical_tasks, ...) into two JSONB columns instead
    (legacy_scores/legacy_analysis — production's real shape, docs/14 §5).
    This unpacks them back to the flat top-level keys the frontend's `Role`
    type already expects, so the wire contract doesn't change even though
    storage did. `id` is stringified (UUID) and `node_type` is derived from
    instance_type/target_basis, same as before."""
    role = dict(role)
    role["id"] = str(role["id"])
    role["node_type"] = instance_type_to_app_kind(role["instance_type"], role.get("target_basis"))
    for jsonb_col in ("legacy_scores", "legacy_analysis"):
        nested = role.pop(jsonb_col, None) or {}
        for k, v in nested.items():
            role.setdefault(k, v)
    return role


def upsert_role_instance(cur, role_id: str | None, columns: dict, skills: list[dict]) -> str:
    """Insert a new role_instance row, or overwrite an existing one's columns
    + skill observations. `columns` must already use production column names
    (instance_type/target_basis, not kind) — see `app_kind_to_instance_type`.
    Skill observations are a full delete+reinsert on edit, same as the
    pre-Phase-2 behaviour: they are explicitly the legacy flat model
    (jobber.role_skill_observation), not the append-only requirement_claim
    model, so this is not the doc 11 §3.4 violation that append-only claims
    must avoid.

    Takes a cursor rather than opening its own — callers that also create the
    role's document (create_document) or set its embedding need that write
    visible in the *same* transaction, not a separate pooled connection that
    can't see the other's uncommitted rows.
    """
    role_cols = {k: v for k, v in columns.items() if k in _ROLE_INSTANCE_COLUMNS}
    for key in _ROLE_INSTANCE_JSON_COLUMNS:
        if key in role_cols:
            role_cols[key] = to_json_param(role_cols[key])

    if role_id is None:
        cols = list(role_cols.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cur.execute(
            f"INSERT INTO jobber.role_instance ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
            [role_cols[c] for c in cols],
        )
        role_id = str(cur.fetchone()["id"])
    else:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise ValueError("role not found")
        if role_cols:
            set_clause = ", ".join(f"{c} = %s" for c in role_cols)
            cur.execute(
                f"UPDATE jobber.role_instance SET {set_clause}, updated_at = now() WHERE id = %s",
                [*role_cols.values(), role_id],
            )
        cur.execute("DELETE FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_id,))

    # Lazy import: keeps db.py free of a module-load-time dependency on
    # embeddings.py, same reasoning as the pre-Phase-2 module.
    from .concept_linking import exact_match_concept_id, normalize_name

    for skill in skills:
        canonical_concept_id = exact_match_concept_id(cur, normalize_name(skill["name"]))
        cur.execute(
            "INSERT INTO jobber.role_skill_observation "
            "(role_instance_id, surface_form, category, importance, requirement_type, observation_basis, canonical_concept_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                role_id,
                skill["name"],
                skill.get("category"),
                skill.get("importance"),
                skill.get("requirement_type"),
                "app_capture",  # distinct from 'legacy_extraction', reserved for the original migrated 327 rows
                canonical_concept_id,
            ),
        )
    return role_id
