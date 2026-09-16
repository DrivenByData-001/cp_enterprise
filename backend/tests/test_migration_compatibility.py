"""Proves the Phase 2 production-schema reconciliation's central safety claim:
every migration in backend/migrations/ applies cleanly on top of the live
`jobber` baseline (modelled locally by backend/scripts/local_baseline.sql),
and refuses to run at all — loudly, with a clear message — against a
database that never had that baseline. Every other test in this suite is
already an implicit proof of the first half (the session fixture in
conftest.py runs local_baseline.sql + all migrations before any test body
executes); this file makes both halves explicit and independently checkable.
"""

import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app import db as db_module
from app.config import test_database_url as get_test_database_url

EXPECTED_MIGRATIONS = {
    "0001_live_schema_preflight.sql",
    "0002_vocabulary_extensions.sql",
    "0003_requirement_claims_and_runs.sql",
    "0004_profile360_mapping.sql",
    "0005_preferences.sql",
    "0006_phase3_capability_derivations.sql",
    "0007_document_processing_lifecycle.sql",
    "0008_role_skill_observation_basis_check.sql",
    "0009_consolidation_indexes_and_bootstrap.sql",
}


def test_all_migrations_applied(client):
    """The session fixture already ran migrations against the local_baseline.sql
    stand-in for the live schema — confirm every migration file is recorded,
    not just that run_migrations() returned without raising."""
    with db_module.db_cursor() as cur:
        cur.execute("SELECT filename FROM jobber.migration_history")
        applied = {row["filename"] for row in cur.fetchall()}
    assert EXPECTED_MIGRATIONS <= applied


def test_baseline_identity_columns_are_uuid(client):
    """The single fact the whole reconciliation pass hinges on: production's
    core entity ids are UUID, not BIGSERIAL (docs/14 §3). Spot-check it
    directly against information_schema rather than trusting that no
    exception during migration implies this."""
    with db_module.db_cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'jobber' AND data_type = 'uuid'
              AND (table_name, column_name) IN (
                  ('document', 'id'),
                  ('role_instance', 'id'),
                  ('role_instance', 'document_id'),
                  ('role_instance', 'archetype_concept_id'),
                  ('concept', 'id'),
                  ('concept_alias', 'id'),
                  ('role_skill_observation', 'role_instance_id'),
                  ('role_skill_observation', 'canonical_concept_id'),
                  ('requirement_claim', 'id'),
                  ('requirement_claim', 'concept_id'),
                  ('extraction_run', 'id'),
                  ('extraction_run', 'vocabulary_version_id'),
                  ('profile360_claim_mapping', 'jobber_concept_id'),
                  ('preference_observation', 'profile360_claim_id')
              )
            """
        )
        found = {(row["table_name"], row["column_name"]) for row in cur.fetchall()}
    expected = {
        ("document", "id"),
        ("role_instance", "id"),
        ("role_instance", "document_id"),
        ("role_instance", "archetype_concept_id"),
        ("concept", "id"),
        ("concept_alias", "id"),
        ("role_skill_observation", "role_instance_id"),
        ("role_skill_observation", "canonical_concept_id"),
        ("requirement_claim", "id"),
        ("requirement_claim", "concept_id"),
        ("extraction_run", "id"),
        ("extraction_run", "vocabulary_version_id"),
        ("profile360_claim_mapping", "jobber_concept_id"),
        ("preference_observation", "profile360_claim_id"),
    }
    assert found == expected


def test_role_instance_has_confirmed_direct_columns(client):
    """docs/14 §3: legacy_role_analysis was never a separate table in
    production — description/requirements/legacy_scores/legacy_analysis/etc.
    live directly on role_instance. Confirm the columns this codebase reads
    and writes via db.flatten_role_instance/upsert_role_instance actually
    exist, by name, post-migration."""
    with db_module.db_cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'role_instance'"
        )
        columns = {row["column_name"] for row in cur.fetchall()}
    for expected in (
        "instance_type", "target_basis", "description", "requirements", "responsibilities",
        "summary", "career_track", "legacy_scores", "legacy_analysis", "extraction_status",
        "extraction_notes", "status",
    ):
        assert expected in columns, f"jobber.role_instance.{expected} missing after migrations"
    # "kind" was this codebase's original (wrong) guess at the column name —
    # confirm it was never actually created.
    assert "kind" not in columns


def test_document_provenance_quality_values(client):
    """docs/14 §3: the real column is provenance_quality with these four
    values — not a separate "provenance" column, and not free text."""
    with db_module.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.document (source_key, kind, content_text, provenance_quality) "
            "VALUES ('test-provenance-quality', 'posting', 'x', 'legacy_extracted') RETURNING id"
        )
        assert cur.fetchone() is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO jobber.document (source_key, kind, content_text, provenance_quality) "
                "VALUES ('test-provenance-quality-bad', 'posting', 'x', 'not_a_real_value')"
            )


def test_local_baseline_episodes_and_snapshots_match_confirmed_production_shape(client):
    """docs/14 §5: profile360.episodes/snapshots have a fully confirmed live
    shape now (episode fields like title/organisation/start_date/end_date/
    responsibilities, and the self-referencing parent_episode_id FK; summary/
    structured_state on snapshots) — local_baseline.sql must model that, not
    an id-only approximation."""
    with db_module.db_cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'profile360' AND table_name = 'episodes'"
        )
        episode_columns = {row["column_name"] for row in cur.fetchall()}
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'profile360' AND table_name = 'snapshots'"
        )
        snapshot_columns = {row["column_name"] for row in cur.fetchall()}
        cur.execute(
            """
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'profile360' AND tc.table_name = 'episodes'
              AND tc.constraint_type = 'FOREIGN KEY' AND kcu.column_name = 'parent_episode_id'
            """
        )
        has_self_referencing_fk = cur.fetchone() is not None

    for expected in (
        "episode_key", "parent_episode_id", "episode_type", "organisation", "title",
        "start_date", "end_date", "date_precision", "context", "responsibilities",
        "autonomy", "accountability", "stakeholder_scope", "team_size", "outcomes",
        "status", "uncertainty",
    ):
        assert expected in episode_columns, f"profile360.episodes.{expected} missing from local baseline"
    assert has_self_referencing_fk, "profile360.episodes.parent_episode_id must reference episodes(id)"

    for expected in ("snapshot_key", "snapshot_type", "created_for", "summary", "structured_state"):
        assert expected in snapshot_columns, f"profile360.snapshots.{expected} missing from local baseline"


def test_phase3_derived_tables_have_no_person_id(client):
    """brief §41: doc 11's original d_capability_coverage/d_role_fit DDL
    carried a person_id FK to a jobber.person table that no longer exists in
    production and must not be reintroduced. Confirm the Phase 3 tables were
    not built against that assumption."""
    with db_module.db_cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name IN ('d_capability_coverage', 'd_role_fit') "
            "AND column_name = 'person_id'"
        )
        assert cur.fetchall() == []


def test_phase3_derived_tables_use_uuid_identity(client):
    with db_module.db_cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'jobber' AND data_type = 'uuid'
              AND (table_name, column_name) IN (
                  ('d_capability_coverage', 'capability_concept_id'),
                  ('d_role_fit', 'role_instance_id'),
                  ('gold_document', 'document_id'),
                  ('gold_claim', 'id'),
                  ('gold_claim', 'concept_id'),
                  ('eval_run', 'id'),
                  ('capability_gold_judgment', 'capability_concept_id')
              )
            """
        )
        found = {(row["table_name"], row["column_name"]) for row in cur.fetchall()}
    expected = {
        ("d_capability_coverage", "capability_concept_id"),
        ("d_role_fit", "role_instance_id"),
        ("gold_document", "document_id"),
        ("gold_claim", "id"),
        ("gold_claim", "concept_id"),
        ("eval_run", "id"),
        ("capability_gold_judgment", "capability_concept_id"),
    }
    assert found == expected


def test_concept_edge_necessity_and_status_constrained(client):
    """0006 adds the CHECK constraints the app-layer edge validation
    (routes/capabilities.py) also enforces — confirm the database itself
    now rejects an invalid necessity/status rather than relying solely on
    the API layer (brief §6: 'Do not rely only on frontend validation')."""
    with db_module.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('tool', 'test-edge-tool', 'active', 'curator', now()) RETURNING id"
        )
        tool_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('capability', 'test-edge-capability', 'active', 'curator', now()) RETURNING id"
        )
        cap_id = cur.fetchone()["id"]

        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, necessity, origin) "
                "VALUES (%s, %s, 'component_of', 'not_a_real_necessity', 'curator')",
                (tool_id, cap_id),
            )


def test_extraction_run_gained_result_linkage_columns(client):
    """docs/17 §3: extraction_run.result_role_instance_id/output_payload,
    added by 0007, are what a job_posting_extract run's outcome links
    through — confirm both exist with the right shape post-migration."""
    with db_module.db_cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'extraction_run' "
            "AND column_name IN ('result_role_instance_id', 'output_payload')"
        )
        found = {row["column_name"]: row["data_type"] for row in cur.fetchall()}
    assert found == {"result_role_instance_id": "uuid", "output_payload": "jsonb"}


def test_extraction_run_status_check_now_permits_running(client):
    with db_module.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.document (source_key, kind, content_text, provenance_quality) "
            "VALUES ('test-running-status', 'job_posting', 'x', 'original') RETURNING id"
        )
        document_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO jobber.extraction_run
                (task, subject_type, document_id, model, prompt_name, prompt_version, vocabulary_version_id, started_at, status)
            VALUES ('job_posting_extract', 'document', %s, 'test-model', 'p', 'v', NULL, now(), 'running')
            RETURNING id
            """,
            (document_id,),
        )
        assert cur.fetchone() is not None

        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO jobber.extraction_run
                    (task, subject_type, document_id, model, prompt_name, prompt_version, vocabulary_version_id, started_at, status)
                VALUES ('job_posting_extract', 'document', %s, 'test-model', 'p', 'v', NULL, now(), 'not_a_real_status')
                """,
                (document_id,),
            )


def test_vocabulary_version_id_nullable_only_for_job_posting_extract(client):
    """docs/17 §4: vocabulary_version_id is nullable at the column level now,
    but a guarded CHECK still requires a real one for every vocabulary-
    dependent task — only job_posting_extract may omit it."""
    with db_module.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.document (source_key, kind, content_text, provenance_quality) "
            "VALUES ('test-vocab-null', 'job_posting', 'x', 'original') RETURNING id"
        )
        document_id = cur.fetchone()["id"]

        # job_posting_extract: NULL vocabulary_version_id is fine.
        cur.execute(
            """
            INSERT INTO jobber.extraction_run
                (task, subject_type, document_id, model, prompt_name, prompt_version, vocabulary_version_id, started_at, status)
            VALUES ('job_posting_extract', 'document', %s, 'test-model', 'p', 'v', NULL, now(), 'ok')
            """,
            (document_id,),
        )

        # requirement_extract (a real vocabulary-dependent task): NULL must
        # still be rejected — the relaxation is scoped to job_posting_extract
        # only, never a blanket removal of the guarantee.
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO jobber.extraction_run
                    (task, subject_type, document_id, model, prompt_name, prompt_version, vocabulary_version_id, started_at, status)
                VALUES ('requirement_extract', 'document', %s, 'test-model', 'p', 'v', NULL, now(), 'ok')
                """,
                (document_id,),
            )


def test_migrations_reject_database_without_baseline(client, monkeypatch):
    """0001_live_schema_preflight.sql exists specifically so that pointing
    Phase 2 migrations at a database that never had the live jobber baseline
    fails immediately and legibly, instead of a later CREATE TABLE ...
    REFERENCES failing confusingly several files deep — or worse, silently
    creating a divergent schema. Prove it actually fires. (Depending on
    `client` just guarantees the session fixture already proved Postgres is
    reachable at TEST_DATABASE_URL before this test bothers creating a
    second, deliberately bare, database alongside it.)"""
    admin_url = get_test_database_url()
    db_name = f"cp_test_nobaseline_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')
    parts = urlsplit(admin_url)
    bare_url = urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))
    try:
        db_module.reset_pool()
        monkeypatch.setenv("DATABASE_URL", bare_url)
        with pytest.raises(psycopg.errors.RaiseException) as exc_info:
            db_module.run_migrations()
        assert "Phase 2 preflight failed" in str(exc_info.value)
        assert "jobber.document.id must be uuid" in str(exc_info.value)
    finally:
        db_module.reset_pool()
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')


def test_migration_0020_preflight_rejects_existing_duplicate_current_claims(client, monkeypatch):
    """Code-review follow-up: the *old* application code (before this build's
    conservative rerun-dedup handling) allowed repeated extraction to leave
    two current (superseded_by IS NULL) requirement_claim rows for the same
    (role_instance_id, concept_id) pair. Migration 0020's partial unique index
    would fail outright against a database already in that state, with an
    ordinary index-creation error as the first anyone learns of it — the
    migration file adds an explicit DO-block preflight specifically to fail
    loudly with a diagnostic instead. Prove that preflight actually fires, by
    replaying every migration up to (but not including) 0020 against a fresh
    database, seeding exactly that duplicate, and then applying 0020's SQL
    text directly."""
    admin_url = get_test_database_url()
    db_name = f"cp_test_dupclaims_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')
    parts = urlsplit(admin_url)
    scoped_url = urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))
    target_name = "0020_requirement_claim_current_uniqueness.sql"
    target_path = db_module.MIGRATIONS_DIR / target_name
    baseline_sql = (db_module.MIGRATIONS_DIR.parent / "scripts" / "local_baseline.sql").read_text(encoding="utf-8")
    try:
        with psycopg.connect(scoped_url, autocommit=True) as conn:
            conn.execute(baseline_sql)
            # Pre-record 0020 as already "applied" so run_migrations() skips
            # it — leaving its unique index/deferred-FK change out entirely
            # while still applying every other file (0021 has no dependency
            # on 0020's schema change) in the normal, real migration-runner
            # code path rather than a hand-rolled re-implementation of it.
            conn.execute(
                "CREATE SCHEMA IF NOT EXISTS jobber; "
                "CREATE TABLE IF NOT EXISTS jobber.migration_history "
                "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            conn.execute(
                "INSERT INTO jobber.migration_history (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                (target_name,),
            )

        db_module.reset_pool()
        monkeypatch.setenv("DATABASE_URL", scoped_url)
        applied = db_module.run_migrations()
        assert target_name not in applied

        with db_module.db_cursor() as cur:
            role_id = db_module.upsert_role_instance(
                cur, None, {"instance_type": "observed_posting", "title": "T"}, skills=[]
            )
            cur.execute(
                "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
                "VALUES ('tool', 'Python', 'active', 'curator', now()) RETURNING id"
            )
            concept_id = cur.fetchone()["id"]
            # The exact pre-0020 state the migration must now refuse to run
            # against: two current (superseded_by IS NULL) claims for one
            # (role, concept) pair.
            for _ in range(2):
                cur.execute(
                    "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
                    "VALUES (%s, %s, 'required', 'user_asserted', 'unreviewed')",
                    (role_id, concept_id),
                )

        with pytest.raises(psycopg.errors.RaiseException) as exc_info:
            with db_module.db_cursor() as cur:
                cur.execute(target_path.read_text(encoding="utf-8"))
        assert "migration 0020 preflight failed" in str(exc_info.value)
        assert "more than one current" in str(exc_info.value)
    finally:
        db_module.reset_pool()
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')


def test_migration_0022_backfill_creates_claims_for_already_resolved_proposals(client):
    """Migration 0022's backfill only matters for a database where some
    concept_proposal_occurrence row already carries requirement_type/basis/
    evidence_span (impossible on a database seeing this migration for the
    first time — those columns don't exist until it runs) AND its proposal
    was already resolved before that. Since that combination can't be
    constructed by replaying earlier migrations, this proves the backfill
    SQL itself is correct by re-running migration 0022's file text directly
    against this session's already-fully-migrated test database (`ADD
    COLUMN IF NOT EXISTS` and the backfill's own `NOT EXISTS` guard make
    this a safe, idempotent replay) after seeding exactly that scenario by
    hand."""
    migrations_dir = db_module.MIGRATIONS_DIR
    target_path = next(p for p in migrations_dir.glob("*.sql") if p.name.startswith("0022_"))

    with db_module.db_cursor() as cur:
        document_id, _ = db_module.create_document(
            cur, kind="job_posting", content_text="Requires Zap Modelling.", provenance_quality="original"
        )
        role_id = db_module.upsert_role_instance(
            cur, None, {"instance_type": "observed_posting", "title": "T", "document_id": document_id}, skills=[]
        )
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('domain', 'Zap Modelling', 'active', 'curator', now()) RETURNING id"
        )
        concept_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, status, resolved_concept_id, resolved_at) "
            "VALUES ('zap modelling', 1, %s, 'accepted_new', %s, now()) RETURNING id",
            (document_id, concept_id),
        )
        proposal_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.concept_proposal_occurrence "
            "(concept_proposal_id, role_instance_id, document_id, requirement_type, basis, evidence_span) "
            "VALUES (%s, %s, %s, 'required', 'stated', 'Zap Modelling')",
            (proposal_id, role_id, document_id),
        )

        cur.execute(target_path.read_text(encoding="utf-8"))

        cur.execute(
            "SELECT requirement_type, basis, evidence_span, review_status, document_id "
            "FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s",
            (role_id, concept_id),
        )
        claim = cur.fetchone()
        assert claim is not None
        assert claim["requirement_type"] == "required"
        assert claim["basis"] == "stated"
        assert claim["evidence_span"] == "Zap Modelling"
        assert claim["review_status"] == "unreviewed"
        assert str(claim["document_id"]) == document_id

        # Idempotent replay: running it again must not create a second claim.
        cur.execute(target_path.read_text(encoding="utf-8"))
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s",
            (role_id, concept_id),
        )
        assert cur.fetchone()["n"] == 1
