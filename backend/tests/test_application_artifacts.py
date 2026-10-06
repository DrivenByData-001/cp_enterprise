"""Phase 4: grounded Application artifact generation + lifecycle (docs/35).

Real Postgres, app.application_artifacts.run_json_task mocked (never a live
OpenAI call — same convention as test_concept_dossier.py/test_role_context.py).
Covers: migration shape, draft/active/superseded lifecycle, explicit-AI-only
behaviour, the reviewed-vs-legacy grounding split, source_ref/episode_id/
concept_id rejection, the CV chronology safeguard, staleness fingerprinting,
and the Profile360/requirement-review/application-status evidence boundaries.
"""

import uuid

import psycopg
import pytest

from app import ai, application_artifacts as artifacts, application_generation as gen, db
from app.models import (
    ApplicationCoverLetterGeneration,
    ApplicationCVGeneration,
    ApplicationPositioningGeneration,
    ApplicationSupportingStatementGeneration,
    CoverLetterBlock,
    CVExperienceBullet,
    CVExperienceEntry,
    CVSkillLine,
    PositioningGapOrCaution,
    PositioningRequirementToLead,
    PositioningTheme,
    SourcedText,
    SupportingStatementSection,
)

# --- fixtures ----------------------------------------------------------------


def _concept(cur, name: str, type_code: str = "tool", status: str = "active") -> str:
    # A short random suffix keeps every call unique even when tests reuse the
    # same display name across setups — jobber.concept has a
    # (type_code, canonical_name) uniqueness constraint.
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
    # basis is always 'user_asserted' here (never 'stated'/'implied') — those
    # require a document_id + evidence_span pair per requirement_claim's own
    # CHECK constraint (migration 0003); user_asserted has no such
    # requirement and can still carry evidence_span text for display.
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


def _episode(cur, *, title="Senior Actuary", organisation="PrevCo", start_date="2020-01-01", end_date=None, responsibilities=None) -> str:
    cur.execute(
        "INSERT INTO profile360.episodes (title, organisation, start_date, end_date, responsibilities, status) "
        "VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id",
        (title, organisation, start_date, end_date, responsibilities),
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


def _note(cur, application_id: str, *, concept_id=None, note_type="general", text="A note") -> str:
    cur.execute(
        "INSERT INTO jobber.application_note (application_id, concept_id, note_type, note_text) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (application_id, concept_id, note_type, text),
    )
    return str(cur.fetchone()["id"])


def _grounded_role(cur):
    """One posting with a single reviewed (accepted claim) requirement,
    backed by an accepted Profile360 claim mapping and an accepted episode.

    Deliberately carries NO role_skill_observation row: role_requirements.py's
    fallback is role-*level*, not concept-level (see its own module
    docstring, point 1/3 — "The two sources are never merged for one role.
    Exactly one is used.") — the instant a role has any usable accepted
    claim, ALL of its role_skill_observation rows are excluded from
    evidence, for every concept, not just the claimed one. A role that
    should exercise the legacy fallback path must therefore carry no
    accepted claim at all — see `_legacy_only_role` below."""
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
    """A role with only a legacy role_skill_observation and zero accepted
    requirement_claim rows, so the fallback path is actually exercised (see
    `_grounded_role`'s docstring for why it can't carry both). Also seeds one
    Profile360 episode with no claim mapping at all — `build_source_registry`
    always offers every episode as `canonical_evidence` regardless of
    mapping, so this gives tests a genuine person-side ref to pair with the
    role-only one, without granting this role any accepted requirement."""
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


def _positioning_output(
    *, ref: str, person_ref: str | None = None, concept_id: str | None = None, gap_concept_id=None,
) -> ApplicationPositioningGeneration:
    """`person_ref` (a canonical/partial-evidence or user-supplied-context
    ref) is paired with the role-only `ref` on every block the hardening note
    requires person-side backing for. `gaps_and_cautions` deliberately never
    gets `person_ref` — a gap/caution is the one exempt block and may be
    grounded in role-side context alone."""
    refs = [ref, person_ref] if person_ref else [ref]
    return ApplicationPositioningGeneration(
        positioning_statement=SourcedText(text="Strong fit for this role.", source_refs=refs),
        themes=[PositioningTheme(title="Track record", message="Relevant delivery experience.", source_refs=refs)],
        requirements_to_lead_with=(
            [PositioningRequirementToLead(concept_id=concept_id, reason="Directly evidenced.", source_refs=refs)] if concept_id else []
        ),
        gaps_and_cautions=([PositioningGapOrCaution(concept_id=gap_concept_id, message="Some uncertainty.", source_refs=[ref])] if gap_concept_id else []),
        language_to_mirror=["stakeholder management"],
        avoid_claiming=["Do not claim board-level ownership."],
    )


def _cv_output(*, ref: str, episode_id: str) -> ApplicationCVGeneration:
    # `ref` is always a profile_episode:<id> ref at every call site — already
    # canonical_evidence, so every block here already satisfies the
    # person-side hardening rule with no separate person_ref needed.
    return ApplicationCVGeneration(
        profile_summary=SourcedText(text="Experienced actuarial professional.", source_refs=[ref]),
        experience=[CVExperienceEntry(episode_id=episode_id, bullets=[CVExperienceBullet(text="Led Solvency II reporting.", source_refs=[ref])])],
        skills=[CVSkillLine(text="Solvency II", source_refs=[ref])],
        omissions_or_cautions=[],
    )


def _cover_letter_output(*, ref: str, person_ref: str) -> ApplicationCoverLetterGeneration:
    block = CoverLetterBlock(text="Relevant experience.", source_refs=[ref, person_ref])
    return ApplicationCoverLetterGeneration(salutation="Dear Hiring Manager,", opening=block, body=[block], closing=block)


def _supporting_statement_output(
    *, ref: str, person_ref: str, concept_id: str | None = None, is_gap_or_caution: bool = False,
) -> ApplicationSupportingStatementGeneration:
    block = CoverLetterBlock(text="Directly relevant experience.", source_refs=[ref, person_ref])
    return ApplicationSupportingStatementGeneration(
        opening=block,
        sections=[SupportingStatementSection(heading="Solvency II", concept_id=concept_id, is_gap_or_caution=is_gap_or_caution, paragraphs=[block])],
        gaps_addressed=[],
    )


# --- migration shape ---------------------------------------------------------


def test_migration_0028_applied(client):
    with db.db_cursor() as cur:
        cur.execute("SELECT filename FROM jobber.migration_history")
        applied = {row["filename"] for row in cur.fetchall()}
    assert "0028_application_artifacts.sql" in applied


def test_artifact_table_shape(client):
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'application_artifact'"
        )
        columns = {row["column_name"] for row in cur.fetchall()}
    assert {
        "id", "application_id", "artifact_type", "status", "origin", "generator_version", "model",
        "prompt_name", "prompt_version", "guidance", "input_fingerprint", "source_manifest", "content",
        "raw_output", "created_at", "updated_at", "superseded_at",
    } <= columns


def test_status_check_constraint_rejects_invalid_value(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO jobber.application_artifact "
                "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
                "VALUES (%s, 'positioning', 'not_a_status', 'ai', '1', 'fp', '{}'::jsonb)",
                (setup["application_id"],),
            )


def test_db_enforces_one_active_and_one_draft_per_type(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        cur.execute(
            "INSERT INTO jobber.application_artifact "
            "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
            "VALUES (%s, 'positioning', 'draft', 'ai', '1', 'fp1', '{}'::jsonb)",
            (setup["application_id"],),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO jobber.application_artifact "
                "(application_id, artifact_type, status, origin, generator_version, input_fingerprint, content) "
                "VALUES (%s, 'positioning', 'draft', 'ai', '1', 'fp2', '{}'::jsonb)",
                (setup["application_id"],),
            )


# --- explicit AI only --------------------------------------------------------


def test_get_artifacts_never_calls_ai(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)

    def _boom(**kwargs):
        raise AssertionError("GET must never call the AI provider")

    monkeypatch.setattr(artifacts, "run_json_task", _boom)
    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.status_code == 200
    body = resp.json()
    for artifact_type in artifacts.ARTIFACT_TYPES:
        assert body["artifacts"][artifact_type]["active"] is None
        assert body["artifacts"][artifact_type]["draft"] is None
    assert body["generation_context"]["reviewed_requirements_used"] == 1
    assert body["generation_context"]["legacy_requirements_excluded"] == 0


def test_generation_context_summary_counts_legacy_requirements(client):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    body = resp.json()["generation_context"]
    assert body["reviewed_requirements_used"] == 0
    assert body["legacy_requirements_excluded"] == 1


def test_generation_context_summary_counts_application_examples(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        _note(cur, setup["application_id"], concept_id=setup["concept_id"], note_type="evidence_example", text="Shipped an ORSA once.")
    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.json()["generation_context"]["application_examples"] == 1


def test_ai_failure_leaves_active_and_draft_untouched(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)

    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(None, raise_error=ai.AIProviderError("boom")))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 502

    with db.db_cursor() as cur:
        assert artifacts._active_row(cur, setup["application_id"], "positioning") is None
        assert artifacts._draft_row(cur, setup["application_id"], "positioning") is None


# --- grounding context --------------------------------------------------------


def test_context_treats_accepted_claim_as_reviewed_requirement(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="positioning")
    accepted_ids = {r["concept_id"] for r in ctx.bundle.accepted_requirements}
    assert accepted_ids == {setup["concept_id"]}
    assert ctx.bundle.legacy_requirements == []
    assert setup["concept_id"] in ctx.accepted_concept_ids()
    reviewed_refs = [s for s in ctx.sources if s.kind == "role_requirement"]
    assert any(s.ref == f"role_requirement:{setup['concept_id']}" for s in reviewed_refs)


def test_context_treats_legacy_observation_as_context_never_accepted(client):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="positioning")
    legacy_ids = {r["concept_id"] for r in ctx.bundle.legacy_requirements}
    assert legacy_ids == {setup["legacy_concept_id"]}
    assert ctx.bundle.accepted_requirements == []
    # accepted_concept_ids() (used to validate requirements_to_lead_with) must
    # never include a legacy/unreviewed concept, even though it's still
    # offered as context under its own ref kind.
    assert setup["legacy_concept_id"] not in ctx.accepted_concept_ids()
    legacy_refs = [s for s in ctx.sources if s.kind == "role_requirement_legacy"]
    assert any(s.ref == f"role_requirement_legacy:{setup['legacy_concept_id']}" for s in legacy_refs)


def test_application_note_labelled_user_supplied_not_evidence(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        note_id = _note(cur, setup["application_id"], concept_id=setup["concept_id"], note_type="evidence_example", text="Shipped an ORSA once.")
        ctx = gen.build_application_generation_context(cur, setup["application_id"], artifact_type="positioning")
    note_sources = [s for s in ctx.sources if s.ref == f"application_note:{note_id}"]
    assert len(note_sources) == 1
    assert note_sources[0].category == gen.CATEGORY_USER_SUPPLIED
    assert "NOT Profile360 evidence" in note_sources[0].content


def test_role_document_never_labelled_as_person_evidence(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
        db.update_role_metadata(cur, role_id, {})  # no-op, keeps helper symmetry
        cur.execute("UPDATE jobber.role_instance SET description = %s WHERE id = %s", ("Must know Python.", role_id))
        application_id = _application(cur, role_id)
        ctx = gen.build_application_generation_context(cur, application_id, artifact_type="positioning")
    doc_sources = [s for s in ctx.sources if s.kind == "role_document"]
    assert len(doc_sources) == 1
    assert doc_sources[0].category == gen.CATEGORY_ROLE_SIDE


# --- generate / regenerate / adopt / discard / edit lifecycle ---------------


def test_generate_creates_a_draft_never_active(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))

    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={"guidance": "Be concise."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["artifact"]["status"] == "draft"
    assert body["artifact"]["origin"] == "ai"
    assert body["artifact"]["grounding_status"] == "grounded_generation"
    assert body["artifact"]["stale"] is False

    with db.db_cursor() as cur:
        assert artifacts._active_row(cur, setup["application_id"], "positioning") is None
        draft = artifacts._draft_row(cur, setup["application_id"], "positioning")
        assert draft is not None


def test_regenerate_supersedes_only_prior_draft_never_active(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))

    first = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    adopted = client.post(f"/api/applications/{setup['application_id']}/artifacts/{first['artifact']['id']}/adopt").json()
    active_id = adopted["artifact"]["id"]

    second = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    assert second["artifact"]["id"] != first["artifact"]["id"]

    with db.db_cursor() as cur:
        active = artifacts._active_row(cur, setup["application_id"], "positioning")
        assert str(active["id"]) == active_id
        assert active["status"] == "active"
        draft = artifacts._draft_row(cur, setup["application_id"], "positioning")
        assert str(draft["id"]) == second["artifact"]["id"]
        cur.execute("SELECT status FROM jobber.application_artifact WHERE id = %s", (first["artifact"]["id"],))
        # `first` was adopted, so it IS the active row — regenerating only
        # ever supersedes a prior *draft*, never touching active.
        assert cur.fetchone()["status"] == "active"


def test_adopt_supersedes_prior_active(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))

    d1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    a1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/{d1['artifact']['id']}/adopt").json()
    d2 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    a2 = client.post(f"/api/applications/{setup['application_id']}/artifacts/{d2['artifact']['id']}/adopt").json()
    assert a2["artifact"]["status"] == "active"

    with db.db_cursor() as cur:
        cur.execute("SELECT status FROM jobber.application_artifact WHERE id = %s", (a1["artifact"]["id"],))
        assert cur.fetchone()["status"] == "superseded"


def test_discard_leaves_active_untouched(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))

    d1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    a1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/{d1['artifact']['id']}/adopt").json()
    d2 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()

    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/{d2['artifact']['id']}/discard")
    assert resp.status_code == 200
    assert resp.json()["status"] == "discarded"

    with db.db_cursor() as cur:
        active = artifacts._active_row(cur, setup["application_id"], "positioning")
        assert str(active["id"]) == a1["artifact"]["id"]
        assert artifacts._draft_row(cur, setup["application_id"], "positioning") is None


def test_discard_with_no_draft_is_409(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/{uuid.uuid4()}/discard")
    assert resp.status_code == 409


def test_manual_edit_creates_user_edit_draft_preserves_generated_history(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))

    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    generated_id = generated["artifact"]["id"]
    edited_content = dict(generated["artifact"]["content"])
    edited_content["positioning_statement"] = {"text": "A user-rewritten statement.", "source_refs": [ref]}

    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated_id}/edit", json={"content": edited_content})
    assert resp.status_code == 200
    edited = resp.json()["artifact"]
    assert edited["id"] != generated_id
    assert edited["origin"] == "user_edit"
    assert edited["status"] == "draft"
    assert edited["grounding_status"] == "user_edited_not_revalidated"
    assert edited["content"]["positioning_statement"]["text"] == "A user-rewritten statement."

    with db.db_cursor() as cur:
        cur.execute("SELECT status, origin FROM jobber.application_artifact WHERE id = %s", (generated_id,))
        row = cur.fetchone()
        assert row["status"] == "superseded"
        assert row["origin"] == "ai"  # generated history preserved, not mutated in place


def test_manual_edit_rejects_wrong_shape(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()

    resp = client.post(
        f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/edit",
        json={"content": {"not": "the right shape"}},
    )
    assert resp.status_code == 422


def test_edit_on_superseded_version_is_409(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    d1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{d1['artifact']['id']}/adopt")  # d1 -> active
    d2 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{d2['artifact']['id']}/adopt")  # d1 -> superseded, d2 -> active

    resp = client.post(
        f"/api/applications/{setup['application_id']}/artifacts/{d1['artifact']['id']}/edit",
        json={"content": {}},
    )
    assert resp.status_code == 409


def test_history_endpoint_retains_every_version(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    d1 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{d1['artifact']['id']}/adopt")
    client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts/positioning/history")
    assert resp.status_code == 200
    statuses = {h["status"] for h in resp.json()["history"]}
    assert statuses == {"active", "draft"}
    assert len(resp.json()["history"]) == 2


# --- source / episode / concept validation -----------------------------------


def test_unknown_source_ref_is_rejected(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    bad = _positioning_output(ref="role_requirement:00000000-0000-0000-0000-000000000000", concept_id=setup["concept_id"])
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))

    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 422
    with db.db_cursor() as cur:
        assert artifacts._draft_row(cur, setup["application_id"], "positioning") is None


def test_requirements_to_lead_with_rejects_non_accepted_concept(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        ref = f"role_requirement_legacy:{setup['legacy_concept_id']}"
    bad = _positioning_output(ref=ref, concept_id=setup["legacy_concept_id"])  # legacy concept, not accepted
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))

    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 422
    assert "requirements_to_lead_with" in resp.text


def test_gaps_and_cautions_accepts_a_known_legacy_concept(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        ref = f"role_requirement_legacy:{setup['legacy_concept_id']}"
        person_ref = f"profile_episode:{setup['episode_id']}"
    # no accepted concept exists on this role at all, so requirements_to_lead_with
    # must stay empty — only gaps_and_cautions may cite the legacy concept.
    # gaps_and_cautions itself is deliberately given only the role-only `ref`
    # (never person_ref) — a gap is exempt from the person-side requirement
    # (docs/35 hardening note) and this proves that exemption still works.
    ok = _positioning_output(ref=ref, person_ref=person_ref, gap_concept_id=setup["legacy_concept_id"])
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(ok))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 200


def test_unknown_episode_id_rejected_for_cv(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"profile_episode:{setup['episode_id']}"
    bad = _cv_output(ref=ref, episode_id=str(uuid.uuid4()))
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))

    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/cv/generate", json={})
    assert resp.status_code == 422
    assert "episode_id" in resp.text


def test_supporting_statement_rejects_unknown_section_concept(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    bad = _supporting_statement_output(ref=ref, person_ref=person_ref, concept_id=str(uuid.uuid4()))
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/supporting_statement/generate", json={})
    assert resp.status_code == 422
    assert "concept_id" in resp.text


# --- CV chronology safeguard -------------------------------------------------


def test_cv_episode_metadata_resolved_from_profile360_not_model(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"profile_episode:{setup['episode_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_cv_output(ref=ref, episode_id=setup["episode_id"])))

    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/cv/generate", json={})
    assert resp.status_code == 200
    entry = resp.json()["artifact"]["content"]["experience"][0]
    assert entry["title"] == "Senior Actuary"
    assert entry["organisation"] == "PrevCo"
    assert entry["start_date"] == "2020-01-01"
    # the model's own output schema has no field for these at all — proven by
    # construction (_cv_output/CVExperienceEntry never sets them) — this
    # assertion documents that the resolved values came from profile360.


def test_cv_experience_sorted_most_recent_first(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, "Solvency II")
        role_id = _posting(cur)
        _accepted_claim(cur, role_id, concept_id)
        older = _episode(cur, title="Analyst", organisation="OldCo", start_date="2015-01-01", end_date="2018-01-01")
        newer = _episode(cur, title="Manager", organisation="NewCo", start_date="2019-01-01", end_date="2022-01-01")
        claim1 = _claim(cur, "Analyst work.", episode_id=older)
        claim2 = _claim(cur, "Manager work.", episode_id=newer)
        _claim_mapping(cur, claim1, concept_id)
        _claim_mapping(cur, claim2, concept_id)
        application_id = _application(cur, role_id)

    output = ApplicationCVGeneration(
        profile_summary=SourcedText(text="Summary.", source_refs=[f"profile_episode:{older}"]),
        experience=[
            CVExperienceEntry(episode_id=older, bullets=[CVExperienceBullet(text="Analyst bullet.", source_refs=[f"profile_episode:{older}"])]),
            CVExperienceEntry(episode_id=newer, bullets=[CVExperienceBullet(text="Manager bullet.", source_refs=[f"profile_episode:{newer}"])]),
        ],
        skills=[], omissions_or_cautions=[],
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(output))
    resp = client.post(f"/api/applications/{application_id}/artifacts/cv/generate", json={})
    assert resp.status_code == 200
    experience = resp.json()["artifact"]["content"]["experience"]
    assert [e["episode_id"] for e in experience] == [newer, older]


# --- staleness -----------------------------------------------------------


def test_fresh_artifact_is_not_stale(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.json()["artifacts"]["positioning"]["active"]["stale"] is False


def test_artifact_becomes_stale_when_application_note_changes(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    with db.db_cursor() as cur:
        _note(cur, setup["application_id"], text="A brand new note added after generation.")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.json()["artifacts"]["positioning"]["active"]["stale"] is True


def test_artifact_becomes_stale_when_profile_claim_mapping_changes(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    with db.db_cursor() as cur:
        new_claim = _claim(cur, "A brand new claim mapped after generation.", episode_id=setup["episode_id"])
        _claim_mapping(cur, new_claim, setup["concept_id"])

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.json()["artifacts"]["positioning"]["active"]["stale"] is True


def test_cv_becomes_stale_when_active_positioning_changes(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        pref = f"role_requirement:{setup['concept_id']}"
        cvref = f"profile_episode:{setup['episode_id']}"

    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=pref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    pos_draft = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{pos_draft['artifact']['id']}/adopt")

    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_cv_output(ref=cvref, episode_id=setup["episode_id"])))
    cv_draft = client.post(f"/api/applications/{setup['application_id']}/artifacts/cv/generate", json={}).json()
    cv_active = client.post(f"/api/applications/{setup['application_id']}/artifacts/{cv_draft['artifact']['id']}/adopt").json()
    assert cv_active["artifact"]["stale"] is False

    # adopt a freshly generated (superseding) positioning draft
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=pref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    pos_draft2 = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{pos_draft2['artifact']['id']}/adopt")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.json()["artifacts"]["cv"]["active"]["stale"] is True


def test_guidance_alone_does_not_make_a_prior_version_look_stale(client, monkeypatch):
    """A stored artifact's staleness check reuses that artifact's own
    guidance (docs/35 §8) — unrelated to whatever guidance a *future* call
    might use — so nothing about guidance itself can spuriously flip stale."""
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    generated = client.post(
        f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={"guidance": "Keep it formal."}
    ).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert resp.json()["artifacts"]["positioning"]["active"]["stale"] is False


# --- evidence boundaries ------------------------------------------------------


def test_generation_never_writes_profile360_or_assertions_or_requirement_claims(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        claims_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.person_capability_assertion")
        assertions_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
        requirement_claims_before = cur.fetchone()["n"]

    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        assert cur.fetchone()["n"] == claims_before
        cur.execute("SELECT COUNT(*) AS n FROM jobber.person_capability_assertion")
        assert cur.fetchone()["n"] == assertions_before
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
        assert cur.fetchone()["n"] == requirement_claims_before


def test_generation_never_changes_application_status(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    client.post(f"/api/applications/{setup['application_id']}/artifacts/{generated['artifact']['id']}/adopt")

    resp = client.get(f"/api/applications/{setup['application_id']}")
    assert resp.json()["application"]["status"] == "preparing"


def test_application_note_used_in_generation_remains_a_plain_note(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        note_id = _note(cur, setup["application_id"], concept_id=setup["concept_id"], note_type="evidence_example", text="Shipped an ORSA once.")
        ref = f"application_note:{note_id}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT note_text FROM jobber.application_note WHERE id = %s", (note_id,))
        assert cur.fetchone()["note_text"] == "Shipped an ORSA once."
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        # still only the one claim _grounded_role itself created
        assert cur.fetchone()["n"] == 1


def test_raw_output_never_returned_by_normal_read(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup['claim_id']}", concept_id=setup["concept_id"])))
    generated = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={}).json()
    assert "raw_output" not in generated["artifact"]

    resp = client.get(f"/api/applications/{setup['application_id']}/artifacts")
    assert "raw_output" not in resp.json()["artifacts"]["positioning"]["draft"]


# --- ownership -----------------------------------------------------------


def test_adopt_on_another_applications_artifact_is_rejected(client, monkeypatch):
    with db.db_cursor() as cur:
        setup_a = _grounded_role(cur)
        setup_b = _grounded_role(cur)
        ref = f"role_requirement:{setup_a['concept_id']}"
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(_positioning_output(ref=ref, person_ref=f"profile_claim:{setup_a['claim_id']}", concept_id=setup_a["concept_id"])))
    draft = client.post(f"/api/applications/{setup_a['application_id']}/artifacts/positioning/generate", json={}).json()

    resp = client.post(f"/api/applications/{setup_b['application_id']}/artifacts/{draft['artifact']['id']}/adopt")
    assert resp.status_code == 409


def test_generate_on_unknown_application_is_404(client):
    resp = client.post(f"/api/applications/{uuid.uuid4()}/artifacts/positioning/generate", json={})
    assert resp.status_code == 404


def test_generate_on_unknown_artifact_type_is_404(client):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/not_a_real_type/generate", json={})
    assert resp.status_code == 404


# --- hardening: grounding sufficiency (see docs/35 hardening note) ----------
#
# Rejecting an *unknown* ref/id (tested above) isn't the whole story: a
# response can cite only ids that genuinely exist and still be a problem —
# an empty source_refs list, or an applicant-facing claim backed only by
# role-side/strategy context with nothing person-side behind it at all.


def test_empty_source_refs_is_rejected(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
    bad = ApplicationPositioningGeneration(
        positioning_statement=SourcedText(text="Strong fit for this role.", source_refs=[]),
        themes=[], requirements_to_lead_with=[], gaps_and_cautions=[], language_to_mirror=[], avoid_claiming=[],
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 422
    assert "no source_refs" in resp.text
    with db.db_cursor() as cur:
        assert artifacts._draft_row(cur, setup["application_id"], "positioning") is None


def test_positioning_statement_with_only_role_side_source_is_rejected(client, monkeypatch):
    """A claim about the applicant ('Strong fit for this role') citing only a
    role_requirement ref — never anything person-side — must be rejected: a
    role requirement may supplement such a claim, but can't be its sole
    source."""
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
    bad = _positioning_output(ref=ref, concept_id=setup["concept_id"])  # no person_ref
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 422
    assert "person-side source" in resp.text
    with db.db_cursor() as cur:
        assert artifacts._draft_row(cur, setup["application_id"], "positioning") is None


def test_cv_bullet_with_only_role_side_source_is_rejected(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        role_ref = f"role_requirement:{setup['concept_id']}"
    bad = ApplicationCVGeneration(
        profile_summary=SourcedText(text="Summary.", source_refs=[f"profile_episode:{setup['episode_id']}"]),
        experience=[CVExperienceEntry(episode_id=setup["episode_id"], bullets=[CVExperienceBullet(text="Did the thing.", source_refs=[role_ref])])],
        skills=[], omissions_or_cautions=[],
    )
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/cv/generate", json={})
    assert resp.status_code == 422
    assert "person-side source" in resp.text


def test_gaps_and_cautions_remains_exempt_from_person_side_requirement(client, monkeypatch):
    """The one deliberate exception: a gap/caution describes an *absence* of
    applicant evidence, so it may be grounded in role-side context alone."""
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        ref = f"role_requirement_legacy:{setup['legacy_concept_id']}"
        person_ref = f"profile_episode:{setup['episode_id']}"
    ok = _positioning_output(ref=ref, person_ref=person_ref, gap_concept_id=setup["legacy_concept_id"])
    assert ok.gaps_and_cautions[0].source_refs == [ref]  # role-side only, by construction
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(ok))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/positioning/generate", json={})
    assert resp.status_code == 200


def test_supporting_statement_section_rejects_legacy_concept_when_not_marked_gap(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        ref = f"role_requirement_legacy:{setup['legacy_concept_id']}"
        person_ref = f"profile_episode:{setup['episode_id']}"
    bad = _supporting_statement_output(ref=ref, person_ref=person_ref, concept_id=setup["legacy_concept_id"], is_gap_or_caution=False)
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(bad))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/supporting_statement/generate", json={})
    assert resp.status_code == 422
    assert "is_gap_or_caution" in resp.text
    with db.db_cursor() as cur:
        assert artifacts._draft_row(cur, setup["application_id"], "supporting_statement") is None


def test_supporting_statement_section_accepts_legacy_concept_when_marked_gap(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _legacy_only_role(cur)
        ref = f"role_requirement_legacy:{setup['legacy_concept_id']}"
        person_ref = f"profile_episode:{setup['episode_id']}"
    ok = _supporting_statement_output(ref=ref, person_ref=person_ref, concept_id=setup["legacy_concept_id"], is_gap_or_caution=True)
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(ok))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/supporting_statement/generate", json={})
    assert resp.status_code == 200


def test_supporting_statement_section_still_accepts_an_accepted_concept_without_the_gap_flag(client, monkeypatch):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        ref = f"role_requirement:{setup['concept_id']}"
        person_ref = f"profile_claim:{setup['claim_id']}"
    ok = _supporting_statement_output(ref=ref, person_ref=person_ref, concept_id=setup["concept_id"], is_gap_or_caution=False)
    monkeypatch.setattr(artifacts, "run_json_task", _fake_run_json_task(ok))
    resp = client.post(f"/api/applications/{setup['application_id']}/artifacts/supporting_statement/generate", json={})
    assert resp.status_code == 200


def test_partial_capability_coverage_is_categorised_as_partial_evidence():
    """Regression for the categorisation bug the hardening note fixed: a
    'partial' coverage status used to be grouped with 'evidenced' under
    canonical_evidence — only a fully-met coverage may read as canonical."""
    person_side = {
        "mappings": [], "assertion": None,
        "coverage": {
            "capability_concept_id": "cap-1", "status": "partial",
            "trace": {"status_reason": {"code": "partial", "message": "Some but not all core components evidenced."}},
        },
    }
    sources = gen._mapping_sources("Capital Management", person_side)
    coverage_sources = [s for s in sources if s.kind == "profile_capability_coverage"]
    assert len(coverage_sources) == 1
    assert coverage_sources[0].category == gen.CATEGORY_PARTIAL_EVIDENCE


def test_evidenced_capability_coverage_remains_canonical_evidence():
    person_side = {
        "mappings": [], "assertion": None,
        "coverage": {
            "capability_concept_id": "cap-1", "status": "evidenced",
            "trace": {"status_reason": {"code": "evidenced", "message": "All core components evidenced."}},
        },
    }
    sources = gen._mapping_sources("Capital Management", person_side)
    coverage_sources = [s for s in sources if s.kind == "profile_capability_coverage"]
    assert len(coverage_sources) == 1
    assert coverage_sources[0].category == gen.CATEGORY_CANONICAL_EVIDENCE
