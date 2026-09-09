"""Accepted-vocabulary maintenance API (cp_round_of_changes.md §B):
PATCH /api/concepts/{id} (metadata + safe type changes) and the alias
add/remove endpoints. See app/concept_curation.py for the safety rules this
exercises."""

import uuid

from app import db
from app.embeddings import set_embedding


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
    """A same-type-code collision against another *active* concept is now
    caught by the application-level active-vocabulary integrity check
    (409, naming the conflict) before it would ever reach the DB's
    UNIQUE(type_code, canonical_name) constraint — see
    test_duplicate_canonical_name_against_deprecated_concept_still_hits_db_constraint
    below for the one case where that DB-level 400 path is still reached."""
    with db.db_cursor() as cur:
        _concept(cur, name="Capital Management", type_code="capability")
        other_id = _concept(cur, name="Capital Modelling", type_code="capability")

    resp = client.patch(f"/api/concepts/{other_id}", json={"canonical_name": "Capital Management"})
    assert resp.status_code == 409


def test_duplicate_canonical_name_against_deprecated_concept_still_hits_db_constraint(client):
    """The new active-vocabulary check deliberately never treats a
    deprecated concept's canonical_name as a conflict source (§1) — but the
    pre-existing DB-level UNIQUE(type_code, canonical_name) constraint
    applies regardless of status, so this same-type-code collision is still
    rejected, just via the older 400 path rather than the new 409 one."""
    with db.db_cursor() as cur:
        _concept(cur, name="Capital Management", type_code="capability", status="deprecated")
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


# --- active-vocabulary surface-form integrity (follow-up patch §1) ---------
#
# Alias-add and canonical-name-rename must not create an ambiguous active
# surface form — a normalised alias/canonical_name colliding with another
# *active* concept's canonical_name or alias is rejected with a 409. Only
# 'active' concepts are ever a conflict source: a deprecated concept's old
# name/aliases must never block reuse of that surface form elsewhere.

def test_add_alias_conflicts_with_another_active_alias(client):
    with db.db_cursor() as cur:
        holder_id = _concept(cur, name="Solvency II", type_code="regulation")
        other_id = _concept(cur, name="Own Risk and Solvency Assessment", type_code="knowledge")
    add = client.post(f"/api/concepts/{holder_id}/aliases", json={"alias": "SII"})
    assert add.status_code == 200

    conflict = client.post(f"/api/concepts/{other_id}/aliases", json={"alias": "  sii  "})
    assert conflict.status_code == 409
    assert "Solvency II" in conflict.json()["detail"]

    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.concept_alias WHERE concept_id = %s", (other_id,))
        assert cur.fetchone()["n"] == 0


def test_add_alias_conflicts_with_another_active_canonical_name(client):
    with db.db_cursor() as cur:
        _concept(cur, name="Solvency II", type_code="regulation")
        other_id = _concept(cur, name="ORSA", type_code="knowledge")

    conflict = client.post(f"/api/concepts/{other_id}/aliases", json={"alias": "solvency ii"})
    assert conflict.status_code == 409
    assert "Solvency II" in conflict.json()["detail"]


def test_canonical_rename_conflicts_with_another_active_alias(client):
    with db.db_cursor() as cur:
        holder_id = _concept(cur, name="Solvency II", type_code="regulation")
        renamer_id = _concept(cur, name="ORSA", type_code="knowledge")
    client.post(f"/api/concepts/{holder_id}/aliases", json={"alias": "SII"})

    conflict = client.patch(f"/api/concepts/{renamer_id}", json={"canonical_name": "SII"})
    assert conflict.status_code == 409
    assert "Solvency II" in conflict.json()["detail"]

    with db.db_cursor() as cur:
        cur.execute("SELECT canonical_name FROM jobber.concept WHERE id = %s", (renamer_id,))
        assert cur.fetchone()["canonical_name"] == "ORSA"


def test_canonical_rename_conflicts_with_another_active_canonical_name_across_types(client):
    """The DB's own UNIQUE(type_code, canonical_name) constraint would not
    catch a cross-type collision — this is exactly what the new
    application-level check adds."""
    with db.db_cursor() as cur:
        _concept(cur, name="Capital Management", type_code="capability")
        renamer_id = _concept(cur, name="Capital Modelling", type_code="capability")
        other_type_renamer_id = _concept(cur, name="Something Else", type_code="knowledge")

    same_type_conflict = client.patch(f"/api/concepts/{renamer_id}", json={"canonical_name": "Capital Management"})
    assert same_type_conflict.status_code == 409

    cross_type_conflict = client.patch(f"/api/concepts/{other_type_renamer_id}", json={"canonical_name": "capital management"})
    assert cross_type_conflict.status_code == 409
    assert "Capital Management" in cross_type_conflict.json()["detail"]


def test_harmless_aliases_on_the_same_concept(client):
    """Adding several distinct aliases to one concept never conflicts with
    itself, and renaming a concept to match one of its own aliases is not a
    conflict (only *other* concepts are ever a conflict source)."""
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA", type_code="knowledge")

    first = client.post(f"/api/concepts/{concept_id}/aliases", json={"alias": "Own Risk and Solvency Assessment"})
    assert first.status_code == 200
    second = client.post(f"/api/concepts/{concept_id}/aliases", json={"alias": "ORSA process"})
    assert second.status_code == 200

    rename = client.patch(f"/api/concepts/{concept_id}", json={"canonical_name": "Own Risk and Solvency Assessment"})
    assert rename.status_code == 200
    assert rename.json()["canonical_name"] == "Own Risk and Solvency Assessment"


def test_deprecated_concept_alias_does_not_block_active_concept(client):
    with db.db_cursor() as cur:
        deprecated_id = _concept(cur, name="Old Term", type_code="knowledge", status="deprecated")
        active_id = _concept(cur, name="New Term", type_code="knowledge")
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) VALUES (%s, 'Shared Label', 'curator', now())",
            (deprecated_id,),
        )

    add = client.post(f"/api/concepts/{active_id}/aliases", json={"alias": "Shared Label"})
    assert add.status_code == 200

    rename = client.patch(f"/api/concepts/{active_id}", json={"canonical_name": "Shared Label"})
    assert rename.status_code == 200


def test_deprecated_concept_canonical_name_does_not_block_active_concept(client):
    with db.db_cursor() as cur:
        deprecated_id = _concept(cur, name="Prophet", type_code="tool", status="deprecated")
        active_id = _concept(cur, name="Something Unrelated", type_code="tool")
    assert deprecated_id  # the deprecated row exists but must not block reuse below

    add = client.post(f"/api/concepts/{active_id}/aliases", json={"alias": "Prophet"})
    assert add.status_code == 200

    with db.db_cursor() as cur:
        other_id = _concept(cur, name="Another Concept", type_code="tool")
    rename = client.patch(f"/api/concepts/{other_id}", json={"canonical_name": "Prophet"})
    # "Prophet" is now an active alias (of active_id) — this collision IS
    # real and must still be rejected, proving the deprecated concept's
    # original canonical_name specifically was not what was blocking things.
    assert rename.status_code == 409


# --- concept embedding invalidation (follow-up patch §2) -------------------

def _has_concept_embedding(cur, concept_id: str) -> bool:
    cur.execute("SELECT 1 FROM jobber.d_embedding WHERE owner_kind = 'concept' AND owner_id = %s", (concept_id,))
    return cur.fetchone() is not None


def test_rename_invalidates_existing_concept_embedding(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Solvency II", type_code="regulation")
        set_embedding(cur, "concept", concept_id, [1.0, 0.0, 0.0] + [0.0] * 381)
        assert _has_concept_embedding(cur, concept_id)

    resp = client.patch(f"/api/concepts/{concept_id}", json={"canonical_name": "Solvency II Directive"})
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        assert not _has_concept_embedding(cur, concept_id)


def test_definition_edit_invalidates_existing_concept_embedding(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA", type_code="knowledge", definition="Old definition.")
        set_embedding(cur, "concept", concept_id, [0.0, 1.0, 0.0] + [0.0] * 381)
        assert _has_concept_embedding(cur, concept_id)

    resp = client.patch(f"/api/concepts/{concept_id}", json={"definition": "A materially different definition."})
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        assert not _has_concept_embedding(cur, concept_id)


def test_unrelated_metadata_edit_does_not_delete_embedding(client):
    """A status-only or type-only edit never touches canonical_name/
    definition — the embedded text — so any existing vector must survive."""
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Prophet", type_code="tool")
        set_embedding(cur, "concept", concept_id, [0.0, 0.0, 1.0] + [0.0] * 381)
        assert _has_concept_embedding(cur, concept_id)

    deprecate = client.patch(f"/api/concepts/{concept_id}", json={"status": "deprecated"})
    assert deprecate.status_code == 200
    with db.db_cursor() as cur:
        assert _has_concept_embedding(cur, concept_id)

    retype = client.patch(f"/api/concepts/{concept_id}", json={"type_code": "knowledge"})
    assert retype.status_code == 200
    with db.db_cursor() as cur:
        assert _has_concept_embedding(cur, concept_id)


def test_noop_canonical_name_resubmission_does_not_delete_embedding(client):
    """Resubmitting the same canonical_name (no actual semantic change)
    must not needlessly invalidate the vector."""
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Prophet", type_code="tool")
        set_embedding(cur, "concept", concept_id, [0.2, 0.2, 0.2] + [0.0] * 381)

    resp = client.patch(f"/api/concepts/{concept_id}", json={"canonical_name": "Prophet"})
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        assert _has_concept_embedding(cur, concept_id)
