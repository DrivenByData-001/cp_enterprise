"""Lightweight, reviewable metadata enrichment for source-aware roles
(source-aware ingest cleanup, problem #5). Deliberately narrow: proposes only
title/organisation/location/country/remote_type/employment_type/
seniority_level/posting_date from the role's own linked source document —
never a description/requirements/skills extraction (that stays
`app.extraction.extract_role_requirements`'s job, a separate explicit
action).

Never persists anything by itself — `propose_role_metadata` only returns a
proposal for the user to review/edit; writing it onto the role_instance
happens through `app.db.update_role_metadata`, the same endpoint manual Edit
uses, only once the user explicitly accepts (brief: "AI-derived metadata must
not silently become authoritative without review").

Built on `app.ai.run_json_task`, same provider-agnostic task layer every
other AI feature in this app uses — no new provider, no new failure-handling
pattern."""

from datetime import datetime, timezone

from .ai import AIConfigError, AITaskError, ai_model_name, load_prompt, prompt_version, run_json_task
from .db import to_json_param
from .models import RoleMetadataProposal

TASK = "role_metadata_enrich"
PROMPT_NAME = "enrich_role_metadata.md"


class MetadataEnrichmentSubjectError(ValueError):
    """No such role_instance, or it has no linked source document to
    propose metadata from — a 4xx at the route layer, not a failed AI run."""


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


def propose_role_metadata(cur, role_instance_id: str) -> dict:
    """Runs the metadata-only extraction against one role's linked document
    and records an extraction_run row, success or failure — same provenance
    convention as extract_role_requirements/document_processing. Never
    writes to role_instance itself: the caller reviews (and may edit) the
    proposal before accepting it via PATCH /api/role-instances/{id}/metadata.

    Note what is deliberately *not* given to the model: the document's own
    `captured_at`/upload time. The prompt has no "today" to reason from, so
    it cannot substitute a capture date for a genuinely unstated posting
    date — the only source of `posting_date` here is the source text itself,
    or null."""
    cur.execute("SELECT id, document_id FROM jobber.role_instance WHERE id = %s", (role_instance_id,))
    role = cur.fetchone()
    if not role:
        raise MetadataEnrichmentSubjectError("role_instance not found")
    if not role["document_id"]:
        raise MetadataEnrichmentSubjectError("this role_instance has no source document — nothing to propose metadata from")

    cur.execute("SELECT id, content_text FROM jobber.document WHERE id = %s", (role["document_id"],))
    document = cur.fetchone()
    document_id = str(document["id"])
    content_text = (document["content_text"] or "").strip()
    if not content_text:
        raise MetadataEnrichmentSubjectError("the linked source document has no usable content_text")

    started_at = datetime.now(timezone.utc)
    model, pversion = _safe_task_metadata()

    try:
        result = run_json_task(
            task=TASK, prompt_name=PROMPT_NAME, user_input=content_text, output_model=RoleMetadataProposal,
        )
    except AITaskError as e:
        run_id = _record_run(
            cur, role_instance_id=role_instance_id, document_id=document_id, model=model, pversion=pversion,
            started_at=started_at, status="failed", error_type=type(e).__name__, error_message=str(e),
            input_chars=len(content_text), output_payload=None,
        )
        return {
            "status": "failed", "extraction_run_id": run_id, "error": str(e),
            "error_type": type(e).__name__, "proposal": None,
        }

    proposal = result.output.model_dump()
    run_id = _record_run(
        cur, role_instance_id=role_instance_id, document_id=document_id,
        model=result.run.model, pversion=result.run.prompt_version, started_at=started_at, status="ok",
        input_chars=result.run.input_chars, output_chars=result.run.output_chars, output_payload=proposal,
    )
    return {"status": "ok", "extraction_run_id": run_id, "error": None, "error_type": None, "proposal": proposal}


def _record_run(
    cur,
    *,
    role_instance_id: str,
    document_id: str,
    model: str,
    pversion: str,
    started_at: datetime,
    status: str,
    input_chars: int,
    output_chars: int | None = None,
    output_payload: dict | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> str:
    cur.execute(
        """
        INSERT INTO jobber.extraction_run
            (task, subject_type, document_id, role_instance_id, model, prompt_name, prompt_version,
             started_at, finished_at, status, input_chars, output_chars, output_payload,
             error_type, error_message)
        VALUES (%s, 'role_instance', %s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            TASK, document_id, role_instance_id, model, PROMPT_NAME, pversion,
            started_at, status, input_chars, output_chars, to_json_param(output_payload),
            error_type, error_message,
        ),
    )
    return str(cur.fetchone()["id"])
