"""Phase 9: explicit promotion of application notes/events into Profile360's
manual_import_queue (docs/40, build §44). Same confirmed queue shape as
test_promotion.py (source_key TEXT PRIMARY KEY, source_label, payload,
processed, processed_at, processing_notes) — this is the second, independent
writer of that one table.
"""

import uuid

from app import application_learning as learning
from app import db


def _active_concept(cur, name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _posting(cur, title="Life Actuarial Manager", organisation="Acme") -> str:
    return str(db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": title, "organisation": organisation}, skills=[]))


def _application(cur, role_id: str) -> str:
    cur.execute("INSERT INTO jobber.application (role_instance_id, status) VALUES (%s, 'preparing') RETURNING id", (role_id,))
    return str(cur.fetchone()["id"])


def _note(cur, application_id: str, *, text="A concrete example.", note_type="evidence_example", concept_id=None) -> str:
    cur.execute(
        "INSERT INTO jobber.application_note (application_id, concept_id, note_type, note_text) VALUES (%s, %s, %s, %s) RETURNING id",
        (application_id, concept_id, note_type, text),
    )
    return str(cur.fetchone()["id"])


def _event(cur, application_id: str, *, event_type="interview_completed", notes="Reflection on the interview.") -> str:
    cur.execute(
        "INSERT INTO jobber.application_event (application_id, event_type, event_at, notes) VALUES (%s, %s, now(), %s) RETURNING id",
        (application_id, event_type, notes),
    )
    return str(cur.fetchone()["id"])


def _scenario(cur, *, concept_id=None, note_text="A concrete example.", event_notes="Reflection."):
    role_id = _posting(cur)
    application_id = _application(cur, role_id)
    note_id = _note(cur, application_id, text=note_text, concept_id=concept_id)
    event_id = _event(cur, application_id, notes=event_notes)
    return {"role_id": role_id, "application_id": application_id, "note_id": note_id, "event_id": event_id}


# --- Deterministic source keys (build §11/§44) ------------------------------


def test_note_and_event_source_keys_are_deterministic():
    note_id, event_id = str(uuid.uuid4()), str(uuid.uuid4())
    assert learning.note_source_key(note_id) == f"cp_enterprise_application_note:{note_id}"
    assert learning.event_source_key(event_id) == f"cp_enterprise_application_event:{event_id}"


# --- Promotion endpoints: scoping, payload, idempotency (build §12/§44) ----


def test_promote_note_creates_queue_row_with_provenance(client):
    with db.db_cursor() as cur:
        s = _scenario(cur, note_text="I led the capital model rebuild.")

    resp = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued_pending"
    assert body["queue_source_key"] == learning.note_source_key(s["note_id"])

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM profile360.manual_import_queue WHERE source_key = %s", (body["queue_source_key"],))
        row = cur.fetchone()
    assert row["source_label"] == learning.SOURCE_LABEL
    assert row["processed"] is False
    assert row["payload"]["source"] == "jobber.application_note"
    assert row["payload"]["application_id"] == s["application_id"]
    assert row["payload"]["application_note_id"] == s["note_id"]
    assert row["payload"]["note_text"] == "I led the capital model rebuild."
    assert row["payload"]["role_title"] == "Life Actuarial Manager"
    assert "content_sha256" in row["payload"]


def test_note_with_concept_includes_concept_metadata(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Capital modelling", type_code="capability")
        s = _scenario(cur, concept_id=concept_id)

    resp = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    with db.db_cursor() as cur:
        cur.execute("SELECT payload FROM profile360.manual_import_queue WHERE source_key = %s", (resp.json()["queue_source_key"],))
        payload = cur.fetchone()["payload"]
    assert payload["jobber_concept_id"] == concept_id
    assert payload["concept_canonical_name"] == "Capital modelling"


def test_general_note_without_concept_remains_unclassified(client):
    with db.db_cursor() as cur:
        s = _scenario(cur, concept_id=None)
    resp = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    with db.db_cursor() as cur:
        cur.execute("SELECT payload FROM profile360.manual_import_queue WHERE source_key = %s", (resp.json()["queue_source_key"],))
        payload = cur.fetchone()["payload"]
    assert payload["jobber_concept_id"] is None
    assert payload["concept_canonical_name"] is None


def test_promote_event_creates_queue_row(client):
    with db.db_cursor() as cur:
        s = _scenario(cur, event_notes="They asked hard questions about reserving judgment.")
    resp = client.post(f"/api/applications/{s['application_id']}/events/{s['event_id']}/promote")
    assert resp.status_code == 200
    with db.db_cursor() as cur:
        cur.execute("SELECT payload FROM profile360.manual_import_queue WHERE source_key = %s", (resp.json()["queue_source_key"],))
        payload = cur.fetchone()["payload"]
    assert payload["source"] == "jobber.application_event"
    assert payload["event_type"] == "interview_completed"
    assert payload["notes"] == "They asked hard questions about reserving judgment."


def test_event_with_no_notes_cannot_be_promoted(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        application_id = _application(cur, role_id)
        event_id = _event(cur, application_id, notes=None)
    resp = client.post(f"/api/applications/{application_id}/events/{event_id}/promote")
    assert resp.status_code == 400


def test_note_scoped_to_its_own_application(client):
    with db.db_cursor() as cur:
        s1 = _scenario(cur)
        other_role = _posting(cur, title="Other role")
        other_application = _application(cur, other_role)
    resp = client.post(f"/api/applications/{other_application}/notes/{s1['note_id']}/promote")
    assert resp.status_code == 404


def test_event_scoped_to_its_own_application(client):
    with db.db_cursor() as cur:
        s1 = _scenario(cur)
        other_role = _posting(cur, title="Other role")
        other_application = _application(cur, other_role)
    resp = client.post(f"/api/applications/{other_application}/events/{s1['event_id']}/promote")
    assert resp.status_code == 404


def test_promote_unknown_application_is_404(client):
    resp = client.post(f"/api/applications/{uuid.uuid4()}/notes/{uuid.uuid4()}/promote")
    assert resp.status_code == 404


def test_repeated_promote_of_unchanged_note_is_idempotent(client):
    with db.db_cursor() as cur:
        s = _scenario(cur)
    first = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote").json()
    second = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote").json()
    assert first["queue_source_key"] == second["queue_source_key"]
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM profile360.manual_import_queue WHERE source_key = %s", (first["queue_source_key"],))
        assert cur.fetchone()["n"] == 1  # never duplicated


def test_promoting_never_mutates_the_source_note_or_event(client):
    with db.db_cursor() as cur:
        s = _scenario(cur, note_text="Original wording.")
        cur.execute("SELECT note_text, note_type, updated_at FROM jobber.application_note WHERE id = %s", (s["note_id"],))
        before = dict(cur.fetchone())

    client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")

    with db.db_cursor() as cur:
        cur.execute("SELECT note_text, note_type, updated_at FROM jobber.application_note WHERE id = %s", (s["note_id"],))
        after = dict(cur.fetchone())
    assert after == before


# --- Queue status: not_queued / queued_pending / processed / source_changed


def test_learning_state_reports_not_queued_before_any_promotion(client):
    with db.db_cursor() as cur:
        s = _scenario(cur)
    body = client.get(f"/api/applications/{s['application_id']}/learning-state").json()
    note_state = next(n for n in body["notes"] if n["source_id"] == s["note_id"])
    assert note_state["status"] == learning.NOT_QUEUED


def test_learning_state_reports_queued_pending_after_promotion(client):
    with db.db_cursor() as cur:
        s = _scenario(cur)
    client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    body = client.get(f"/api/applications/{s['application_id']}/learning-state").json()
    note_state = next(n for n in body["notes"] if n["source_id"] == s["note_id"])
    assert note_state["status"] == learning.QUEUED_PENDING


def test_learning_state_reports_processed_once_profile360_marks_it(client):
    with db.db_cursor() as cur:
        s = _scenario(cur)
    key = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote").json()["queue_source_key"]
    with db.db_cursor() as cur:
        cur.execute("UPDATE profile360.manual_import_queue SET processed = true, processed_at = now() WHERE source_key = %s", (key,))
    body = client.get(f"/api/applications/{s['application_id']}/learning-state").json()
    note_state = next(n for n in body["notes"] if n["source_id"] == s["note_id"])
    assert note_state["status"] == learning.PROCESSED


def test_learning_state_reports_source_changed_after_edit_without_requeue(client):
    with db.db_cursor() as cur:
        s = _scenario(cur, note_text="Version one.")
    client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")

    edit_resp = client.patch(f"/api/applications/{s['application_id']}/notes/{s['note_id']}", json={"note_text": "Version two — edited after queueing."})
    assert edit_resp.status_code == 200

    body = client.get(f"/api/applications/{s['application_id']}/learning-state").json()
    note_state = next(n for n in body["notes"] if n["source_id"] == s["note_id"])
    assert note_state["status"] == learning.SOURCE_CHANGED


def test_explicit_requeue_after_edit_resets_to_pending(client):
    with db.db_cursor() as cur:
        s = _scenario(cur, note_text="Version one.")
    client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    client.patch(f"/api/applications/{s['application_id']}/notes/{s['note_id']}", json={"note_text": "Version two."})

    requeue_resp = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    assert requeue_resp.status_code == 200
    assert requeue_resp.json()["status"] == learning.QUEUED_PENDING

    with db.db_cursor() as cur:
        cur.execute("SELECT payload, processed FROM profile360.manual_import_queue WHERE source_key = %s", (requeue_resp.json()["queue_source_key"],))
        row = cur.fetchone()
    assert row["processed"] is False
    assert row["payload"]["note_text"] == "Version two."


def test_get_never_silently_requeues(client):
    """A GET must only ever read/compare — never write to the queue (build
    §11's "Do not silently requeue on GET")."""
    with db.db_cursor() as cur:
        s = _scenario(cur, note_text="Version one.")
    client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    client.patch(f"/api/applications/{s['application_id']}/notes/{s['note_id']}", json={"note_text": "Version two."})

    with db.db_cursor() as cur:
        cur.execute("SELECT payload FROM profile360.manual_import_queue WHERE source_key = %s", (learning.note_source_key(s["note_id"]),))
        before = cur.fetchone()["payload"]

    for _ in range(3):
        client.get(f"/api/applications/{s['application_id']}/learning-state")

    with db.db_cursor() as cur:
        cur.execute("SELECT payload FROM profile360.manual_import_queue WHERE source_key = %s", (learning.note_source_key(s["note_id"]),))
        after = cur.fetchone()["payload"]
    assert before == after  # unchanged — still the stale, pre-edit payload


# --- Failure behaviour never falsely succeeds (build §12/§44) --------------


def test_promotion_db_failure_never_falsely_reports_success(client):
    with db.db_cursor() as cur:
        s = _scenario(cur)

    with db.db_cursor() as cur:
        cur.execute("ALTER TABLE profile360.manual_import_queue RENAME TO manual_import_queue_tmp")
    try:
        resp = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
        assert resp.status_code == 503
    finally:
        with db.db_cursor() as cur:
            cur.execute("ALTER TABLE profile360.manual_import_queue_tmp RENAME TO manual_import_queue")

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM profile360.manual_import_queue WHERE source_key = %s", (learning.note_source_key(s["note_id"]),))
        assert cur.fetchone()["n"] == 0


# --- Profile360 boundary: only the queue is ever written (build §10/§44) ---


def test_promotion_writes_only_to_manual_import_queue(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Capital modelling", type_code="capability")
        s = _scenario(cur, concept_id=concept_id)
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        claims_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM profile360.capabilities")
        capabilities_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.profile360_claim_mapping")
        mappings_before = cur.fetchone()["n"]

    client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote")
    client.post(f"/api/applications/{s['application_id']}/events/{s['event_id']}/promote")

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        assert cur.fetchone()["n"] == claims_before
        cur.execute("SELECT COUNT(*) AS n FROM profile360.capabilities")
        assert cur.fetchone()["n"] == capabilities_before
        cur.execute("SELECT COUNT(*) AS n FROM jobber.profile360_claim_mapping")
        assert cur.fetchone()["n"] == mappings_before


def test_queueing_never_changes_capability_status(client):
    """Queueing (even a since-processed queue row) must never itself change
    what the capability engine reports — only a genuine accepted Profile360
    claim/mapping can (build §34/§45)."""
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Capital modelling", type_code="capability")
        cur.execute(
            "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) VALUES (%s, %s, 'owned')",
            (concept_id, "Demonstrate it"),
        )
        s = _scenario(cur, concept_id=concept_id, note_text="I ran the capital model end to end.")

    key = client.post(f"/api/applications/{s['application_id']}/notes/{s['note_id']}/promote").json()["queue_source_key"]
    with db.db_cursor() as cur:
        cur.execute("UPDATE profile360.manual_import_queue SET processed = true, processed_at = now() WHERE source_key = %s", (key,))

    from app import capability_engine

    with db.db_cursor() as cur:
        coverage = capability_engine.derive_capability_coverage(cur, concept_id)
    assert coverage["status"] == "not_found"
