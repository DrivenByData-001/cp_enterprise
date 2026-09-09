"""Concept Dossier (cp_round_of_changes.md §C/§D/§E/§F) — real Postgres,
app.concept_dossier.run_json_task mocked (never a live OpenAI call, same
convention as test_role_context.py)."""

import uuid

from app import ai, concept_dossier as dossier, db
from app.models import ConceptDossierGeneration, RelatedConceptSuggestion


def _concept(cur, *, name: str, type_code: str = "knowledge", status: str = "active", definition: str | None = None) -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at) "
        "VALUES (%s, %s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, name, definition, status),
    )
    return str(cur.fetchone()["id"])


def _role_with_skills(cur, *names: str) -> str:
    return db.upsert_role_instance(
        cur, None,
        {"instance_type": "observed_posting", "title": "Senior Actuary"},
        skills=[{"name": n, "requirement_type": "required"} for n in names],
    )


def _fake_output(**overrides) -> ConceptDossierGeneration:
    defaults = dict(
        plain_definition="A plain-English definition.",
        classification_rationale="Because it fits the type.",
        practical_meaning="What it means day to day.",
        underlying_elements=["Element A"],
        stronger_expressions=["A stronger, more specific form"],
        weaker_expressions=["A vaguer way of saying the same thing"],
        boundaries_and_overlaps="Distinct from its closest neighbour.",
        related_concepts=[],
        caveats="Illustrative, not exhaustive.",
    )
    defaults.update(overrides)
    return ConceptDossierGeneration(**defaults)


def _fake_run_json_task(output: ConceptDossierGeneration | None = None, raise_error: Exception | None = None):
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


# --- GET: no generation, no dossier yet -------------------------------------

def test_get_with_no_dossier_returns_null_not_an_error(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    resp = client.get(f"/api/concepts/{concept_id}/dossier")
    assert resp.status_code == 200
    body = resp.json()
    assert body["concept_id"] == concept_id
    assert body["active"] is None
    assert body["draft"] is None


def test_get_on_unknown_concept_is_404(client):
    resp = client.get(f"/api/concepts/{uuid.uuid4()}/dossier")
    assert resp.status_code == 404


def test_get_never_triggers_generation(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    def _boom(**kwargs):
        raise AssertionError("GET must never call the AI provider")

    monkeypatch.setattr(dossier, "run_json_task", _boom)
    resp = client.get(f"/api/concepts/{concept_id}/dossier")
    assert resp.status_code == 200
    assert resp.json()["active"] is None


# --- first-time generation ----------------------------------------------------

def test_generate_creates_the_first_active_dossier(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA", definition="Own Risk and Solvency Assessment.")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    d = body["dossier"]
    assert d["concept_id"] == concept_id
    assert d["status"] == "active"
    assert d["origin"] == "ai"
    assert d["model"] == "test-model"
    assert d["generator_version"] == dossier.GENERATOR_VERSION
    assert d["plain_definition"] == "A plain-English definition."
    assert d["underlying_elements"] == ["Element A"]

    fetched = client.get(f"/api/concepts/{concept_id}/dossier").json()
    assert fetched["active"]["id"] == d["id"]
    assert fetched["draft"] is None


def test_generate_again_without_prior_active_change_is_noop_no_ai_call(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    first_id = first.json()["dossier"]["id"]

    def _boom(**kwargs):
        raise AssertionError("plain generate must not re-call the AI provider when one is already active")

    monkeypatch.setattr(dossier, "run_json_task", _boom)
    second = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["dossier"]["id"] == first_id


def test_generate_on_unknown_concept_is_404(client, monkeypatch):
    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/concepts/{uuid.uuid4()}/dossier/generate")
    assert resp.status_code == 404


def test_guidance_is_persisted_on_the_generated_version(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/concepts/{concept_id}/dossier/generate", json={"guidance": "Focus on actuarial use."})
    assert resp.status_code == 200
    assert resp.json()["dossier"]["guidance"] == "Focus on actuarial use."


def test_generate_with_no_body_at_all_still_works(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    assert resp.status_code == 200
    assert resp.json()["dossier"]["guidance"] is None


# --- regeneration: draft, never touches active --------------------------------

def test_regenerate_creates_a_draft_without_touching_active(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    active_id = first.json()["dossier"]["id"]

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(output=_fake_output(caveats="A different version.")))
    second = client.post(f"/api/concepts/{concept_id}/dossier/regenerate", json={"guidance": "Explain how this differs from Capital Management."})
    assert second.status_code == 200
    body = second.json()
    assert body["created"] is True
    assert body["dossier"]["status"] == "draft"
    assert body["dossier"]["caveats"] == "A different version."
    assert body["dossier"]["guidance"] == "Explain how this differs from Capital Management."

    fetched = client.get(f"/api/concepts/{concept_id}/dossier").json()
    assert fetched["active"]["id"] == active_id  # unchanged
    assert fetched["draft"]["id"] == body["dossier"]["id"]


def test_regenerate_without_an_active_dossier_is_409_no_ai_call(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    def _boom(**kwargs):
        raise AssertionError("regenerate must not call the AI provider when there is no active dossier yet")

    monkeypatch.setattr(dossier, "run_json_task", _boom)
    resp = client.post(f"/api/concepts/{concept_id}/dossier/regenerate")
    assert resp.status_code == 409


def test_regenerate_replaces_an_existing_draft_rather_than_creating_a_second_one(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    client.post(f"/api/concepts/{concept_id}/dossier/generate")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(output=_fake_output(caveats="Draft 1.")))
    d1 = client.post(f"/api/concepts/{concept_id}/dossier/regenerate").json()["dossier"]

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(output=_fake_output(caveats="Draft 2.")))
    d2 = client.post(f"/api/concepts/{concept_id}/dossier/regenerate").json()["dossier"]
    assert d2["id"] != d1["id"]
    assert d2["caveats"] == "Draft 2."

    with db.db_cursor() as cur:
        cur.execute("SELECT status, caveats FROM jobber.concept_dossier WHERE concept_id = %s ORDER BY created_at", (concept_id,))
        rows = cur.fetchall()
    statuses = [r["status"] for r in rows]
    assert statuses.count("draft") == 1
    assert statuses.count("active") == 1
    assert statuses.count("superseded") == 1  # the first draft, superseded by the second


# --- adopt / discard -----------------------------------------------------------

def test_adopt_draft_promotes_it_and_supersedes_old_active(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/concepts/{concept_id}/dossier/generate").json()["dossier"]

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(output=_fake_output(caveats="Better version.")))
    draft = client.post(f"/api/concepts/{concept_id}/dossier/regenerate").json()["dossier"]

    adopt = client.post(f"/api/concepts/{concept_id}/dossier/adopt")
    assert adopt.status_code == 200
    adopted = adopt.json()["dossier"]
    assert adopted["id"] == draft["id"]
    assert adopted["status"] == "active"
    assert adopted["caveats"] == "Better version."

    fetched = client.get(f"/api/concepts/{concept_id}/dossier").json()
    assert fetched["active"]["id"] == draft["id"]
    assert fetched["draft"] is None

    with db.db_cursor() as cur:
        cur.execute("SELECT id, status FROM jobber.concept_dossier WHERE id = %s", (first["id"],))
        assert cur.fetchone()["status"] == "superseded"


def test_adopt_without_a_draft_is_409(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")
    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    client.post(f"/api/concepts/{concept_id}/dossier/generate")

    resp = client.post(f"/api/concepts/{concept_id}/dossier/adopt")
    assert resp.status_code == 409


def test_discard_draft_leaves_active_untouched(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    active = client.post(f"/api/concepts/{concept_id}/dossier/generate").json()["dossier"]
    client.post(f"/api/concepts/{concept_id}/dossier/regenerate")

    discard = client.post(f"/api/concepts/{concept_id}/dossier/discard")
    assert discard.status_code == 200
    assert discard.json()["status"] == "discarded"

    fetched = client.get(f"/api/concepts/{concept_id}/dossier").json()
    assert fetched["active"]["id"] == active["id"]
    assert fetched["draft"] is None


def test_discard_without_a_draft_is_409(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")
    resp = client.post(f"/api/concepts/{concept_id}/dossier/discard")
    assert resp.status_code == 409


# --- manual edit / version history --------------------------------------------

def test_manual_edit_creates_new_active_version_and_retains_history(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="Capital Management", type_code="capability")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/concepts/{concept_id}/dossier/generate").json()["dossier"]

    def _boom(**kwargs):
        raise AssertionError("manual edit must never call the AI provider")

    monkeypatch.setattr(dossier, "run_json_task", _boom)
    edit = client.put(f"/api/concepts/{concept_id}/dossier", json={"plain_definition": "Curator-authored definition."})
    assert edit.status_code == 200
    edited = edit.json()["dossier"]
    assert edited["id"] != first["id"]
    assert edited["origin"] == "curator"
    assert edited["model"] is None
    assert edited["plain_definition"] == "Curator-authored definition."
    # Untouched fields carried over unchanged from the version being superseded.
    assert edited["practical_meaning"] == first["practical_meaning"]
    assert edited["underlying_elements"] == first["underlying_elements"]

    history = client.get(f"/api/concepts/{concept_id}/dossier/history").json()
    assert len(history) == 2
    by_status = {h["status"]: h for h in history}
    assert by_status["active"]["id"] == edited["id"]
    assert by_status["superseded"]["id"] == first["id"]


def test_manual_edit_without_an_active_dossier_is_409(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")
    resp = client.put(f"/api/concepts/{concept_id}/dossier", json={"plain_definition": "x"})
    assert resp.status_code == 409


# --- generation failure preserves the active dossier --------------------------

def test_regenerate_failure_leaves_active_and_prior_draft_untouched(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task())
    active = client.post(f"/api/concepts/{concept_id}/dossier/generate").json()["dossier"]

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(raise_error=ai.AIProviderError("provider unreachable")))
    failed = client.post(f"/api/concepts/{concept_id}/dossier/regenerate")
    assert failed.status_code == 502

    fetched = client.get(f"/api/concepts/{concept_id}/dossier").json()
    assert fetched["active"]["id"] == active["id"]
    assert fetched["draft"] is None


def test_first_generation_failure_leaves_no_dossier_at_all(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")

    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(raise_error=ai.AISchemaValidationError("bad shape")))
    resp = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    assert resp.status_code == 422

    assert client.get(f"/api/concepts/{concept_id}/dossier").json()["active"] is None


def test_missing_api_key_returns_a_clear_operational_error(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA")
    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(raise_error=ai.AIConfigError("OPENAI_API_KEY is not set")))
    resp = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    assert resp.status_code == 503


# --- related concepts restricted to accepted candidates (§F) ------------------

def test_related_concepts_restricted_to_accepted_candidates(client, monkeypatch):
    with db.db_cursor() as cur:
        target_id = _concept(cur, name="ORSA", type_code="knowledge")
        co_occurring_id = _concept(cur, name="Solvency II", type_code="regulation")
        # A real, accepted concept that never co-occurs with the target and
        # has no concept_edge to it — NOT a candidate for this generation.
        unrelated_id = _concept(cur, name="IFRS 17", type_code="regulation")
        _role_with_skills(cur, "ORSA", "Solvency II")

    output = _fake_output(
        related_concepts=[
            RelatedConceptSuggestion(concept_id=co_occurring_id, relationship="related to", explanation="Both feature in Solvency II reporting."),
            RelatedConceptSuggestion(concept_id=unrelated_id, relationship="related to", explanation="Not actually offered as a candidate."),
            RelatedConceptSuggestion(concept_id=str(uuid.uuid4()), relationship="related to", explanation="A hallucinated id."),
        ]
    )
    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(output=output))

    resp = client.post(f"/api/concepts/{target_id}/dossier/generate")
    assert resp.status_code == 200
    related = resp.json()["dossier"]["related_concepts"]
    assert len(related) == 1
    assert related[0]["concept_id"] == co_occurring_id
    assert related[0]["canonical_name"] == "Solvency II"
    assert related[0]["relationship"] == "related to"


def test_related_concept_chip_data_lets_the_frontend_load_that_concept(client, monkeypatch):
    """Each stored related-concept entry carries enough to render a clickable
    chip and jump straight to that concept (§F) without another lookup."""
    with db.db_cursor() as cur:
        target_id = _concept(cur, name="ORSA")
        neighbour_id = _concept(cur, name="Solvency II", type_code="regulation")
        _role_with_skills(cur, "ORSA", "Solvency II")

    output = _fake_output(related_concepts=[RelatedConceptSuggestion(concept_id=neighbour_id, relationship="informs", explanation="ORSA is performed under Solvency II.")])
    monkeypatch.setattr(dossier, "run_json_task", _fake_run_json_task(output=output))

    resp = client.post(f"/api/concepts/{target_id}/dossier/generate")
    related = resp.json()["dossier"]["related_concepts"][0]
    assert related["concept_id"] == neighbour_id
    assert related["type_code"] == "regulation"

    # The chip's concept_id resolves to a real, independently-loadable concept.
    assert client.get(f"/api/concepts/{neighbour_id}").status_code == 200


# --- grounding never reads profile360 (§E) -------------------------------------

def test_generation_input_never_includes_profile360(client, monkeypatch):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, name="ORSA", definition="A Solvency II self-assessment.")
        _role_with_skills(cur, "ORSA")

    captured = {}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        captured["user_input"] = user_input
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=_fake_output(), run=run)

    monkeypatch.setattr(dossier, "run_json_task", _dispatch)
    resp = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    assert resp.status_code == 200
    assert "profile360" not in captured["user_input"].lower()
    assert "ORSA" in captured["user_input"]


# --- auth ----------------------------------------------------------------

def test_dossier_endpoints_reject_unauthenticated_calls(anon_client):
    concept_id = str(uuid.uuid4())
    assert anon_client.get(f"/api/concepts/{concept_id}/dossier").status_code in (401, 403)
    assert anon_client.get(f"/api/concepts/{concept_id}/dossier/history").status_code in (401, 403)
    assert anon_client.post(f"/api/concepts/{concept_id}/dossier/generate").status_code in (401, 403)
    assert anon_client.post(f"/api/concepts/{concept_id}/dossier/regenerate").status_code in (401, 403)
    assert anon_client.post(f"/api/concepts/{concept_id}/dossier/adopt").status_code in (401, 403)
    assert anon_client.post(f"/api/concepts/{concept_id}/dossier/discard").status_code in (401, 403)
    assert anon_client.put(f"/api/concepts/{concept_id}/dossier", json={}).status_code in (401, 403)
