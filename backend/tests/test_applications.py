"""Phase 3: persistent Application workspace backbone (docs/34).

Covers: migration shape, create/reopen idempotency (including the database-
level concurrency guarantee, not just the app-layer check-then-insert), the
target/posting boundary, role deletion protection, status updates, notes
CRUD and their non-effect on comparison/Profile360 evidence, the evidence
pack's structural semantics, and the reviewed-vs-legacy flag.
"""

import threading

import psycopg
import pytest

from app import db
from tests.query_counter import count_queries


def _active_concept(cur, name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _posting(cur, title="Life Actuarial Manager", organisation="Acme", **overrides) -> str:
    columns = {"instance_type": "observed_posting", "title": title, "organisation": organisation, **overrides}
    return db.upsert_role_instance(cur, None, columns, skills=[])


def _target(cur, title="Head of Risk") -> str:
    return db.upsert_role_instance(
        cur, None, {"instance_type": "user_defined_target", "target_basis": "real_role", "title": title}, skills=[]
    )


def _role_with_accepted_claim(cur, concept_id: str, requirement_type="required") -> str:
    role_id = _posting(cur)
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
        "VALUES (%s, %s, %s, 'user_asserted', 'accepted')",
        (role_id, concept_id, requirement_type),
    )
    return role_id


# --- migration shape ---------------------------------------------------------


def test_migration_0027_applied(client):
    with db.db_cursor() as cur:
        cur.execute("SELECT filename FROM jobber.migration_history")
        applied = {row["filename"] for row in cur.fetchall()}
    assert "0027_application_workspace.sql" in applied


def test_application_and_note_tables_have_expected_shape(client):
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'application'"
        )
        app_columns = {row["column_name"] for row in cur.fetchall()}
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'application_note'"
        )
        note_columns = {row["column_name"] for row in cur.fetchall()}
    assert {"id", "role_instance_id", "status", "created_at", "updated_at"} <= app_columns
    assert {"id", "application_id", "concept_id", "note_type", "note_text", "created_at", "updated_at"} <= note_columns


def test_status_check_constraint_rejects_invalid_value(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO jobber.application (role_instance_id, status) VALUES (%s, 'not_a_real_status')",
                (role_id,),
            )


# --- create / reopen ---------------------------------------------------------


def test_create_application_for_observed_posting(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    resp = client.post("/api/applications", json={"role_instance_id": role_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role_instance_id"] == role_id
    assert body["status"] == "preparing"
    assert body["created"] is True


def test_target_cannot_become_an_application(client):
    with db.db_cursor() as cur:
        target_id = _target(cur)
    resp = client.post("/api/applications", json={"role_instance_id": target_id})
    assert resp.status_code == 400
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.application WHERE role_instance_id = %s", (target_id,))
        assert cur.fetchone()["n"] == 0


def test_create_application_for_missing_role_is_404(client):
    resp = client.post("/api/applications", json={"role_instance_id": "11111111-1111-1111-1111-111111111111"})
    assert resp.status_code == 404


def test_repeated_post_reopens_the_same_active_application(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    first = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    second = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    assert first["id"] == second["id"]
    assert first["created"] is True
    assert second["created"] is False
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.application WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1


def test_repeated_post_reopens_regardless_of_which_active_status(client):
    """Reopening must find *any* active-status application, not only a
    'preparing' one — a user who has progressed to 'submitted' and revisits
    the opportunity must land back on that same application, not a duplicate."""
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    assert client.patch(f"/api/applications/{created['id']}", json={"status": "submitted"}).status_code == 200

    reopened = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    assert reopened["id"] == created["id"]
    assert reopened["status"] == "submitted"
    assert reopened["created"] is False


def test_closed_application_permits_a_new_active_attempt(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    first = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    assert client.patch(f"/api/applications/{first['id']}", json={"status": "closed"}).status_code == 200

    second = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    assert second["created"] is True
    assert second["id"] != first["id"]
    assert second["status"] == "preparing"

    with db.db_cursor() as cur:
        cur.execute("SELECT status FROM jobber.application WHERE role_instance_id = %s ORDER BY created_at", (role_id,))
        statuses = [r["status"] for r in cur.fetchall()]
    assert statuses == ["closed", "preparing"]


def test_database_constraint_prevents_concurrent_duplicate_active_applications(client):
    """The real safety net is the partial unique index (migration 0027), not
    the app-layer check-then-insert — prove it directly at the SQL level: two
    concurrent raw inserts for the same role must never both succeed, even
    bypassing the ON CONFLICT upsert entirely."""
    with db.db_cursor() as cur:
        role_id = _posting(cur)

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _insert():
        try:
            with db.db_cursor() as cur:
                barrier.wait(timeout=5)
                cur.execute(
                    "INSERT INTO jobber.application (role_instance_id, status) VALUES (%s, 'preparing')",
                    (role_id,),
                )
            results.append(True)
        except psycopg.errors.UniqueViolation:
            results.append(False)

    threads = [threading.Thread(target=_insert) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.application WHERE role_instance_id = %s AND status IN "
            "('preparing', 'ready', 'submitted', 'interviewing')",
            (role_id,),
        )
        assert cur.fetchone()["n"] == 1


# --- list ----------------------------------------------------------------


def test_list_joins_role_metadata_without_per_row_fetches(client):
    with db.db_cursor() as cur:
        role_ids = [_posting(cur, title=f"Role {i}", organisation=f"Org {i}") for i in range(5)]
    for role_id in role_ids:
        client.post("/api/applications", json={"role_instance_id": role_id})

    with count_queries() as counted:
        resp = client.get("/api/applications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    assert {item["role"]["title"] for item in body["items"]} == {f"Role {i}" for i in range(5)}
    # A fixed, small number of queries regardless of row count (count + page)
    # — never one role lookup per application.
    assert counted.count <= 3


def test_list_supports_status_filter_and_pagination(client):
    with db.db_cursor() as cur:
        role_a, role_b = _posting(cur, title="A"), _posting(cur, title="B")
    app_a = client.post("/api/applications", json={"role_instance_id": role_a}).json()
    client.post("/api/applications", json={"role_instance_id": role_b})
    client.patch(f"/api/applications/{app_a['id']}", json={"status": "withdrawn"})

    withdrawn = client.get("/api/applications", params={"status": "withdrawn"}).json()
    assert withdrawn["total"] == 1
    assert withdrawn["items"][0]["id"] == app_a["id"]

    bounded = client.get("/api/applications", params={"limit": 1, "offset": 0}).json()
    assert len(bounded["items"]) == 1
    assert bounded["total"] == 2


def test_empty_list_is_a_clean_empty_state(client):
    body = client.get("/api/applications").json()
    assert body == {"items": [], "total": 0, "limit": 100, "offset": 0}


# --- detail / status ---------------------------------------------------------


def test_get_application_detail(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur, title="Detail role")
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()

    resp = client.get(f"/api/applications/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["application"]["id"] == created["id"]
    assert body["role"]["title"] == "Detail role"
    assert body["notes"] == []


def test_get_missing_application_is_404(client):
    assert client.get("/api/applications/11111111-1111-1111-1111-111111111111").status_code == 404


def test_status_update(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()

    resp = client.patch(f"/api/applications/{created['id']}", json={"status": "submitted"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "submitted"

    again = client.get(f"/api/applications/{created['id']}").json()
    assert again["application"]["status"] == "submitted"


def test_status_update_rejects_invalid_value(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    resp = client.patch(f"/api/applications/{created['id']}", json={"status": "hired"})
    assert resp.status_code == 422


def test_status_update_never_auto_derived_from_evidence(client):
    """Build §6/§10: structural checks passing/failing must never change
    status by themselves — creating an application with zero evidence and
    zero requirements must still default to, and stay at, 'preparing' until
    the user explicitly changes it."""
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id = _role_with_accepted_claim(cur, concept_id)
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    assert created["status"] == "preparing"
    # No accepted evidence exists for this role's one required concept
    # (blocking gap) — still 'preparing', not auto-advanced or auto-blocked.
    evidence = client.get(f"/api/applications/{created['id']}/evidence").json()
    assert len(evidence["blocking_gaps"]) == 1
    still = client.get(f"/api/applications/{created['id']}").json()
    assert still["application"]["status"] == "preparing"


# --- role deletion protection -------------------------------------------------


def test_role_deletion_with_application_is_rejected(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()

    resp = client.delete(f"/api/roles/{role_id}")
    assert resp.status_code == 409

    # Both the role and the application history survive intact.
    assert client.get(f"/api/roles/{role_id}").status_code == 200
    assert client.get(f"/api/applications/{created['id']}").status_code == 200


def test_role_deletion_still_rejected_once_application_is_closed(client):
    """Closed/withdrawn is still application *history* — it must not
    silently disappear just because the application is no longer active."""
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    client.patch(f"/api/applications/{created['id']}", json={"status": "withdrawn"})

    assert client.delete(f"/api/roles/{role_id}").status_code == 409


def test_role_without_application_still_deletes_normally(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    assert client.delete(f"/api/roles/{role_id}").status_code == 200
    assert client.get(f"/api/roles/{role_id}").status_code == 404


# --- notes ----------------------------------------------------------------


def test_notes_create_edit_delete(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id = _posting(cur)
    created = client.post("/api/applications", json={"role_instance_id": role_id}).json()
    app_id = created["id"]

    note = client.post(
        f"/api/applications/{app_id}/notes",
        json={"concept_id": concept_id, "note_type": "evidence_example", "note_text": "Led a Python migration at OldCo."},
    ).json()
    assert note["note_type"] == "evidence_example"
    assert note["note_text"] == "Led a Python migration at OldCo."

    edited = client.patch(f"/api/applications/{app_id}/notes/{note['id']}", json={"note_text": "Led a 6-month Python migration."}).json()
    assert edited["note_text"] == "Led a 6-month Python migration."
    assert edited["note_type"] == "evidence_example"  # untouched field preserved

    detail = client.get(f"/api/applications/{app_id}").json()
    assert len(detail["notes"]) == 1

    delete_resp = client.delete(f"/api/applications/{app_id}/notes/{note['id']}")
    assert delete_resp.status_code == 200
    detail_after = client.get(f"/api/applications/{app_id}").json()
    assert detail_after["notes"] == []


def test_general_note_needs_no_concept(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]
    resp = client.post(f"/api/applications/{app_id}/notes", json={"note_type": "general", "note_text": "Ask about hybrid policy."})
    assert resp.status_code == 200
    assert resp.json()["concept_id"] is None


def test_note_rejects_inactive_or_missing_concept(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('tool', 'Deprecated Thing', 'deprecated', 'curator', now()) RETURNING id"
        )
        deprecated_id = str(cur.fetchone()["id"])
    app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]

    resp = client.post(f"/api/applications/{app_id}/notes", json={"concept_id": deprecated_id, "note_type": "evidence_example", "note_text": "x"})
    assert resp.status_code == 400

    resp2 = client.post(
        f"/api/applications/{app_id}/notes",
        json={"concept_id": "11111111-1111-1111-1111-111111111111", "note_type": "evidence_example", "note_text": "x"},
    )
    assert resp2.status_code == 400


def test_note_on_missing_application_is_404(client):
    resp = client.post(
        "/api/applications/11111111-1111-1111-1111-111111111111/notes",
        json={"note_type": "general", "note_text": "x"},
    )
    assert resp.status_code == 404


def test_notes_do_not_alter_comparison_or_profile360_evidence(client):
    """Build §2/§6: an application-only note/example must never change the
    canonical comparison status, never create a requirement_claim, never
    create a person_capability_assertion, and never touch profile360."""
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id = _role_with_accepted_claim(cur, concept_id)
    app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]

    before = client.get(f"/api/comparison/role/{role_id}").json()
    assert before["items"][0]["status"] == "not_found"

    client.post(
        f"/api/applications/{app_id}/notes",
        json={"concept_id": concept_id, "note_type": "evidence_example", "note_text": "I have done this for years."},
    )

    after = client.get(f"/api/comparison/role/{role_id}").json()
    assert after["items"][0]["status"] == "not_found"  # unchanged by the note

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.person_capability_assertion WHERE jobber_concept_id = %s", (concept_id,))
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE concept_id = %s AND role_instance_id != %s", (concept_id, role_id))
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        assert cur.fetchone()["n"] == 0


# --- evidence pack ---------------------------------------------------------


def test_evidence_matches_comparison_semantics(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id = _role_with_accepted_claim(cur, concept_id)
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Used Python daily.') RETURNING id")
        claim_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
            "VALUES (%s, %s, 'curator_asserted', 'accepted')",
            (claim_id, concept_id),
        )
    app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]

    comparison = client.get(f"/api/comparison/role/{role_id}").json()
    evidence = client.get(f"/api/applications/{app_id}/evidence").json()

    assert evidence["counts"] == comparison["counts"]
    assert evidence["blocking_gaps"] == comparison["blocking_gaps"]
    assert evidence["unverified_required"] == comparison["unverified_required"]
    assert evidence["items"][0]["status"] == comparison["items"][0]["status"] == "evidenced"
    assert evidence["role_instance_id"] == role_id
    assert evidence["application_id"] == app_id


def test_evidence_flags_reviewed_claim_vs_legacy_observation(client):
    """Build §5: a `claim`-sourced (accepted, human-reviewed) requirement
    must read role_requirement_reviewed=true; a legacy
    role_skill_observation fallback item must read false — and must never be
    called a 'reviewed requirement'."""
    with db.db_cursor() as cur:
        reviewed_concept = _active_concept(cur, "Python")
        legacy_concept = _active_concept(cur, "SQL")
        role_id = _role_with_accepted_claim(cur, reviewed_concept)
        # A second, *legacy* role gets only a role_skill_observation, no
        # requirement_claim at all — the fallback path in role_requirements.py.
        legacy_role_id = _posting(cur, title="Legacy role")
        cur.execute(
            "INSERT INTO jobber.role_skill_observation "
            "(role_instance_id, surface_form, requirement_type, observation_basis, canonical_concept_id) "
            "VALUES (%s, 'SQL', 'required', 'legacy_extraction', %s) RETURNING id",
            (legacy_role_id, legacy_concept),
        )
        observation_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.role_skill_observation_concept "
            "(role_skill_observation_id, concept_id, mapping_basis) VALUES (%s, %s, 'legacy_single')",
            (observation_id, legacy_concept),
        )
    reviewed_app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]
    legacy_app_id = client.post("/api/applications", json={"role_instance_id": legacy_role_id}).json()["id"]

    reviewed_evidence = client.get(f"/api/applications/{reviewed_app_id}/evidence").json()
    legacy_evidence = client.get(f"/api/applications/{legacy_app_id}/evidence").json()

    assert reviewed_evidence["items"][0]["role_requirement_reviewed"] is True
    assert reviewed_evidence["items"][0]["role_side"]["requirement_source"] == "claim"

    assert legacy_evidence["items"][0]["role_requirement_reviewed"] is False
    assert legacy_evidence["items"][0]["role_side"]["requirement_source"] == "role_skill_observation"


def test_evidence_pack_attaches_notes_to_their_concept_and_lists_all_notes(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id = _role_with_accepted_claim(cur, concept_id)
    app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]

    client.post(f"/api/applications/{app_id}/notes", json={"note_type": "general", "note_text": "General prep note."})
    client.post(
        f"/api/applications/{app_id}/notes",
        json={"concept_id": concept_id, "note_type": "evidence_example", "note_text": "Example for Python."},
    )

    evidence = client.get(f"/api/applications/{app_id}/evidence").json()
    assert len(evidence["notes"]) == 2
    item = next(i for i in evidence["items"] if i["concept"]["id"] == concept_id)
    assert len(item["notes"]) == 1
    assert item["notes"][0]["note_text"] == "Example for Python."


def test_evidence_on_missing_application_is_404(client):
    assert client.get("/api/applications/11111111-1111-1111-1111-111111111111/evidence").status_code == 404
