"""CP Ent Phase 3B: raw jobber.document -> job_posting_extract ->
jobber.role_instance (docs/17-document-processing-pipeline.md, brief §23).
app.document_processing directly, with run_json_task mocked — never a live
OpenAI call, same convention as test_requirement_claims.py."""

import threading
import time

import pytest

from app import ai, db, document_processing
from app.models import Analysis, Job, JobPostingImport, Metadata, Skill


def _fake_result(
    *,
    title="Senior Pricing Actuary",
    posting_date=None,
    extraction_status="ok",
    source=None,
    url=None,
    input_text="",
    task="job_posting_extract",
    prompt_name="extract_job_posting.md",
    skills=None,
    job_overrides=None,
    analysis_overrides=None,
):
    job_kwargs = {"title": title, "posting_date": posting_date, **(job_overrides or {})}
    analysis_kwargs = {"summary": "A role.", **(analysis_overrides or {})}
    output = JobPostingImport(
        metadata=Metadata(source=source, url=url, extraction_status=extraction_status),
        job=Job(**job_kwargs),
        skills=skills or [],
        analysis=Analysis(**analysis_kwargs),
    )
    run = ai.AITaskRun(
        task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=len(input_text), output_chars=10,
    )
    return ai.AITaskResult(output=output, run=run)


def _raising(exc):
    def _fn(**kwargs):
        raise exc
    return _fn


def _seed_document(cur, content_text: str, **kwargs) -> str:
    kwargs.setdefault("kind", "job_posting")
    kwargs.setdefault("provenance_quality", "original")
    document_id, _duplicate_of = db.create_document(cur, content_text=content_text, **kwargs)
    return document_id


def test_processing_does_not_modify_the_raw_document(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(
            cur, "Verbatim historical advert text.", source_date="2008-03-27", title="Economic Modeller"
        )

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    result = document_processing.process_job_posting_document(document_id)
    assert result["status"] == "ok"

    with db.db_cursor() as cur:
        cur.execute("SELECT content_text, source_date, title FROM jobber.document WHERE id = %s", (document_id,))
        doc = cur.fetchone()
    assert doc["content_text"] == "Verbatim historical advert text."
    assert str(doc["source_date"]) == "2008-03-27"
    assert doc["title"] == "Economic Modeller"


def test_existing_document_is_reused_not_duplicated(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "A posting to reuse.")
        cur.execute("SELECT COUNT(*) AS n FROM jobber.document")
        before = cur.fetchone()["n"]

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.document")
        after = cur.fetchone()["n"]
        cur.execute("SELECT document_id FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        role_document_id = str(cur.fetchone()["document_id"])

    assert after == before  # no new document created
    assert role_document_id == document_id


def test_successful_processing_creates_exactly_one_role(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "One role only.")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.role_instance WHERE document_id = %s", (document_id,))
        roles = cur.fetchall()
    assert len(roles) == 1
    assert str(roles[0]["id"]) == result["role_instance_id"]


def test_output_payload_stores_validated_job_posting_import(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Payload check posting.")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result(title="Payload Title"))
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT output_payload FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        output_payload = cur.fetchone()["output_payload"]
    assert output_payload["job"]["title"] == "Payload Title"
    assert output_payload == result["output_payload"]


def test_successful_run_links_result_role_instance_id(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Linkage check posting.")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT document_id, result_role_instance_id, subject_type FROM jobber.extraction_run WHERE id = %s",
            (result["extraction_run_id"],),
        )
        run = cur.fetchone()
    assert str(run["result_role_instance_id"]) == result["role_instance_id"]
    assert str(run["document_id"]) == document_id
    assert run["subject_type"] == "document"


def test_running_to_ok_lifecycle(client, monkeypatch):
    """Proves the running row is committed *before* the AI call: a fake
    run_json_task that peeks at the database mid-call, from a fresh
    connection, sees a durable 'running' row — not merely an in-memory
    intention (brief §6)."""
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Lifecycle check posting.")

    def _peek_then_succeed(**kwargs):
        with db.db_cursor() as cur:
            cur.execute(
                "SELECT status, finished_at FROM jobber.extraction_run "
                "WHERE document_id = %s AND task = 'job_posting_extract'",
                (document_id,),
            )
            mid_flight = cur.fetchone()
        assert mid_flight["status"] == "running"
        assert mid_flight["finished_at"] is None
        return _fake_result()

    monkeypatch.setattr(document_processing, "run_json_task", _peek_then_succeed)
    result = document_processing.process_job_posting_document(document_id)
    assert result["status"] == "ok"

    with db.db_cursor() as cur:
        cur.execute("SELECT status, finished_at FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        run = cur.fetchone()
    assert run["status"] == "ok"
    assert run["finished_at"] is not None


def test_substantive_zero_skill_extraction_is_partial_with_preserved_provenance(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Substantive posting with no extracted skills.")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            job_overrides={
                "description": "Lead actuarial work across Solvency II technical provisions.",
                "requirements": "Strong analytical competence and stakeholder management.",
                "responsibilities": "Own the ORSA and asset-liability management reporting.",
            },
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    assert result["status"] == "partial"
    assert result["role_instance_id"] is not None
    assert result["output_payload"]["skills"] == []
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT status, output_payload, result_role_instance_id FROM jobber.extraction_run WHERE id = %s",
            (result["extraction_run_id"],),
        )
        run = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.role_skill_observation WHERE role_instance_id = %s",
            (result["role_instance_id"],),
        )
        skill_count = cur.fetchone()["n"]

    assert run["status"] == "partial"
    assert str(run["result_role_instance_id"]) == result["role_instance_id"]
    assert run["output_payload"] == result["output_payload"]
    assert skill_count == 0
    assert document_processing.process_job_posting_document(document_id)["status"] == "already_analysed"


def test_substantive_extraction_with_skills_remains_ok(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Substantive posting with extracted skills.")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            job_overrides={"requirements": "Experience with Solvency II technical provisions."},
            skills=[Skill(name="Solvency II", category="technical", importance=5, requirement_type="required")],
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    assert result["status"] == "ok"
    assert result["output_payload"]["skills"][0]["name"] == "Solvency II"


def test_zero_skills_without_substantive_parsed_content_remains_ok(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Minimal but processable raw posting text.")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            job_overrides={"description": " ", "requirements": "", "responsibilities": None},
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    assert result["status"] == "ok"
    assert result["output_payload"]["skills"] == []


def test_zero_skill_guardrail_does_not_change_historical_policy(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(
            cur,
            "Historical substantive posting with no extracted skills.",
            source_date="2017-02-01",
        )

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            job_overrides={"description": "Manage historical actuarial technical provisions."},
            analysis_overrides={
                "market_demand_score": 0.9,
                "automation_risk_score": 0.2,
                "top_adjacent_roles": ["Actuary"],
            },
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    assert result["status"] == "partial"
    analysis = result["output_payload"]["analysis"]
    assert analysis["market_demand_score"] is None
    assert analysis["automation_risk_score"] is None
    assert analysis["top_adjacent_roles"] is None


def test_provider_failure_produces_running_to_failed(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Provider failure posting.")

    monkeypatch.setattr(document_processing, "run_json_task", _raising(ai.AIProviderError("OpenAI API error: connection reset")))
    result = document_processing.process_job_posting_document(document_id)

    assert result["status"] == "failed"
    assert result["role_instance_id"] is None
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT status, finished_at, error_type, error_message, result_role_instance_id "
            "FROM jobber.extraction_run WHERE id = %s",
            (result["extraction_run_id"],),
        )
        run = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_instance WHERE document_id = %s", (document_id,))
        role_count = cur.fetchone()["n"]
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    assert run["error_type"] == "AIProviderError"
    assert run["error_message"]
    assert run["result_role_instance_id"] is None
    assert role_count == 0  # a failed attempt must never leave a role behind


def test_schema_validation_failure_recorded_as_failed(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Schema failure posting.")

    monkeypatch.setattr(document_processing, "run_json_task", _raising(ai.AISchemaValidationError("missing required field 'job.title'")))
    result = document_processing.process_job_posting_document(document_id)

    assert result["status"] == "failed"
    assert result["error_type"] == "AISchemaValidationError"
    with db.db_cursor() as cur:
        cur.execute("SELECT status, error_type FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        run = cur.fetchone()
    assert run["status"] == "failed"
    assert run["error_type"] == "AISchemaValidationError"


def test_failed_document_can_be_retried(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Retry-after-failure posting.")

    monkeypatch.setattr(document_processing, "run_json_task", _raising(ai.AIProviderError("boom")))
    first = document_processing.process_job_posting_document(document_id)
    assert first["status"] == "failed"

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    second = document_processing.process_job_posting_document(document_id)
    assert second["status"] == "ok"
    assert second["role_instance_id"] is not None

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_instance WHERE document_id = %s", (document_id,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT COUNT(*) AS n FROM jobber.extraction_run WHERE document_id = %s", (document_id,))
        assert cur.fetchone()["n"] == 2


def test_successful_document_is_skipped_on_rerun(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Skip-on-rerun posting.")

    calls = []

    def _fake(**kwargs):
        calls.append(1)
        return _fake_result()

    monkeypatch.setattr(document_processing, "run_json_task", _fake)
    first = document_processing.process_job_posting_document(document_id)
    assert first["status"] == "ok"

    second = document_processing.process_job_posting_document(document_id)
    assert second["status"] == "already_analysed"
    assert second["role_instance_id"] == first["role_instance_id"]
    assert len(calls) == 1  # the AI provider was never called a second time

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_instance WHERE document_id = %s", (document_id,))
        assert cur.fetchone()["n"] == 1


def test_concurrent_processing_cannot_create_two_roles(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Concurrent processing test posting.")

    def _slow_fake(**kwargs):
        time.sleep(0.15)
        return _fake_result()

    monkeypatch.setattr(document_processing, "run_json_task", _slow_fake)

    results: list = [None, None]

    def _worker(idx):
        try:
            results[idx] = document_processing.process_job_posting_document(document_id)
        except Exception as e:  # noqa: BLE001 - surfaced via the assertion below, not swallowed
            results[idx] = {"status": f"EXCEPTION: {type(e).__name__}: {e}"}

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    statuses = sorted(r["status"] for r in results)
    assert statuses == ["already_processing", "ok"] or statuses == ["already_analysed", "ok"], statuses

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_instance WHERE document_id = %s", (document_id,))
        assert cur.fetchone()["n"] == 1


def test_vocabulary_version_id_is_null_for_job_posting_extract(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Vocabulary-null check posting.")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT vocabulary_version_id FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        assert cur.fetchone()["vocabulary_version_id"] is None


def test_historical_source_date_is_included_in_model_context(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(
            cur, "Historical advert body.", source_date="2008-03-27", title="Economic Modeller", source="11. Recruit",
        )

    captured = {}

    def _fake(**kwargs):
        captured["user_input"] = kwargs["user_input"]
        return _fake_result()

    monkeypatch.setattr(document_processing, "run_json_task", _fake)
    document_processing.process_job_posting_document(document_id)

    assert "Known original posting date: 2008-03-27" in captured["user_input"]
    assert "Known source: 11. Recruit" in captured["user_input"]
    assert "Original listing title: Economic Modeller" in captured["user_input"]
    assert "Historical advert body." in captured["user_input"]
    assert "Historical analysis instruction" in captured["user_input"]


def test_historical_date_is_not_replaced_with_current_date(client, monkeypatch):
    """The model leaves job.posting_date null (as it should when the era is
    known only from our own metadata, not restated in the body) — the
    pipeline fills it from the document's own source_date, never from
    today's date."""
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "No date in the body itself.", source_date="1999-11-02")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result(posting_date=None))
    result = document_processing.process_job_posting_document(document_id)

    assert result["output_payload"]["job"]["posting_date"] == "1999-11-02"
    with db.db_cursor() as cur:
        cur.execute("SELECT posting_date FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        assert str(cur.fetchone()["posting_date"]) == "1999-11-02"


# --- historical-extraction policy (CP Ent Phase 3B 0.2, docs/17 §8a) -------
#
# `_enforce_historical_extraction_policy` is a deterministic backstop: these
# tests deliberately construct *non-compliant* fake model output (as if the
# model had ignored the prompt's HISTORICAL CONTEXT instructions) to prove
# the pipeline still enforces the contract regardless of what the model
# actually returns.


def test_historical_stated_salary_kept_but_estimate_not_copied(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Historical posting with a stated salary.", source_date="2008-03-27")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            job_overrides={"salary_min": 28000, "salary_max": 32000, "currency": "GBP"},
            # Non-compliant: model echoed the stated salary into the estimate fields.
            analysis_overrides={"salary_estimate_min": 28000, "salary_estimate_max": 32000},
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        flat = db.flatten_role_instance(cur.fetchone())

    assert flat["salary_min"] == 28000
    assert flat["salary_max"] == 32000
    assert flat["currency"] == "GBP"
    assert flat["salary_estimate_min"] is None
    assert flat["salary_estimate_max"] is None


def test_historical_advert_with_no_salary_leaves_both_null(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Historical posting with no salary stated anywhere.", source_date="2011-07-01")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        flat = db.flatten_role_instance(cur.fetchone())

    assert flat["salary_min"] is None
    assert flat["salary_max"] is None
    assert flat["salary_estimate_min"] is None
    assert flat["salary_estimate_max"] is None


def test_historical_market_demand_score_is_null(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Historical posting.", source_date="2015-01-15")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        # Non-compliant: model produced a confident modern-looking score anyway.
        lambda **kw: _fake_result(analysis_overrides={"market_demand_score": 0.87}),
    )
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["market_demand_score"] is None


def test_historical_automation_risk_score_is_null(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Historical posting.", source_date="2019-09-30")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(analysis_overrides={"automation_risk_score": 0.42}),
    )
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["automation_risk_score"] is None


def test_historical_top_adjacent_roles_is_empty_or_null(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Historical posting.", source_date="2013-04-11")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(analysis_overrides={"top_adjacent_roles": ["Pricing Analyst", "Reserving Actuary"]}),
    )
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat.get("top_adjacent_roles") in (None, [])


def test_historical_structural_scores_remain_allowed(client, monkeypatch):
    """seniority/complexity/specialisation/transferability/rarity describe the
    role's own documented shape, not the labour market — the historical
    safeguard must leave them exactly as the model judged them."""
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Historical posting.", source_date="2008-06-12")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            analysis_overrides={
                "seniority_score": 0.7,
                "complexity_score": 0.6,
                "specialisation_score": 0.55,
                "transferability_score": 0.4,
                "rarity_score": 0.65,
            }
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["seniority_score"] == 0.7
    assert flat["complexity_score"] == 0.6
    assert flat["specialisation_score"] == 0.55
    assert flat["transferability_score"] == 0.4
    assert flat["rarity_score"] == 0.65


def test_historical_skills_and_requirements_extraction_unaffected(client, monkeypatch):
    """The historical safeguard only touches the three deferred analysis
    fields and the salary-estimate/salary-fact interaction — ordinary
    factual extraction (skills, requirements, responsibilities) must pass
    through untouched."""
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Historical posting with real requirements.", source_date="2009-02-20")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            job_overrides={
                "requirements": "5+ years pricing experience; actuarial exams in progress.",
                "responsibilities": "Own pricing models for the personal lines book.",
            },
            skills=[Skill(name="Pricing", category="technical", importance=5, requirement_type="required")],
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT requirements, responsibilities FROM jobber.role_instance WHERE id = %s",
            (result["role_instance_id"],),
        )
        role = cur.fetchone()
        cur.execute(
            "SELECT surface_form FROM jobber.role_skill_observation WHERE role_instance_id = %s",
            (result["role_instance_id"],),
        )
        skills = [r["surface_form"] for r in cur.fetchall()]

    assert role["requirements"] == "5+ years pricing experience; actuarial exams in progress."
    assert role["responsibilities"] == "Own pricing models for the personal lines book."
    assert skills == ["Pricing"]


def test_non_historical_document_keeps_analytical_fields(client, monkeypatch):
    """Control case: a document with no known source_date is an ordinary
    (non-historical) extraction, and the historical safeguard must never
    fire for it — market_demand_score/automation_risk_score/
    top_adjacent_roles pass through exactly as the model produced them."""
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "A current posting, no historical date known.")

    monkeypatch.setattr(
        document_processing,
        "run_json_task",
        lambda **kw: _fake_result(
            analysis_overrides={
                "market_demand_score": 0.5,
                "automation_risk_score": 0.3,
                "top_adjacent_roles": ["Pricing Analyst", "Data Scientist"],
            }
        ),
    )
    result = document_processing.process_job_posting_document(document_id)

    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.role_instance WHERE id = %s", (result["role_instance_id"],))
        flat = db.flatten_role_instance(cur.fetchone())
    assert flat["market_demand_score"] == 0.5
    assert flat["automation_risk_score"] == 0.3
    assert flat["top_adjacent_roles"] == ["Pricing Analyst", "Data Scientist"]


def test_embedding_failure_does_not_roll_back_successful_role(client, monkeypatch):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Embedding failure posting.")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    monkeypatch.setattr(document_processing, "embed_text", _raising(RuntimeError("embedding model unavailable")))

    result = document_processing.process_job_posting_document(document_id)

    assert result["status"] == "ok"
    assert result["role_instance_id"] is not None
    assert result["embedding_error"] is not None
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT status, result_role_instance_id FROM jobber.extraction_run WHERE id = %s",
            (result["extraction_run_id"],),
        )
        run = cur.fetchone()
    assert run["status"] == "ok"
    assert run["result_role_instance_id"] is not None


def test_processing_status_derivation(client, monkeypatch):
    with db.db_cursor() as cur:
        raw_id = _seed_document(cur, "Raw doc.")
        running_id = _seed_document(cur, "Running doc.")
        failed_id = _seed_document(cur, "Failed doc.")
        partial_id = _seed_document(cur, "Partial doc.")
        analysed_id = _seed_document(cur, "Analysed doc.")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    assert document_processing.process_job_posting_document(analysed_id)["status"] == "ok"

    monkeypatch.setattr(document_processing, "run_json_task", _raising(ai.AIProviderError("x")))
    document_processing.process_job_posting_document(failed_id)

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result(extraction_status="partial"))
    document_processing.process_job_posting_document(partial_id)

    with db.db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobber.extraction_run
                (task, subject_type, document_id, model, prompt_name, prompt_version, vocabulary_version_id, started_at, status)
            VALUES ('job_posting_extract', 'document', %s, 'test-model', 'extract_job_posting.md', 'testversion', NULL, now(), 'running')
            """,
            (running_id,),
        )

        assert document_processing.document_processing_state(cur, raw_id) == "raw"
        assert document_processing.document_processing_state(cur, running_id) == "running"
        assert document_processing.document_processing_state(cur, analysed_id) == "analysed"
        assert document_processing.document_processing_state(cur, failed_id) == "failed"
        assert document_processing.document_processing_state(cur, partial_id) == "partial"

        counts = document_processing.job_posting_processing_counts(cur)
    assert counts["raw"] >= 1
    assert counts["running"] >= 1
    assert counts["analysed"] >= 1
    assert counts["failed"] >= 1
    assert counts["partial"] >= 1
    assert counts["total"] == sum(v for k, v in counts.items() if k != "total")


def test_no_profile360_writes_occur(client, monkeypatch):
    tables = ("claims", "capabilities", "episodes", "snapshots", "manual_import_queue")
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "No profile360 writes posting.")
        before = {}
        for t in tables:
            cur.execute(f"SELECT COUNT(*) AS n FROM profile360.{t}")
            before[t] = cur.fetchone()["n"]

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    result = document_processing.process_job_posting_document(document_id)
    assert result["status"] == "ok"

    with db.db_cursor() as cur:
        after = {}
        for t in tables:
            cur.execute(f"SELECT COUNT(*) AS n FROM profile360.{t}")
            after[t] = cur.fetchone()["n"]
    assert after == before


# --- document eligibility / kind / content validation -----------------------


def test_wrong_kind_document_is_rejected(client):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "A narrative, not a posting.", kind="narrative")

    with pytest.raises(document_processing.DocumentNotProcessableError):
        document_processing.process_job_posting_document(document_id)


def test_blank_content_document_is_rejected(client):
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "   ")

    with pytest.raises(document_processing.DocumentNotProcessableError):
        document_processing.process_job_posting_document(document_id)


def test_missing_document_is_rejected(client):
    import uuid

    with pytest.raises(document_processing.DocumentNotFoundError):
        document_processing.process_job_posting_document(str(uuid.uuid4()))


# --- batch eligibility (brief §17) ------------------------------------------


def test_list_eligible_documents_skips_successful_includes_raw_and_retries_failed(client, monkeypatch):
    with db.db_cursor() as cur:
        raw_id = _seed_document(cur, "Eligible raw doc.", source_key="eligible-test:raw")
        analysed_id = _seed_document(cur, "Eligible analysed doc.", source_key="eligible-test:analysed")
        failed_id = _seed_document(cur, "Eligible failed doc.", source_key="eligible-test:failed")

    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    document_processing.process_job_posting_document(analysed_id)

    monkeypatch.setattr(document_processing, "run_json_task", _raising(ai.AIProviderError("x")))
    document_processing.process_job_posting_document(failed_id)

    with db.db_cursor() as cur:
        default_run = document_processing.list_eligible_documents(cur, source_prefix="eligible-test:")
        ids_default = {str(d["id"]) for d in default_run}
        retry_run = document_processing.list_eligible_documents(cur, source_prefix="eligible-test:", retry_failed=True)
        ids_retry = {str(d["id"]) for d in retry_run}

    assert ids_default == {raw_id}
    assert ids_retry == {raw_id, failed_id}
    assert analysed_id not in ids_default and analysed_id not in ids_retry


# --- docs/18 §5: role-level extraction quality is the *run's* verdict, not just the model's self-report ---


def test_role_extraction_quality_reflects_the_run_status_even_when_it_diverges_from_self_reported_status(client, monkeypatch):
    """The empty-skills deterministic guardrail (§process_job_posting_document)
    can force run_status='partial' even when the model itself self-reported
    metadata.extraction_status='ok'. role_instance.extraction_status stores
    the model's self-report verbatim (posting_persistence.py) and is
    therefore NOT trustworthy alone — role_extraction_quality must report the
    run's own, more authoritative 'partial' verdict."""
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "A posting with prose but no parsed skills list.")

    monkeypatch.setattr(
        document_processing, "run_json_task",
        lambda **kw: _fake_result(
            extraction_status="ok",
            skills=[],
            job_overrides={"description": "A full role description with real substantive content."},
        ),
    )
    result = document_processing.process_job_posting_document(document_id)
    assert result["status"] == "partial"
    role_id = result["role_instance_id"]

    with db.db_cursor() as cur:
        cur.execute("SELECT extraction_status FROM jobber.role_instance WHERE id = %s", (role_id,))
        self_reported = cur.fetchone()["extraction_status"]
        quality = document_processing.role_extraction_quality(cur, role_id)

    assert self_reported == "ok"  # the model's own claim, preserved verbatim
    assert quality["status"] == "partial"  # the actually-authoritative signal


def test_role_extraction_quality_is_none_for_a_role_never_processed_through_this_pipeline(client):
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "Hand-entered role"}, skills=[])
        quality = document_processing.role_extraction_quality(cur, role_id)
    assert quality is None


def test_role_extraction_quality_bulk_matches_single_lookup_for_a_mixed_set(client, monkeypatch):
    monkeypatch.setattr(document_processing, "run_json_task", lambda **kw: _fake_result())
    with db.db_cursor() as cur:
        document_id = _seed_document(cur, "Bulk-quality posting.")
    pipeline_role_id = document_processing.process_job_posting_document(document_id)["role_instance_id"]

    with db.db_cursor() as cur:
        manual_role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "Manual role"}, skills=[])
        bulk = document_processing.role_extraction_quality_bulk(cur, [pipeline_role_id, manual_role_id])

    assert bulk[pipeline_role_id]["status"] == "ok"
    assert manual_role_id not in bulk
