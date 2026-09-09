"""Accepted-vocabulary maintenance API (cp_round_of_changes.md §B):
PATCH /api/concepts/{id} (metadata + safe type changes) and the alias
add/remove endpoints. See app/concept_curation.py for the safety rules this
exercises."""

import uuid

from app import db


def _concept(cur, *, type_code="knowledge", name="Solvency II", status="active", definition=None):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at) "
        "VALUES (%s, %s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, name, definition, status),
    )
    return str(cur.fetchone()["id"])


def _capability(cur, name="Lead a reserving process"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('capability', %s, 'active', 'curator', now()) RETURNING id",
        (name,),
    )
    cap_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) "
        "VALUES (%s, 'Named owner end to end.', 'owned')",
        (cap_id,),
    )
    return cap_id


# --- metadata editing --------------------------------------------------------

def test_update_canonical_name_and_definition(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    resp = client.patch(f"/api/concepts/{concept_id}", json={"canonical_name": "ORSA (Own Risk and Solvency Assessment)", "definition": "A Solvency II self-assessment process."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["canonical_name"] == "ORSA (Own Risk and Solvency Assessment)"
    assert body["definition"] == "A Solvency II self-assessment process."

    reread = client.get(f"/api/concepts/{concept_id}")
    assert reread.json()["canonical_name"] == "ORSA (Own Risk and Solvency Assessment)"


def test_partial_update_leaves_other_fields_untouched(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Prophet", definition="An actuarial modelling tool.", type_code="tool")

    resp = client.patch(f"/api/concepts/{concept_id}", json={"definition": "Updated definition."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["canonical_name"] == "Prophet"
    assert body["type_code"] == "tool"
    assert body["definition"] == "Updated definition."


def test_update_unknown_concept_is_404(client):
    resp = client.patch(f"/api/concepts/{uuid.uuid4()}", json={"definition": "x"})
    assert resp.status_code == 404


def test_duplicate_canonical_name_rejected(client):
    with db.db_cursor() as cur:
        _concept(cur, name="Capital Management", type_code="capability")
        other_id = _concept(cur, name="Capital Modelling", type_code="capability")

    resp = client.patch(f"/api/concepts/{other_id}", json={"canonical_name": "Capital Management"})
    assert resp.status_code == 400


def test_editing_a_merged_concept_is_rejected(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Old duplicate", status="merged")

    resp = client.patch(f"/api/concepts/{concept_id}", json={"definition": "x"})
    assert resp.status_code == 409


# --- deprecate / reactivate ---------------------------------------------------

def test_deprecate_then_reactivate(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Stakeholder engagement")

    deprecate = client.patch(f"/api/concepts/{concept_id}", json={"status": "deprecated"})
    assert deprecate.status_code == 200
    assert deprecate.json()["status"] == "deprecated"

    # Still editable while deprecated — deprecation is not a hard delete.
    reactivate = client.patch(f"/api/concepts/{concept_id}", json={"status": "active"})
    assert reactivate.status_code == 200
    assert reactivate.json()["status"] == "active"


def test_invalid_status_value_rejected(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur)

    resp = client.patch(f"/api/concepts/{concept_id}", json={"status": "rejected"})
    assert resp.status_code == 422  # Pydantic validation — not a route this generic editor exposes


def test_concept_is_never_hard_deleted(client):
    """There is deliberately no DELETE /api/concepts/{id} — deprecation is
    the only "remove from active use" path."""
    with db.db_cursor() as cur:
        concept_id = _concept(cur)

    resp = client.delete(f"/api/concepts/{concept_id}")
    assert resp.status_code in (404, 405)
    with db.db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.concept WHERE id = %s", (concept_id,))
        assert cur.fetchone() is not None


# --- aliases -------------------------------------------------------------------

def test_add_and_remove_alias_independently(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Solvency II")

    add = client.post(f"/api/concepts/{concept_id}/aliases", json={"alias": "SII"})
    assert add.status_code == 200
    alias_id = add.json()["id"]

    detail = client.get(f"/api/concepts/{concept_id}").json()
    assert {a["alias"] for a in detail["aliases"]} == {"SII"}

    remove = client.delete(f"/api/concepts/{concept_id}/aliases/{alias_id}")
    assert remove.status_code == 200

    detail_after = client.get(f"/api/concepts/{concept_id}").json()
    assert detail_after["aliases"] == []


def test_duplicate_alias_on_same_concept_rejected(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Solvency II")

    first = client.post(f"/api/concepts/{concept_id}/aliases", json={"alias": "SII"})
    assert first.status_code == 200
    second = client.post(f"/api/concepts/{concept_id}/aliases", json={"alias": "SII"})
    assert second.status_code == 400


def test_empty_alias_rejected(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur)

    resp = client.post(f"/api/concepts/{concept_id}/aliases", json={"alias": "   "})
    assert resp.status_code == 422


def test_remove_nonexistent_alias_is_404(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur)

    resp = client.delete(f"/api/concepts/{concept_id}/aliases/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_alias_add_on_unknown_concept_is_404(client):
    resp = client.post(f"/api/concepts/{uuid.uuid4()}/aliases", json={"alias": "x"})
    assert resp.status_code == 404


# --- safe type corrections (atomic-to-atomic) ---------------------------------

def test_atomic_to_atomic_type_correction_works(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, type_code="knowledge", name="Prophet")

    resp = client.patch(f"/api/concepts/{concept_id}", json={"type_code": "tool"})
    assert resp.status_code == 200
    assert resp.json()["type_code"] == "tool"


def test_type_correction_to_unknown_type_rejected(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur)

    resp = client.patch(f"/api/concepts/{concept_id}", json={"type_code": "not_a_real_type"})
    assert resp.status_code == 400


def test_type_change_that_would_break_broader_than_edge_is_rejected(client):
    """broader_than requires from_type == to_type (concept_edge_rule seed
    data). Retyping one side away from the shared atom type must be rejected
    with a 409, never silently applied."""
    with db.db_cursor() as cur:
        narrower_id = _concept(cur, type_code="knowledge", name="Reserving under Solvency II")
        broader_id = _concept(cur, type_code="knowledge", name="Reserving")
        cur.execute(
            "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, origin, status) "
            "VALUES (%s, %s, 'broader_than', 'curator', 'accepted')",
            (broader_id, narrower_id),
        )

    resp = client.patch(f"/api/concepts/{broader_id}", json={"type_code": "tool"})
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert "conflicts" in body

    # Nothing was changed.
    with db.db_cursor() as cur:
        cur.execute("SELECT type_code FROM jobber.concept WHERE id = %s", (broader_id,))
        assert cur.fetchone()["type_code"] == "knowledge"


# --- capability transitions ----------------------------------------------------

def test_entering_capability_without_demonstration_standard_is_rejected(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, type_code="method", name="Reserving process leadership")

    resp = client.patch(f"/api/concepts/{concept_id}", json={"type_code": "capability"})
    assert resp.status_code == 409

    with db.db_cursor() as cur:
        cur.execute("SELECT type_code FROM jobber.concept WHERE id = %s", (concept_id,))
        assert cur.fetchone()["type_code"] == "method"
        cur.execute("SELECT 1 FROM jobber.capability_detail WHERE concept_id = %s", (concept_id,))
        assert cur.fetchone() is None


def test_entering_capability_with_demonstration_standard_succeeds(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, type_code="method", name="Reserving process leadership")

    resp = client.patch(
        f"/api/concepts/{concept_id}",
        json={"type_code": "capability", "capability_detail": {"demonstration_standard": "Named owner end to end."}},
    )
    assert resp.status_code == 200
    assert resp.json()["type_code"] == "capability"

    with db.db_cursor() as cur:
        cur.execute("SELECT demonstration_standard, min_depth FROM jobber.capability_detail WHERE concept_id = %s", (concept_id,))
        row = cur.fetchone()
    assert row["demonstration_standard"] == "Named owner end to end."
    assert row["min_depth"] == "owned"

    # Now visible via the Capabilities API — the two rows never drift apart.
    cap = client.get(f"/api/capabilities/{concept_id}")
    assert cap.status_code == 200


def test_leaving_capability_with_no_components_removes_detail_row(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur)

    resp = client.patch(f"/api/concepts/{cap_id}", json={"type_code": "tool"})
    assert resp.status_code == 200
    assert resp.json()["type_code"] == "tool"

    with db.db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.capability_detail WHERE concept_id = %s", (cap_id,))
        assert cur.fetchone() is None


def test_leaving_capability_with_existing_component_edges_is_rejected(client):
    """A capability with real component_of edges cannot be silently retyped
    out from under them — 409, and neither the type nor the edges change."""
    with db.db_cursor() as cur:
        cap_id = _capability(cur)
        tool_id = _concept(cur, type_code="tool", name="Prophet")
        cur.execute(
            "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, origin, status) "
            "VALUES (%s, %s, 'component_of', 'curator', 'accepted')",
            (tool_id, cap_id),
        )

    resp = client.patch(f"/api/concepts/{cap_id}", json={"type_code": "tool"})
    assert resp.status_code == 409

    with db.db_cursor() as cur:
        cur.execute("SELECT type_code FROM jobber.concept WHERE id = %s", (cap_id,))
        assert cur.fetchone()["type_code"] == "capability"
        cur.execute("SELECT 1 FROM jobber.capability_detail WHERE concept_id = %s", (cap_id,))
        assert cur.fetchone() is not None
        cur.execute("SELECT count(*) AS n FROM jobber.concept_edge WHERE to_concept_id = %s", (cap_id,))
        assert cur.fetchone()["n"] == 1


# --- auth ------------------------------------------------------------------

def test_concept_maintenance_endpoints_reject_unauthenticated_calls(anon_client):
    concept_id = str(uuid.uuid4())
    assert anon_client.patch(f"/api/concepts/{concept_id}", json={"definition": "x"}).status_code in (401, 403)
    assert anon_client.post(f"/api/concepts/{concept_id}/aliases", json={"alias": "x"}).status_code in (401, 403)
    assert anon_client.delete(f"/api/concepts/{concept_id}/aliases/{uuid.uuid4()}").status_code in (401, 403)
