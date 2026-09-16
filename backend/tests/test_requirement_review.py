"""Requirement review as a real human-curation gate (routes/role_instances.py
accept/edit/reject/reopen/add-requirement): correction-aware review, non-
destructive supersession, server-side provenance/span enforcement, and the
default (current-only) requirement listing. `app.role_requirements`/
`app.extraction`'s own semantics are covered in test_role_requirements.py
and test_requirement_claims.py respectively — this file is the HTTP-route
layer that puts a human curator in the loop."""

import uuid

from app import db
from app.concept_linking import get_or_create_current_vocabulary_version


def _active_concept(cur, name: str, type_code: str = "tool", status: str = "active") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, name, status),
    )
    return str(cur.fetchone()["id"])


def _role_with_document(cur, body: str = "Requires strong Python skills.", provenance_quality: str = "original") -> tuple[str, str]:
    document_id, _ = db.create_document(cur, kind="job_posting", content_text=body, provenance_quality=provenance_quality)
    role_id = db.upsert_role_instance(
        cur, None, {"instance_type": "observed_posting", "title": "Test posting", "document_id": document_id}, skills=[]
    )
    return role_id, document_id


def _claim(cur, role_id, concept_id, *, document_id=None, evidence_span=None, basis="stated",
           requirement_type="required", review_status="unreviewed", extraction_run_id=None, claim_id=None) -> str:
    if claim_id is not None:
        cur.execute(
            """
            INSERT INTO jobber.requirement_claim
                (id, role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span,
                 review_status, extraction_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (claim_id, role_id, concept_id, requirement_type, basis, document_id, evidence_span, review_status, extraction_run_id),
        )
        return str(cur.fetchone()["id"])
    cur.execute(
        """
        INSERT INTO jobber.requirement_claim
            (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span,
             review_status, extraction_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (role_id, concept_id, requirement_type, basis, document_id, evidence_span, review_status, extraction_run_id),
    )
    return str(cur.fetchone()["id"])


def _extraction_run(cur, role_id, document_id) -> str:
    vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
    cur.execute(
        """
        INSERT INTO jobber.extraction_run
            (task, subject_type, document_id, role_instance_id, model, prompt_name, prompt_version,
             vocabulary_version_id, started_at, finished_at, status)
        VALUES ('requirement_extract', 'document', %s, %s, 'test-model', 'p', 'v', %s, now(), now(), 'ok')
        RETURNING id
        """,
        (document_id, role_id, vocabulary_version_id),
    )
    return str(cur.fetchone()["id"])


# --- Accept: unchanged, provenance-preserving -------------------------------

def test_accept_marks_the_original_claim_accepted_without_a_replacement_row(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        run_id = _extraction_run(cur, role_id, document_id)
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id,
                           evidence_span="strong Python skills", extraction_run_id=run_id)

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/accept")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == claim_id
    assert body["review_status"] == "accepted"

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1  # no replacement row
        cur.execute("SELECT extraction_run_id, evidence_span, superseded_by FROM jobber.requirement_claim WHERE id = %s", (claim_id,))
        row = cur.fetchone()
    assert str(row["extraction_run_id"]) == run_id  # original provenance untouched
    assert row["evidence_span"] == "strong Python skills"
    assert row["superseded_by"] is None


def test_accept_only_valid_from_unreviewed(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id,
                           evidence_span="strong Python skills", review_status="accepted")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/accept")
    assert resp.status_code == 409


def test_reject_only_valid_from_unreviewed_or_accepted(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id,
                           evidence_span="strong Python skills", review_status="rejected")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/reject")
    assert resp.status_code == 409


def test_reject_un_accepts_an_accepted_claim_via_supersession_preserving_audit_trail(client):
    """A review mistake must be recoverable even after Accept — Edit alone
    cannot express 'this isn't a requirement at all'. Un-accepting never
    mutates the accepted decision in place: the original accepted row is
    preserved as 'corrected' history (not deleted, not silently flipped),
    and a *new* rejected row becomes current, exactly as an /edit correction
    would produce."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        accepted_claim_id = _claim(cur, role_id, concept_id, document_id=document_id,
                                    evidence_span="strong Python skills", review_status="accepted")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{accepted_claim_id}/reject")
    assert resp.status_code == 200
    new = resp.json()
    assert new["id"] != accepted_claim_id
    assert new["review_status"] == "rejected"
    assert new["concept_id"] == concept_id
    assert new["evidence_span"] == "strong Python skills"  # carried over from the accepted claim, not lost

    with db.db_cursor() as cur:
        cur.execute("SELECT review_status, superseded_by FROM jobber.requirement_claim WHERE id = %s", (accepted_claim_id,))
        old = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        total = cur.fetchone()["n"]
    assert old["review_status"] == "corrected"  # preserved as history, not deleted or flipped in place
    assert str(old["superseded_by"]) == new["id"]
    assert total == 2

    # And the newly-current rejected claim can itself be reopened, same as any other.
    reopen_resp = client.post(f"/api/role-instances/{role_id}/requirements/{new['id']}/reopen")
    assert reopen_resp.status_code == 200
    assert reopen_resp.json()["review_status"] == "unreviewed"


# --- Reject / Reopen ---------------------------------------------------------

def test_reject_then_reopen_returns_to_unreviewed(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    reject_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/reject")
    assert reject_resp.status_code == 200
    assert reject_resp.json()["review_status"] == "rejected"

    reopen_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/reopen")
    assert reopen_resp.status_code == 200
    assert reopen_resp.json()["review_status"] == "unreviewed"

    with db.db_cursor() as cur:
        cur.execute("SELECT reviewed_at FROM jobber.requirement_claim WHERE id = %s", (claim_id,))
        assert cur.fetchone()["reviewed_at"] is None  # no longer "reviewed"


def test_reopen_only_valid_from_rejected(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/reopen")
    assert resp.status_code == 409


def test_no_direct_database_intervention_needed_to_undo_a_rejection(client):
    """An accidental Reject must be recoverable entirely through the API."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/reject")
    client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/reopen")
    accept_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/accept")
    assert accept_resp.status_code == 200
    assert accept_resp.json()["review_status"] == "accepted"


# --- Edit & accept / Edit: non-destructive supersession ---------------------

def test_edit_and_accept_creates_new_row_marks_old_corrected_and_links_superseded_by(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills, ideally with SQL.")
        python_id = _active_concept(cur, "Python")
        sql_id = _active_concept(cur, "SQL")
        run_id = _extraction_run(cur, role_id, document_id)
        old_claim_id = _claim(cur, role_id, python_id, document_id=document_id,
                               evidence_span="strong Python skills", extraction_run_id=run_id)

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{old_claim_id}/edit",
        json={"concept_id": sql_id, "evidence_span": "SQL"},
    )
    assert resp.status_code == 200
    new = resp.json()
    assert new["id"] != old_claim_id
    assert new["review_status"] == "accepted"
    assert new["concept_id"] == sql_id
    assert new["evidence_span"] == "SQL"
    assert new["extraction_run_id"] is None  # not the output of any extraction run

    with db.db_cursor() as cur:
        cur.execute("SELECT review_status, superseded_by, extraction_run_id, concept_id, evidence_span "
                    "FROM jobber.requirement_claim WHERE id = %s", (old_claim_id,))
        old = cur.fetchone()
    assert old["review_status"] == "corrected"
    assert str(old["superseded_by"]) == new["id"]
    # The original AI claim is preserved untouched, not overwritten in place.
    assert str(old["extraction_run_id"]) == run_id
    assert str(old["concept_id"]) == python_id
    assert old["evidence_span"] == "strong Python skills"


def test_an_accepted_claim_can_later_be_corrected_via_the_same_path(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id,
                           evidence_span="strong Python skills", review_status="accepted")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"requirement_type": "preferred"},
    )
    assert resp.status_code == 200
    new = resp.json()
    assert new["requirement_type"] == "preferred"
    assert new["review_status"] == "accepted"

    with db.db_cursor() as cur:
        cur.execute("SELECT review_status, superseded_by FROM jobber.requirement_claim WHERE id = %s", (claim_id,))
        old = cur.fetchone()
    assert old["review_status"] == "corrected"
    assert str(old["superseded_by"]) == new["id"]


def test_edit_requires_reopening_a_rejected_claim_first(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id,
                           evidence_span="strong Python skills", review_status="rejected")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/edit", json={"requirement_type": "preferred"})
    assert resp.status_code == 409


def test_a_superseded_claim_cannot_be_acted_on_directly(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        old_claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")
        # Migration 0020's partial unique index allows at most one *current*
        # claim per (role, concept), so the old row must stop being current
        # before the replacement (same concept) is inserted — the deferred
        # superseded_by FK lets the old row point at the new row's
        # pre-generated id before that row exists, same as
        # routes/role_instances.py::_supersede_with_new_claim.
        new_claim_id = str(uuid.uuid4())
        cur.execute("UPDATE jobber.requirement_claim SET review_status = 'corrected', superseded_by = %s WHERE id = %s",
                    (new_claim_id, old_claim_id))
        _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills",
               review_status="accepted", claim_id=new_claim_id)

    for action in ("accept", "reject", "reopen", "edit"):
        payload = {} if action != "edit" else {"requirement_type": "preferred"}
        resp = client.post(f"/api/role-instances/{role_id}/requirements/{old_claim_id}/{action}", json=payload)
        assert resp.status_code == 409, action


# --- Concept remapping validation --------------------------------------------

def test_remapping_to_an_active_concept_succeeds(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        python_id = _active_concept(cur, "Python")
        sql_id = _active_concept(cur, "SQL")
        claim_id = _claim(cur, role_id, python_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/edit", json={"concept_id": sql_id})
    assert resp.status_code == 200
    assert resp.json()["concept_id"] == sql_id


def test_remapping_to_a_deprecated_concept_is_rejected(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        python_id = _active_concept(cur, "Python")
        deprecated_id = _active_concept(cur, "Old thing", status="deprecated")
        claim_id = _claim(cur, role_id, python_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/edit", json={"concept_id": deprecated_id})
    assert resp.status_code == 400
    with db.db_cursor() as cur:
        cur.execute("SELECT review_status FROM jobber.requirement_claim WHERE id = %s", (claim_id,))
        assert cur.fetchone()["review_status"] == "unreviewed"  # untouched by the rejected edit


def test_remapping_to_a_nonexistent_concept_is_rejected(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        python_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, python_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"concept_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 400


def test_remapping_onto_a_concept_this_role_already_has_a_current_claim_for_is_rejected(client):
    """Two current claims for the same (role, concept) would double-count in
    capability_engine.derive_role_fit and break the rerun-dedup code's
    at-most-one-current-claim assumption — an Edit that would create that
    situation is rejected with a clear 409, not left to a raw database
    integrity error."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        python_id = _active_concept(cur, "Python")
        sql_id = _active_concept(cur, "SQL")
        _claim(cur, role_id, sql_id, document_id=document_id, evidence_span="strong Python skills", review_status="accepted")
        python_claim_id = _claim(cur, role_id, python_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{python_claim_id}/edit", json={"concept_id": sql_id})
    assert resp.status_code == 409
    with db.db_cursor() as cur:
        cur.execute("SELECT review_status, superseded_by FROM jobber.requirement_claim WHERE id = %s", (python_claim_id,))
        row = cur.fetchone()
    assert row["review_status"] == "unreviewed"  # untouched by the rejected edit
    assert row["superseded_by"] is None


def test_editing_without_remapping_away_from_the_same_concept_is_unaffected_by_the_duplicate_check(client):
    """The duplicate-concept check must not trip over a claim's own current
    row when the concept isn't actually changing (the common case: editing
    type/basis/span/importance without remapping)."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"concept_id": concept_id, "requirement_type": "preferred"},
    )
    assert resp.status_code == 200
    assert resp.json()["requirement_type"] == "preferred"


def test_edit_with_a_field_identical_patch_does_not_manufacture_a_replacement(client):
    """The frontend collapses a no-op edit into a plain Accept, but the
    server must enforce this too — a direct API call with a patch matching
    the claim's current values exactly must never create a pointless
    replacement/corrected pair."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"concept_id": concept_id, "requirement_type": "required", "basis": "stated",
              "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == claim_id  # same row — no replacement was created
    assert body["review_status"] == "accepted"

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1

    # Editing an already-accepted claim with an identical patch is a true no-op.
    resp2 = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"concept_id": concept_id, "requirement_type": "required", "basis": "stated",
              "evidence_span": "strong Python skills"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["id"] == claim_id
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1


# --- Span/provenance server-side enforcement --------------------------------

def test_edited_stated_span_is_validated_against_the_immutable_document(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"evidence_span": "a quote that was never in the document"},
    )
    assert resp.status_code == 400


def test_stated_edit_requires_a_document_and_span(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        # A user_asserted claim with no document at all, as a target role might carry.
        claim_id = _claim(cur, role_id, concept_id, document_id=None, evidence_span=None, basis="user_asserted")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"basis": "stated", "evidence_span": "anything"},
    )
    assert resp.status_code == 400


def test_weak_provenance_document_cannot_back_promoted_stated_evidence(client):
    """A legacy/reconstructed document can never support stated/implied
    evidence, however good the span looks — not even by editing to say so."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.", provenance_quality="legacy_extracted")
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span=None, basis="inferred")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"basis": "stated", "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 400
    with db.db_cursor() as cur:
        cur.execute("SELECT basis, review_status FROM jobber.requirement_claim WHERE id = %s", (claim_id,))
        row = cur.fetchone()
    assert row["basis"] == "inferred"  # untouched
    assert row["review_status"] == "unreviewed"


def test_genuine_original_document_can_back_an_edit_into_stated_evidence(client):
    """The positive case for the same rule: a genuinely original capture CAN
    support a promotion into stated evidence, with a validated exact span."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.", provenance_quality="original")
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span=None, basis="inferred")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"basis": "stated", "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 200
    assert resp.json()["basis"] == "stated"
    assert resp.json()["evidence_span"] == "strong Python skills"


def test_edit_drops_evidence_span_once_basis_no_longer_needs_it(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements/{claim_id}/edit",
        json={"basis": "inferred", "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 200
    assert resp.json()["basis"] == "inferred"
    assert resp.json()["evidence_span"] is None


def test_importance_out_of_range_is_rejected(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/edit", json={"importance": 9})
    assert resp.status_code == 400


def test_importance_can_be_cleared_to_blank(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")
        cur.execute("UPDATE jobber.requirement_claim SET importance = 3 WHERE id = %s", (claim_id,))

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/edit", json={"importance": None})
    assert resp.status_code == 200
    assert resp.json()["importance"] is None


def test_invalid_requirement_type_is_rejected(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/edit", json={"requirement_type": "mandatory"})
    assert resp.status_code == 400


# --- Default listing excludes superseded history ----------------------------

def test_default_listing_excludes_superseded_history_but_history_flag_shows_it(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur)
        concept_id = _active_concept(cur, "Python")
        claim_id = _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills")

    edit_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/edit", json={"requirement_type": "preferred"})
    new_id = edit_resp.json()["id"]

    default = client.get(f"/api/role-instances/{role_id}/requirements").json()
    default_ids = {item["id"] for item in default["items"]}
    assert default_ids == {new_id}  # the superseded original is not a duplicate current card
    assert default["review_summary"] == {
        "accepted": 1, "unreviewed": 0, "rejected": 0,
        "unresolved_proposals": 0, "extraction_attempted": False, "needs_reextraction": 0, "complete": True,
    }

    history = client.get(f"/api/role-instances/{role_id}/requirements", params={"history": "true"}).json()
    history_ids = {item["id"] for item in history["items"]}
    assert history_ids == {claim_id, new_id}


def test_listing_404s_for_a_nonexistent_role(client):
    resp = client.get("/api/role-instances/00000000-0000-0000-0000-000000000000/requirements")
    assert resp.status_code == 404


# --- Manual "Add requirement" -----------------------------------------------

def test_add_requirement_creates_an_accepted_source_backed_claim(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        concept_id = _active_concept(cur, "Python")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements",
        json={"concept_id": concept_id, "requirement_type": "required", "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_status"] == "accepted"
    assert body["extraction_run_id"] is None
    assert body["basis"] == "stated"
    assert body["evidence_span"] == "strong Python skills"


def test_add_requirement_validates_the_span_server_side(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        concept_id = _active_concept(cur, "Python")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements",
        json={"concept_id": concept_id, "requirement_type": "required", "evidence_span": "an invented requirement"},
    )
    assert resp.status_code == 400
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 0


def test_add_requirement_only_accepts_an_active_concept(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        deprecated_id = _active_concept(cur, "Old thing", status="deprecated")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements",
        json={"concept_id": deprecated_id, "requirement_type": "required", "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 400


def test_add_requirement_rejects_a_concept_this_role_already_has_a_current_claim_for(client):
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        concept_id = _active_concept(cur, "Python")
        _claim(cur, role_id, concept_id, document_id=document_id, evidence_span="strong Python skills", review_status="accepted")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements",
        json={"concept_id": concept_id, "requirement_type": "required", "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 409
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1  # the duplicate was never inserted


def test_add_requirement_rejects_a_non_source_backed_basis(client):
    """Never lets the user casually invent an unsupported employer
    requirement and present it as source-stated — basis is restricted to
    stated/implied for a manual add, both independently span-validated."""
    with db.db_cursor() as cur:
        role_id, document_id = _role_with_document(cur, "Requires strong Python skills.")
        concept_id = _active_concept(cur, "Python")

    resp = client.post(
        f"/api/role-instances/{role_id}/requirements",
        json={"concept_id": concept_id, "requirement_type": "required", "basis": "user_asserted",
              "evidence_span": "strong Python skills"},
    )
    assert resp.status_code == 400


def test_add_requirement_404s_for_a_nonexistent_role(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
    resp = client.post(
        "/api/role-instances/00000000-0000-0000-0000-000000000000/requirements",
        json={"concept_id": concept_id, "requirement_type": "required", "evidence_span": "anything"},
    )
    assert resp.status_code == 404
