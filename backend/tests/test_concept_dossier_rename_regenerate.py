"""Regression coverage for generate -> rename -> regenerate Concept Dossier."""

from app import ai, concept_dossier as dossier, db
from app.models import ConceptDossierGeneration


def _fake_output() -> ConceptDossierGeneration:
    return ConceptDossierGeneration(
        plain_definition="A plain-English definition.",
        classification_rationale="Because it fits the recorded type.",
        practical_meaning="What it means in practice.",
        underlying_elements=["Element A"],
        stronger_expressions=["More specific expression"],
        weaker_expressions=["Broader wording"],
        boundaries_and_overlaps="Distinct from its neighbours.",
        related_concepts=[],
        caveats=None,
    )


def test_rename_then_regenerate_without_guidance_uses_current_canonical_name(client, monkeypatch):
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('knowledge', 'Old label', 'active', 'curator', now()) RETURNING id"
        )
        concept_id = str(cur.fetchone()["id"])

    inputs: list[str] = []

    def fake_run_json_task(*, task, prompt_name, user_input, output_model):
        inputs.append(user_input)
        run = ai.AITaskRun(
            task=task,
            model="test-model",
            prompt_name=prompt_name,
            prompt_version="test-version",
            started_at="2026-09-14T00:00:00+00:00",
            finished_at="2026-09-14T00:00:01+00:00",
            status="ok",
            input_chars=len(user_input),
            output_chars=10,
        )
        return ai.AITaskResult(output=_fake_output(), run=run)

    monkeypatch.setattr(dossier, "run_json_task", fake_run_json_task)

    generated = client.post(f"/api/concepts/{concept_id}/dossier/generate")
    assert generated.status_code == 200
    assert generated.json()["dossier"]["status"] == "active"

    renamed = client.patch(f"/api/concepts/{concept_id}", json={"canonical_name": "Renamed label"})
    assert renamed.status_code == 200
    assert renamed.json()["canonical_name"] == "Renamed label"

    regenerated = client.post(f"/api/concepts/{concept_id}/dossier/regenerate")
    assert regenerated.status_code == 200
    assert regenerated.json()["dossier"]["status"] == "draft"
    assert regenerated.json()["dossier"]["guidance"] is None

    assert len(inputs) == 2
    assert "Canonical name: Old label" in inputs[0]
    assert "Canonical name: Renamed label" in inputs[1]
    assert "Curator guidance" not in inputs[1]
