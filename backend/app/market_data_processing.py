"""Market survey document -> `compensation_extract` -> draft (unreviewed)
`jobber.compensation_observation` rows (Phase 4 prompt §8).

Mirrors `document_processing.py`'s transaction-safe lifecycle exactly: the
running-row is inserted and committed *before* the AI provider is ever
called, a provider/validation failure marks that row failed in a fresh
transaction, and a successful extraction is persisted in one short
transaction. `jobber.document` is never mutated here — this module only
ever turns an *existing* `kind='market_survey'` document into draft
observations; it never calls `db.create_document()` itself (that happens in
`routes/market_data.py`'s ingest endpoints, before any AI call).

Every extracted row is written `review_status='unreviewed'` — a draft, not
an accepted economic fact (prompt §8: "The AI extraction should produce
draft/unreviewed compensation observations, not accepted economic facts.").
Neither `archetype_concept_id` nor `market_id` is ever set by this module —
"Do not create canonical role archetypes automatically" and the parallel
rule for market both mean assignment is always a separate, explicit curator
action (`routes/market_data.py::correct_observation`). A malformed item
(missing/unrecognised component, pay_period, or currency — all three are
`NOT NULL` columns with no safe default) is dropped rather than guessed,
and counted in the run's own summary."""

from datetime import datetime, timezone

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import db_cursor, to_json_param
from .models import MarketSurveyExtractionResult

TASK = "compensation_extract"
PROMPT_NAME = "extract_market_survey.md"
DOCUMENT_KIND = "market_survey"

_VALID_COMPONENTS = {"base", "bonus_pct", "total_package", "day_rate"}
_VALID_PAY_PERIODS = {"annual", "daily"}
_VALID_EMPLOYMENT_BASES = {"permanent", "contract", "unknown"}


class MarketDataProcessingError(ValueError):
    """A document_id this task cannot run against — a 4xx at the route
    layer. No extraction_run row is ever written for one of these."""


class DocumentNotFoundError(MarketDataProcessingError):
    pass


class DocumentNotProcessableError(MarketDataProcessingError):
    pass


def _safe_task_metadata() -> tuple[str, str]:
    try:
        model = ai_model_name()
    except AIConfigError:
        model = "unconfigured"
    try:
        version = prompt_version(load_prompt(PROMPT_NAME))
    except AIConfigError:
        version = "unknown"
    return model, version


def _load_document(cur, document_id: str) -> dict:
    cur.execute("SELECT id, kind, content_text FROM jobber.document WHERE id = %s", (document_id,))
    row = cur.fetchone()
    if not row:
        raise DocumentNotFoundError(f"document {document_id!r} not found")
    document = dict(row)
    document["id"] = str(document["id"])
    if document["kind"] != DOCUMENT_KIND:
        raise DocumentNotProcessableError(f"document.kind must be {DOCUMENT_KIND!r} for {TASK}, got {document['kind']!r}")
    if not (document.get("content_text") or "").strip():
        raise DocumentNotProcessableError("document has no usable content_text")
    return document


def _successful_run(cur, document_id: str) -> dict | None:
    cur.execute(
        "SELECT id FROM jobber.extraction_run WHERE task = %s AND subject_type = 'document' AND document_id = %s "
        "AND status IN ('ok', 'partial') ORDER BY started_at DESC LIMIT 1",
        (TASK, document_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _running_run(cur, document_id: str) -> dict | None:
    cur.execute(
        "SELECT id FROM jobber.extraction_run WHERE task = %s AND subject_type = 'document' AND document_id = %s "
        "AND status = 'running' ORDER BY started_at DESC LIMIT 1",
        (TASK, document_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _mark_run_failed(run_id: str, *, error_type: str, error_message: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.extraction_run SET status = 'failed', finished_at = now(), "
            "error_type = %s, error_message = %s WHERE id = %s",
            (error_type, error_message, run_id),
        )


def _persist_draft_observations(document_id: str, run_id: str, items, ai_run) -> dict:
    """One short atomic transaction: every draft row plus the run's own
    output/status linkage. A currency is upper-cased for consistency with
    the posting backfill's convention; never otherwise normalised or
    converted (prompt §16: no FX conversion, ever)."""
    created = skipped_incomplete = 0
    with db_cursor() as cur:
        for i, item in enumerate(items):
            currency = (item.currency or "").strip().upper() or None
            if item.component not in _VALID_COMPONENTS or item.pay_period not in _VALID_PAY_PERIODS or not currency:
                skipped_incomplete += 1
                continue
            employment_basis = item.employment_basis if item.employment_basis in _VALID_EMPLOYMENT_BASES else None
            cur.execute(
                """
                INSERT INTO jobber.compensation_observation
                    (source_key, raw_role_label, component, pay_period, employment_basis, currency,
                     amount_min, amount_mid, amount_max, reported_p25, reported_p50, reported_p75, bonus_pct,
                     basis, review_status, document_id, page_reference, table_reference, source_note,
                     reported_sample_size, extraction_run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'survey', 'unreviewed', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_key) DO NOTHING
                RETURNING id
                """,
                (
                    f"survey:{run_id}:{i}", item.raw_role_label, item.component, item.pay_period,
                    employment_basis, currency, item.amount_min, item.amount_mid, item.amount_max,
                    item.reported_p25, item.reported_p50, item.reported_p75, item.bonus_pct,
                    document_id, item.page_reference, item.table_reference, item.source_note,
                    item.reported_sample_size, run_id,
                ),
            )
            created += 1 if cur.fetchone() else 0

        run_status = "ok" if created else "partial"
        cur.execute(
            "UPDATE jobber.extraction_run SET status = %s, finished_at = now(), output_payload = %s, "
            "model = %s, prompt_version = %s, input_chars = %s, output_chars = %s WHERE id = %s",
            (
                run_status,
                to_json_param({"items_extracted": len(items), "items_created": created, "items_skipped_incomplete": skipped_incomplete}),
                ai_run.model, ai_run.prompt_version, ai_run.input_chars, ai_run.output_chars, run_id,
            ),
        )
    return {"created": created, "skipped_incomplete": skipped_incomplete, "status": run_status}


def process_market_data_document(document_id: str) -> dict:
    """Explicit, on-demand extraction only — never triggered by viewing a
    document. A document that already has a completed (ok/partial) run for
    this task, or one another attempt currently owns, is not reprocessed —
    review the existing draft observations, or reject them first, rather
    than accumulating repeat AI passes over the same report."""
    with db_cursor() as cur:
        document = _load_document(cur, document_id)

    model, pversion = _safe_task_metadata()

    with db_cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", (document_id, TASK))
        blocking = _successful_run(cur, document_id) or _running_run(cur, document_id)
        if blocking is not None:
            return {"document_id": document_id, "extraction_run_id": str(blocking["id"]), "status": "already_processed"}

        started_at = datetime.now(timezone.utc)
        cur.execute(
            """
            INSERT INTO jobber.extraction_run
                (task, subject_type, document_id, model, prompt_name, prompt_version, vocabulary_version_id, started_at, status)
            VALUES (%s, 'document', %s, %s, %s, %s, NULL, %s, 'running')
            RETURNING id
            """,
            (TASK, document_id, model, PROMPT_NAME, pversion, started_at),
        )
        run_id = str(cur.fetchone()["id"])
    # committed here — before run_json_task is ever called.

    try:
        ai_result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=document["content_text"], output_model=MarketSurveyExtractionResult,
        )
    except AITaskError as e:
        _mark_run_failed(run_id, error_type=type(e).__name__, error_message=str(e))
        return {"document_id": document_id, "extraction_run_id": run_id, "status": "failed", "error": str(e), "error_type": type(e).__name__}

    try:
        persisted = _persist_draft_observations(document_id, run_id, ai_result.output.items, ai_result.run)
    except Exception as e:
        _mark_run_failed(run_id, error_type=type(e).__name__, error_message=f"persistence failed: {e}")
        return {"document_id": document_id, "extraction_run_id": run_id, "status": "failed", "error": str(e), "error_type": type(e).__name__}

    return {
        "document_id": document_id,
        "extraction_run_id": run_id,
        "status": persisted["status"],
        "observations_created": persisted["created"],
        "items_skipped_incomplete": persisted["skipped_incomplete"],
        "model": ai_result.run.model,
    }
