"""Reviewed role-archetype classification (build §5), archetype
Day-in-the-Life context (build §10), and target context inheritance
(build §11).
"""

import pytest

from app import archetype_classification as classification
from app import archetype_context, role_context
from app.ai import AIProviderError, AITaskResult, AITaskRun
from app.db import create_document, db_cursor, upsert_role_instance
from app.models import RoleContextGeneration


# --- Fixtures ---------------------------------------------------------------

def _archetype(cur, name, *, status="active", seniority_band=None, notes=None):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('role_archetype', %s, %s, 'curator', now()) RETURNING id",
        (name, status),
    )
    concept_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.role_archetype_detail (concept_id, seniority_band, notes) VALUES (%s, %s, %s)",
        (concept_id, seniority_band, notes),
    )
    return concept_id


def _capability(cur, name):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('capability', %s, 'active', 'curator', now()) RETURNING id",
        (name,),
    )
    concept_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) "
        "VALUES (%s, %s, 'owned')",
        (concept_id, f"Demonstrate {name}"),
    )
    return concept_id


def _role(cur, *, title="Senior Actuary", archetype_concept_id=None, text=None, instance_type="observed_posting"):
    document_id = None
    if text:
        document_id, _dup = create_document(
            cur, kind="job_posting", content_text=text, provenance_quality="original", title=title,
            source="user_paste",
        )
    role_id = upsert_role_instance(
        cur, None,
        {"instance_type": instance_type, "title": title, "document_id": document_id,
         "description": text, "seniority_level": "senior"},
        skills=[],
    )
    if archetype_concept_id:
        cur.execute(
            "UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s",
            (archetype_concept_id, role_id),
        )
    return str(role_id)


def _accepted_requirement(cur, role_id, concept_id, requirement_type="required"):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
        "VALUES (%s, %s, %s, 'user_asserted', 'accepted')",
        (role_id, concept_id, requirement_type),
    )


def _fake_proposal(name, *, confidence="medium", alternatives=None):
    def fake_run(**kwargs):
        output = classification.ArchetypeProposal.model_validate(
            {"archetype_name": name, "confidence": confidence,
             "rationale": "test rationale", "alternatives": alternatives or []}
        )
        run = AITaskRun("role_archetype_classify", "test-model", "classify_role_archetype.md", "v1",
                        "2026-09-16", "2026-09-16", "ok", 100, 50)
        return AITaskResult(output, run)
    return fake_run


_CONTEXT_PAYLOAD = {
    "day_in_life": [
        {"time_or_phase": "Morning", "activity": "Run the reporting pack",
         "detail": "Review the capital numbers.", "basis": "advert_grounded", "confidence": "high"}
    ],
    "typical_week": [
        {"day_or_theme": "Monday", "activity": "Planning", "detail": "Set the week's priorities.",
         "basis": "inferred", "confidence": "medium"}
    ],
    "team_context": {"expected_team_size": {"min": 3, "max": 6, "basis": "inferred", "confidence": "medium"},
                     "team_work": [{"text": "Reporting and validation", "basis": "advert_grounded", "confidence": "high"}]},
    "manager_context": {"likely_manager_title": "Chief Actuary", "title_basis": "inferred",
                        "dynamic": "Close review cadence.", "dynamic_basis": "inferred",
                        "dynamic_confidence": "medium"},
    "stakeholders": [{"text": "Risk function", "basis": "advert_grounded", "confidence": "high"}],
    "career_progression": [{"step": "Chief Actuary", "basis": "inferred", "confidence": "medium"}],
    "grounding_summary": {"advert_grounded_points": ["Solvency II reporting is named"],
                          "inferred_points": ["Team size is typical for this kind of role"]},
    "caveats": "Model caveat.",
}


def _fake_context_run(captured=None):
    def fake_run(**kwargs):
        if captured is not None:
            captured.append(kwargs["user_input"])
        output = RoleContextGeneration.model_validate(_CONTEXT_PAYLOAD)
        run = AITaskRun(kwargs["task"], "test-model", kwargs["prompt_name"], "v1",
                        "2026-09-16", "2026-09-16", "ok", 100, 50)
        return AITaskResult(output, run)
    return fake_run


# --- Archetype classification (build §5) ------------------------------------

def test_proposal_is_restricted_to_active_archetypes(monkeypatch):
    with db_cursor() as cur:
        _archetype(cur, "Senior Life Actuary", seniority_band="senior")
        _archetype(cur, "Retired Archetype", status="deprecated")
        role_id = _role(cur, text="Solvency II reporting lead.")

    monkeypatch.setattr(classification, "run_json_task", _fake_proposal("Senior Life Actuary"))
    with db_cursor() as cur:
        result = classification.propose_archetype(cur, role_id)

    assert result["status"] == "ok"
    assert result["proposal"]["matched"] is True
    assert result["proposal"]["archetype_name"] == "Senior Life Actuary"
    assert result["proposal"]["catalogue_size"] == 1  # the deprecated one is not offered


def test_a_suggestion_outside_the_catalogue_is_reported_unmatched_not_created(monkeypatch):
    with db_cursor() as cur:
        _archetype(cur, "Senior Life Actuary")
        role_id = _role(cur, text="Something else entirely.")

    monkeypatch.setattr(classification, "run_json_task", _fake_proposal("Chief Data Officer"))
    with db_cursor() as cur:
        result = classification.propose_archetype(cur, role_id)
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE type_code = 'role_archetype'")
        count = cur.fetchone()["n"]

    assert result["proposal"]["matched"] is False
    assert result["proposal"]["archetype_concept_id"] is None
    assert result["proposal"]["raw_suggestion"] == "Chief Data Officer"
    assert "never created automatically" in result["proposal"]["note"]
    assert count == 1  # no archetype was invented


def test_a_null_suggestion_is_a_valid_answer(monkeypatch):
    with db_cursor() as cur:
        _archetype(cur, "Senior Life Actuary")
        role_id = _role(cur)

    monkeypatch.setattr(classification, "run_json_task", _fake_proposal(None))
    with db_cursor() as cur:
        result = classification.propose_archetype(cur, role_id)

    assert result["proposal"]["matched"] is False
    assert result["proposal"]["raw_suggestion"] is None


def test_alternatives_are_filtered_to_real_catalogue_entries(monkeypatch):
    with db_cursor() as cur:
        _archetype(cur, "Senior Life Actuary")
        _archetype(cur, "Actuarial Reporting Manager")
        role_id = _role(cur)

    monkeypatch.setattr(
        classification, "run_json_task",
        _fake_proposal("Senior Life Actuary", alternatives=["Actuarial Reporting Manager", "Invented Role"]),
    )
    with db_cursor() as cur:
        result = classification.propose_archetype(cur, role_id)

    assert [a["canonical_name"] for a in result["proposal"]["alternatives"]] == ["Actuarial Reporting Manager"]


def test_proposing_never_assigns_the_archetype(monkeypatch):
    with db_cursor() as cur:
        _archetype(cur, "Senior Life Actuary")
        role_id = _role(cur)

    monkeypatch.setattr(classification, "run_json_task", _fake_proposal("Senior Life Actuary"))
    with db_cursor() as cur:
        classification.propose_archetype(cur, role_id)
        cur.execute("SELECT archetype_concept_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        assert cur.fetchone()["archetype_concept_id"] is None


def test_assignment_accepting_choosing_another_and_leaving_unclassified():
    with db_cursor() as cur:
        first = _archetype(cur, "Senior Life Actuary")
        second = _archetype(cur, "Actuarial Reporting Manager")
        role_id = _role(cur)

        accepted = classification.assign_archetype(cur, role_id, first)
        assert accepted["archetype_name"] == "Senior Life Actuary"
        assert accepted["status"] == "assigned"

        changed = classification.assign_archetype(cur, role_id, second)
        assert changed["archetype_name"] == "Actuarial Reporting Manager"

        cleared = classification.assign_archetype(cur, role_id, None)
        assert cleared["archetype_concept_id"] is None
        assert cleared["status"] == "unclassified"

        summary = classification.role_archetype_summary(cur, role_id)
        assert summary["assigned"] is False
        assert summary["state"] == "unclassified"


def test_assignment_refuses_a_non_archetype_or_deprecated_concept():
    with db_cursor() as cur:
        deprecated = _archetype(cur, "Retired Archetype", status="deprecated")
        capability = _capability(cur, "Capital management")
        role_id = _role(cur)

        with pytest.raises(classification.ArchetypeAssignmentError):
            classification.assign_archetype(cur, role_id, deprecated)
        with pytest.raises(classification.ArchetypeAssignmentError):
            classification.assign_archetype(cur, role_id, capability)


def test_proposing_with_an_empty_catalogue_is_refused_rather_than_invented():
    with db_cursor() as cur:
        role_id = _role(cur)
        with pytest.raises(classification.NoArchetypeCatalogueError):
            classification.propose_archetype(cur, role_id)


def test_legacy_career_track_is_left_untouched_by_archetype_assignment():
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        role_id = upsert_role_instance(
            cur, None,
            {"instance_type": "observed_posting", "title": "Actuary", "career_track": "actuarial"},
            skills=[],
        )
        classification.assign_archetype(cur, str(role_id), archetype_id)
        cur.execute("SELECT career_track FROM jobber.role_instance WHERE id = %s", (role_id,))
        assert cur.fetchone()["career_track"] == "actuarial"


def test_archetype_endpoints_round_trip(client):
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary", seniority_band="senior")
        role_id = _role(cur)

    catalogue = client.get("/api/role-instances/archetype-catalogue").json()
    assert [a["canonical_name"] for a in catalogue] == ["Senior Life Actuary"]

    assigned = client.put(f"/api/role-instances/{role_id}/archetype",
                          json={"archetype_concept_id": archetype_id})
    assert assigned.status_code == 200
    assert assigned.json()["archetype_name"] == "Senior Life Actuary"

    summary = client.get(f"/api/role-instances/{role_id}/archetype").json()
    assert summary["assigned"] is True
    assert summary["seniority_band"] == "senior"

    cleared = client.put(f"/api/role-instances/{role_id}/archetype", json={"archetype_concept_id": None})
    assert cleared.json()["status"] == "unclassified"


def test_role_detail_exposes_the_reviewed_archetype(client):
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        role_id = _role(cur, archetype_concept_id=archetype_id)

    body = client.get(f"/api/roles/{role_id}").json()
    assert body["archetype"]["assigned"] is True
    assert body["archetype"]["archetype_name"] == "Senior Life Actuary"


# --- Archetype context (build §10) -------------------------------------------

def test_get_never_generates(client, monkeypatch):
    def explode(**kwargs):
        raise AssertionError("GET must never call the model")

    monkeypatch.setattr(archetype_context, "run_json_task", explode)
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Solvency II reporting.")

    body = client.get(f"/api/archetypes/{archetype_id}/context").json()
    assert body["enrichment"] is None
    assert "synthesis across the evidence" in body["synthesis_note"]


def test_explicit_generation_persists_an_active_enrichment(monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Solvency II reporting lead.")

    result = archetype_context.generate_archetype_context(archetype_id)
    assert result["created"] is True
    enrichment = result["enrichment"]
    assert enrichment["status"] == "active"
    assert enrichment["day_in_life"][0]["activity"] == "Run the reporting pack"
    assert enrichment["grounding_provenance"]["reads_profile360"] is False

    # A second plain generate is a no-op that makes no model call.
    def explode(**kwargs):
        raise AssertionError("a repeated plain generate must not call the model")

    monkeypatch.setattr(archetype_context, "run_json_task", explode)
    again = archetype_context.generate_archetype_context(archetype_id)
    assert again["created"] is False
    assert again["enrichment"]["id"] == enrichment["id"]


def test_regenerate_supersedes_the_prior_version(monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Solvency II reporting lead.")

    first = archetype_context.generate_archetype_context(archetype_id)["enrichment"]
    second = archetype_context.generate_archetype_context(archetype_id, force=True)["enrichment"]

    assert first["id"] != second["id"]
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, status FROM jobber.archetype_context_enrichment WHERE archetype_concept_id = %s",
            (archetype_id,),
        )
        statuses = {str(r["id"]): r["status"] for r in cur.fetchall()}
    assert statuses[first["id"]] == "superseded"
    assert statuses[second["id"]] == "active"
    assert sum(s == "active" for s in statuses.values()) == 1


def test_a_failed_generation_preserves_the_active_version(monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Solvency II reporting lead.")

    original = archetype_context.generate_archetype_context(archetype_id)["enrichment"]

    def fail(**kwargs):
        raise AIProviderError("provider down")

    monkeypatch.setattr(archetype_context, "run_json_task", fail)
    with pytest.raises(archetype_context.ArchetypeContextGenerationError):
        archetype_context.generate_archetype_context(archetype_id, force=True)

    with db_cursor() as cur:
        still = archetype_context.get_archetype_context(cur, archetype_id)
    assert still["enrichment"]["id"] == original["id"]
    assert still["enrichment"]["status"] == "active"


def test_generation_is_grounded_in_assigned_roles_and_accepted_requirements(monkeypatch):
    captured = []
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run(captured))
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary", seniority_band="senior")
        capability_id = _capability(cur, "Capital management")
        unrelated_id = _capability(cur, "Never required here")
        role_id = _role(cur, archetype_concept_id=archetype_id, title="Head of Capital",
                        text="Lead the capital function under Solvency II.")
        _accepted_requirement(cur, role_id, capability_id)
        # An unreviewed claim is a proposal, not evidence — it must not appear.
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
            "VALUES (%s, %s, 'required', 'user_asserted', 'unreviewed')",
            (role_id, unrelated_id),
        )

    archetype_context.generate_archetype_context(archetype_id)
    prompt = captured[0]
    assert "Senior Life Actuary" in prompt
    assert "Head of Capital" in prompt
    assert "Capital management" in prompt
    assert "Never required here" not in prompt


def test_generation_never_reads_profile360_personal_evidence(monkeypatch):
    """The boundary rule for archetype context, asserted directly: the
    prompt is built from market-side evidence only."""
    captured = []
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run(captured))
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Capital reporting.")
        cur.execute(
            "INSERT INTO profile360.claims (claim_key, claim_text) VALUES ('t:1', 'PERSONAL SECRET EVIDENCE')"
        )

    archetype_context.generate_archetype_context(archetype_id)
    assert "PERSONAL SECRET EVIDENCE" not in captured[0]

    with db_cursor() as cur:
        enrichment = archetype_context.get_archetype_context(cur, archetype_id)["enrichment"]
    assert enrichment["grounding_provenance"]["reads_profile360"] is False


def test_thin_evidence_produces_explicit_caveats(monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="One single advert.")

    enrichment = archetype_context.generate_archetype_context(archetype_id)["enrichment"]
    assert "Only one posting is assigned" in enrichment["caveats"]
    assert "Model caveat." in enrichment["caveats"]


def test_an_archetype_with_no_assigned_postings_refuses_to_generate(monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Empty archetype")

    with pytest.raises(archetype_context.ArchetypeContextGroundingError):
        archetype_context.generate_archetype_context(archetype_id)


def test_archetype_context_endpoints(client, monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Capital reporting.")

    created = client.post(f"/api/archetypes/{archetype_id}/context/generate")
    assert created.status_code == 200
    assert created.json()["created"] is True

    read = client.get(f"/api/archetypes/{archetype_id}/context").json()
    assert read["enrichment"]["day_in_life"][0]["activity"] == "Run the reporting pack"

    regenerated = client.post(f"/api/archetypes/{archetype_id}/context/regenerate")
    assert regenerated.json()["created"] is True

    assert client.get("/api/archetypes/00000000-0000-0000-0000-000000000000/context").status_code == 404


def test_archetype_with_no_postings_returns_400_from_the_endpoint(client):
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Empty archetype")
    response = client.post(f"/api/archetypes/{archetype_id}/context/generate")
    assert response.status_code == 400
    assert "no assigned postings" in response.json()["detail"]


# --- Target context inheritance (build §11) ---------------------------------

def test_target_generation_uses_archetype_context_as_secondary_grounding(monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Capital reporting posting.")
    archetype_context.generate_archetype_context(archetype_id)

    with db_cursor() as cur:
        target_id = _role(cur, title="My target role", archetype_concept_id=archetype_id,
                          text="Target-specific detail: leading the ORSA process.",
                          instance_type="user_defined_target")

    captured = []
    monkeypatch.setattr(role_context, "run_json_task", _fake_context_run(captured))
    role_context.generate_role_context(target_id)

    prompt = captured[0]
    # Target-specific material is present, and the archetype block is
    # present but explicitly framed as subordinate.
    assert "leading the ORSA process" in prompt
    assert "GENERIC ARCHETYPE CONTEXT — SECONDARY GROUNDING ONLY" in prompt
    assert "Senior Life Actuary" in prompt
    assert "the role's own material wins" in prompt
    assert "Solvency II reporting is named" in prompt  # the archetype's grounded points


def test_a_target_without_an_archetype_generates_exactly_as_before(monkeypatch):
    captured = []
    monkeypatch.setattr(role_context, "run_json_task", _fake_context_run(captured))
    with db_cursor() as cur:
        target_id = _role(cur, title="Unclassified target", text="Some target detail.",
                          instance_type="user_defined_target")

    result = role_context.generate_role_context(target_id)
    assert result["created"] is True
    assert "GENERIC ARCHETYPE CONTEXT" not in captured[0]


def test_an_archetype_without_generated_context_adds_no_grounding_block(monkeypatch):
    captured = []
    monkeypatch.setattr(role_context, "run_json_task", _fake_context_run(captured))
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        target_id = _role(cur, title="Target", archetype_concept_id=archetype_id,
                          text="Detail.", instance_type="user_defined_target")

    role_context.generate_role_context(target_id)
    assert "GENERIC ARCHETYPE CONTEXT" not in captured[0]


def test_role_context_read_reports_whether_archetype_grounding_is_available(client, monkeypatch):
    monkeypatch.setattr(archetype_context, "run_json_task", _fake_context_run())
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Senior Life Actuary")
        _role(cur, archetype_concept_id=archetype_id, text="Posting.")
        target_id = _role(cur, title="Target", archetype_concept_id=archetype_id, text="Detail.",
                          instance_type="user_defined_target")

    before = client.get(f"/api/roles/{target_id}/context").json()
    assert before["archetype_grounding"]["available"] is False

    archetype_context.generate_archetype_context(archetype_id)
    after = client.get(f"/api/roles/{target_id}/context").json()
    assert after["archetype_grounding"]["available"] is True
    assert after["archetype_grounding"]["archetype_name"] == "Senior Life Actuary"
