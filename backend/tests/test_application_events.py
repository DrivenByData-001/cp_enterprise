"""Phase 5: Application lifecycle events (docs/36).

Covers: migration shape (the event table itself, plus the widened
interview_prep artifact_type constraint and its draft/active uniqueness),
event CRUD, ownership scoping, bounded newest-first ordering, that event
mutations never touch jobber.application.status or Profile360/comparison
evidence, that role deletion remains blocked by an Application even after
this phase, and the Applications-index lifecycle aggregate's determinism and
query-count bound.
"""

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from app import db
from tests.query_counter import count_queries


def _posting(cur, title="Life Actuarial Manager", organisation="Acme") -> str:
    return db.upsert_role_instance(
        cur, None, {"instance_type": "observed_posting", "title": title, "organisation": organisation}, skills=[],
    )


def _application(cur, role_id: str, status="preparing") -> str:
    cur.execute(
        "INSERT INTO jobber.application (role_instance_id, status) VALUES (%s, %s) RETURNING id", (role_id, status),
    )
    return str(cur.fetchone()["id"])


# --- migration shape ---------------------------------------------------------


def test_migration_0029_applied(client):
    with db.db_cursor() as cur:
        cur.execute("SELECT filename FROM jobber.migration_history")
        applied = {row["filename"] for row in cur.fetchall()}
    assert "0029_application_lifecycle_interview.sql" in applied


def test_event_table_shape(client):
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'application_event'"
        )
        columns = {row["column_name"] for row in cur.fetchall()}
    assert {
        "id", "application_id", "event_type", "event_at", "label", "notes", "details", "created_at", "updated_at",
    } <= columns


def test_event_type_check_constraint_rejects_invalid_value(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO jobber.application_event (application_id, event_type, event_at) "
                "VALUES (%s, 'not_a_real_type', now())",
                (application_id,),
            )


def test_interview_prep_accepted_by_artifact_type_constraint(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
        cur.execute(
            "INSERT INTO jobber.application_artifact "
            "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
            "VALUES (%s, 'interview_prep', 'draft', 'ai', '1', 'fp', '{}'::jsonb) RETURNING id",
            (application_id,),
        )
        assert cur.fetchone() is not None


def test_existing_artifact_types_still_accepted(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
        for artifact_type in ("positioning", "cv", "cover_letter", "supporting_statement"):
            cur.execute(
                "INSERT INTO jobber.application_artifact "
                "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
                "VALUES (%s, %s, 'superseded', 'ai', '1', 'fp', '{}'::jsonb)",
                (application_id, artifact_type),
            )


def test_interview_prep_draft_and_active_uniqueness_enforced(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
        cur.execute(
            "INSERT INTO jobber.application_artifact "
            "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
            "VALUES (%s, 'interview_prep', 'draft', 'ai', '1', 'fp1', '{}'::jsonb)",
            (application_id,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO jobber.application_artifact "
                "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
                "VALUES (%s, 'interview_prep', 'draft', 'ai', '1', 'fp2', '{}'::jsonb)",
                (application_id,),
            )


# --- lifecycle event CRUD ----------------------------------------------------


def test_create_list_edit_delete_event(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)

    resp = client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "submitted", "event_at": "2026-01-05T10:00:00+00:00", "label": "Applied via portal"},
    )
    assert resp.status_code == 200
    event = resp.json()
    assert event["event_type"] == "submitted"
    assert event["label"] == "Applied via portal"
    assert event["notes"] is None
    assert event["details"] == {}

    listed = client.get(f"/api/applications/{application_id}/events").json()
    assert listed["application_id"] == application_id
    assert len(listed["events"]) == 1
    assert listed["events"][0]["id"] == event["id"]

    edited = client.patch(
        f"/api/applications/{application_id}/events/{event['id']}", json={"notes": "Confirmed by email"}
    ).json()
    assert edited["notes"] == "Confirmed by email"
    assert edited["event_type"] == "submitted"  # unset fields are unchanged

    delete_resp = client.delete(f"/api/applications/{application_id}/events/{event['id']}")
    assert delete_resp.status_code == 200
    assert client.get(f"/api/applications/{application_id}/events").json()["events"] == []


def test_events_ordered_newest_first(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "submitted", "event_at": "2026-01-01T00:00:00+00:00"},
    )
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "interview_scheduled", "event_at": "2026-01-10T00:00:00+00:00"},
    )
    resp = client.get(f"/api/applications/{application_id}/events").json()
    assert [e["event_type"] for e in resp["events"]] == ["interview_scheduled", "submitted"]


def test_event_scoped_to_its_own_application(client):
    with db.db_cursor() as cur:
        role_a = _posting(cur)
        role_b = _posting(cur, title="Other role")
        app_a = _application(cur, role_a)
        app_b = _application(cur, role_b)
    created = client.post(
        f"/api/applications/{app_a}/events", json={"event_type": "submitted", "event_at": "2026-01-01T00:00:00+00:00"}
    ).json()

    assert client.patch(f"/api/applications/{app_b}/events/{created['id']}", json={"notes": "x"}).status_code == 404
    assert client.delete(f"/api/applications/{app_b}/events/{created['id']}").status_code == 404
    assert client.get(f"/api/applications/{app_b}/events").json()["events"] == []
    # untouched on the owning application throughout
    assert len(client.get(f"/api/applications/{app_a}/events").json()["events"]) == 1


def test_create_event_on_unknown_application_is_404(client):
    resp = client.post(
        f"/api/applications/{uuid.uuid4()}/events",
        json={"event_type": "submitted", "event_at": "2026-01-01T00:00:00+00:00"},
    )
    assert resp.status_code == 404


def test_edit_and_delete_on_unknown_event_are_404(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    missing = str(uuid.uuid4())
    assert client.patch(f"/api/applications/{application_id}/events/{missing}", json={"notes": "x"}).status_code == 404
    assert client.delete(f"/api/applications/{application_id}/events/{missing}").status_code == 404


def test_create_event_rejects_unknown_event_type(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    resp = client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "not_a_type", "event_at": "2026-01-01T00:00:00+00:00"},
    )
    assert resp.status_code == 422


def test_create_event_rejects_oversized_details(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    resp = client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "other", "event_at": "2026-01-01T00:00:00+00:00", "details": {"blob": "x" * 6000}},
    )
    assert resp.status_code == 422


def test_all_documented_event_types_are_accepted(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    for event_type in (
        "submitted", "interview_scheduled", "interview_completed", "offer_received", "offer_accepted",
        "offer_declined", "rejected", "role_closed", "withdrawn", "closed", "other",
    ):
        resp = client.post(
            f"/api/applications/{application_id}/events",
            json={"event_type": event_type, "event_at": "2026-01-01T00:00:00+00:00"},
        )
        assert resp.status_code == 200, f"{event_type} was rejected: {resp.text}"


# --- status / evidence boundary ----------------------------------------------


def test_creating_event_never_changes_application_status(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id, status="preparing")
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "interview_scheduled", "event_at": "2026-02-01T00:00:00+00:00"},
    )
    resp = client.get(f"/api/applications/{application_id}")
    assert resp.json()["application"]["status"] == "preparing"


def test_event_and_status_may_legitimately_differ(client):
    """An offer can arrive while status is still 'interviewing' — the
    timeline and the user-set status are independent (build §3)."""
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id, status="interviewing")
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "offer_received", "event_at": "2026-02-01T00:00:00+00:00"},
    )
    resp = client.get(f"/api/applications/{application_id}")
    assert resp.json()["application"]["status"] == "interviewing"
    events_resp = client.get(f"/api/applications/{application_id}/events").json()
    assert events_resp["events"][0]["event_type"] == "offer_received"


def test_event_notes_do_not_alter_profile360_or_evidence(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        claims_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
        requirement_claims_before = cur.fetchone()["n"]

    client.post(
        f"/api/applications/{application_id}/events",
        json={
            "event_type": "interview_completed", "event_at": "2026-02-01T00:00:00+00:00",
            "notes": "Asked about Solvency II modelling; felt strong on technical depth.",
        },
    )

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        assert cur.fetchone()["n"] == claims_before
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
        assert cur.fetchone()["n"] == requirement_claims_before

    evidence = client.get(f"/api/applications/{application_id}/evidence").json()
    assert evidence["items"] == []


def test_role_deletion_remains_blocked_by_application_with_events(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "submitted", "event_at": "2026-01-01T00:00:00+00:00"},
    )
    resp = client.delete(f"/api/roles/{role_id}")
    assert resp.status_code == 409


def test_deleting_an_event_never_deletes_the_application_or_artifacts(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
        cur.execute(
            "INSERT INTO jobber.application_artifact "
            "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
            "VALUES (%s, 'interview_prep', 'active', 'ai', '1', 'fp', '{}'::jsonb)",
            (application_id,),
        )
    created = client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "submitted", "event_at": "2026-01-01T00:00:00+00:00"},
    ).json()
    client.delete(f"/api/applications/{application_id}/events/{created['id']}")

    assert client.get(f"/api/applications/{application_id}").status_code == 200
    artifacts = client.get(f"/api/applications/{application_id}/artifacts").json()
    assert artifacts["artifacts"]["interview_prep"]["active"] is not None


# --- Applications-index lifecycle aggregate ----------------------------------


def test_list_applications_surfaces_latest_event_and_next_interview(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "submitted", "event_at": "2026-01-01T00:00:00+00:00"},
    )
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "interview_scheduled", "event_at": future, "label": "Technical panel"},
    )

    resp = client.get("/api/applications").json()
    item = next(i for i in resp["items"] if i["id"] == application_id)
    assert item["latest_event"]["event_type"] == "interview_scheduled"
    assert item["next_interview"]["label"] == "Technical panel"


def test_list_applications_next_interview_ignores_past_events(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    client.post(
        f"/api/applications/{application_id}/events",
        json={"event_type": "interview_scheduled", "event_at": past, "label": "Old panel"},
    )

    resp = client.get("/api/applications").json()
    item = next(i for i in resp["items"] if i["id"] == application_id)
    assert item["next_interview"] is None
    assert item["latest_event"]["event_type"] == "interview_scheduled"


def test_list_applications_with_no_events_has_null_lifecycle_fields(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
    resp = client.get("/api/applications").json()
    item = next(i for i in resp["items"] if i["id"] == application_id)
    assert item["latest_event"] is None
    assert item["next_interview"] is None


def test_list_applications_lifecycle_aggregate_has_no_per_row_queries(client):
    with db.db_cursor() as cur:
        for i in range(5):
            role_id = _posting(cur, title=f"Role {i}")
            application_id = _application(cur, role_id)
            cur.execute(
                "INSERT INTO jobber.application_event (application_id, event_type, event_at) VALUES (%s, 'submitted', now())",
                (application_id,),
            )

    with count_queries() as counted:
        resp = client.get("/api/applications")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 5
    # A fixed, small number of queries regardless of row count (count + the
    # one joined/aggregated page query) — never one events lookup per
    # application (same bound test_applications.py's own list test asserts
    # for the role-metadata join).
    assert counted.count <= 3
