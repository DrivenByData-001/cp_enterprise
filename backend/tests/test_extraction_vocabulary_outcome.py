"""Vocabulary outcome reporting on extraction (Explicit Role Save / Current
Roles / Vocabulary Feedback brief §7/§8/§11): an extracted term that
resolves to an already-accepted concept or alias must be reported as
matched — never mistaken for having been silently ignored just because it
didn't create a pending cluster — while a genuinely unknown term must show
up as (or contribute to) a pending vocabulary-review item.

Mirrors test_requirement_evidence.py's conventions: app.extraction directly,
with app.extraction.run_json_task mocked (never a live OpenAI call)."""

from app import ai, db, extraction
from app.models import ConceptAdjudicationResult, RequirementExtractionResult, RequirementItem


def _fake_run(output, task, prompt_name):
    run = ai.AITaskRun(
        task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=10, output_chars=10,
    )
    return ai.AITaskResult(output=output, run=run)


def _make_role_with_document(cur, body: str) -> tuple[str, str]:
    document_id, _ = db.create_document(cur, kind="job_posting", content_text=body, provenance_quality="original")
    role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "Test posting", "document_id": document_id}, skills=[])
    return role_id, document_id


def _make_active_concept(cur, canonical_name: str, type_code: str = "capability", aliases: tuple[str, ...] = ()) -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, canonical_name),
    )
    concept_id = str(cur.fetchone()["id"])
    for alias in aliases:
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin) VALUES (%s, %s, 'curator')",
            (concept_id, alias),
        )
    return concept_id


def test_alias_match_reports_matched_existing_and_creates_no_pending_proposal(client, monkeypatch):
    body = "Familiarity with ALM is preferred. Experience with NumPy is a bonus."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        if task == "concept_link_adjudicate":
            # "NumPy" has no exact match, so it reaches embedding-candidate
            # retrieval against the one seeded concept (ALM) — decline every
            # candidate so it genuinely falls through to concept_proposal,
            # same as a real model finding no good match would.
            return _fake_run(ConceptAdjudicationResult(decisions=[]), task, prompt_name)
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="ALM", requirement_type="preferred", basis="stated",
                                 evidence_span="Familiarity with ALM is preferred."),
                RequirementItem(surface_form="NumPy", requirement_type="preferred", basis="stated",
                                 evidence_span="Experience with NumPy is a bonus."),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        concept_id = _make_active_concept(cur, "asset-liability management (alm)", aliases=("alm",))
        role_id, _ = _make_role_with_document(cur, body)
        result = extraction.extract_role_requirements(cur, role_id)

        outcome = result["vocabulary_outcome"]

        # ALM resolves to the active concept via its accepted alias...
        assert outcome["matched_existing_count"] == 1
        assert outcome["matched_existing"] == [
            {"surface_form": "ALM", "concept_id": concept_id, "canonical_name": "asset-liability management (alm)", "type_code": "capability"}
        ]

        # ...and an unreviewed requirement claim exists under that concept.
        cur.execute(
            "SELECT review_status FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s AND superseded_by IS NULL",
            (role_id, concept_id),
        )
        claim = cur.fetchone()
        assert claim is not None
        assert claim["review_status"] == "unreviewed"

        # ...never as a new pending proposal for "alm".
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept_proposal WHERE surface_form = 'alm'")
        assert cur.fetchone()["n"] == 0

        # The genuinely unknown term becomes/contributes to a pending proposal
        # and is reported as such.
        assert outcome["pending_term_count"] == 1
        assert outcome["new_pending_proposal_count"] == 1
        assert len(outcome["pending_terms"]) == 1
        pending = outcome["pending_terms"][0]
        assert pending["surface_form"] == "NumPy"
        assert pending["created_new_proposal"] is True

        cur.execute("SELECT status FROM jobber.concept_proposal WHERE surface_form = 'numpy'")
        proposal = cur.fetchone()
        assert proposal is not None
        assert proposal["status"] == "pending"


def test_rerun_does_not_misreport_an_already_pending_term_as_newly_created(client, monkeypatch):
    """§8.1: an identical rerun must not misleadingly say it created a fresh
    pending term once the occurrence was deduplicated — the term is still
    reported as pending (this run still contributed to it), but
    `created_new_proposal` must read False the second time."""
    body = "Experience with NumPy is a bonus."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="NumPy", requirement_type="preferred", basis="stated",
                                 evidence_span="Experience with NumPy is a bonus."),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        role_id, _ = _make_role_with_document(cur, body)

        first = extraction.extract_role_requirements(cur, role_id)
        first_pending = first["vocabulary_outcome"]["pending_terms"][0]
        assert first_pending["created_new_proposal"] is True
        assert first["vocabulary_outcome"]["new_pending_proposal_count"] == 1

        second = extraction.extract_role_requirements(cur, role_id)
        second_pending = second["vocabulary_outcome"]["pending_terms"][0]
        assert second["vocabulary_outcome"]["pending_term_count"] == 1  # still contributed to...
        assert second_pending["created_new_proposal"] is False  # ...but not newly created this time
        assert second["vocabulary_outcome"]["new_pending_proposal_count"] == 0


def test_a_second_role_contributing_to_an_already_pending_term_is_not_double_counted_as_new(client, monkeypatch):
    """A term already pending from another role still shows up in *this*
    role's own pending_terms (it contributed new evidence to that pending
    decision), but must not be reported as though this role created it."""
    def _dispatch_for(body_text):
        def _dispatch(*, task, prompt_name, user_input, output_model):
            return _fake_run(
                RequirementExtractionResult(requirements=[
                    RequirementItem(surface_form="NumPy", requirement_type="preferred", basis="stated", evidence_span=body_text),
                ]),
                task, prompt_name,
            )
        return _dispatch

    with db.db_cursor() as cur:
        role_1, _ = _make_role_with_document(cur, "Experience with NumPy is a bonus.")
    monkeypatch.setattr(extraction, "run_json_task", _dispatch_for("Experience with NumPy is a bonus."))
    with db.db_cursor() as cur:
        first = extraction.extract_role_requirements(cur, role_1)
    assert first["vocabulary_outcome"]["pending_terms"][0]["created_new_proposal"] is True

    with db.db_cursor() as cur:
        role_2, _ = _make_role_with_document(cur, "Solid NumPy skills required.")
    monkeypatch.setattr(extraction, "run_json_task", _dispatch_for("Solid NumPy skills required."))
    with db.db_cursor() as cur:
        second = extraction.extract_role_requirements(cur, role_2)

    outcome = second["vocabulary_outcome"]
    assert outcome["pending_term_count"] == 1
    assert outcome["new_pending_proposal_count"] == 0
    assert outcome["pending_terms"][0]["surface_form"] == "NumPy"
    assert outcome["pending_terms"][0]["created_new_proposal"] is False


def test_matched_existing_is_deduplicated_by_surface_form_within_one_run(client, monkeypatch):
    """A concept mentioned via the same surface form twice in one posting
    must be reported once, not once per occurrence."""
    body = "Requires Python. Strong Python skills expected."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python."),
                RequirementItem(surface_form="Python", requirement_type="preferred", basis="stated", evidence_span="Strong Python skills expected."),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python", type_code="tool")
        role_id, _ = _make_role_with_document(cur, body)
        result = extraction.extract_role_requirements(cur, role_id)

    outcome = result["vocabulary_outcome"]
    assert outcome["matched_existing_count"] == 1
    assert len(outcome["matched_existing"]) == 1
