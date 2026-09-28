"""Phase 6: Career Direction / property-first target discovery (docs/37).
Real Postgres; app.career_directions.run_json_task mocked — never a live
OpenAI call, same convention as test_concept_dossier.py."""

import uuid

import psycopg
import pytest
from pydantic import ValidationError

from app import ai, career_directions as directions, db
from app.models import CareerDirectionCandidate, CareerDirectionDiscoveryResult


# --- fixtures ----------------------------------------------------------------


def _target(cur, *, title="Head of Actuarial Function", organisation=None, imagined=False) -> str:
    return db.upsert_role_instance(
        cur, None,
        {"instance_type": "user_defined_target", "target_basis": "imagined" if imagined else "real_role",
         "title": title, "organisation": organisation, "summary": f"Summary for {title}"},
        skills=[],
    )


def _posting(cur, *, title="Senior Actuary") -> str:
    return db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": title}, skills=[])


def _archetype(client, *, name="Technical Actuarial Leadership") -> str:
    resp = client.post("/api/archetypes", json={"canonical_name": name})
    assert resp.status_code == 200
    return resp.json()["id"]


def _preference_observation(cur, *, dimension_code="autonomy", direction="toward", strength=2, basis="user_stated") -> str:
    cur.execute(
        "INSERT INTO jobber.preference_observation (dimension_code, direction, strength, basis) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (dimension_code, direction, strength, basis),
    )
    return str(cur.fetchone()["id"])


def _episode(cur, *, title="Senior Actuary", organisation="Acme Re") -> str:
    cur.execute(
        "INSERT INTO profile360.episodes (title, organisation) VALUES (%s, %s) RETURNING id",
        (title, organisation),
    )
    return str(cur.fetchone()["id"])


def _fake_run_json_task(output=None, raise_error: Exception | None = None):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        if raise_error is not None:
            raise raise_error
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=output or CareerDirectionDiscoveryResult(insufficient_evidence=True, insufficient_evidence_reason="no evidence set up"), run=run)

    return _dispatch


def _grounded_candidate(*, direction_ref: str, archetype_ref: str, preference_ref: str, episode_ref: str, name="Technical Actuarial Leadership") -> CareerDirectionCandidate:
    return CareerDirectionCandidate(
        name=name,
        summary={"text": "A technically-deep leadership direction.", "source_refs": [direction_ref]},
        primary_archetype_id=archetype_ref.split(":", 1)[1],
        priority_alignment=[
            {"dimension_code": "autonomy", "alignment": "supports", "explanation": "Matches stated autonomy preference.",
             "source_refs": [direction_ref, preference_ref]},
        ],
        market_basis=[{"text": "Archetype has assigned postings.", "source_refs": [archetype_ref]}],
        person_basis=[{"text": "Prior experience overlaps with this direction.", "source_refs": [episode_ref]}],
        tradeoffs=[{"text": "May require relocation.", "source_refs": [direction_ref]}],
        unknowns=[{"text": "Unclear how compensation compares locally.", "source_refs": [archetype_ref]}],
    )


# --- schema-level: no score/rank field can ever pass validation ------------


def test_candidate_schema_rejects_extra_score_field():
    with pytest.raises(ValidationError):
        CareerDirectionCandidate(name="x", summary={"text": "y"}, fit_score=0.9)


def test_result_schema_rejects_extra_rank_field():
    with pytest.raises(ValidationError):
        CareerDirectionDiscoveryResult(candidates=[], rank=1)


# --- manual create / update (never AI; build §19) ---------------------------


def test_create_manual_minimal(client):
    resp = client.post("/api/career-directions", json={"name": "Technical leadership track"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Technical leadership track"
    assert body["state"] == "exploring"
    assert body["origin"] == "user"
    assert body["dimensions"] == []
    assert body["target"] is None
    assert body["archetype"] is None


def test_create_with_dimensions_and_constraints(client):
    resp = client.post(
        "/api/career-directions",
        json={
            "name": "Deep technical track",
            "summary": "Stay hands-on.",
            "dimensions": [{"dimension_code": "technical_engagement", "desired_direction": "toward", "importance": 3, "note": "Non-negotiable"}],
            "constraints": {
                "locations": ["Ireland", "United Kingdom"],
                "remote_types": ["hybrid"],
                "compensation_floor": {"amount": 150000, "currency": "gbp", "pay_period": "annual", "employment_basis": "permanent", "hard": False},
                "other": ["Retain meaningful hands-on technical work"],
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimensions"] == [{"dimension_code": "technical_engagement", "desired_direction": "toward", "importance": 3, "note": "Non-negotiable"}]
    assert body["constraints"]["locations"] == ["Ireland", "United Kingdom"]
    assert body["constraints"]["compensation_floor"]["currency"] == "GBP"  # normalised


def test_create_rejects_unknown_dimension_code(client):
    resp = client.post("/api/career-directions", json={"name": "x", "dimensions": [{"dimension_code": "not_real", "desired_direction": "toward", "importance": 1}]})
    assert resp.status_code == 400


def test_create_rejects_duplicate_dimension_code(client):
    resp = client.post(
        "/api/career-directions",
        json={"name": "x", "dimensions": [
            {"dimension_code": "autonomy", "desired_direction": "toward", "importance": 1},
            {"dimension_code": "autonomy", "desired_direction": "away", "importance": 2},
        ]},
    )
    assert resp.status_code == 422  # pydantic model_validator rejection


def test_create_rejects_blank_name(client):
    resp = client.post("/api/career-directions", json={"name": "   "})
    assert resp.status_code == 422


def test_compensation_floor_rejects_non_positive_amount(client):
    resp = client.post(
        "/api/career-directions",
        json={"name": "x", "constraints": {"compensation_floor": {"amount": 0, "currency": "GBP"}}},
    )
    assert resp.status_code == 422


def test_list_and_detail_shape(client):
    client.post("/api/career-directions", json={"name": "A"})
    client.post("/api/career-directions", json={"name": "B"})
    listed = client.get("/api/career-directions").json()["items"]
    assert len(listed) == 2
    names = {d["name"] for d in listed}
    assert names == {"A", "B"}

    detail = client.get(f"/api/career-directions/{listed[0]['id']}").json()
    assert detail["id"] == listed[0]["id"]
    assert detail["archetype_evidence"] is None


def test_get_unknown_direction_is_404(client):
    assert client.get(f"/api/career-directions/{uuid.uuid4()}").status_code == 404


def test_update_dimensions_and_constraints(client):
    created = client.post("/api/career-directions", json={"name": "x"}).json()
    resp = client.put(
        f"/api/career-directions/{created['id']}",
        json={"summary": "Updated summary", "dimensions": [{"dimension_code": "autonomy", "desired_direction": "toward", "importance": 2}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "Updated summary"
    assert body["dimensions"][0]["dimension_code"] == "autonomy"
    assert body["state"] == "exploring"  # untouched by a generic update


def test_update_cannot_change_state_directly(client):
    """PATCH has no `state` field at all — CareerDirectionUpdate doesn't
    accept one, so there is no way to bypass the explicit select transaction
    through this endpoint (build §7)."""
    created = client.post("/api/career-directions", json={"name": "x"}).json()
    resp = client.put(f"/api/career-directions/{created['id']}", json={"state": "selected"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "exploring"


# --- Target / archetype linking ---------------------------------------------


def test_linking_valid_target_succeeds(client):
    with db.db_cursor() as cur:
        target_id = _target(cur)
    created = client.post("/api/career-directions", json={"name": "x"}).json()
    resp = client.put(f"/api/career-directions/{created['id']}", json={"target_role_instance_id": target_id})
    assert resp.status_code == 200
    assert resp.json()["target"]["id"] == target_id


def test_linking_observed_posting_rejected(client):
    with db.db_cursor() as cur:
        posting_id = _posting(cur)
    created = client.post("/api/career-directions", json={"name": "x"}).json()
    resp = client.put(f"/api/career-directions/{created['id']}", json={"target_role_instance_id": posting_id})
    assert resp.status_code == 400


def test_create_rejects_posting_as_target(client):
    with db.db_cursor() as cur:
        posting_id = _posting(cur)
    resp = client.post("/api/career-directions", json={"name": "x", "target_role_instance_id": posting_id})
    assert resp.status_code == 400


def test_deleting_linked_target_nulls_the_link(client):
    with db.db_cursor() as cur:
        target_id = _target(cur)
    created = client.post("/api/career-directions", json={"name": "x", "target_role_instance_id": target_id}).json()
    assert created["target"]["id"] == target_id

    with db.db_cursor() as cur:
        assert db.delete_role_instance(cur, target_id) is True

    refetched = client.get(f"/api/career-directions/{created['id']}").json()
    assert refetched["target"] is None
    assert refetched["state"] == "exploring"  # the direction itself survives


def test_linking_active_archetype_succeeds(client):
    archetype_id = _archetype(client)
    created = client.post("/api/career-directions", json={"name": "x"}).json()
    resp = client.put(f"/api/career-directions/{created['id']}", json={"target_archetype_concept_id": archetype_id})
    assert resp.status_code == 200
    assert resp.json()["archetype"]["id"] == archetype_id
    assert resp.json()["archetype_evidence"] is not None


def test_linking_unknown_archetype_rejected(client):
    created = client.post("/api/career-directions", json={"name": "x"}).json()
    resp = client.put(f"/api/career-directions/{created['id']}", json={"target_archetype_concept_id": str(uuid.uuid4())})
    assert resp.status_code == 400


# --- selection invariant (build §3) -----------------------------------------


def test_select_swaps_transactionally(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    b = client.post("/api/career-directions", json={"name": "B"}).json()

    resp = client.post(f"/api/career-directions/{a['id']}/select")
    assert resp.status_code == 200
    assert resp.json()["state"] == "selected"
    assert resp.json()["selected_at"] is not None

    resp = client.post(f"/api/career-directions/{b['id']}/select")
    assert resp.status_code == 200
    assert resp.json()["state"] == "selected"

    a_now = client.get(f"/api/career-directions/{a['id']}").json()
    assert a_now["state"] == "exploring"

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.career_direction WHERE state = 'selected'")
        assert cur.fetchone()["n"] == 1


def test_select_is_idempotent(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    client.post(f"/api/career-directions/{a['id']}/select")
    resp = client.post(f"/api/career-directions/{a['id']}/select")
    assert resp.status_code == 200
    assert resp.json()["state"] == "selected"


def test_one_selected_invariant_enforced_at_db_level(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    b = client.post("/api/career-directions", json={"name": "B"}).json()
    client.post(f"/api/career-directions/{a['id']}/select")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.db_cursor() as cur:
            cur.execute("UPDATE jobber.career_direction SET state = 'selected' WHERE id = %s", (b["id"],))


def test_selected_endpoint_reflects_selection(client):
    assert client.get("/api/career-directions/selected").json()["direction"] is None
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    client.post(f"/api/career-directions/{a['id']}/select")
    selected = client.get("/api/career-directions/selected").json()["direction"]
    assert selected["id"] == a["id"]


def test_cannot_select_archived_direction(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    client.post(f"/api/career-directions/{a['id']}/archive")
    resp = client.post(f"/api/career-directions/{a['id']}/select")
    assert resp.status_code == 409


def test_archive_selected_leaves_none_selected(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    client.post(f"/api/career-directions/{a['id']}/select")
    resp = client.post(f"/api/career-directions/{a['id']}/archive")
    assert resp.status_code == 200
    assert resp.json()["state"] == "archived"
    assert client.get("/api/career-directions/selected").json()["direction"] is None


def test_reopen_archived_direction(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    client.post(f"/api/career-directions/{a['id']}/archive")
    resp = client.post(f"/api/career-directions/{a['id']}/reopen")
    assert resp.status_code == 200
    assert resp.json()["state"] == "exploring"


def test_reopen_non_archived_is_409(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    resp = client.post(f"/api/career-directions/{a['id']}/reopen")
    assert resp.status_code == 409


def test_list_excludes_archived_by_default(client):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    client.post("/api/career-directions", json={"name": "B"})
    client.post(f"/api/career-directions/{a['id']}/archive")
    listed = client.get("/api/career-directions").json()["items"]
    assert {d["name"] for d in listed} == {"B"}
    archived_listed = client.get("/api/career-directions", params={"state": "archived"}).json()["items"]
    assert {d["name"] for d in archived_listed} == {"A"}


# --- preferences / context boundary (build §9/§31) --------------------------


def test_preference_observations_unchanged_by_direction_creation(client):
    with db.db_cursor() as cur:
        _preference_observation(cur)
    client.post(
        "/api/career-directions",
        json={"name": "x", "dimensions": [{"dimension_code": "autonomy", "desired_direction": "toward", "importance": 3, "note": "strong"}]},
    )
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.preference_observation")
        assert cur.fetchone()["n"] == 1  # exactly the one seeded — nothing added


def test_no_direction_writes_into_preference_observation(client):
    client.post("/api/career-directions", json={"name": "x", "dimensions": [{"dimension_code": "autonomy", "desired_direction": "toward", "importance": 2}]})
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.preference_observation")
        assert cur.fetchone()["n"] == 0


def test_preference_summary_reports_agreement(client):
    with db.db_cursor() as cur:
        _preference_observation(cur, dimension_code="autonomy", direction="toward", basis="user_stated")
        _preference_observation(cur, dimension_code="autonomy", direction="toward", basis="observed_behavior")
    summary = client.get("/api/career-directions/preference-summary").json()["dimensions"]
    assert summary["autonomy"]["agreement"] == "agree"
    assert summary["autonomy"]["suggested_direction"] == "toward"
    assert summary["autonomy"]["conflict"] is False
    assert summary["autonomy"]["basis"] is not None


def test_preference_summary_reports_conflict_not_invented_consensus(client):
    with db.db_cursor() as cur:
        _preference_observation(cur, dimension_code="autonomy", direction="toward", basis="user_stated")
        _preference_observation(cur, dimension_code="autonomy", direction="away", basis="user_stated")
    summary = client.get("/api/career-directions/preference-summary").json()["dimensions"]
    assert summary["autonomy"]["conflict"] is True
    assert summary["autonomy"]["suggested_direction"] is None
    assert summary["autonomy"]["agreement"] == "conflict"


def test_preference_summary_no_evidence_state(client):
    summary = client.get("/api/career-directions/preference-summary").json()["dimensions"]
    assert summary["autonomy"]["agreement"] == "no_evidence"
    assert summary["autonomy"]["suggested_direction"] is None


# --- GET never calls AI ------------------------------------------------------


def test_get_endpoints_never_call_ai(client, monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("a GET endpoint must never call the AI provider")

    monkeypatch.setattr(directions, "run_json_task", _boom)
    a = client.post("/api/career-directions", json={"name": "A"}).json()

    assert client.get("/api/career-directions").status_code == 200
    assert client.get(f"/api/career-directions/{a['id']}").status_code == 200
    assert client.get("/api/career-directions/selected").status_code == 200
    assert client.get("/api/career-directions/preference-summary").status_code == 200
    assert client.get("/api/career-directions/discovery-runs").status_code == 200


# --- explicit discovery / grounding (build §11/§14/§15) ---------------------


def _setup_grounded_evidence(client):
    with db.db_cursor() as cur:
        preference_ref = f"preference_observation:{_preference_observation(cur, dimension_code='autonomy')}"
        episode_ref = f"profile_episode:{_episode(cur)}"
    archetype_id = _archetype(client)
    return {
        "direction_ref": "direction_input:autonomy",
        "archetype_ref": f"archetype:{archetype_id}",
        "preference_ref": preference_ref,
        "episode_ref": episode_ref,
        "archetype_id": archetype_id,
    }


def _discover_payload():
    return {"dimensions": [{"dimension_code": "autonomy", "desired_direction": "toward", "importance": 3}]}


def test_discover_happy_path_persists_run_and_returns_candidates(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["result"]["candidates"]) == 1
    saved = body["result"]["candidates"][0]
    assert saved["name"] == "Technical Actuarial Leadership"
    assert "id" in saved  # stable per-candidate id assigned server-side
    assert "corpus_disclosure" in body
    assert body["corpus_disclosure"]["active_archetypes_total"] >= 1

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.career_direction")
        assert cur.fetchone()["n"] == 0  # discovery alone creates no Direction

    run = client.get(f"/api/career-directions/discovery-runs/{body['discovery_run_id']}").json()
    assert run["status"] == "ok"
    assert "raw_output" not in run


def test_discover_rejects_unknown_source_ref(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    candidate.tradeoffs[0].source_refs = ["archetype:00000000-0000-0000-0000-000000000000"]
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 422

    with db.db_cursor() as cur:
        cur.execute("SELECT status, error_type FROM jobber.career_direction_discovery_run ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    assert row["status"] == "failed"
    assert row["error_type"] == "CareerDirectionValidationError"


def test_discover_rejects_unknown_archetype_id(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    candidate.primary_archetype_id = str(uuid.uuid4())
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 422


def test_discover_rejects_person_basis_grounded_only_in_market_evidence(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    candidate.person_basis[0].source_refs = [refs["archetype_ref"]]  # market evidence cannot prove capability
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 422


def test_discover_rejects_priority_alignment_grounded_only_in_market_evidence(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    candidate.priority_alignment[0].source_refs = [refs["archetype_ref"]]
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 422


def test_discover_rejects_market_basis_grounded_only_in_direction_input(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    candidate.market_basis[0].source_refs = [refs["direction_ref"]]
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 422


def test_discover_rejects_block_with_no_source_refs(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    candidate.summary.source_refs = []
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 422


def test_discover_fewer_candidates_accepted_when_evidence_is_thin(client, monkeypatch):
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[])))
    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 200
    body = resp.json()["result"]
    assert body["candidates"] == []
    assert body["insufficient_evidence"] is True
    assert body["insufficient_evidence_reason"]


def test_discover_insufficient_evidence_response_is_valid(client, monkeypatch):
    monkeypatch.setattr(
        directions, "run_json_task",
        _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[], insufficient_evidence=True, insufficient_evidence_reason="No archetypes exist yet.")),
    )
    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 200
    assert resp.json()["result"]["insufficient_evidence_reason"] == "No archetypes exist yet."


def test_discover_provider_failure_creates_no_direction_and_records_failed_run(client, monkeypatch):
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(raise_error=ai.AIProviderError("unreachable")))
    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 502

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.career_direction")
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT status, error_type FROM jobber.career_direction_discovery_run ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    assert row["status"] == "failed"
    assert row["error_type"] == "AIProviderError"


def test_discover_missing_api_key_is_503(client, monkeypatch):
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(raise_error=ai.AIConfigError("OPENAI_API_KEY is not set")))
    resp = client.post("/api/career-directions/discover", json=_discover_payload())
    assert resp.status_code == 503


def test_discover_failure_does_not_touch_existing_selection(client, monkeypatch):
    a = client.post("/api/career-directions", json={"name": "A"}).json()
    client.post(f"/api/career-directions/{a['id']}/select")

    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(raise_error=ai.AIProviderError("unreachable")))
    client.post("/api/career-directions/discover", json=_discover_payload())

    assert client.get("/api/career-directions/selected").json()["direction"]["id"] == a["id"]


def test_discover_rejects_unknown_dimension_code_before_calling_ai(client, monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("must not call AI when criteria fail validation")

    monkeypatch.setattr(directions, "run_json_task", _boom)
    resp = client.post("/api/career-directions/discover", json={"dimensions": [{"dimension_code": "not_real", "desired_direction": "toward", "importance": 1}]})
    assert resp.status_code == 400


# --- candidate adoption (build §18) -----------------------------------------


def test_adopt_candidate_creates_ai_adopted_direction_unselected(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    discovered = client.post("/api/career-directions/discover", json=_discover_payload()).json()
    run_id = discovered["discovery_run_id"]
    candidate_id = discovered["result"]["candidates"][0]["id"]

    resp = client.post(f"/api/career-directions/discovery-runs/{run_id}/candidates/{candidate_id}/adopt")
    assert resp.status_code == 200
    saved = resp.json()["direction"]
    assert saved["origin"] == "ai_adopted"
    assert saved["state"] == "exploring"  # never auto-selected
    assert saved["archetype"]["id"] == refs["archetype_id"]
    assert saved["dimensions"][0]["dimension_code"] == "autonomy"
    assert saved["source_discovery_run_id"] == run_id
    assert saved["source_candidate_id"] == candidate_id


def test_adopt_candidate_allows_rename(client, monkeypatch):
    refs = _setup_grounded_evidence(client)
    candidate = _grounded_candidate(**{k: refs[k] for k in ("direction_ref", "archetype_ref", "preference_ref", "episode_ref")})
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(candidates=[candidate])))

    discovered = client.post("/api/career-directions/discover", json=_discover_payload()).json()
    run_id = discovered["discovery_run_id"]
    candidate_id = discovered["result"]["candidates"][0]["id"]

    resp = client.post(
        f"/api/career-directions/discovery-runs/{run_id}/candidates/{candidate_id}/adopt",
        json={"name": "My renamed direction"},
    )
    assert resp.json()["direction"]["name"] == "My renamed direction"


def test_adopt_unknown_candidate_is_404(client, monkeypatch):
    monkeypatch.setattr(directions, "run_json_task", _fake_run_json_task(CareerDirectionDiscoveryResult(insufficient_evidence=True)))
    discovered = client.post("/api/career-directions/discover", json=_discover_payload()).json()
    run_id = discovered["discovery_run_id"]
    resp = client.post(f"/api/career-directions/discovery-runs/{run_id}/candidates/{uuid.uuid4()}/adopt")
    assert resp.status_code == 404


def test_adopt_unknown_run_is_404(client):
    resp = client.post(f"/api/career-directions/discovery-runs/{uuid.uuid4()}/candidates/{uuid.uuid4()}/adopt")
    assert resp.status_code == 404


# --- auth --------------------------------------------------------------------


def test_endpoints_reject_unauthenticated_calls(anon_client):
    direction_id = str(uuid.uuid4())
    assert anon_client.get("/api/career-directions").status_code in (401, 403)
    assert anon_client.get(f"/api/career-directions/{direction_id}").status_code in (401, 403)
    assert anon_client.post("/api/career-directions", json={"name": "x"}).status_code in (401, 403)
    assert anon_client.post("/api/career-directions/discover", json={}).status_code in (401, 403)
