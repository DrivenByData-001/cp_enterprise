from datetime import date

from app import ai
from app import market_data_processing as mdp
from app.economics_engine import select_reference_compensation
from app.models import MarketSurveyExtractionItem


def _fake_result(items, *, report_source_type=None, methodology_notes=None):
    output = mdp.MarketSurveyExtractionEnvelope(
        items=[MarketSurveyExtractionItem(**item) for item in items],
        report_source_type=report_source_type,
        methodology_notes=methodology_notes,
    )
    run = ai.AITaskRun(task=mdp.TASK, model="test-model", prompt_name=mdp.PROMPT_NAME, prompt_version="test", started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00", status="ok", input_chars=10, output_chars=10)
    return ai.AITaskResult(output=output, run=run)


def test_report_context_mean_median_and_bands_survive_persistence(client, monkeypatch):
    doc = client.post("/api/market-data/documents/ingest", json={"text": "Raretec representative row", "publisher": "Raretec", "report_title": "Salary Survey 2026", "report_date": "2026-06-30"}).json()
    monkeypatch.setattr(mdp, "run_json_task", lambda **kw: _fake_result([{
        "raw_role_label": "Qualified Actuary — Life — 9–12 years PQE",
        "geography": "Ireland", "domain_or_practice_area": "Life", "seniority_band": "Qualified Actuary",
        "pqe_band": "9–12 years", "source_kind": "respondent_survey",
        "component": "base", "pay_period": "annual", "currency": "EUR",
        "amount_min": 100000, "amount_max": 178000, "reported_mean": 141134, "reported_p50": 142000,
        "page_reference": "20", "table_reference": "Salaries - Post Qualification Experience",
    }]))
    assert client.post(f"/api/market-data/documents/{doc['id']}/extract").status_code == 200
    rows = client.get("/api/market-data/compensation-observations").json()
    row = next(r for r in rows if r["raw_role_label"].startswith("Qualified Actuary"))
    assert row["reported_mean"] == 141134
    assert row["reported_p50"] == 142000
    assert row["pqe_band"] == "9–12 years"
    assert row["domain_or_practice_area"] == "Life"
    assert row["geography_reported"] == "Ireland"
    assert row["source_quality"] == "document_linked_unknown_sample"
    assert row["publisher"] == "Raretec"
    assert row["report_date"] == "2026-06-30"


def _survey(id, *, n, end, p50=None, mean=None, source_quality=None):
    return {"id": id, "basis": "survey", "reported_p50": p50, "amount_mid": None, "reported_mean": mean, "reported_sample_size": n, "document_id": f"doc-{id}", "period_end": end, "source_quality": source_quality, "source_kind": "respondent_survey", "amount_min": None, "amount_max": None}


def test_explicit_sample_quality_outranks_newer_unknown_sample():
    result = select_reference_compensation([
        _survey("older", n=21, end=date(2025, 10, 8), p50=117000, source_quality="explicit_sample_n_ge_5"),
        _survey("newer", n=None, end=date(2026, 6, 30), p50=120000, source_quality="document_linked_unknown_sample"),
    ])
    assert result["reference_comp"] == 117000
    assert result["reference_basis_detail"]["observation_id"] == "older"
    assert result["reference_basis_detail"]["document_id"] == "doc-older"


def test_unknown_sample_document_linked_survey_can_be_reference_and_mean_is_preserved():
    result = select_reference_compensation([_survey("raretec", n=None, end=date(2026, 6, 30), mean=141134, source_quality="document_linked_unknown_sample")])
    assert result["reference_comp"] == 141134
    assert result["reference_basis_detail"]["statistic"] == "reported_mean"
    assert result["reference_basis_detail"]["quality_tier"] == "document_linked_unknown_sample"


def test_known_subfive_survey_does_not_qualify():
    result = select_reference_compensation([_survey("thin", n=4, end=date(2026, 6, 30), p50=100000, source_quality="explicit_sample_n_lt_5")])
    assert result["reference_comp"] is None


def test_unknown_sample_recruiter_range_can_use_labelled_derived_midpoint():
    row = _survey("cavehill", n=None, end=date(2026, 6, 30), source_quality="document_linked_unknown_sample")
    row["source_kind"] = "recruiter_benchmark"
    row["amount_min"] = 144000
    row["amount_max"] = 200000
    result = select_reference_compensation([row])
    assert result["reference_comp"] == 172000
    assert result["reference_basis_detail"]["statistic"] == "derived_range_midpoint"
    assert result["reference_basis_detail"]["source_kind"] == "recruiter_benchmark"


def test_source_quality_helper_keeps_curator_sample_edits_consistent():
    assert mdp.source_quality_for_sample_size(None) == "document_linked_unknown_sample"
    assert mdp.source_quality_for_sample_size(4) == "explicit_sample_n_lt_5"
    assert mdp.source_quality_for_sample_size(5) == "explicit_sample_n_ge_5"


def test_report_methodology_is_preserved_as_extraction_metadata(client, monkeypatch):
    doc = client.post(
        "/api/market-data/documents/ingest",
        json={
            "text": "Cavehill representative recruiter benchmark",
            "publisher": "Cavehill Consulting Group",
            "report_title": "H1 2026 Review",
            "report_date": "2026-06-30",
        },
    ).json()
    monkeypatch.setattr(
        mdp,
        "run_json_task",
        lambda **kw: _fake_result(
            [{
                "raw_role_label": "Actuarial Manager (Capital / Reporting)",
                "geography": "Dublin",
                "domain_or_practice_area": "Life",
                "source_kind": "recruiter_benchmark",
                "component": "base",
                "pay_period": "annual",
                "currency": "EUR",
                "amount_min": 92000,
                "amount_max": 128000,
            }],
            report_source_type="recruiter_benchmark",
            methodology_notes="Placement evidence, candidate offers and direct market conversations.",
        ),
    )

    response = client.post(f"/api/market-data/documents/{doc['id']}/extract")
    assert response.status_code == 200
    body = response.json()
    assert body["report_source_type"] == "recruiter_benchmark"
    assert "Placement evidence" in body["methodology_notes"]

    detail = client.get(f"/api/market-data/documents/{doc['id']}").json()
    payload = detail["extraction_runs"][0]["output_payload"]
    assert payload["report_source_type"] == "recruiter_benchmark"
    assert "candidate offers" in payload["methodology_notes"]
    assert detail["observations"][0]["source_kind"] == "recruiter_benchmark"
    assert "report_source_type" not in detail["observations"][0]


def test_market_survey_prompt_forbids_copying_overall_sample_to_each_row():
    prompt = mdp.load_prompt(mdp.PROMPT_NAME)
    assert "overall report" in prompt
    assert "must not be copied into every row" in prompt
    assert "not a source-quality score or a claim of statistical validation" in prompt
