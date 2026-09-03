"""Postgres repository/persistence behaviour (brief §16) — db.py primitives
directly, against the real throwaway test database (conftest.py)."""

import psycopg
import pytest

from app import db


def test_get_or_create_document_is_idempotent_by_content(client):
    with db.db_cursor() as cur:
        id1, created1 = db.get_or_create_document(cur, kind="job_posting", body="Same text.", provenance="original_capture")
        id2, created2 = db.get_or_create_document(cur, kind="job_posting", body="Same text.", provenance="original_capture")
    assert created1 is True
    assert created2 is False
    assert id1 == id2


def test_get_or_create_document_rejects_invalid_provenance(client):
    with db.db_cursor() as cur:
        with pytest.raises(ValueError):
            db.get_or_create_document(cur, kind="job_posting", body="x", provenance="not_a_real_value")


def test_upsert_role_instance_splits_columns_across_tables(client):
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(
            cur,
            None,
            {"kind": "posting", "title": "Test Role", "seniority_score": 0.8, "description": "desc"},
            skills=[{"name": "Python"}],
        )
        cur.execute("SELECT title, kind FROM jobber.role_instance WHERE id = %s", (role_id,))
        role_row = cur.fetchone()
        cur.execute("SELECT seniority_score, description FROM jobber.legacy_role_analysis WHERE role_instance_id = %s", (role_id,))
        legacy_row = cur.fetchone()
        cur.execute("SELECT name FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_id,))
        skills = cur.fetchall()

    assert role_row["title"] == "Test Role"
    assert role_row["kind"] == "posting"
    assert legacy_row["seniority_score"] == 0.8
    assert legacy_row["description"] == "desc"
    assert [s["name"] for s in skills] == ["Python"]


def test_upsert_role_instance_update_replaces_skills(client):
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(cur, None, {"kind": "posting", "title": "T"}, skills=[{"name": "Python"}])
        db.upsert_role_instance(cur, role_id, {"kind": "posting", "title": "T2"}, skills=[{"name": "SQL"}])
        cur.execute("SELECT title FROM jobber.role_instance WHERE id = %s", (role_id,))
        title = cur.fetchone()["title"]
        cur.execute("SELECT name FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_id,))
        skills = [r["name"] for r in cur.fetchall()]

    assert title == "T2"
    assert skills == ["SQL"]


def test_upsert_role_instance_update_missing_role_raises(client):
    with db.db_cursor() as cur:
        with pytest.raises(ValueError):
            db.upsert_role_instance(cur, 999999, {"kind": "posting", "title": "x"}, skills=[])


def test_get_or_create_person_is_a_singleton(client):
    with db.db_cursor() as cur:
        id1 = db.get_or_create_person(cur)
        id2 = db.get_or_create_person(cur, display_name="Someone Else")
    assert id1 == id2


def test_run_migrations_is_idempotent(client):
    first = db.run_migrations()
    assert first == []  # already applied by the session fixture before any test runs


def test_document_provenance_check_constraint_enforced(client):
    # The whole db_cursor() block must be inside pytest.raises: a Postgres
    # error aborts the transaction server-side, so db_cursor()'s own
    # commit-on-clean-exit must never run afterwards — only its
    # rollback-on-exception path is valid once the INSERT below fails.
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.db_cursor() as cur:
            cur.execute(
                "INSERT INTO jobber.document (kind, body, body_sha256, provenance) VALUES (%s, %s, %s, %s)",
                ("job_posting", "x", "deadbeef", "not_a_real_value"),
            )
