"""profile360 claim/capability mapping (brief §7/§8/§16), against the
profile360.claims/capabilities stub backend/scripts/local_baseline.sql
creates in the throwaway test database — matching the confirmed live column
shapes (docs/14 §5), not a loosely-typed test double."""

import psycopg
import pytest

from app import ai, db, extraction
from app.models import ClaimMappingResult
from app.profile360_reader import Profile360UnavailableError, display_text, get_claim, list_claims


def _insert_fake_claim(cur, claim_text: str, evidence_class: str = "stated") -> str:
    cur.execute(
        "INSERT INTO profile360.claims (claim_text, evidence_class) VALUES (%s, %s) RETURNING id",
        (claim_text, evidence_class),
    )
    return str(cur.fetchone()["id"])


def _insert_fake_capability(cur, name: str) -> str:
    cur.execute("INSERT INTO profile360.capabilities (name) VALUES (%s) RETURNING id", (name,))
    return str(cur.fetchone()["id"])


def _active_concept(cur, name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _fake_run(output, task, prompt_name):
    run = ai.AITaskRun(
        task=task, model="test-model", prompt_name=prompt_name, prompt_version="v",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=1, output_chars=1,
    )
    return ai.AITaskResult(output=output, run=run)


def test_reader_reads_fake_claims_table(client):
    with db.db_cursor() as cur:
        claim_id = _insert_fake_claim(cur, "Used Python for reserving models.")
        rows = list_claims(cur)
        row = get_claim(cur, claim_id)

    assert any(r["id"] == row["id"] for r in rows)
    assert row["claim_text"] == "Used Python for reserving models."
    assert display_text(row) == "Used Python for reserving models."


def test_reader_raises_for_unreachable_table(client):
    with db.db_cursor() as cur:
        with pytest.raises(Profile360UnavailableError):
            from app.profile360_reader import fetch_rows

            fetch_rows(cur, "documents")  # allowlisted by name, but this fake schema never created it


def test_reader_rejects_non_allowlisted_table(client):
    with db.db_cursor() as cur:
        with pytest.raises(ValueError):
            from app.profile360_reader import fetch_rows

            fetch_rows(cur, "some_other_table")


def test_profile360_claim_mapping_fk_is_real_and_enforced(client):
    """local_baseline.sql creates profile360.claims *before* migrations run,
    so 0004_profile360_mapping.sql's real (not defensive/best-effort) FK to
    profile360.claims(id) applies for real — verified behaviourally: a
    bogus, non-existent profile360_claim_id must be rejected at the
    database level."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.db_cursor() as cur:
            concept_id = _active_concept(cur, "Python")
            cur.execute(
                "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis) "
                "VALUES (gen_random_uuid(), %s, 'curator_asserted')",
                (concept_id,),
            )


def test_map_profile360_claim_creates_unreviewed_mapping(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        assert prompt_name == "map_profile360_claim.md"
        return _fake_run(ClaimMappingResult(chosen_canonical_name="Python", reasoning="direct match"), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        python_id = _active_concept(cur, "Python")
        claim_id = _insert_fake_claim(cur, "Built reserving models in Python.")
        result = extraction.map_profile360_claim(cur, claim_id)

        assert result["mapped"] is True
        cur.execute(
            "SELECT jobber_concept_id, mapping_basis, review_status FROM jobber.profile360_claim_mapping WHERE profile360_claim_id = %s",
            (claim_id,),
        )
        mapping = cur.fetchone()

    assert str(mapping["jobber_concept_id"]) == python_id
    assert mapping["mapping_basis"] == "ai_suggested"
    assert mapping["review_status"] == "unreviewed"  # AI proposals are never auto-accepted (brief §7)


def test_map_profile360_claim_declines_without_creating_a_mapping(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(ClaimMappingResult(chosen_canonical_name=None, reasoning="no confident match"), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _active_concept(cur, "Python")
        claim_id = _insert_fake_claim(cur, "Something unrelated to any concept.")
        result = extraction.map_profile360_claim(cur, claim_id)

        assert result["mapped"] is False
        cur.execute("SELECT COUNT(*) AS n FROM jobber.profile360_claim_mapping WHERE profile360_claim_id = %s", (claim_id,))
        count = cur.fetchone()["n"]

    assert count == 0
    # docs/18 §11: real candidates existed and were considered — this is a
    # genuine "not confident" judgment, distinguishable from an empty
    # vocabulary (see the next test).
    assert result["reason"] == "declined_all_candidates"
    assert result["candidates_considered"] == 1


def test_map_profile360_claim_with_no_active_concepts_reports_empty_vocabulary_not_weak_evidence(client, monkeypatch):
    """docs/18 §11: until the canonical vocabulary is curated, this is the
    routine production state (0 active capabilities/concepts of many types).
    run_json_task must never even be called — there is nothing to adjudicate,
    exactly as the existing 'no embedding candidates retrieved' short-circuit
    already behaved; this test pins the machine-readable reason code that
    lets the UI say so honestly instead of implying weak person-side evidence."""

    def _dispatch(*, task, prompt_name, user_input, output_model):
        raise AssertionError("must not call the model when there are no candidates to adjudicate")

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        claim_id = _insert_fake_claim(cur, "Built reserving models in Python.")
        result = extraction.map_profile360_claim(cur, claim_id)

    assert result["mapped"] is False
    assert result["reason"] == "no_candidates_available"
    assert result["candidates_considered"] == 0


def test_map_profile360_capability_only_offers_capability_type_candidates(client, monkeypatch):
    seen_candidate_types = []

    def _dispatch(*, task, prompt_name, user_input, output_model):
        seen_candidate_types.append(user_input)
        return _fake_run(ClaimMappingResult(chosen_canonical_name="Own a production model", reasoning="match"), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        cap_id = _active_concept(cur, "Own a production model", type_code="capability")
        _active_concept(cur, "Python", type_code="tool")  # must never be offered as a capability candidate
        capability_id = _insert_fake_capability(cur, "Owns actuarial models end to end")
        result = extraction.map_profile360_capability(cur, capability_id)

    assert result["mapped"] is True
    assert result["concept_id"] == cap_id
    assert "Python" not in seen_candidate_types[0]


def test_profile360_claim_not_found_raises_subject_error(client):
    import uuid

    with db.db_cursor() as cur:
        with pytest.raises(extraction.ExtractionSubjectError):
            extraction.map_profile360_claim(cur, str(uuid.uuid4()))


def test_mapping_review_endpoint(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(ClaimMappingResult(chosen_canonical_name="Python"), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _active_concept(cur, "Python")
        claim_id = _insert_fake_claim(cur, "Python again.")

    map_result = client.post(f"/api/profile360/claims/{claim_id}/map")
    assert map_result.status_code == 200
    mapping_id = map_result.json()["mapping_id"]

    review = client.post(f"/api/profile360/mappings/{mapping_id}/review", json={"kind": "claim", "action": "accept"})
    assert review.status_code == 200
    assert review.json()["review_status"] == "accepted"

    listed = client.get("/api/profile360/mappings", params={"kind": "claim", "review_status": "accepted"})
    assert any(m["id"] == mapping_id for m in listed.json())
