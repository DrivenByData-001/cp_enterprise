"""Day-in-the-Life / Role Context enrichment (next-build brief §6/§7) —
real Postgres, app.role_context.run_json_task mocked (never a live OpenAI
call, same convention as test_document_processing.py/test_requirement_claims.py)."""

import uuid

import pytest

from app import ai, db, role_context
from app.models import (
    CareerStep,
    DayInLifeItem,
    GroundedNote,
    GroundingSummary,
    ManagerContext,
    RoleContextGeneration,
    TeamContext,
    TeamSizeEstimate,
    TypicalWeekItem,
)


def _make_role(cur, **cols) -> str:
    columns = {"instance_type": "observed_posting", "title": "Senior Actuary", **cols}
    return db.upsert_role_instance(cur, None, columns, skills=[{"name": "Solvency II", "requirement_type": "required"}])


def _fake_output(**overrides) -> RoleContextGeneration:
    defaults = dict(
        day_in_life=[
            DayInLifeItem(time_or_phase="08:30", activity="Review the numbers", detail="Check valuation outputs.", basis="inferred", confidence="medium"),
        ],
        typical_week=[
            TypicalWeekItem(day_or_theme="Monday", activity="Planning", detail="Set the week's priorities.", basis="inferred", confidence="medium"),
        ],
        team_context=TeamContext(
            expected_team_size=TeamSizeEstimate(min=2, max=6, basis="inferred", confidence="medium"),
            team_work=[GroundedNote(text="Investigate reserve movements.", basis="inferred", confidence="medium")],
        ),
        manager_context=ManagerContext(
            likely_manager_title="Head of Actuarial Function", title_basis="advert_grounded",
            dynamic="Regular technical check-ins.", dynamic_basis="inferred", dynamic_confidence="medium",
        ),
        stakeholders=[GroundedNote(text="Finance team", basis="inferred", confidence="medium")],
        career_progression=[CareerStep(step="Head of Actuarial Function", basis="advert_grounded", confidence="high")],
        grounding_summary=GroundingSummary(advert_grounded_points=["Works with the HoAF"], inferred_points=["Team size 2-6"]),
        caveats="Illustrative, not a guarantee.",
    )
    defaults.update(overrides)
    return RoleContextGeneration(**defaults)


def _fake_run_json_task(output: RoleContextGeneration | None = None, raise_error: Exception | None = None):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        if raise_error is not None:
            raise raise_error
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=output or _fake_output(), run=run)

    return _dispatch


# --- GET: no generation, no enrichment yet ----------------------------------

def test_get_with_no_enrichment_returns_null_not_an_error(client):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    resp = client.get(f"/api/roles/{role_id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role_instance_id"] == role_id
    assert body["enrichment"] is None


def test_get_on_unknown_role_is_404(client):
    resp = client.get(f"/api/roles/{uuid.uuid4()}/context")
    assert resp.status_code == 404


def test_get_never_triggers_generation(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    def _boom(**kwargs):
        raise AssertionError("GET must never call the AI provider")

    monkeypatch.setattr(role_context, "run_json_task", _boom)
    resp = client.get(f"/api/roles/{role_id}/context")
    assert resp.status_code == 200
    assert resp.json()["enrichment"] is None


# --- generation --------------------------------------------------------------

def test_generate_creates_and_persists_an_enrichment(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur, description="Works closely with the Head of Actuarial Function.")

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/roles/{role_id}/context/generate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    enrichment = body["enrichment"]
    assert enrichment["role_instance_id"] == role_id
    assert enrichment["status"] == "active"
    assert enrichment["model"] == "test-model"
    assert enrichment["generator_version"] == role_context.GENERATOR_VERSION
    assert enrichment["source_content_sha256"]  # fingerprint persisted
    assert enrichment["day_in_life"][0]["basis"] == "inferred"
    assert enrichment["manager_context"]["likely_manager_title"] == "Head of Actuarial Function"
    assert enrichment["manager_context"]["title_basis"] == "advert_grounded"

    with db.db_cursor() as cur:
        cur.execute("SELECT status FROM jobber.extraction_run WHERE task = 'role_context_generate' AND role_instance_id = %s", (role_id,))
        run = cur.fetchone()
    assert run["status"] == "ok"


def test_persisted_repeat_get_causes_no_ai_call(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/roles/{role_id}/context/generate")
    assert first.status_code == 200
    generated_at = first.json()["enrichment"]["generated_at"]

    def _boom(**kwargs):
        raise AssertionError("a persisted GET must never re-call the AI provider")

    monkeypatch.setattr(role_context, "run_json_task", _boom)
    second = client.get(f"/api/roles/{role_id}/context")
    assert second.status_code == 200
    assert second.json()["enrichment"]["generated_at"] == generated_at


def test_generate_again_without_force_is_a_noop_no_ai_call(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/roles/{role_id}/context/generate")
    first_id = first.json()["enrichment"]["id"]

    def _boom(**kwargs):
        raise AssertionError("plain generate must not re-call the AI provider when one is already active")

    monkeypatch.setattr(role_context, "run_json_task", _boom)
    second = client.post(f"/api/roles/{role_id}/context/generate")
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["enrichment"]["id"] == first_id


def test_regenerate_creates_a_new_active_version_and_supersedes_the_old_one(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/roles/{role_id}/context/generate")
    first_id = first.json()["enrichment"]["id"]

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task(output=_fake_output(caveats="A different version.")))
    second = client.post(f"/api/roles/{role_id}/context/regenerate")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["created"] is True
    assert second_body["enrichment"]["id"] != first_id
    assert second_body["enrichment"]["caveats"] == "A different version."

    with db.db_cursor() as cur:
        cur.execute("SELECT id, status FROM jobber.role_context_enrichment WHERE role_instance_id = %s ORDER BY created_at", (role_id,))
        rows = cur.fetchall()
    assert len(rows) == 2  # version history retained, never deleted
    assert [r["status"] for r in rows] == ["superseded", "active"]

    # Only one active row can ever exist (idx_role_context_enrichment_one_active).
    active = client.get(f"/api/roles/{role_id}/context").json()["enrichment"]
    assert active["id"] == second_body["enrichment"]["id"]


def test_model_failure_leaves_prior_enrichment_intact(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/roles/{role_id}/context/generate")
    first_id = first.json()["enrichment"]["id"]

    monkeypatch.setattr(
        role_context, "run_json_task",
        _fake_run_json_task(raise_error=ai.AIProviderError("provider unreachable")),
    )
    failed = client.post(f"/api/roles/{role_id}/context/regenerate")
    assert failed.status_code == 502

    still_active = client.get(f"/api/roles/{role_id}/context").json()["enrichment"]
    assert still_active["id"] == first_id  # untouched by the failed regenerate

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT status, error_type FROM jobber.extraction_run WHERE task = 'role_context_generate' "
            "AND role_instance_id = %s ORDER BY started_at DESC LIMIT 1",
            (role_id,),
        )
        run = cur.fetchone()
    assert run["status"] == "failed"
    assert run["error_type"] == "AIProviderError"


def test_invalid_ai_schema_is_rejected_not_persisted(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    monkeypatch.setattr(
        role_context, "run_json_task",
        _fake_run_json_task(raise_error=ai.AISchemaValidationError("bad basis value")),
    )
    resp = client.post(f"/api/roles/{role_id}/context/generate")
    assert resp.status_code == 422

    assert client.get(f"/api/roles/{role_id}/context").json()["enrichment"] is None


def test_missing_api_key_returns_a_clear_operational_error(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur)

    monkeypatch.setattr(
        role_context, "run_json_task",
        _fake_run_json_task(raise_error=ai.AIConfigError("OPENAI_API_KEY is not set")),
    )
    resp = client.post(f"/api/roles/{role_id}/context/generate")
    assert resp.status_code == 503


def test_generate_on_unknown_role_is_404(client, monkeypatch):
    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/roles/{uuid.uuid4()}/context/generate")
    assert resp.status_code == 404


# --- auth ----------------------------------------------------------------

def test_context_endpoints_reject_unauthenticated_calls(anon_client):
    role_id = str(uuid.uuid4())
    assert anon_client.get(f"/api/roles/{role_id}/context").status_code in (401, 403)
    assert anon_client.post(f"/api/roles/{role_id}/context/generate").status_code in (401, 403)
    assert anon_client.post(f"/api/roles/{role_id}/context/regenerate").status_code in (401, 403)


# --- input construction uses role evidence, not profile360 -----------------

def test_generation_input_never_includes_profile360_evidence(client, monkeypatch):
    with db.db_cursor() as cur:
        role_id = _make_role(cur, description="A description mentioning nothing about the user.")

    captured = {}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        captured["user_input"] = user_input
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=_fake_output(), run=run)

    monkeypatch.setattr(role_context, "run_json_task", _dispatch)
    resp = client.post(f"/api/roles/{role_id}/context/generate")
    assert resp.status_code == 200
    assert "profile360" not in captured["user_input"].lower()
    assert "Senior Actuary" in captured["user_input"]


def test_generation_falls_back_to_source_aware_ingest_evidence(client, monkeypatch):
    """A role captured via the source-aware ingest + extract-requirements
    pipeline (no description/requirements/role_skill_observation) must still
    generate from its document text + requirement_claim skills (the same
    evidence the 2026 Role Detail fix surfaces)."""
    from app import extraction
    from app.models import RequirementExtractionResult, RequirementItem

    body = "Senior Actuary role working on Solvency II and ORSA reporting."
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('knowledge', 'Solvency II', 'active', 'curator', now())"
        )
        document_id, _ = db.create_document(cur, kind="job_posting", content_text=body, provenance_quality="original")
        role_id = db.upsert_role_instance(
            cur, None,
            {"instance_type": "observed_posting", "title": "Senior Actuary", "document_id": document_id},
            skills=[],
        )

        def _req_dispatch(*, task, prompt_name, user_input, output_model):
            run = ai.AITaskRun(
                task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
                started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
                status="ok", input_chars=10, output_chars=10,
            )
            return ai.AITaskResult(
                output=RequirementExtractionResult(
                    requirements=[RequirementItem(surface_form="Solvency II", requirement_type="required", basis="stated", evidence_span="Solvency II")]
                ),
                run=run,
            )

        import app.extraction as extraction_module
        monkeypatch.setattr(extraction_module, "run_json_task", _req_dispatch)
        extraction.extract_role_requirements(cur, role_id)

    captured = {}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        captured["user_input"] = user_input
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=_fake_output(), run=run)

    monkeypatch.setattr(role_context, "run_json_task", _dispatch)
    resp = client.post(f"/api/roles/{role_id}/context/generate")
    assert resp.status_code == 200
    assert "Solvency II" in captured["user_input"]
    assert body in captured["user_input"]
