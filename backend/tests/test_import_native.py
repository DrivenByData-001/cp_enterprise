from app import ai, db
from app.models import Analysis, Job, JobPostingImport, Metadata
from app.routes import import_routes


def _fake_run(**kwargs):
    output = JobPostingImport(
        metadata=Metadata(source="user_paste", extraction_status="ok"),
        job=Job(title="Senior Actuarial Analyst", organisation="Aviva"),
        skills=[],
        analysis=Analysis(summary="A reserving role."),
    )
    run = ai.AITaskRun(
        task=kwargs["task"],
        model="gpt-4o-mini",
        prompt_name=kwargs["prompt_name"],
        prompt_version="deadbeefcafe",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status="ok",
        input_chars=len(kwargs["user_input"]),
        output_chars=42,
    )
    return ai.AITaskResult(output=output, run=run)


def test_native_import_feeds_existing_pipeline(client, monkeypatch):
    monkeypatch.setattr(import_routes, "run_json_task", _fake_run)

    resp = client.post("/api/import/native", json={"text": "Senior Actuarial Analyst at Aviva..."})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "imported"
    assert isinstance(body["id"], int)
    assert body["extraction"]["job"]["title"] == "Senior Actuarial Analyst"
    assert body["run"]["task"] == "job_posting_extract"
    assert body["run"]["model"] == "gpt-4o-mini"

    # actually landed in the same table the legacy import path writes to
    with db.db_cursor() as cur:
        cur.execute("SELECT title, organisation, kind FROM jobber.role_instance WHERE id = %s", (body["id"],))
        row = cur.fetchone()
    assert row["title"] == "Senior Actuarial Analyst"
    assert row["organisation"] == "Aviva"
    assert row["kind"] == "posting"


def test_native_import_creates_source_document(client, monkeypatch):
    monkeypatch.setattr(import_routes, "run_json_task", _fake_run)

    resp = client.post("/api/import/native", json={"text": "Senior Actuarial Analyst at Aviva..."})
    role_id = resp.json()["id"]

    with db.db_cursor() as cur:
        cur.execute("SELECT document_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        document_id = cur.fetchone()["document_id"]
        assert document_id is not None
        cur.execute("SELECT provenance, kind FROM jobber.document WHERE id = %s", (document_id,))
        doc = cur.fetchone()
    assert doc["provenance"] == "original_capture"
    assert doc["kind"] == "job_posting"


def test_native_import_rejects_blank_text(client):
    resp = client.post("/api/import/native", json={"text": "   "})
    assert resp.status_code == 400


def test_native_import_surfaces_missing_config_as_503(client, monkeypatch):
    def _raise_config(**kwargs):
        raise ai.AIConfigError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(import_routes, "run_json_task", _raise_config)

    resp = client.post("/api/import/native", json={"text": "some posting text"})
    assert resp.status_code == 503


def test_native_import_surfaces_schema_failure_as_422(client, monkeypatch):
    def _raise_schema(**kwargs):
        raise ai.AISchemaValidationError("missing required field")

    monkeypatch.setattr(import_routes, "run_json_task", _raise_schema)

    resp = client.post("/api/import/native", json={"text": "some posting text"})
    assert resp.status_code == 422
