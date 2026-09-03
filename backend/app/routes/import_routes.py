import json
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..ai import (
    AIConfigError,
    AIProviderError,
    AIResponseFormatError,
    AISchemaValidationError,
    run_json_task,
)
from ..db import db_cursor, get_or_create_document, upsert_role_instance
from ..embeddings import embed_text, set_embedding
from ..models import JobPostingImport

router = APIRouter(prefix="/api/import", tags=["import"])


class NativePostingImport(BaseModel):
    text: str
    source_url: str | None = None
    known_posting_date: str | None = None


def compose_role_text(job, analysis) -> str:
    parts = [
        job.title,
        job.description,
        job.requirements,
        job.responsibilities,
        analysis.key_skills_summary if analysis else None,
    ]
    return "\n\n".join(p for p in parts if p)


def posting_columns(cur, payload: JobPostingImport) -> dict:
    """Builds the flat column dict `upsert_role_instance` expects, plus — new
    in Phase 2 — a `jobber.document` row backing the posting whenever there is
    real posting text to capture (brief §4: "retain whenever available:
    original advert text..."). `provenance='original_capture'`: this path is
    always a fresh user paste of a real posting, never a reconstruction — the
    weaker `legacy_extracted` provenance only ever applies to the 23
    pre-existing migrated rows (docs/14 §4), never to anything this code path
    writes.
    """
    job, meta, analysis = payload.job, payload.metadata, payload.analysis
    text = compose_role_text(job, analysis)

    document_id = None
    if text.strip():
        document_id, _ = get_or_create_document(
            cur,
            kind="job_posting",
            body=text,
            provenance="original_capture",
            title=job.title,
            source=meta.source,
            url=meta.url,
            document_date=job.posting_date,
        )

    return {
        "kind": "posting",
        "document_id": document_id,
        "title": job.title,
        "organisation": job.organisation,
        "location": job.location,
        "country": job.country,
        "remote_type": job.remote_type,
        "employment_type": job.employment_type,
        "seniority_level": job.seniority_level,
        "posting_date": job.posting_date,
        "captured_at": meta.captured_at or datetime.now(timezone.utc).isoformat(),
        "url": meta.url,
        "summary": analysis.summary,
        "career_track": analysis.career_track,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_estimate_min": analysis.salary_estimate_min,
        "salary_estimate_max": analysis.salary_estimate_max,
        "currency": job.currency,
        "key_skills_summary": analysis.key_skills_summary,
        "description": job.description,
        "requirements": job.requirements,
        "responsibilities": job.responsibilities,
        "notes": analysis.notes,
        "seniority_score": analysis.seniority_score,
        "complexity_score": analysis.complexity_score,
        "specialisation_score": analysis.specialisation_score,
        "transferability_score": analysis.transferability_score,
        "market_demand_score": analysis.market_demand_score,
        "rarity_score": analysis.rarity_score,
        "automation_risk_score": analysis.automation_risk_score,
        "top_adjacent_roles": analysis.top_adjacent_roles,
        "extraction_status": meta.extraction_status,
        "extraction_notes": meta.notes_for_user,
        "raw_json": json.loads(payload.model_dump_json()),
        "_embedding_text": text,
    }


def _insert_posting(payload: JobPostingImport) -> int:
    skills = [s.model_dump() for s in payload.skills]
    with db_cursor() as cur:
        columns = posting_columns(cur, payload)
        embedding_text = columns.pop("_embedding_text")
        role_id = upsert_role_instance(cur, None, columns, skills)
        vector = embed_text(embedding_text) if embedding_text else []
        if vector:
            set_embedding(cur, "role_instance", role_id, vector)
    return role_id


@router.post("")
def import_posting(payload: JobPostingImport):
    role_id = _insert_posting(payload)
    return {"id": role_id, "status": "imported"}


@router.post("/native")
def import_posting_native(payload: NativePostingImport):
    """Raw posting text -> native AI extraction -> typed validation -> existing import path.

    The AI task layer (`app.ai`) never touches storage directly: it returns a
    validated `JobPostingImport`, which is then handed to the same
    `_insert_posting` used by the legacy JSON/bulk paths below.
    """
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Posting text is required")
    context = [f"Posting text:\n{payload.text.strip()}"]
    if payload.source_url:
        context.append(f"Source URL: {payload.source_url}")
    if payload.known_posting_date:
        context.append(f"Known posting date: {payload.known_posting_date}")

    try:
        result = run_json_task(
            task="job_posting_extract",
            prompt_name="extract_job_posting.md",
            user_input="\n\n".join(context),
            output_model=JobPostingImport,
        )
    except AIConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except AIProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except (AIResponseFormatError, AISchemaValidationError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if payload.source_url and not result.output.metadata.url:
        result.output.metadata.url = payload.source_url
    if payload.known_posting_date and not result.output.job.posting_date:
        result.output.job.posting_date = payload.known_posting_date

    role_id = _insert_posting(result.output)
    return {
        "id": role_id,
        "status": "imported",
        "extraction": result.output,
        "run": asdict(result.run),
    }


@router.post("/bulk")
async def import_bulk(files: list[UploadFile]):
    results = []
    for f in files:
        try:
            raw = await f.read()
            data = json.loads(raw)
            payload = JobPostingImport.model_validate(data)
            role_id = _insert_posting(payload)
            results.append({"file": f.filename, "id": role_id, "status": "imported"})
        except Exception as e:
            results.append({"file": f.filename, "status": "error", "error": str(e)})
    return {"results": results}
