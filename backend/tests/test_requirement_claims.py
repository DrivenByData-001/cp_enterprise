"""Requirement claim validation, evidence-span rules, legacy-reconstructed-
source restrictions, unresolved-concept proposals, and failed-AI-run
persistence (brief §5/§8/§9/§16) — app.extraction directly, with
app.extraction.run_json_task mocked (never a live OpenAI call)."""

import uuid

import pytest

from app import ai, db, extraction
from app.models import (
    ConceptAdjudicationDecision,
    ConceptAdjudicationResult,
    RequirementExtractionResult,
    RequirementItem,
)


def _fake_run(output, task, prompt_name):
    run = ai.AITaskRun(
        task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=10, output_chars=10,
    )
    return ai.AITaskResult(output=output, run=run)


def _make_role_with_document(cur, body: str, provenance_quality: str = "original") -> tuple[str, str]:
    document_id, _ = db.create_document(cur, kind="job_posting", content_text=body, provenance_quality=provenance_quality)
    role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "Test posting", "document_id": document_id}, skills=[])
    return role_id, document_id


def _make_active_concept(cur, canonical_name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, canonical_name),
    )
    return str(cur.fetchone()["id"])


def test_exact_match_resolves_without_adjudication_call(client, monkeypatch):
    body = "Requires Python experience."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        assert prompt_name == "extract_role_requirements.md", "adjudication must not run when exact match resolves everything"
        return _fake_run(
            RequirementExtractionResult(
                requirements=[RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python experience.")]
            ),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        python_id = _make_active_concept(cur, "Python")
        role_id, _ = _make_role_with_document(cur, body)
        result = extraction.extract_role_requirements(cur, role_id)

        assert result["status"] == "ok"
        assert result["claims_created"] == 1
        cur.execute("SELECT concept_id, basis, evidence_span, document_id FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        claim = cur.fetchone()

    assert str(claim["concept_id"]) == python_id
    assert claim["basis"] == "stated"
    assert claim["evidence_span"] == "Requires Python experience."
    assert claim["document_id"] is not None


def test_hallucinated_span_is_rejected_not_stored(client, monkeypatch):
    body = "Requires Python experience."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        if prompt_name == "extract_role_requirements.md":
            return _fake_run(
                RequirementExtractionResult(
                    requirements=[RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="a quote that was never in the document")]
                ),
                task, prompt_name,
            )
        raise AssertionError("adjudication should not be reached — the item is dropped before Phase B")

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python")
        role_id, _ = _make_role_with_document(cur, body)
        result = extraction.extract_role_requirements(cur, role_id)

        assert result["status"] == "ok"
        assert result["claims_created"] == 0
        assert result["rejected_span_count"] == 1
        cur.execute("SELECT status, notes FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        run_row = cur.fetchone()

    assert run_row["status"] == "partial"
    assert "invalid" in run_row["notes"] or "verbatim" in run_row["notes"]


def test_legacy_extracted_document_downgrades_basis_and_drops_span(client, monkeypatch):
    body = "Requires Python experience."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(
                requirements=[RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python experience.")]
            ),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python")
        role_id, doc_id = _make_role_with_document(cur, body, provenance_quality="legacy_extracted")
        extraction.extract_role_requirements(cur, role_id)

        cur.execute("SELECT basis, evidence_span, document_id FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        claim = cur.fetchone()

    # The model said "stated" with a span that DOES validate verbatim — but the
    # source document itself is a reconstruction, so it must never be trusted
    # as if it were original-source evidence (brief §4/§5, docs/14 §4).
    assert claim["basis"] == "inferred"
    assert claim["evidence_span"] is None
    assert str(claim["document_id"]) == doc_id  # provenance link is kept even though the span isn't


def test_unresolved_surface_form_becomes_concept_proposal_and_accumulates(client, monkeypatch):
    body = "Requires Solvency II expertise."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        if prompt_name == "extract_role_requirements.md":
            return _fake_run(
                RequirementExtractionResult(
                    requirements=[RequirementItem(surface_form="Solvency II", requirement_type="required", basis="stated", evidence_span="Solvency II expertise")]
                ),
                task, prompt_name,
            )
        # No active concepts exist at all in this test, so nearest_concepts()
        # returns no candidates and adjudication is never invoked either.
        raise AssertionError("adjudication should not run with an empty vocabulary")

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        role_id, _ = _make_role_with_document(cur, body)
        result1 = extraction.extract_role_requirements(cur, role_id)
        assert result1["proposals_created"] == 1

        cur.execute("SELECT occurrence_count, status FROM jobber.concept_proposal WHERE surface_form = 'solvency ii'")
        proposal = cur.fetchone()
        assert proposal["occurrence_count"] == 1
        assert proposal["status"] == "pending"

        result2 = extraction.extract_role_requirements(cur, role_id)
        assert result2["proposals_created"] == 0
        assert result2["proposals_updated"] == 1

        cur.execute("SELECT COUNT(*) AS n, MAX(occurrence_count) AS occ FROM jobber.concept_proposal WHERE surface_form = 'solvency ii'")
        after = cur.fetchone()

    assert after["n"] == 1  # never duplicated
    assert after["occ"] == 2  # accumulated across the two runs


def test_unresolved_rerun_refreshes_occurrence_when_the_new_reading_is_stronger(client, monkeypatch):
    """A rerun against the same still-unresolved term can genuinely read it
    better the second time (e.g. more context, a clearer sentence). The
    occurrence (concept_proposal_occurrence, migration 0022) should adopt
    the stronger requirement_type — together with that run's own
    basis/evidence_span/document/extraction_run provenance, never a
    Frankenstein row mixing one run's type with another's span."""
    body = "Requires Widget Modelling context."
    calls = {"n": 0}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        assert prompt_name == "extract_role_requirements.md"
        calls["n"] += 1
        reading = (
            RequirementItem(surface_form="Widget Modelling", requirement_type="contextual", basis="implied", evidence_span="Widget Modelling context.")
            if calls["n"] == 1
            else RequirementItem(surface_form="Widget Modelling", requirement_type="required", basis="stated", evidence_span="Requires Widget Modelling")
        )
        return _fake_run(RequirementExtractionResult(requirements=[reading]), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        role_id, doc_id = _make_role_with_document(cur, body)
        first_run = extraction.extract_role_requirements(cur, role_id)

        cur.execute(
            "SELECT o.requirement_type, o.basis, o.evidence_span, o.extraction_run_id "
            "FROM jobber.concept_proposal_occurrence o JOIN jobber.concept_proposal cp ON cp.id = o.concept_proposal_id "
            "WHERE cp.surface_form = 'widget modelling' AND o.role_instance_id = %s",
            (role_id,),
        )
        before = cur.fetchone()
        assert before["requirement_type"] == "contextual"

        second_run = extraction.extract_role_requirements(cur, role_id)
        assert second_run["extraction_run_id"] != first_run["extraction_run_id"]

        cur.execute(
            "SELECT o.requirement_type, o.basis, o.evidence_span, o.document_id, o.extraction_run_id "
            "FROM jobber.concept_proposal_occurrence o JOIN jobber.concept_proposal cp ON cp.id = o.concept_proposal_id "
            "WHERE cp.surface_form = 'widget modelling' AND o.role_instance_id = %s",
            (role_id,),
        )
        after = cur.fetchone()

    assert after["requirement_type"] == "required"
    assert after["basis"] == "stated"
    assert after["evidence_span"] == "Requires Widget Modelling"
    assert str(after["document_id"]) == doc_id
    assert str(after["extraction_run_id"]) == second_run["extraction_run_id"]


def test_unresolved_rerun_does_not_downgrade_occurrence_with_a_weaker_reading(client, monkeypatch):
    """The reverse of the test above: a rerun that reads the same
    still-unresolved term *less* confidently than before must not overwrite
    the stronger, already-valid evidence — including its provenance, which
    must stay pinned to the run that actually produced the stronger
    reading, not silently move to whichever run happened to be latest."""
    body = "Requires Gadget Tuning expertise."
    calls = {"n": 0}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        assert prompt_name == "extract_role_requirements.md"
        calls["n"] += 1
        reading = (
            RequirementItem(surface_form="Gadget Tuning", requirement_type="required", basis="stated", evidence_span="Gadget Tuning expertise")
            if calls["n"] == 1
            else RequirementItem(surface_form="Gadget Tuning", requirement_type="contextual", basis="implied", evidence_span="Requires Gadget Tuning expertise.")
        )
        return _fake_run(RequirementExtractionResult(requirements=[reading]), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        role_id, doc_id = _make_role_with_document(cur, body)
        first_run = extraction.extract_role_requirements(cur, role_id)

        second_run = extraction.extract_role_requirements(cur, role_id)
        assert second_run["extraction_run_id"] != first_run["extraction_run_id"]

        cur.execute(
            "SELECT o.requirement_type, o.basis, o.evidence_span, o.document_id, o.extraction_run_id "
            "FROM jobber.concept_proposal_occurrence o JOIN jobber.concept_proposal cp ON cp.id = o.concept_proposal_id "
            "WHERE cp.surface_form = 'gadget tuning' AND o.role_instance_id = %s",
            (role_id,),
        )
        after = cur.fetchone()

    assert after["requirement_type"] == "required"
    assert after["basis"] == "stated"
    assert after["evidence_span"] == "Gadget Tuning expertise"
    assert str(after["document_id"]) == doc_id
    assert str(after["extraction_run_id"]) == first_run["extraction_run_id"]  # never moved to the weaker rerun


def test_declined_adjudication_falls_through_to_proposal(client, monkeypatch):
    body = "Requires stochastic reserving skills."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        if prompt_name == "extract_role_requirements.md":
            return _fake_run(
                RequirementExtractionResult(
                    requirements=[RequirementItem(surface_form="stochastic reserving", requirement_type="required", basis="stated", evidence_span="stochastic reserving skills")]
                ),
                task, prompt_name,
            )
        return _fake_run(
            ConceptAdjudicationResult(decisions=[ConceptAdjudicationDecision(item_index=0, chosen_canonical_name=None)]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "reserving", type_code="function")  # a *different* concept, so exact match won't fire
        role_id, _ = _make_role_with_document(cur, body)
        result = extraction.extract_role_requirements(cur, role_id)

    assert result["claims_created"] == 0
    assert result["proposals_created"] == 1


def test_failed_ai_call_is_recorded_and_creates_no_claims(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        raise ai.AIProviderError("provider unreachable")

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        role_id, _ = _make_role_with_document(cur, "Some posting text.")
        result = extraction.extract_role_requirements(cur, role_id)

        assert result["status"] == "failed"
        cur.execute("SELECT status, error_type, error_message FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        run_row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        claim_count = cur.fetchone()["n"]

    assert run_row["status"] == "failed"
    assert run_row["error_type"] == "AIProviderError"
    assert "provider unreachable" in run_row["error_message"]
    assert claim_count == 0


def test_extraction_subject_errors(client):
    with db.db_cursor() as cur:
        with pytest.raises(extraction.ExtractionSubjectError):
            extraction.extract_role_requirements(cur, str(uuid.uuid4()))

        role_id = db.upsert_role_instance(
            cur, None, {"instance_type": "user_defined_target", "target_basis": "imagined", "title": "no document"}, skills=[]
        )
        with pytest.raises(extraction.ExtractionSubjectError):
            extraction.extract_role_requirements(cur, role_id)


def test_out_of_range_importance_is_clamped_to_null(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(
                requirements=[RequirementItem(surface_form="Python", requirement_type="required", basis="stated", importance=99, evidence_span="Python required.")]
            ),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python")
        role_id, _ = _make_role_with_document(cur, "Python required.")
        extraction.extract_role_requirements(cur, role_id)
        cur.execute("SELECT importance FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        importance = cur.fetchone()["importance"]

    assert importance is None


def test_rerun_does_not_duplicate_an_identical_unreviewed_proposal(client, monkeypatch):
    body = "Requires Python experience."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(
                requirements=[RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python experience.")]
            ),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python")
        role_id, _ = _make_role_with_document(cur, body)

        result1 = extraction.extract_role_requirements(cur, role_id)
        assert result1["claims_created"] == 1
        assert result1["claims_deduplicated"] == 0

        result2 = extraction.extract_role_requirements(cur, role_id)
        assert result2["claims_created"] == 0
        assert result2["claims_deduplicated"] == 1

        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1  # never duplicated


def test_rerun_with_a_different_interpretation_supersedes_the_earlier_unreviewed_proposal(client, monkeypatch):
    body = "Requires Python experience, ideally advanced."
    calls = {"n": 0}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        calls["n"] += 1
        # Same concept, but a genuinely different interpretation on rerun —
        # "interpretation" is defined by (requirement_type, basis,
        # evidence_span); importance alone does not count as one (extraction.py).
        requirement_type = "preferred" if calls["n"] == 1 else "required"
        return _fake_run(
            RequirementExtractionResult(
                requirements=[RequirementItem(surface_form="Python", requirement_type=requirement_type, basis="stated",
                                               evidence_span="Requires Python experience, ideally advanced.")]
            ),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python")
        role_id, _ = _make_role_with_document(cur, body)

        result1 = extraction.extract_role_requirements(cur, role_id)
        assert result1["claims_created"] == 1
        old_claim_id = _first_claim_id(cur, role_id)

        result2 = extraction.extract_role_requirements(cur, role_id)
        assert result2["claims_created"] == 1
        assert result2["claims_superseded"] == 1

        cur.execute("SELECT id, review_status, superseded_by, requirement_type FROM jobber.requirement_claim "
                    "WHERE role_instance_id = %s AND superseded_by IS NULL", (role_id,))
        current = cur.fetchone()
        cur.execute("SELECT review_status, superseded_by FROM jobber.requirement_claim WHERE id = %s", (old_claim_id,))
        old = cur.fetchone()

    assert current["requirement_type"] == "required"
    assert str(current["id"]) != old_claim_id
    # Preserved through supersession, not left as a second current row — and
    # still 'unreviewed' (no human ever looked at it), never 'corrected'
    # (that status is reserved for an actual human correction).
    assert old["review_status"] == "unreviewed"
    assert str(old["superseded_by"]) == str(current["id"])


def test_rerun_never_touches_an_accepted_human_reviewed_decision(client, monkeypatch):
    body = "Requires Python experience."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(
                requirements=[RequirementItem(surface_form="Python", requirement_type="preferred", basis="stated", evidence_span="Requires Python experience.")]
            ),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python")
        role_id, _ = _make_role_with_document(cur, body)
        extraction.extract_role_requirements(cur, role_id)
        claim_id = _first_claim_id(cur, role_id)

    accept_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/accept")
    assert accept_resp.status_code == 200

    with db.db_cursor() as cur:
        result = extraction.extract_role_requirements(cur, role_id)
        assert result["claims_created"] == 0
        assert result["claims_superseded"] == 0
        assert result["claims_deduplicated"] == 0

        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT review_status, requirement_type FROM jobber.requirement_claim WHERE id = %s", (claim_id,))
        row = cur.fetchone()
    assert row["review_status"] == "accepted"
    assert row["requirement_type"] == "preferred"  # untouched by the rerun's different proposal


def test_rerun_never_touches_a_rejected_human_reviewed_decision(client, monkeypatch):
    body = "Requires Python experience."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(
                requirements=[RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python experience.")]
            ),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python")
        role_id, _ = _make_role_with_document(cur, body)
        extraction.extract_role_requirements(cur, role_id)
        claim_id = _first_claim_id(cur, role_id)

    reject_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/reject")
    assert reject_resp.status_code == 200

    with db.db_cursor() as cur:
        result = extraction.extract_role_requirements(cur, role_id)
        assert result["claims_created"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT review_status FROM jobber.requirement_claim WHERE id = %s", (claim_id,))
        assert cur.fetchone()["review_status"] == "rejected"  # untouched


def _first_claim_id(cur, role_id: str) -> str:
    cur.execute(
        "SELECT id FROM jobber.requirement_claim WHERE role_instance_id = %s AND superseded_by IS NULL "
        "ORDER BY created_at LIMIT 1",
        (role_id,),
    )
    return str(cur.fetchone()["id"])


def test_extract_requirements_via_http_route(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(RequirementExtractionResult(requirements=[]), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    ingest = client.post("/api/role-instances/ingest", json={"text": "Some role text."}).json()
    resp = client.post(f"/api/role-instances/{ingest['id']}/extract-requirements")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
