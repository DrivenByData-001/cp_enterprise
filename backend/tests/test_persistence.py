"""Postgres repository/persistence behaviour (brief §16) — db.py primitives
directly, against the real throwaway test database (conftest.py)."""

import uuid

import psycopg
import pytest

from app import db


def test_create_document_always_inserts_but_flags_duplicate_content(client):
    """jobber.document's real identity column is source_key, not a content
    hash (docs/14 §4) — production has two distinct real postings that share
    identical reconstructed text, so create_document must never collapse
    same-content calls into one row. content_sha256 match is reported only
    informationally via the second return value."""
    with db.db_cursor() as cur:
        id1, dup1 = db.create_document(cur, kind="job_posting", content_text="Same text.", provenance_quality="original")
        id2, dup2 = db.create_document(cur, kind="job_posting", content_text="Same text.", provenance_quality="original")
    assert dup1 is None
    assert dup2 == id1
    assert id1 != id2


def test_create_document_rejects_invalid_provenance_quality(client):
    with db.db_cursor() as cur:
        with pytest.raises(ValueError):
            db.create_document(cur, kind="job_posting", content_text="x", provenance_quality="not_a_real_value")


def test_upsert_role_instance_stores_columns_directly_on_role_instance(client):
    """No more separate legacy_role_analysis table (docs/14 §3) — description
    and the rest are plain columns on jobber.role_instance itself."""
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(
            cur,
            None,
            {"instance_type": "observed_posting", "title": "Test Role", "description": "desc"},
            skills=[{"name": "Python"}],
        )
        cur.execute("SELECT title, instance_type, description FROM jobber.role_instance WHERE id = %s", (role_id,))
        role_row = cur.fetchone()
        cur.execute("SELECT surface_form FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_id,))
        skills = cur.fetchall()

    assert role_row["title"] == "Test Role"
    assert role_row["instance_type"] == "observed_posting"
    assert role_row["description"] == "desc"
    assert [s["surface_form"] for s in skills] == ["Python"]


def test_upsert_role_instance_packs_legacy_scores_jsonb(client):
    """docs/14 §5: scores/derived analysis this app used to keep as flat
    columns are packed into legacy_scores/legacy_analysis JSONB on the real
    table — db.flatten_role_instance is what unpacks them back for the API
    response, so round-trip through it here rather than just checking the
    raw JSONB made it in."""
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(
            cur,
            None,
            {
                "instance_type": "observed_posting",
                "title": "Test Role",
                "legacy_scores": {"seniority_score": 0.8},
                "legacy_analysis": {"top_adjacent_roles": ["Analyst"]},
            },
            skills=[],
        )
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (role_id,))
        flat = db.flatten_role_instance(cur.fetchone())

    assert flat["seniority_score"] == 0.8
    assert flat["top_adjacent_roles"] == ["Analyst"]
    assert flat["node_type"] == "posting"


def test_upsert_role_instance_update_replaces_skills(client):
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "T"}, skills=[{"name": "Python"}])
        db.upsert_role_instance(cur, role_id, {"instance_type": "observed_posting", "title": "T2"}, skills=[{"name": "SQL"}])
        cur.execute("SELECT title FROM jobber.role_instance WHERE id = %s", (role_id,))
        title = cur.fetchone()["title"]
        cur.execute("SELECT surface_form FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_id,))
        skills = [r["surface_form"] for r in cur.fetchall()]

    assert title == "T2"
    assert skills == ["SQL"]


def test_upsert_role_instance_update_missing_role_raises(client):
    with db.db_cursor() as cur:
        with pytest.raises(ValueError):
            db.upsert_role_instance(cur, str(uuid.uuid4()), {"instance_type": "observed_posting", "title": "x"}, skills=[])


def test_run_migrations_is_idempotent(client):
    first = db.run_migrations()
    assert first == []  # already applied by the session fixture before any test runs


def test_document_provenance_quality_check_constraint_enforced(client):
    # The whole db_cursor() block must be inside pytest.raises: a Postgres
    # error aborts the transaction server-side, so db_cursor()'s own
    # commit-on-clean-exit must never run afterwards — only its
    # rollback-on-exception path is valid once the INSERT below fails.
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.db_cursor() as cur:
            cur.execute(
                "INSERT INTO jobber.document (source_key, kind, content_text, provenance_quality) VALUES (%s, %s, %s, %s)",
                ("test-bad-provenance", "job_posting", "x", "not_a_real_value"),
            )
