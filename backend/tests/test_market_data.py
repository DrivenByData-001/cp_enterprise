"""Market survey document capture + on-demand AI extraction + review
(Phase 4 prompt §8), HTTP-level against the real Postgres test database.
AI calls are mocked exactly like `test_document_processing.py`/
`test_import_native.py` mock `run_json_task` — never a live provider call."""

import io

from app import ai, db
from app import market_data_processing as mdp
from app.models import MarketSurveyExtractionItem, MarketSurveyExtractionResult


def _fake_result(items: list[dict], methodology_notes: str | None = None) -> ai.AITaskResult:
    output = MarketSurveyExtractionResult(
        items=[MarketSurveyExtractionItem(**item) for item in items],
        methodology_notes=methodology_notes,
    )
    run = ai.AITaskRun(
        task=mdp.TASK, model="test-model", prompt_name=mdp.PROMPT_NAME, prompt_version="testversion",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=10, output_chars=10,
    )
    return ai.AITaskResult(output=output, run=run)


def test_ingest_text_creates_market_survey_document(client):
    resp = client.post(
        "/api/market-data/documents/ingest",
        json={"text": "Salary Survey 2026: Actuarial roles...", "publisher": "Acme Recruiters", "report_title": "2026 Actuarial Survey"},
    )
    assert resp.status_code == 200
    document_id = resp.json()["id"]

    doc = client.get(f"/api/market-data/documents/{document_id}").json()
    assert doc["source"] == "Acme Recruiters"
    assert doc["content_text"] == "Salary Survey 2026: Actuarial roles..."
    assert doc["observations"] == []


def test_report_date_propagates_to_extracted_observations(client, monkeypatch):
    resp = client.post(
        "/api/market-data/documents/ingest",
        json={"text": "A survey report.", "report_date": "2026-03-01"},
    )
    document_id = resp.json()["id"]
    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {"raw_role_label": "Dated Row", "component": "base", "pay_period": "annual", "currency": "GBP", "amount_mid": 60000},
        ]),
    )
    client.post(f"/api/market-data/documents/{document_id}/extract")
    draft = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    row = next(r for r in draft if r["raw_role_label"] == "Dated Row")
    assert row["observed_at"] == "2026-03-01"
    assert row["period_end"] == "2026-03-01"


def test_missing_report_date_leaves_observed_at_and_period_end_null(client, monkeypatch):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report with no date."})
    document_id = resp.json()["id"]
    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {"raw_role_label": "Undated Row", "component": "base", "pay_period": "annual", "currency": "GBP", "amount_mid": 60000},
        ]),
    )
    client.post(f"/api/market-data/documents/{document_id}/extract")
    draft = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    row = next(r for r in draft if r["raw_role_label"] == "Undated Row")
    assert row["observed_at"] is None
    assert row["period_end"] is None


def test_curator_can_correct_observation_date(client, monkeypatch):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]
    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {"raw_role_label": "Fix My Date", "component": "base", "pay_period": "annual", "currency": "GBP", "amount_mid": 60000},
        ]),
    )
    client.post(f"/api/market-data/documents/{document_id}/extract")
    draft = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    observation_id = next(r["id"] for r in draft if r["raw_role_label"] == "Fix My Date")

    patch_resp = client.patch(
        f"/api/market-data/compensation-observations/{observation_id}",
        json={"observed_at": "2026-05-15", "period_end": "2026-05-15"},
    )
    assert patch_resp.status_code == 200

    draft_after = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    row = next(r for r in draft_after if r["id"] == observation_id)
    assert row["observed_at"] == "2026-05-15"
    assert row["period_end"] == "2026-05-15"


def test_correct_observation_rejects_invalid_date(client):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.compensation_observation (source_key, raw_role_label, component, pay_period, "
            "currency, basis, review_status, document_id) "
            "VALUES ('bad-date-test-row', 'Bad Date Row', 'base', 'annual', 'GBP', 'survey', 'unreviewed', %s) RETURNING id",
            (document_id,),
        )
        observation_id = str(cur.fetchone()["id"])

    resp = client.patch(
        f"/api/market-data/compensation-observations/{observation_id}",
        json={"observed_at": "not-a-date"},
    )
    assert resp.status_code == 422


def test_ingest_rejects_invalid_report_date(client):
    resp = client.post(
        "/api/market-data/documents/ingest",
        json={"text": "A survey report.", "report_date": "15th of March"},
    )
    assert resp.status_code == 422


def test_ingest_pdf_extracts_selectable_text(client, monkeypatch):
    import pypdf

    class _FakePage:
        def extract_text(self):
            return "Extracted survey text."

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    resp = client.post(
        "/api/market-data/documents/ingest/pdf",
        files={"file": ("survey.pdf", io.BytesIO(b"not a real pdf"), "application/pdf")},
    )
    assert resp.status_code == 200
    doc = client.get(f"/api/market-data/documents/{resp.json()['id']}").json()
    assert doc["content_text"] == "Extracted survey text."


def test_ingest_pdf_rejects_image_only_pdf(client):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    resp = client.post("/api/market-data/documents/ingest/pdf", files={"file": ("scan.pdf", buf, "application/pdf")})
    assert resp.status_code == 422
    assert "OCR" in resp.json()["detail"]


def test_viewing_document_never_calls_ai(client, monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("GET must never call the AI provider")

    monkeypatch.setattr(mdp, "run_json_task", _boom)
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]

    for _ in range(3):
        assert client.get(f"/api/market-data/documents/{document_id}").status_code == 200


def test_extraction_creates_unreviewed_draft_observations(client, monkeypatch):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report with salary data."})
    document_id = resp.json()["id"]

    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {
                "raw_role_label": "Senior Actuarial Analyst",
                "component": "base", "pay_period": "annual", "currency": "gbp",
                "amount_mid": 75000, "reported_sample_size": 40,
            }
        ]),
    )
    extract_resp = client.post(f"/api/market-data/documents/{document_id}/extract")
    assert extract_resp.status_code == 200
    body = extract_resp.json()
    assert body["observations_created"] == 1

    draft = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    row = next(r for r in draft if r["raw_role_label"] == "Senior Actuarial Analyst")
    assert row["review_status"] == "unreviewed"
    assert row["currency"] == "GBP"  # normalised upper-case
    # Never auto-assigned — requires explicit curator action.
    assert row["archetype_concept_id"] is None
    assert row["market_id"] is None


def test_extraction_skips_items_with_unrecognised_component(client, monkeypatch):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]

    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {"raw_role_label": "Ambiguous Row", "component": "some_unrecognised_thing", "pay_period": "annual", "currency": "GBP"},
        ]),
    )
    extract_resp = client.post(f"/api/market-data/documents/{document_id}/extract")
    body = extract_resp.json()
    assert body["observations_created"] == 0
    assert body["items_skipped_incomplete"] == 1


def test_sample_size_remains_null_if_source_omitted_it(client, monkeypatch):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]

    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {"raw_role_label": "No Sample Size Row", "component": "base", "pay_period": "annual", "currency": "GBP", "amount_mid": 60000},
        ]),
    )
    client.post(f"/api/market-data/documents/{document_id}/extract")
    draft = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    row = next(r for r in draft if r["raw_role_label"] == "No Sample Size Row")
    assert row["reported_sample_size"] is None


def test_extraction_failure_leaves_document_and_accepted_observations_untouched(client, monkeypatch):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "Original verbatim text."})
    document_id = resp.json()["id"]

    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.compensation_observation (source_key, raw_role_label, market_id, component, "
            "pay_period, currency, basis, review_status, document_id) "
            "VALUES ('preexisting-accepted', 'Untouched Row', NULL, 'base', 'annual', 'GBP', 'survey', 'accepted', %s)",
            (document_id,),
        )

    def _boom(**kwargs):
        raise ai.AIProviderError("simulated provider failure")

    monkeypatch.setattr(mdp, "run_json_task", _boom)
    extract_resp = client.post(f"/api/market-data/documents/{document_id}/extract")
    assert extract_resp.json()["status"] == "failed"

    doc = client.get(f"/api/market-data/documents/{document_id}").json()
    assert doc["content_text"] == "Original verbatim text."
    accepted = client.get("/api/market-data/compensation-observations", params={"review_status": "accepted"}).json()
    assert any(r["raw_role_label"] == "Untouched Row" for r in accepted)


def test_accept_and_reject_review_workflow(client, monkeypatch):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]
    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {"raw_role_label": "Accept Me", "component": "base", "pay_period": "annual", "currency": "GBP", "amount_mid": 60000},
            {"raw_role_label": "Reject Me", "component": "base", "pay_period": "annual", "currency": "GBP", "amount_mid": 60000},
        ]),
    )
    client.post(f"/api/market-data/documents/{document_id}/extract")
    draft = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    accept_id = next(r["id"] for r in draft if r["raw_role_label"] == "Accept Me")
    reject_id = next(r["id"] for r in draft if r["raw_role_label"] == "Reject Me")

    assert client.post(f"/api/market-data/compensation-observations/{accept_id}/review", json={"action": "accept"}).status_code == 200
    assert client.post(f"/api/market-data/compensation-observations/{reject_id}/review", json={"action": "reject"}).status_code == 200

    accepted = client.get("/api/market-data/compensation-observations", params={"review_status": "accepted"}).json()
    rejected = client.get("/api/market-data/compensation-observations", params={"review_status": "rejected"}).json()
    assert any(r["id"] == accept_id for r in accepted)
    assert any(r["id"] == reject_id for r in rejected)
    assert not any(r["id"] == reject_id for r in accepted)


def test_correct_observation_assigns_market_and_archetype(client, monkeypatch):
    market_resp = client.post("/api/economics/markets", json={"code": "market-data-test", "label": "Test Market"})
    archetype_resp = client.post("/api/archetypes", json={"canonical_name": "Assignable Archetype"})

    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]
    monkeypatch.setattr(
        mdp, "run_json_task",
        lambda **kw: _fake_result([
            {"raw_role_label": "Assign Me", "component": "base", "pay_period": "annual", "currency": "GBP", "amount_mid": 60000},
        ]),
    )
    client.post(f"/api/market-data/documents/{document_id}/extract")
    draft = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    observation_id = next(r["id"] for r in draft if r["raw_role_label"] == "Assign Me")

    patch_resp = client.patch(
        f"/api/market-data/compensation-observations/{observation_id}",
        json={"market_id": market_resp.json()["id"], "archetype_concept_id": archetype_resp.json()["id"]},
    )
    assert patch_resp.status_code == 200

    draft_after = client.get("/api/market-data/compensation-observations", params={"review_status": "unreviewed"}).json()
    row = next(r for r in draft_after if r["id"] == observation_id)
    assert row["market_id"] == market_resp.json()["id"]
    assert row["archetype_concept_id"] == archetype_resp.json()["id"]


def test_correct_observation_rejects_non_active_archetype(client):
    resp = client.post("/api/market-data/documents/ingest", json={"text": "A survey report."})
    document_id = resp.json()["id"]
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.compensation_observation (source_key, raw_role_label, component, pay_period, "
            "currency, basis, review_status, document_id) "
            "VALUES ('manual-test-row', 'Manual Row', 'base', 'annual', 'GBP', 'survey', 'unreviewed', %s) RETURNING id",
            (document_id,),
        )
        observation_id = str(cur.fetchone()["id"])

    resp = client.patch(
        f"/api/market-data/compensation-observations/{observation_id}",
        json={"archetype_concept_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 400
