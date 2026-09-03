"""Preference evidence semantics (brief §10/§16): reusable dimensions,
source/basis/strength/confidence retained, and psychometric material ranked
as the weakest basis, structurally separate from capability/comparison."""

import psycopg
import pytest

from app import db


def test_dimensions_are_seeded(client):
    resp = client.get("/api/preferences/dimensions")
    codes = {d["code"] for d in resp.json()}
    assert "autonomy" in codes
    assert "bureaucracy_tolerance" in codes
    assert len(codes) == 11


def test_create_and_list_observation(client):
    resp = client.post(
        "/api/preferences",
        json={
            "dimension_code": "autonomy",
            "direction": "toward",
            "strength": 3,
            "basis": "observed_behavior",
            "source_label": "episode 4",
            "note": "Ran the process independently for 3 years.",
        },
    )
    assert resp.status_code == 200

    listed = client.get("/api/preferences", params={"dimension_code": "autonomy"}).json()
    assert len(listed) == 1
    assert listed[0]["basis"] == "observed_behavior"
    assert listed[0]["confidence"] == "low"  # default, not fabricated


def test_unknown_dimension_rejected(client):
    resp = client.post(
        "/api/preferences",
        json={"dimension_code": "not_a_real_dimension", "direction": "toward", "strength": 1, "basis": "user_stated"},
    )
    assert resp.status_code == 400


def test_psychometric_basis_is_accepted_but_ranked_last_in_the_vocabulary(client):
    """MBTI-derived material is a legitimate basis value — but the brief's
    definition of done (#10) requires it can never alter capability evidence
    or a deterministic role-fit score. Structurally verified here as: nothing
    in this table has any foreign key toward jobber.concept, jobber.
    requirement_claim, or the comparison view — a psychometric row simply has
    no path into them at the schema level, not merely by convention."""
    resp = client.post(
        "/api/preferences",
        json={
            "dimension_code": "people_leadership",
            "direction": "away",
            "strength": 2,
            "basis": "typology_hypothesis",
            "source_label": "MBTI: INTP",
        },
    )
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'jobber' AND tc.table_name = 'preference_observation'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name NOT IN ('dimension_code', 'profile360_claim_id', 'profile360_episode_id')
            """
        )
        stray_fks = cur.fetchone()["n"]
    assert stray_fks == 0


def test_invalid_strength_rejected_by_check_constraint(client):
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.db_cursor() as cur:
            cur.execute(
                "INSERT INTO jobber.preference_observation (dimension_code, direction, strength, basis) "
                "VALUES ('autonomy', 'toward', 99, 'user_stated')"
            )


def test_invalid_basis_rejected_by_check_constraint(client):
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.db_cursor() as cur:
            cur.execute(
                "INSERT INTO jobber.preference_observation (dimension_code, direction, strength, basis) "
                "VALUES ('autonomy', 'toward', 1, 'vibes')"
            )
