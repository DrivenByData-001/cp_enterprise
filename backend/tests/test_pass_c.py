"""Phase 3 Pass C: AI-assisted capability attribution (brief §23) — a claim
may only be attributed to a concept from the closed capability catalogue,
review-gated exactly like the Phase 2 profile360 mapping tasks."""

from app import ai, db, extraction
from app.models import ClaimMappingResult


def _fake_run(output, task, prompt_name):
    run = ai.AITaskRun(
        task=task, model="test-model", prompt_name=prompt_name, prompt_version="v",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=1, output_chars=1,
    )
    return ai.AITaskResult(output=output, run=run)


def _active_concept(cur, name, type_code="capability"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def test_pass_c_only_offers_capability_candidates(client, monkeypatch):
    seen = []

    def _dispatch(*, task, prompt_name, user_input, output_model):
        seen.append(user_input)
        return _fake_run(
            ClaimMappingResult(chosen_canonical_name="Own a production model", reasoning="whole capability stated"),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        cap_id = _active_concept(cur, "Own a production model", type_code="capability")
        _active_concept(cur, "Python", type_code="tool")  # must never be offered as a candidate
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Owned the reserving model end to end.') RETURNING id")
        claim_id = str(cur.fetchone()["id"])

        result = extraction.map_profile360_claim_to_capability(cur, claim_id)

        assert result["mapped"] is True
        assert result["concept_id"] == cap_id
        cur.execute("SELECT review_status FROM jobber.profile360_claim_mapping WHERE profile360_claim_id = %s", (claim_id,))
        review_status = cur.fetchone()["review_status"]

    assert "Python" not in seen[0]
    assert review_status == "unreviewed"  # AI proposals are never auto-accepted


def test_pass_c_declines_without_creating_a_mapping(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(ClaimMappingResult(chosen_canonical_name=None, reasoning="no confident match"), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _active_concept(cur, "Some capability", type_code="capability")
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Unrelated text.') RETURNING id")
        claim_id = str(cur.fetchone()["id"])

        result = extraction.map_profile360_claim_to_capability(cur, claim_id)
        assert result["mapped"] is False
        cur.execute("SELECT COUNT(*) AS n FROM jobber.profile360_claim_mapping WHERE profile360_claim_id = %s", (claim_id,))
        assert cur.fetchone()["n"] == 0


def test_run_pass_c_attempts_every_unmapped_claim(client, monkeypatch):
    calls = []

    def _dispatch(*, task, prompt_name, user_input, output_model):
        calls.append(1)
        return _fake_run(
            ClaimMappingResult(chosen_canonical_name="Target capability" if len(calls) == 1 else None),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _active_concept(cur, "Target capability", type_code="capability")
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('First claim.') RETURNING id")
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Second claim.') RETURNING id")

        run = extraction.run_pass_c(cur, limit=25)

    assert run["status"] == "ok"
    assert run["attempted"] == 2
    assert run["mapped"] == 1


def test_pass_c_route_via_api(client, monkeypatch):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(ClaimMappingResult(chosen_canonical_name="API capability"), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _active_concept(cur, "API capability", type_code="capability")
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Some claim.') RETURNING id")
        claim_id = str(cur.fetchone()["id"])

    resp = client.post(f"/api/profile360/claims/{claim_id}/map-capability")
    assert resp.status_code == 200
    assert resp.json()["mapped"] is True
