"""POST /api/documents/{id}/analyse, GET /api/documents/{id}/status, and
GET /api/documents/processing-status (docs/17-document-processing-pipeline.md,
brief §18/§19) — route-level: status-code mapping and response shape. The
underlying service logic itself is covered directly in
test_document_processing.py."""

import uuid

from app import db, document_processing


def _fake_result(**kw):
    from app import ai
    from app.models import Analysis, Job, JobPostingImport, Metadata

    output = JobPostingImport(
        metadata=Metadata(extraction_status="ok"),
        job=Job(title="API Test Role"),
        skills=[],
        analysis=Analysis(),
    )
    run = ai.AITaskRun(
        task="job_posting_extract", model="test-model", prompt_name="extract_job_posting.md",
        prompt_version="testversion", started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00", status="ok", input_chars=1, output_chars=1,
    )
    return ai.AITaskResult(output=output, run=run)


def test_analyse_endpoint_processes_a_document(client, monkeypatch):
    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="API posting text.", provenance_quality="original")

    resp = client.post(f"/api/documents/{document_id}/analyse")
    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == document_id
    assert body["status"] == "ok"
    assert body["role_instance_id"] is not None
    assert body["error"] is None


def test_analyse_endpoint_404_for_missing_document(client):
    resp = client.post(f"/api/documents/{uuid.uuid4()}/analyse")
    assert resp.status_code == 404


def test_analyse_endpoint_422_for_wrong_kind(client):
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="narrative", content_text="Not a posting.", provenance_quality="original")
    resp = client.post(f"/api/documents/{document_id}/analyse")
    assert resp.status_code == 422


def test_status_endpoint_reports_raw_then_analysed(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Status check posting.", provenance_quality="original")

    assert client.get(f"/api/documents/{document_id}/status").json()["state"] == "raw"

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    client.post(f"/api/documents/{document_id}/analyse")

    assert client.get(f"/api/documents/{document_id}/status").json()["state"] == "analysed"


def test_status_endpoint_404_for_missing_document(client):
    resp = client.get(f"/api/documents/{uuid.uuid4()}/status")
    assert resp.status_code == 404


def test_processing_status_counts_endpoint(client, monkeypatch):
    with db.db_cursor() as cur:
        db.create_document(cur, kind="job_posting", content_text="Raw one.", provenance_quality="original", source_key="counts-test:raw1")
        analysed_document_id, _ = db.create_document(
            cur, kind="job_posting", content_text="Analysed one.", provenance_quality="original", source_key="counts-test:analysed1"
        )

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    client.post(f"/api/documents/{analysed_document_id}/analyse")

    counts = client.get("/api/documents/processing-status", params={"source_prefix": "counts-test:"}).json()
    assert counts["raw"] == 1
    assert counts["analysed"] == 1
    assert counts["total"] == 2
