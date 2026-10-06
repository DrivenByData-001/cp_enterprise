"""Phase 5: Interview Prep generation, grounding, and staleness (docs/36).

Real Postgres, app.application_artifacts.run_json_task mocked (never a live
OpenAI call — same convention as test_application_artifacts.py). Interview
Prep reuses Phase 4's versioned artifact lifecycle unchanged, so this file
focuses on what's actually new: the fifth artifact_type wiring, its own
grounding rules (an answer-plan evidence point/closing point needs
person-side backing; a focus area/question/caution/question-to-ask may be
role-side-only), the lifecycle-event and application-material source
categories, and interview-specific staleness — including that it is
completely isolated from ordinary CV/Positioning staleness in both
directions.
"""

import uuid

from app import ai, application_artifacts as artifacts, application_generation as gen, db
from app.models import (
    ApplicationInterviewPrepGeneration,
    InterviewAnswerPlan,
    InterviewCaution,
    InterviewClosingPoint,
    InterviewEvidencePoint,
    InterviewFocusArea,
    InterviewQuestion,
    InterviewQuestionToAsk,
)

# --- fixtures (mirrors test_application_artifacts.py's own local helpers —
# no shared fixture module exists in this suite, by convention) ------------


def _concept(cur, name: str, type_code: str = "tool", status: str = "active") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, f"{name} {uuid.uuid4().hex[:8]}", status),
    )
    return str(cur.fetchone()["id"])


def _posting(cur, title="Life Actuarial Manager", organisation="Acme") -> str:
    return db.upsert_role_instance(
        cur, None, {"instance_type": "observed_posting", "title": title, "organisation": organisation}, skills=[],
    )


def _accepted_claim(cur, role_id: str, concept_id: str, *, requirement_type="required", evidence_span=None) -> str:
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status, evidence_span) "
        "VALUES (%s, %s, %s, 'user_asserted', 'accepted', %s) RETURNING id",
        (role_id, concept_id, requirement_type, evidence_span),
    )
    return str(cur.fetchone()["id"])


def _legacy_observation(cur, role_id: str, concept_id: str, surface_form: str, requirement_type="preferred") -> None:
    cur.execute(
        "INSERT INTO jobber.role_skill_observation "
        "(role_instance_id, surface_form, requirement_type, observation_basis, canonical_concept_id) "
        "VALUES (%s, %s, %s, 'app_capture', %s)",
        (role_id, surface_form, requirement_type, concept_id),
    )
    cur.execute(
        "INSERT INTO jobber.role_skill_observation_concept (role_skill_observation_id, concept_id, mapping_basis) "
        "SELECT id, %s, 'legacy_single' FROM jobber.role_skill_observation "
        "WHERE role_instance_id = %s AND canonical_concept_id = %s ORDER BY created_at DESC LIMIT 1 "
        "ON CONFLICT DO NOTHING",
        (concept_id, role_id, concept_id),
    )


def _episode(cur, *, title="Senior Actuary", organisation="PrevCo", start_date="2020-01-01", end_date=None) -> str:
    cur.execute(
        "INSERT INTO profile360.episodes (title, organisation, start_date, end_date, status) "
        "VALUES (%s, %s, %s, %s, 'active') RETURNING id",
        (title, organisation, start_date, end_date),
    )
    return str(cur.fetchone()["id"])


def _claim(cur, text: str, *, episode_id=None, depth=None) -> str:
    cur.execute(
        "INSERT INTO profile360.claims (claim_text, episode_id, depth) VALUES (%s, %s, %s) RETURNING id",
        (text, episode_id, depth),
    )
    return str(cur.fetchone()["id"])


def _claim_mapping(cur, claim_id: str, concept_id: str, *, review_status="accepted") -> None:
    cur.execute(
        "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
        "VALUES (%s, %s, 'ai_suggested', %s)",
        (claim_id, concept_id, review_status),
    )


def _application(cur, role_id: str, status="preparing") -> str:
    cur.execute(
        "INSERT INTO jobber.application (role_instance_id, status) VALUES (%s, %s) RETURNING id", (role_id, status),
    )
    return str(cur.fetchone()["id"])


def _event(cur, application_id: str, event_type: str, event_at: str, *, label=None, notes=None) -> str:
    cur.execute(
        "INSERT INTO jobber.application_event (application_id, event_type, event_at, label, notes) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (application_id, event_type, event_at, label, notes),
    )
    return str(cur.fetchone()["id"])


def _artifact(cur, application_id: str, artifact_type: str, *, status="active", content, input_fingerprint="fp") -> str:
    cur.execute(
        "INSERT INTO jobber.application_artifact "
        "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
        "VALUES (%s, %s, %s, 'ai', '1', %s, %s) RETURNING id",
        (application_id, artifact_type, status, input_fingerprint, db.to_json_param(content)),
    )
    return str(cur.fetchone()["id"])


def _cv_content(text="Experienced professional.") -> dict:
    return {"profile_summary": {"text": text, "source_refs": []}, "experience": [], "skills": [], "omissions_or_cautions": []}


def _positioning_content(text="Strong fit for this role.") -> dict:
    return {
        "positioning_statement": {"text": text, "source_refs": []}, "themes": [], "requirements_to_lead_with": [],
        "gaps_and_cautions": [], "language_to_mirror": [], "avoid_claiming": [],
    }


def _grounded_role(cur):
    concept_id = _concept(cur, "Solvency II")
    role_id = _posting(cur)
    _accepted_claim(cur, role_id, concept_id, evidence_span="Strong Solvency II knowledge required.")
    episode_id = _episode(cur)
    claim_id = _claim(cur, "Led Solvency II reporting for two years.", episode_id=episode_id, depth="owned")
    _claim_mapping(cur, claim_id, concept_id)

    application_id = _application(cur, role_id)
    return {
        "application_id": application_id, "role_id": role_id, "concept_id": concept_id,
        "episode_id": episode_id, "claim_id": claim_id,
    }


def _legacy_only_role(cur):
    legacy_concept_id = _concept(cur, "Prophet")
    role_id = _posting(cur, title="Legacy-only Role")
    _legacy_observation(cur, role_id, legacy_concept_id, "Prophet")
    episode_id = _episode(cur)
    application_id = _application(cur, role_id)
    return {"application_id": application_id, "role_id": role_id, "legacy_concept_id": legacy_concept_id, "episode_id": episode_id}


def _fake_run_json_task(output, raise_error: Exception | None = None):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        if raise_error is not None:
            raise raise_error
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=output, run=run)

    return _dispatch


def _interview_prep_output(*, role_ref: str, person_ref: str) -> ApplicationInterviewPrepGeneration:
    """`role_ref` alone backs every block the hardening rules allow to be
    role-side-only (focus_areas/questions/questions_to_ask/cautions);
    `person_ref` alone backs every block that requires person-side grounding
    (evidence_points/closing_points) — proving each rule independently."""
    return ApplicationInterviewPrepGeneration(
        focus_areas=[InterviewFocusArea(title="Solvency II", why_it_matters="Core requirement for this role.", source_refs=[role_ref])],
        questions=[
            InterviewQuestion(
                question="Tell me about a Solvency II project you led.", question_type="experience", source_refs=[role_ref],
                answer_plan=InterviewAnswerPlan(
                    approach="Use a structured example.",
                    evidence_points=[InterviewEvidencePoint(text="Led Solvency II reporting for two years.", source_refs=[person_ref])],
                    cautions=[InterviewCaution(text="Limited exposure to reinsurance treaties.", source_refs=[role_ref])],
                ),
            )
        ],
        questions_to_ask=[InterviewQuestionToAsk(text="What does success look like in the first six months?", source_refs=[role_ref])],
        closing_points=[InterviewClosingPoint(text="Reiterate the Solvency II track record.", source_refs=[person_ref])],
        prep_checklist=["Review the job description again.", "Prepare two questions to ask."],
    )


# --- explicit AI-only --------------------------------------------------------


def test_get_artifacts_never_calls_ai_for_interview_prep(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)

    def _boom(**kwargs):
        raise AssertionError("GET must never call the AI provider")

    monkeypatch.setattr(artifacts, "run_json_task", _boom)
    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifacts"]["interview_prep"]["active"] is None
    assert body["artifacts"]["interview_prep"]["draft"] is None
    assert "interview_generation_context" in body
    assert body["interview_generation_context"]["upcoming_interview_available"] is False


def test_creating_lifecycle_event_never_calls_ai(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)

    def _boom(**kwargs):
        raise AssertionError("creating a lifecycle event must never call the AI provider")

    monkeypatch.setattr(artifacts, "run_json_task", _boom)
    resp = client.post(
        f"/api/applications/{setup['application_id']}/events",
        json={"event_type": "interview_completed", "event_at": "2026-01-01T00:00:00+00:00", "notes": "Went well."},
    )
    assert resp.status_code == 200


def test_interview_prep_ai_failure_leaves_existing_artifacts_untouched(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(None, raise_error=ai.AIProviderError("boom")))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 502
    with db.db_cursor() as cur:
        assert artifacts._active_row(cur, setup["application_id"], "interview_prep") is None
        assert artifacts._draft_row(cur, setup["application_id"], "interview_prep") is None


# --- generate / regenerate / adopt / discard / edit / history lifecycle -----


def test_generate_interview_prep_creates_draft_never_active(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_interview_prep_output(role_ref=role_ref, person_ref=person_ref)))

    resp = client.post(
        f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate",
        json={"guidance": "Focus on technical depth."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["artifact"]["artifact_type"] == "interview_prep"
    assert body["artifact"]["status"] == "draft"
    assert body["artifact"]["origin"] == "ai"
    assert body["artifact"]["stale"] is False

    with db.db_cursor() as cur:
        assert artifacts._active_row(cur, setup["application_id"], "interview_prep") is None
        assert artifacts._draft_row(cur, setup["application_id"], "interview_prep") is not None


def test_interview_prep_adopt_regenerate_discard_edit_history_lifecycle(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_interview_prep_output(role_ref=role_ref, person_ref=person_ref)))

    d1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={}).json()
    a1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/{d1['artifact']['id']}/adopt").json()
    assert a1["artifact"]["status"] == "active"

    d2 = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={}).json()
    assert d2["artifact"]["id"] != d1["artifact"]["id"]
    with db.db_cursor() as cur:
        active = artifacts._active_row(cur, setup["application_id"], "interview_prep")
        assert str(active["id"]) == a1["artifact"]["id"]  # regenerate never touches active

    assert client.post(f"/api/applications/{setup['application_id']}/artifacts/{d2['artifact']['id']}/discard").status_code == 200

    edited_content = dict(a1["artifact"]["content"])
    edited_content["prep_checklist"] = ["Bring a printed CV.", "Arrive ten minutes early."]
    edit_resp = client.post(
        f"/api/applications/{setup['application_id']}/artifacts/{a1['artifact']['id']}/edit", json={"content": edited_content},
    )
    assert edit_resp.status_code == 200
    edited = edit_resp.json()["artifact"]
    assert edited["origin"] == "user_edit"
    assert edited["status"] == "draft"
    assert edited["grounding_status"] == "user_edited_not_revalidated"
    assert edited["content"]["prep_checklist"] == ["Bring a printed CV.", "Arrive ten minutes early."]

    history = client.get(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/history").json()["history"]
    assert len(history) == 3
    statuses = {h["status"] for h in history}
    assert statuses == {"active", "superseded", "draft"}


# --- grounding: focus areas / questions / cautions may be role-side-only ---


def test_focus_area_question_and_caution_may_be_grounded_role_side_only(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        role_ref = f"role_requirement_legacy:{setup['legacy_concept_id']}"
        person_ref = f"profile_episode:{setup['episode_id']}"
    output = _interview_prep_output(role_ref=role_ref, person_ref=person_ref)
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(output))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 200


def test_legacy_requirement_may_inform_practice_area_but_is_not_reviewed(client):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="interview_prep")
    assert setup["legacy_concept_id"] not in ctx.accepted_concept_ids()
    legacy_sources = [s for s in ctx.sources if s.kind == "role_requirement_legacy"]
    assert any(s.ref == f"role_requirement_legacy:{setup['legacy_concept_id']}" for s in legacy_sources)


# --- grounding: evidence points / closing points require person-side ------


def test_answer_plan_evidence_point_with_only_role_side_source_is_rejected(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
    bad = ApplicationInterviewPrepGeneration(
        focus_areas=[InterviewFocusArea(title="Solvency II", why_it_matters="Core.", source_refs=[role_ref])],
        questions=[
            InterviewQuestion(
                question="Tell me about Solvency II.", question_type="experience", source_refs=[role_ref],
                answer_plan=InterviewAnswerPlan(
                    approach="STAR.",
                    evidence_points=[InterviewEvidencePoint(text="Did the thing.", source_refs=[role_ref])],
                    cautions=[],
                ),
            )
        ],
        questions_to_ask=[], closing_points=[], prep_checklist=[],
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 422
    assert "person-side source" in resp.text
    with db.db_cursor() as cur:
        assert artifacts._draft_row(cur, setup["application_id"], "interview_prep") is None


def test_closing_point_with_only_role_side_source_is_rejected(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    output = _interview_prep_output(role_ref=role_ref, person_ref=person_ref)
    output.closing_points = [InterviewClosingPoint(text="Reiterate fit.", source_refs=[role_ref])]  # role-side only
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(output))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 422
    assert "person-side source" in resp.text


def test_empty_source_refs_rejected_for_interview_prep(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    bad = ApplicationInterviewPrepGeneration(
        focus_areas=[InterviewFocusArea(title="X", why_it_matters="Y", source_refs=[])],
        questions=[], questions_to_ask=[], closing_points=[], prep_checklist=[],
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 422
    assert "no source_refs" in resp.text


def test_unknown_source_ref_is_rejected_for_interview_prep(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    bad = _interview_prep_output(
        role_ref="role_requirement:00000000-0000-0000-0000-000000000000",
        person_ref=f"profile_claim:{setup['claim_id']}",
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 422
    with db.db_cursor() as cur:
        assert artifacts._draft_row(cur, setup["application_id"], "interview_prep") is None


# --- grounding: lifecycle events and application material -------------------


def test_lifecycle_event_source_satisfies_person_side_grounding(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        event_id = _event(
            cur, setup["application_id"], "interview_completed", "2026-01-01T00:00:00+00:00",
            notes="Discussed my Solvency II delivery in depth.",
        )
        role_ref = f"role_requirement:{setup['concept_id']}"
        event_ref = f"application_event:{event_id}"
    output = ApplicationInterviewPrepGeneration(
        focus_areas=[InterviewFocusArea(title="Solvency II", why_it_matters="Came up before.", source_refs=[role_ref])],
        questions=[
            InterviewQuestion(
                question="Tell me more about that delivery.", question_type="experience", source_refs=[role_ref],
                answer_plan=InterviewAnswerPlan(
                    approach="Expand on the previous round.",
                    evidence_points=[InterviewEvidencePoint(text="Discussed it in the first round.", source_refs=[event_ref])],
                    cautions=[],
                ),
            )
        ],
        questions_to_ask=[], closing_points=[InterviewClosingPoint(text="Reiterate strength here.", source_refs=[event_ref])],
        prep_checklist=[],
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(output))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 200


def test_event_source_is_user_supplied_never_canonical_evidence(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        event_id = _event(
            cur, setup["application_id"], "interview_completed", "2026-01-01T00:00:00+00:00",
            notes="Felt strong on the technical questions.",
        )
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="interview_prep")
    event_sources = [s for s in ctx.sources if s.ref == f"application_event:{event_id}"]
    assert len(event_sources) == 1
    assert event_sources[0].category == gen.CATEGORY_USER_SUPPLIED
    assert "NOT verified evidence" in event_sources[0].content


def test_application_material_alone_is_not_person_side_evidence(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        cv_id = _artifact(cur, setup["application_id"], "cv", status="active", content=_cv_content())
        role_ref = f"role_requirement:{setup['concept_id']}"
        material_ref = f"application_material:{cv_id}"
    bad = ApplicationInterviewPrepGeneration(
        focus_areas=[InterviewFocusArea(title="X", why_it_matters="Y", source_refs=[role_ref])],
        questions=[
            InterviewQuestion(
                question="Q", question_type="experience", source_refs=[role_ref],
                answer_plan=InterviewAnswerPlan(
                    approach="A",
                    evidence_points=[InterviewEvidencePoint(text="Did the thing.", source_refs=[material_ref])],
                    cautions=[],
                ),
            )
        ],
        questions_to_ask=[], closing_points=[], prep_checklist=[],
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={})
    assert resp.status_code == 422
    assert "person-side source" in resp.text


def test_application_material_source_category_is_its_own_never_strategy_or_evidence(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        cv_id = _artifact(cur, setup["application_id"], "cv", status="active", content=_cv_content("Adopted CV summary."))
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="interview_prep")
    material_sources = [s for s in ctx.sources if s.ref == f"application_material:{cv_id}"]
    assert len(material_sources) == 1
    assert material_sources[0].category == gen.CATEGORY_APPLICATION_MATERIAL
    assert "NOT new evidence" in material_sources[0].content
    # a plain positioning/cv/cover_letter/supporting_statement generation call
    # must never see application_material sources at all (docs/36 §8 scopes
    # them to interview_prep only).
    with db.db_cursor() as cur:
        other_ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="cv")
    assert not [s for s in other_ctx.sources if s.kind == "application_material"]


# --- staleness ---------------------------------------------------------------


def test_interview_prep_becomes_stale_when_lifecycle_event_added(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_interview_prep_output(role_ref=role_ref, person_ref=person_ref)))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    fresh = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert fresh["artifacts"]["interview_prep"]["active"]["stale"] is False

    with db.db_cursor() as cur:
        _event(cur, setup["application_id"], "interview_scheduled", "2026-03-01T00:00:00+00:00", label="Technical panel")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert resp["artifacts"]["interview_prep"]["active"]["stale"] is True


def test_interview_prep_becomes_stale_when_application_status_changes(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_interview_prep_output(role_ref=role_ref, person_ref=person_ref)))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    client.patch(f"/api/applications/{setup['application_id']}", json={"status": "interviewing"})

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert resp["artifacts"]["interview_prep"]["active"]["stale"] is True


def test_interview_prep_becomes_stale_when_active_cv_changes(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_interview_prep_output(role_ref=role_ref, person_ref=person_ref)))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    with db.db_cursor() as cur:
        _artifact(cur, setup["application_id"], "cv", status="active", content=_cv_content("A newly adopted CV."))

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert resp["artifacts"]["interview_prep"]["active"]["stale"] is True


def test_interview_prep_becomes_stale_when_active_positioning_changes(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_interview_prep_output(role_ref=role_ref, person_ref=person_ref)))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/interview_prep/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    with db.db_cursor() as cur:
        _artifact(cur, setup["application_id"], "positioning", status="active", content=_positioning_content("A newly adopted positioning."))

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert resp["artifacts"]["interview_prep"]["active"]["stale"] is True


def test_recording_an_offer_does_not_stale_the_adopted_cv(client):
    """The hardening-note-style cross-type isolation check (build §9/§14): a
    lifecycle event must never stale an ordinary CV/Positioning artifact."""
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        bundle = gen.gather_application_evidence(cur, setup["application_id"])
        fp = gen.compute_fingerprint_for(bundle, artifact_type="cv", guidance=None, active_positioning=None)
        _artifact(cur, setup["application_id"], "cv", status="active", content=_cv_content(), input_fingerprint=fp)

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert resp["artifacts"]["cv"]["active"]["stale"] is False

    with db.db_cursor() as cur:
        _event(cur, setup["application_id"], "offer_received", "2026-03-01T00:00:00+00:00")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert resp["artifacts"]["cv"]["active"]["stale"] is False


def test_recording_an_offer_does_not_stale_the_adopted_positioning(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        bundle = gen.gather_application_evidence(cur, setup["application_id"])
        fp = gen.compute_fingerprint_for(bundle, artifact_type="positioning", guidance=None, active_positioning=None)
        _artifact(cur, setup["application_id"], "positioning", status="active", content=_positioning_content(), input_fingerprint=fp)
        _event(cur, setup["application_id"], "offer_received", "2026-03-01T00:00:00+00:00")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    assert resp["artifacts"]["positioning"]["active"]["stale"] is False


def test_interview_generation_context_reports_upcoming_interview(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        _event(cur, setup["application_id"], "interview_scheduled", "2099-01-01T00:00:00+00:00", label="Panel round")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts").json()
    ctx = resp["interview_generation_context"]
    assert ctx["upcoming_interview_available"] is True
    assert ctx["upcoming_interview"]["label"] == "Panel round"
    assert ctx["lifecycle_events_count"] == 1
