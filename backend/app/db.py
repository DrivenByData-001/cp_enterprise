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
import re
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
    DATABASE_URL at a fresh throwaway database per session — and also the
    explicit shutdown call every long-lived entrypoint (the FastAPI app's
    `shutdown` event in app/main.py, and every backend/scripts/*.py CLI that
    touches the database) makes before exiting.

    Closing explicitly here is what avoids psycopg_pool's own
    `ConnectionPool.__del__` finalizer path: left to run at interpreter
    shutdown (a bare process exit with no explicit close), it tries to join
    the pool's worker/scheduler threads within a 5s timeout and logs
    `couldn't stop thread 'pool-N-worker-*'/'pool-N-scheduler' within 5.0
    seconds` when that join doesn't land in time — harmless (the pool's
    connections are already done being used) but noisy. Calling this while
    the interpreter is still fully alive lets `ConnectionPool.close()` join
    those threads under normal scheduling instead."""
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

# Conservative, ASCII-only whitespace normalisation (source-aware ingest
# duplicate-detection cleanup): a PDF re-extraction of the same underlying
# posting routinely differs from an earlier capture only in line endings,
# repeated whitespace, or leading/trailing whitespace — never in wording or
# punctuation. Restricted to this explicit character class (not bare `\s`,
# which in Python is Unicode-aware and would drift from the equivalent SQL
# `regexp_replace` used to backfill historical rows in migration 0016) so
# Python and Postgres always agree on what counts as "the same" text.
_WHITESPACE_RE = re.compile(r"[ \t\r\n\f\v]+")


def normalize_document_text(text: str | None) -> str:
    """Collapse whitespace runs to a single space and trim. Never touches
    case, wording, or punctuation — deliberately conservative so two
    genuinely different postings are never conflated (see module docs on
    duplicate detection)."""
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def _normalized_content_hash(content_text: str | None) -> str | None:
    normalized = normalize_document_text(content_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None


def _best_matching_role(cur, *, kind: str, normalized: bool, value: str) -> dict | None:
    """One candidate document+role pair for a given hash value, or None.
    Joins straight through to role_instance rather than looking the document
    up first and its role second, so the ordering below can see both at
    once: a document that still has a role_instance pointing to it is always
    preferred over one that doesn't (`has_role DESC`), and only *then* by
    earliest captured_at/created_at as a tiebreaker.

    That ordering matters in exactly the case this cleanup exists for: once
    a duplicate role_instance is deleted, its now-orphaned document is still
    around (deleting a role never touches its immutable source), and a
    third, later upload of the same posting must still match the surviving,
    correctly-attributed role — not resurface the orphan just because it
    happens to have an earlier/NULL `captured_at`. `normalized` picks the
    hash column by a plain bool rather than interpolating a column name into
    SQL."""
    where_hash = "d.content_normalized_sha256 = %s" if normalized else "d.content_sha256 = %s"
    cur.execute(
        f"""
        SELECT d.id AS document_id, ri.id AS role_id, ri.title, ri.organisation, ri.posting_date
        FROM jobber.document d
        LEFT JOIN jobber.role_instance ri ON ri.document_id = d.id
        WHERE d.kind = %s AND {where_hash}
        ORDER BY (ri.id IS NOT NULL) DESC, d.captured_at ASC NULLS LAST, ri.created_at ASC NULLS LAST
        LIMIT 1
        """,
        (kind, value),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "document_id": str(row["document_id"]),
        "role_instance_id": str(row["role_id"]) if row["role_id"] else None,
        "title": row["title"],
        "organisation": row["organisation"],
        "posting_date": str(row["posting_date"]) if row["posting_date"] else None,
    }


def find_document_duplicates(cur, content_text: str, *, kind: str) -> dict:
    """The duplicate-detection signal for a capture that has not (yet, or
    ever) been persisted — usable both as a pre-persistence preflight check
    (routes/role_instances.py's duplicate-check endpoint) and, informationally,
    right after a document is actually created (`create_document` already
    excludes the just-inserted row from its own exact-match check, so calling
    this *before* create_document with the same cursor/transaction gives the
    same answer). Scoped to documents of the same `kind` only, so a job
    posting is never flagged against an unrelated narrative capture.

    `exact_duplicate` (identical raw content_sha256) always takes precedence
    over `possible_duplicate` (identical only after conservative whitespace
    normalisation) — a document that matches exactly is never also reported
    as merely possible. Either may be None. Purely a read — never persists
    anything, never blocks a subsequent create_document call."""
    exact = None
    if content_text:
        exact_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        exact = _best_matching_role(cur, kind=kind, normalized=False, value=exact_hash)

    possible = None
    if exact is None:
        normalized_hash = _normalized_content_hash(content_text)
        if normalized_hash:
            possible = _best_matching_role(cur, kind=kind, normalized=True, value=normalized_hash)

    return {"exact_duplicate": exact, "possible_duplicate": possible}


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
    content_normalized_sha256 = _normalized_content_hash(content_text)
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
             content_text, content_sha256, content_normalized_sha256, content_kind,
             provenance_quality, source_payload, notes)
        VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            source_key, kind, title, source, url, source_date,
            content_text, content_sha256, content_normalized_sha256, content_kind, provenance_quality,
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


def _normalize_top_adjacent_roles(value) -> list[str] | None:
    """Enforce the wire contract `top_adjacent_roles: string[] | null`
    (frontend's `Role` type, RoleDetail.tsx's `.join(', ')`) against
    whatever `legacy_analysis.top_adjacent_roles` actually holds. Production
    diagnosis found 23 historical/imported rows storing this as a *string*
    containing JSON (e.g. `"[\\"Deputy Head of Actuarial Function\\", ...]"`)
    rather than a genuine JSON array — one extra layer of encoding somewhere
    upstream. `role.top_adjacent_roles.join(', ')` crashes on a plain string
    (no `.join` method), so this must never hand the frontend anything but a
    real list of strings or null — never the raw malformed value. A value
    that cannot be safely interpreted as `string[]` becomes null rather than
    raising, so one bad historical row can never crash Role Detail again.
    Purely a read-time projection: the caller only ever has an in-memory
    dict here, so this can't and doesn't mutate the stored database row —
    the original legacy_analysis JSON is untouched in persistence."""
    if value is None:
        return None
    if isinstance(value, list):
        return value if all(isinstance(item, str) for item in value) else None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return parsed
        return None
    return None


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
    if "top_adjacent_roles" in role:
        role["top_adjacent_roles"] = _normalize_top_adjacent_roles(role["top_adjacent_roles"])
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
        canonical_concept_id = (skill["_resolved_concept_id"] if "_resolved_concept_id" in skill
                                else exact_match_concept_id(cur, normalize_name(skill["name"])))
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


def role_skills_display(cur, role_instance_id: str) -> dict:
    """Skills/requirements evidence for one role, split into two clearly
    separate lists rather than one merged one — Role Detail's own consumer of
    the same requirement_claim/role_skill_observation duality
    `role_requirements.py` solves for analysis, but with a different job: a
    *display* must never let a stale or unreviewed legacy signal outrank (or
    silently substitute for) a human-reviewed decision, and must never hide
    that a shown item was never reviewed at all.

    - `"skills"`: exactly `role_requirements.load_role_requirements`'s
      *claim*-sourced items (never its own role_skill_observation-fallback
      items — see below for why those move to `legacy_skills` instead). This
      is the same accepted-current-claim authority analysis already uses, so
      a grading (required -> preferred, same concept) or a remap (Python ->
      SQL) shows up here exactly as accepted, never as whatever a stale
      `role_skill_observation` row still says — that row is either superseded
      display-wise (the concept now has a reviewed claim) or, if remapped
      away, excluded by `vetoed_concept_ids` below like any other curator
      rejection.
    - `"legacy_skills"`: every role_skill_observation NOT already represented
      in `"skills"` (by resolved_concept_id) and not vetoed. A pre-curation-
      gate role with no claim history at all therefore shows *everything*
      here, clearly labelled as legacy/unreviewed rather than mixed into a
      list implying human review — a real change from this function's
      previous "observations flow straight into the one skills list"
      behaviour, and the point of this fix: legacy extraction is real
      evidence worth showing, but showing it as if it had been reviewed
      overstated it.

    Never merged at the item level — a concept with a current accepted claim
    never also appears (stale) in `legacy_skills`, and `vetoed_concept_ids`
    (rejected, or corrected away with nothing current left behind — see that
    function's own docstring) is applied to `legacy_skills` for the same
    reason `role_requirements.py`'s own fallback branch applies it: curator
    authority must never be contradicted by a legacy chip that read the
    rejection/remap never happened."""
    # Lazy import: keeps db.py free of a module-load-time dependency on
    # role_requirements.py (same reasoning as concept_linking elsewhere in
    # this module).
    from .role_requirements import load_role_requirements, vetoed_concept_ids

    canonical = load_role_requirements(cur, role_instance_id)
    skills = [
        {
            "name": item["canonical_name"],
            "category": item["type_code"],
            "importance": item["importance"],
            "requirement_type": item["requirement_type"],
            "resolved_concept_id": item["concept_id"],
        }
        for item in canonical
        if item["source"] == "claim"
    ]
    reviewed_concept_ids = {s["resolved_concept_id"] for s in skills}
    vetoed = vetoed_concept_ids(cur, role_instance_id)

    cur.execute(
        "SELECT surface_form AS name, category, importance, requirement_type, canonical_concept_id AS resolved_concept_id "
        "FROM jobber.role_skill_observation WHERE role_instance_id = %s ORDER BY surface_form",
        (role_instance_id,),
    )
    legacy_skills = [
        {**s, "resolved_concept_id": str(s["resolved_concept_id"]) if s["resolved_concept_id"] else None}
        for s in cur.fetchall()
    ]
    legacy_skills = [
        s for s in legacy_skills
        if s["resolved_concept_id"] not in reviewed_concept_ids and s["resolved_concept_id"] not in vetoed
    ]
    return {"skills": skills, "legacy_skills": legacy_skills}


def build_role_view(cur, role_id: str) -> dict | None:
    """The full role_instance projection `GET /api/roles/{id}` returns, and
    what Day-in-the-Life generation (app/role_context.py) reads its evidence
    from: the flattened row, plus `skills`/`legacy_skills` (role_skills_display
    — reviewed requirements and legacy extracted skills, kept separate rather
    than one merged fallback list), plus `source_document_text` (with
    a fallback to the linked document's own verbatim text whenever
    description/requirements/responsibilities are all empty — the other half
    of the 2026 Role Detail regression fix). `_source_document_id` is an
    internal-only field (routes/roles.py pops it before returning the API
    response) so role_context.py can record provenance without a second
    query. None if role_id doesn't exist."""
    cur.execute(
        "SELECT ri.*, d.url AS url, d.captured_at AS captured_at, "
        "d.id AS document_row_id, d.content_text AS document_content_text "
        "FROM jobber.role_instance ri LEFT JOIN jobber.document d ON d.id = ri.document_id "
        "WHERE ri.id = %s",
        (role_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    document_content_text = row["document_content_text"]
    document_id = str(row["document_row_id"]) if row["document_row_id"] else None
    role = flatten_role_instance(
        {k: v for k, v in dict(row).items() if k not in ("document_content_text", "document_row_id")}
    )
    skills_display = role_skills_display(cur, role_id)
    role["skills"] = skills_display["skills"]
    role["legacy_skills"] = skills_display["legacy_skills"]
    role["source_document_text"] = (
        document_content_text
        if document_content_text and not (role.get("description") or role.get("requirements") or role.get("responsibilities"))
        else None
    )
    role["_source_document_id"] = document_id

    # Lazy import: same reasoning as concept_linking above — keeps db.py free
    # of a module-load-time dependency on role_requirements.py. Powers Role
    # Detail's "Requirements review pending" indicator (never a separate
    # round-trip just to know whether review is complete).
    from .role_requirements import load_requirement_review_summary

    role["requirement_review"] = load_requirement_review_summary(cur, role_id)

    # Build §14: Role/Target Detail shows the reviewed archetype assignment
    # (or an explicit unclassified state) alongside the rest of the role. One
    # extra query on a view that already runs several, never a separate
    # round trip from the browser — and never an AI call. Compensation is
    # deliberately NOT resolved here: it has its own endpoint
    # (GET /api/role-instances/{id}/compensation) so a list view that reuses
    # this projection never pays for benchmark resolution it will not show.
    from .archetype_classification import role_archetype_summary

    role["archetype"] = role_archetype_summary(cur, role_id)
    return role


# --- role_instance deletion --------------------------------------------------
#
# jobber.role_skill_observation, d_role_fit, role_context_enrichment, and
# compensation_observation all reference role_instance with ON DELETE
# CASCADE at the database level (confirmed directly against the live
# schema) — a plain DELETE FROM role_instance already removes those.
# requirement_claim also cascades on role_instance_id, but is handled
# explicitly anyway (see below). jobber.extraction_run does NOT cascade: its
# role_instance_id/result_role_instance_id foreign keys are NO ACTION, so a
# naive DELETE FROM role_instance fails outright whenever any extraction was
# ever attempted against that role — which is the normal case for anything
# that went through source-aware ingest + extract-requirements. And
# extraction_run itself is referenced (NO ACTION) by requirement_claim and
# concept_proposal, which blocks *it* from being cleaned up in turn unless
# those are handled first. See delete_role_instance's own docstring for the
# full chain and why each reference needs a different treatment.

_ROLE_METADATA_COLUMNS = {
    "title", "organisation", "location", "country", "remote_type",
    "employment_type", "seniority_level", "posting_date",
}


def delete_role_instance(cur, role_id: str) -> bool:
    """Deletes one jobber.role_instance row and everything that exists only
    to describe it. extraction_run needs two different treatments, not one:

    - `result_role_instance_id` is an *output* linkage (which role a
      document-subject job_posting_extract run produced) — the row is about
      the document, still meaningful with no role behind it, so it is kept
      and just nulled.
    - `role_instance_id` is the *subject* reference on a role-subject run
      (requirement_extract, role_metadata_enrich, role_context_generate):
      extraction_run's own CHECK constraint requires this to be non-null
      whenever subject_type='role_instance', so it cannot be nulled without
      violating that invariant — a row that exists to describe an attempt
      *about this role* has no honest form once the role is gone, so these
      rows are deleted along with it (the same "you lose the derived history
      tied to a deleted duplicate" the user already accepts for its
      requirement_claim rows, which cascade the same way).

    Deleting those role-subject extraction_run rows in turn requires two
    things to happen first, in this order, both confirmed against the live
    schema (not just the pre-Phase-2 migration set, which turned out to
    still be missing one):

    1. `jobber.requirement_claim.extraction_run_id` (NO ACTION) still points
       at them for as long as the claim rows exist — and those rows only
       cascade-delete once role_instance itself is deleted, which can't
       happen yet (extraction_run.role_instance_id is what's still blocking
       that). So requirement_claim rows for this role are deleted explicitly,
       ahead of the role_instance DELETE that would otherwise have cascaded
       them anyway.
    2. `jobber.concept_proposal.extraction_run_id` (also NO ACTION) can point
       at a role-subject run too — a proposal is about a *vocabulary term*,
       not this role, and stays a legitimate curation candidate regardless
       of what happens to the role that first surfaced it, so its rows are
       kept, only the now-dangling run reference is nulled.
    3. `jobber.concept_proposal_occurrence.extraction_run_id` (also NO
       ACTION) can point at one too, same as concept_proposal above — but
       unlike concept_proposal, its own `role_instance_id` is NOT NULL and
       `ON DELETE CASCADE`, so (unlike step 2) there is no row left to keep:
       it is *about* this role's occurrence of the proposal, and the
       role_instance DELETE a few lines down already cascades it away. Only
       the run reference needs nulling here, and only so that cascade can
       still happen — the same dangling-NO-ACTION-reference problem as
       requirement_claim's above, just one step further removed.

    Returns False (nothing deleted) if role_id doesn't exist."""
    cur.execute(
        """
        UPDATE jobber.concept_proposal SET extraction_run_id = NULL
        WHERE extraction_run_id IN (
            SELECT id FROM jobber.extraction_run WHERE subject_type = 'role_instance' AND role_instance_id = %s
        )
        """,
        (role_id,),
    )
    cur.execute(
        """
        UPDATE jobber.concept_proposal_occurrence SET extraction_run_id = NULL
        WHERE extraction_run_id IN (
            SELECT id FROM jobber.extraction_run WHERE subject_type = 'role_instance' AND role_instance_id = %s
        )
        """,
        (role_id,),
    )
    cur.execute("DELETE FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
    cur.execute(
        "DELETE FROM jobber.extraction_run WHERE subject_type = 'role_instance' AND role_instance_id = %s",
        (role_id,),
    )
    cur.execute(
        "UPDATE jobber.extraction_run SET result_role_instance_id = NULL WHERE result_role_instance_id = %s",
        (role_id,),
    )
    cur.execute("DELETE FROM jobber.role_instance WHERE id = %s", (role_id,))
    deleted = cur.rowcount > 0
    if deleted:
        cur.execute(
            "DELETE FROM jobber.d_embedding WHERE owner_kind = 'role_instance' AND owner_id = %s",
            (role_id,),
        )
    return deleted


def update_role_metadata(cur, role_id: str, fields: dict) -> dict | None:
    """Partial metadata-only update for a role_instance — the source-aware
    counterpart to the legacy full-JobPostingImport overwrite
    (`routes.roles.update_role` / `upsert_role_instance`): touches only the
    given columns (restricted to `_ROLE_METADATA_COLUMNS` — title/
    organisation/location/country/remote_type/employment_type/
    seniority_level/posting_date), never skills, never the linked document,
    and never clears a column that wasn't supplied. Used both by manual Edit
    on a role with no `raw_json` and by 'Accept metadata' after a proposed
    enrichment review (docs: source-aware ingest cleanup, problems #5/#7).
    Returns the updated flattened role, or None if role_id doesn't exist."""
    updates = {k: v for k, v in fields.items() if k in _ROLE_METADATA_COLUMNS}
    if updates:
        set_clause = ", ".join(f"{c} = %s" for c in updates)
        cur.execute(
            f"UPDATE jobber.role_instance SET {set_clause}, updated_at = now() WHERE id = %s",
            [*updates.values(), role_id],
        )
    return build_role_view(cur, role_id)
