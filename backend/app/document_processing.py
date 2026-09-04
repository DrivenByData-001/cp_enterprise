"""CP Ent Phase 3B: raw `jobber.document` -> `job_posting_extract` ->
`jobber.role_instance`. See docs/17-document-processing-pipeline.md for the
full design.

`jobber.document` is immutable source evidence; `JobPostingImport` is the
validated analytical contract; `jobber.extraction_run` is the provenance
record of each processing attempt; `jobber.role_instance` is the derived
operational representation. This module is the only thing that turns an
*existing* raw document into a role — it never calls `db.create_document()`,
and never modifies `content_text`/`source_payload`/`source_date` on the
document it processes.

Entry points:
- `process_job_posting_document(document_id)` — the service function (brief
  §16): one document, full running -> ok/partial/failed lifecycle,
  concurrency-protected, safe to call repeatedly.
- `document_processing_state(cur, document_id)` — derived status for one
  document (brief §5); never stored on `document` itself.
- `job_posting_processing_counts(cur, ...)` — aggregate counts for a status
  dashboard (brief §19).
- `list_eligible_documents(cur, ...)` — the selection query the batch CLI
  (`backend/scripts/process_job_documents.py`) and the counts helper share.
"""

import json
from datetime import datetime, timezone

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import db_cursor, to_json_param, upsert_role_instance
from .embeddings import embed_text, set_embedding
from .models import JobPostingImport
from .posting_persistence import posting_role_columns

TASK = "job_posting_extract"
PROMPT_NAME = "extract_job_posting.md"


class DocumentProcessingError(ValueError):
    """A document_id this task cannot run against — a 4xx at the route
    layer. No extraction_run row is ever written for one of these: no attempt
    was actually made, so there is nothing to record as a failed run."""


class DocumentNotFoundError(DocumentProcessingError):
    """No jobber.document with this id."""


class DocumentNotProcessableError(DocumentProcessingError):
    """The document exists but isn't eligible for job_posting_extract (wrong
    `kind`, or no usable `content_text`)."""


def _safe_task_metadata() -> tuple[str, str]:
    """Best-effort model/prompt_version even when run_json_task never got far
    enough to return one (e.g. a missing OPENAI_API_KEY) — mirrors
    extraction.py's `_safe_task_metadata`; must never itself raise, since the
    point of calling this is to still record *something* on a failed run."""
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
    cur.execute(
        "SELECT id, kind, title, source, url, source_date, content_text "
        "FROM jobber.document WHERE id = %s",
        (document_id,),
    )
    row = cur.fetchone()
    if not row:
        raise DocumentNotFoundError(f"document {document_id!r} not found")
    document = dict(row)
    document["id"] = str(document["id"])
    if document["kind"] != "job_posting":
        raise DocumentNotProcessableError(
            f"document.kind must be 'job_posting' for {TASK}, got {document['kind']!r}"
        )
    if not (document.get("content_text") or "").strip():
        raise DocumentNotProcessableError("document has no usable content_text")
    return document


# --- extraction_run lookups (duplicate-processing protection §14, state
# derivation §5) — all scoped to (task='job_posting_extract',
# subject_type='document', document_id). --------------------------------


def _successful_run(cur, document_id: str) -> dict | None:
    """The literal §14 check: a run of this task against this document that
    actually produced a role, regardless of whether it's the *most recent*
    attempt (a later, unrelated retry must never shadow an earlier success)."""
    cur.execute(
        """
        SELECT id, result_role_instance_id FROM jobber.extraction_run
        WHERE task = %s AND subject_type = 'document' AND document_id = %s
          AND result_role_instance_id IS NOT NULL
        ORDER BY started_at DESC LIMIT 1
        """,
        (TASK, document_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _running_run(cur, document_id: str) -> dict | None:
    cur.execute(
        """
        SELECT id, result_role_instance_id FROM jobber.extraction_run
        WHERE task = %s AND subject_type = 'document' AND document_id = %s AND status = 'running'
        ORDER BY started_at DESC LIMIT 1
        """,
        (TASK, document_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _latest_run(cur, document_id: str) -> dict | None:
    cur.execute(
        """
        SELECT id, status, result_role_instance_id FROM jobber.extraction_run
        WHERE task = %s AND subject_type = 'document' AND document_id = %s
        ORDER BY started_at DESC LIMIT 1
        """,
        (TASK, document_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def document_processing_state(cur, document_id: str) -> str:
    """raw | running | analysed | partial | failed (brief §5) — always
    derived fresh from extraction_run history, never stored on
    jobber.document. Keyed off the *latest* attempt's own outcome, per §5's
    literal wording ("latest attempted ... failed" / "latest attempt is
    partial"): a 'partial' run also sets result_role_instance_id (a role was
    still created), so this must not collapse into 'analysed' the way the
    duplicate-processing check (`_successful_run`, §14) deliberately does —
    that check treats ok/partial alike (both mean "don't reprocess"), but
    display must keep 'partial' visibly distinct so it actually gets
    attention. Safe to key off "latest" alone (rather than also checking
    `_successful_run`) because `process_job_posting_document` never starts a
    new attempt once any run has produced a role — so whenever a role
    exists, it is always the latest run's own doing."""
    latest = _latest_run(cur, document_id)
    if latest is None:
        return "raw"
    if latest["status"] == "running":
        return "running"
    if latest["status"] == "partial":
        return "partial"
    if latest["status"] == "ok" and latest["result_role_instance_id"]:
        return "analysed"
    return "failed"


def job_posting_processing_counts(cur, *, source_prefix: str | None = None) -> dict:
    """raw/running/analysed/partial/failed counts over jobber.document
    (kind='job_posting'), optionally scoped by source_key prefix (brief §19
    — "usable later for a dashboard"). Derived live, in one query, from the
    same latest-attempt precedence as `document_processing_state` (see that
    function's docstring for why "latest" alone is the correct thing to key
    display state off of)."""
    clauses = ["d.kind = 'job_posting'"]
    params: list = []
    if source_prefix:
        clauses.append("d.source_key LIKE %s")
        params.append(f"{source_prefix}%")
    where_sql = " AND ".join(clauses)

    cur.execute(
        f"""
        SELECT latest.status AS latest_status, latest.result_role_instance_id AS latest_result_role_instance_id
        FROM jobber.document d
        LEFT JOIN LATERAL (
            SELECT er.status, er.result_role_instance_id
            FROM jobber.extraction_run er
            WHERE er.task = %s AND er.subject_type = 'document' AND er.document_id = d.id
            ORDER BY er.started_at DESC LIMIT 1
        ) latest ON true
        WHERE {where_sql}
        """,
        [TASK, *params],
    )

    counts = {"raw": 0, "running": 0, "analysed": 0, "partial": 0, "failed": 0}
    for row in cur.fetchall():
        status = row["latest_status"]
        if status is None:
            counts["raw"] += 1
        elif status == "running":
            counts["running"] += 1
        elif status == "partial":
            counts["partial"] += 1
        elif status == "ok" and row["latest_result_role_instance_id"]:
            counts["analysed"] += 1
        else:
            counts["failed"] += 1
    counts["total"] = sum(counts.values())
    return counts


def list_eligible_documents(
    cur,
    *,
    source_prefix: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    retry_failed: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Selection query for the batch CLI (brief §17): raw documents, plus
    previously-*failed*-only ones when `retry_failed` — never a document that
    has ever succeeded (§14), never one currently 'running', and (deliberately
    conservative, §14) never one whose latest attempt was 'partial' — that
    already produced a role, and reanalysing a document that already has a
    role is exactly the "successful-role reanalysis" this phase defers.
    Ordered oldest `source_date` first, NULLs last, so a bounded `--limit`
    naturally covers the earliest slice of the corpus first.
    """
    clauses = ["d.kind = 'job_posting'"]
    params: list = []
    if source_prefix:
        clauses.append("d.source_key LIKE %s")
        params.append(f"{source_prefix}%")
    if date_from:
        clauses.append("d.source_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("d.source_date <= %s")
        params.append(date_to)
    where_sql = " AND ".join(clauses)

    cur.execute(
        f"""
        SELECT d.id, d.title, d.source_date,
            succeeded.result_role_instance_id IS NOT NULL AS has_succeeded,
            latest.status AS latest_status
        FROM jobber.document d
        LEFT JOIN LATERAL (
            SELECT er.result_role_instance_id
            FROM jobber.extraction_run er
            WHERE er.task = %s AND er.subject_type = 'document' AND er.document_id = d.id
              AND er.result_role_instance_id IS NOT NULL
            ORDER BY er.started_at DESC LIMIT 1
        ) succeeded ON true
        LEFT JOIN LATERAL (
            SELECT er.status
            FROM jobber.extraction_run er
            WHERE er.task = %s AND er.subject_type = 'document' AND er.document_id = d.id
            ORDER BY er.started_at DESC LIMIT 1
        ) latest ON true
        WHERE {where_sql}
        ORDER BY d.source_date NULLS LAST, d.id
        """,
        [TASK, TASK, *params],
    )

    eligible = []
    for row in cur.fetchall():
        if row["has_succeeded"]:
            continue
        status = row["latest_status"]
        if status is None:
            eligible.append(row)
        elif status == "failed" and retry_failed:
            eligible.append(row)
    if limit is not None:
        eligible = eligible[:limit]
    return [{**dict(r), "id": str(r["id"])} for r in eligible]


# --- historical-context input construction (brief §8) ----------------------


def _build_input_text(document: dict) -> str:
    """Explicit known metadata, then the deterministic historical-context
    instruction the prompt now defines, then the verbatim advert — exactly
    the shape brief §8 specifies. Metadata lines are included only when the
    document actually carries them (many historical captures have no known
    source/URL) — the instruction itself still applies with no date known,
    the prompt's own HISTORICAL CONTEXT section covers that case."""
    lines = []
    if document.get("source_date"):
        lines.append(f"Known original posting date: {document['source_date']}")
    if document.get("source"):
        lines.append(f"Known source: {document['source']}")
    if document.get("title"):
        lines.append(f"Original listing title: {document['title']}")
    if document.get("url"):
        lines.append(f"Known source URL: {document['url']}")

    parts = []
    if lines:
        parts.append("\n".join(lines))
    parts.append(
        "Historical analysis instruction:\n"
        "Interpret the advertised role substantially in the professional and labour-market "
        "context of its original posting date. Do not silently apply present-day assumptions."
    )
    parts.append(f"Original advert text:\n{document['content_text']}")
    return "\n\n".join(parts)


def _enforce_historical_extraction_policy(payload: JobPostingImport, document: dict) -> None:
    """Deterministic backstop for the historical-extraction policy (CP Ent
    Phase 3B 0.2, docs/17 §8): the prompt's HISTORICAL CONTEXT section
    already instructs the model to defer these fields for a historical
    posting, but a model response is never a guarantee — this clamps the
    contract regardless of what actually came back, so a non-compliant
    response can never leak present-day market judgment, or a
    stated-salary-copied-as-an-estimate, into historical data.

    Fires only when the *document* itself carries a known historical
    original posting date (`document['source_date']`) — the same signal
    `_build_input_text` uses to tell the model it is dealing with dated
    historical text in the first place. An ordinary current-posting
    extraction has no known `source_date` and is never touched by this
    function.

    Structural judgments (seniority/complexity/specialisation/
    transferability/rarity) are deliberately left untouched — they describe
    the role's own documented shape, not the external labour market, and
    remain valid historical judgments (brief step 4)."""
    if not (document.get("source_date") or ""):
        return

    analysis = payload.analysis
    analysis.market_demand_score = None
    analysis.automation_risk_score = None
    analysis.top_adjacent_roles = None

    # A "salary estimate" that just restates the advert's own stated salary
    # is not an estimate at all — collapse that specific failure mode
    # deterministically. Once a stated figure is a known fact, any
    # "estimate" of the same figure is redundant, so the estimate field is
    # always null wherever its factual counterpart is known; an estimate
    # grounded in text where no salary is stated is left as the model
    # judged it, per the prompt's narrow carve-out.
    if payload.job.salary_min is not None:
        analysis.salary_estimate_min = None
    if payload.job.salary_max is not None:
        analysis.salary_estimate_max = None


def _apply_known_metadata(payload: JobPostingImport, document: dict) -> None:
    """Fill fields the model left blank with what the source document itself
    already establishes — never overwrites a value the model actually
    provided (brief §16 step 8). `document.source_date`/`title`/`source`/`url`
    are themselves never modified by this — this only fills gaps in the
    *model output*, which is not yet persisted anywhere."""
    if document.get("source_date") and not (payload.job.posting_date or "").strip():
        payload.job.posting_date = str(document["source_date"])
    if document.get("title") and not (payload.job.title or "").strip():
        payload.job.title = document["title"]
    if document.get("source") and not (payload.metadata.source or "").strip():
        payload.metadata.source = document["source"]
    if document.get("url") and not (payload.metadata.url or "").strip():
        payload.metadata.url = document["url"]


def _has_substantive_job_content(payload: JobPostingImport) -> bool:
    """Whether the validated extraction contains content that warrants skills."""
    return any(
        (value or "").strip()
        for value in (
            payload.job.description,
            payload.job.requirements,
            payload.job.responsibilities,
        )
    )


# --- persistence (brief §11/§12) --------------------------------------------


def _mark_run_failed(run_id: str, *, error_type: str, error_message: str) -> None:
    """A fresh, subsequent transaction (§6/§12) — never reuses a transaction
    that may itself have just failed."""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.extraction_run SET status = 'failed', finished_at = now(), "
            "error_type = %s, error_message = %s WHERE id = %s",
            (error_type, error_message, run_id),
        )


def _persist_success(document_id: str, run_id: str, run_status: str, payload: JobPostingImport, ai_run) -> str:
    """§12: role_instance + skills (inside upsert_role_instance) +
    extraction_run's output/result linkage, in one short transaction. `role
    against existing document` (§10) — document_id is passed straight through
    to posting_role_columns; create_document() is never called here. Raises
    on any failure; a mid-block exception rolls the *whole* transaction back
    (db_cursor()'s documented commit-on-success/rollback-on-exception
    behaviour), so a role is never left dangling with no run pointing at it,
    and the run is never left 'ok' pointing at an incomplete role — the
    caller marks the run 'failed' afterwards, in a fresh transaction."""
    columns = posting_role_columns(payload, document_id)
    skills = [s.model_dump() for s in payload.skills]
    output_json = json.loads(payload.model_dump_json())
    with db_cursor() as cur:
        role_id = upsert_role_instance(cur, None, columns, skills)
        cur.execute(
            """
            UPDATE jobber.extraction_run SET
                status = %s, finished_at = now(), output_payload = %s,
                result_role_instance_id = %s, model = %s, prompt_version = %s,
                input_chars = %s, output_chars = %s
            WHERE id = %s
            """,
            (
                run_status, to_json_param(output_json), role_id,
                ai_run.model, ai_run.prompt_version, ai_run.input_chars, ai_run.output_chars,
                run_id,
            ),
        )
    return role_id


def _attempt_role_embedding(role_id: str, document: dict) -> str | None:
    """Best-effort, after the atomic transaction above has already committed
    (brief §13): embeds from the raw `document.content_text` — the same
    "prefer the document's own verbatim text" preference
    `embeddings.rebuild_role_embeddings` already uses for migrated roles, not
    the AI-composed analysis text `compose_role_text` builds for the legacy/
    native import paths. Returns an error string on failure, never raises —
    a transient embedding-model problem must not invalidate an already-
    persisted successful extraction; the role stays eligible for the existing
    `rebuild_role_embeddings` backfill either way."""
    try:
        vector = embed_text(document["content_text"])
        if not vector:
            return None
        with db_cursor() as cur:
            set_embedding(cur, "role_instance", role_id, vector)
        return None
    except Exception as e:  # noqa: BLE001 - genuinely must never propagate, see docstring
        return f"{type(e).__name__}: {e}"


def _result(
    *,
    document_id: str,
    extraction_run_id: str | None,
    status: str,
    role_instance_id: str | None = None,
    error: str | None = None,
    error_type: str | None = None,
    embedding_error: str | None = None,
    model: str | None = None,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    output_payload: dict | None = None,
) -> dict:
    return {
        "document_id": document_id,
        "extraction_run_id": extraction_run_id,
        "status": status,
        "role_instance_id": role_instance_id,
        "error": error,
        "error_type": error_type,
        "embedding_error": embedding_error,
        "model": model,
        "prompt_name": prompt_name,
        "prompt_version": prompt_version,
        "output_payload": output_payload,
    }


def process_job_posting_document(document_id: str) -> dict:
    """The service function (brief §16). Idempotent/resumable: a document
    that already succeeded is skipped (`status='already_analysed'`); one
    another attempt currently owns is skipped too
    (`status='already_processing'`); a document with only failed attempts (or
    none) is (re)tried. Never calls `db.create_document()` — always processes
    the existing document named by `document_id`.

    Concurrency (§15): the claim step below (advisory-lock, re-check,
    insert-running-row) is one short transaction. Two concurrent callers for
    the same document_id serialise on `pg_advisory_xact_lock`; whichever
    commits its 'running' row first wins, and the second sees that row once
    it acquires the lock and returns 'already_processing' instead of
    creating a second running attempt — this is what actually prevents two
    workers from ever creating two roles from the same document, not merely
    the SELECT-then-INSERT check by itself.

    Lifecycle (§6): the running row is inserted and *committed* before the
    AI provider is ever called — no Postgres transaction is held open across
    that network call. A provider/validation failure updates that same row
    to 'failed' in a fresh transaction. A successful, validated
    JobPostingImport is persisted (§12) in one short atomic transaction
    together with the run's own output/result linkage. Embedding (§13) is a
    best-effort step after that transaction has already committed, and
    cannot roll back a successful extraction.
    """
    with db_cursor() as cur:
        document = _load_document(cur, document_id)

    model, pversion = _safe_task_metadata()

    with db_cursor() as cur:
        # Transaction-scoped advisory lock keyed on (document, task): held
        # only for this short claim transaction, never across the AI call.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", (document_id, TASK))

        blocking = _successful_run(cur, document_id) or _running_run(cur, document_id)
        if blocking is not None:
            already_analysed = blocking.get("result_role_instance_id") is not None
            return _result(
                document_id=document_id,
                extraction_run_id=str(blocking["id"]),
                status="already_analysed" if already_analysed else "already_processing",
                role_instance_id=str(blocking["result_role_instance_id"]) if already_analysed else None,
            )

        started_at = datetime.now(timezone.utc)
        cur.execute(
            """
            INSERT INTO jobber.extraction_run
                (task, subject_type, document_id, model, prompt_name, prompt_version,
                 vocabulary_version_id, started_at, status)
            VALUES (%s, 'document', %s, %s, %s, %s, NULL, %s, 'running')
            RETURNING id
            """,
            (TASK, document_id, model, PROMPT_NAME, pversion, started_at),
        )
        run_id = str(cur.fetchone()["id"])
    # committed here: the running row is durable and visible to any other
    # worker before this call ever touches the network.

    try:
        ai_result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=_build_input_text(document), output_model=JobPostingImport,
        )
    except AITaskError as e:
        _mark_run_failed(run_id, error_type=type(e).__name__, error_message=str(e))
        return _result(
            document_id=document_id, extraction_run_id=run_id, status="failed",
            error=str(e), error_type=type(e).__name__, model=model, prompt_name=PROMPT_NAME, prompt_version=pversion,
        )

    payload = ai_result.output
    _apply_known_metadata(payload, document)
    _enforce_historical_extraction_policy(payload, document)
    run_status = "ok" if (payload.metadata.extraction_status or "ok") == "ok" else "partial"
    if not payload.skills and _has_substantive_job_content(payload):
        run_status = "partial"

    try:
        role_id = _persist_success(document_id, run_id, run_status, payload, ai_result.run)
    except Exception as e:
        _mark_run_failed(run_id, error_type=type(e).__name__, error_message=f"structured persistence failed: {e}")
        return _result(
            document_id=document_id, extraction_run_id=run_id, status="failed",
            error=str(e), error_type=type(e).__name__,
            model=ai_result.run.model, prompt_name=PROMPT_NAME, prompt_version=ai_result.run.prompt_version,
        )

    embedding_error = _attempt_role_embedding(role_id, document)

    return _result(
        document_id=document_id, extraction_run_id=run_id, status=run_status, role_instance_id=role_id,
        embedding_error=embedding_error, model=ai_result.run.model, prompt_name=PROMPT_NAME,
        prompt_version=ai_result.run.prompt_version, output_payload=json.loads(payload.model_dump_json()),
    )
