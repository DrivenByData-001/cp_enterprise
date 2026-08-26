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
from ..db import upsert_job_role
from ..embeddings import dumps_vec, embed_text, embedding_model_name
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


def posting_columns(payload: JobPostingImport) -> dict:
    job, meta, analysis = payload.job, payload.metadata, payload.analysis
    text = compose_role_text(job, analysis)
    vector = embed_text(text) if text else []

    return {
        "node_type": "posting",
        "title": job.title,
        "organisation": job.organisation,
        "location": job.location,
        "country": job.country,
        "remote_type": job.remote_type,
        "employment_type": job.employment_type,
        "seniority_level": job.seniority_level,
        "posting_date": job.posting_date,
        "captured_at": meta.captured_at or datetime.now(timezone.utc).isoformat(),
        "source": meta.source,
        "url": meta.url,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_estimate_min": analysis.salary_estimate_min,
        "salary_estimate_max": analysis.salary_estimate_max,
        "currency": job.currency,
        "description": job.description,
        "requirements": job.requirements,
        "responsibilities": job.responsibilities,
        "summary": analysis.summary,
        "key_skills_summary": analysis.key_skills_summary,
        "notes": analysis.notes,
        "career_track": analysis.career_track,
        "seniority_score": analysis.seniority_score,
        "complexity_score": analysis.complexity_score,
        "specialisation_score": analysis.specialisation_score,
        "transferability_score": analysis.transferability_score,
        "market_demand_score": analysis.market_demand_score,
        "rarity_score": analysis.rarity_score,
        "automation_risk_score": analysis.automation_risk_score,
        "top_adjacent_roles": json.dumps(analysis.top_adjacent_roles) if analysis.top_adjacent_roles else None,
        "extraction_status": meta.extraction_status,
        "extraction_notes": meta.notes_for_user,
        "raw_json": payload.model_dump_json(),
        "embedding": dumps_vec(vector) if vector else None,
        "embedding_model": embedding_model_name() if vector else None,
        "embedded_at": datetime.now(timezone.utc).isoformat() if vector else None,
    }


def _insert_posting(payload: JobPostingImport) -> int:
    skills = [s.model_dump() for s in payload.skills]
    return upsert_job_role(None, posting_columns(payload), skills)


@router.post("")
def import_posting(payload: JobPostingImport):
    role_id = _insert_posting(payload)
    return {"id": role_id, "status": "imported"}


@router.post("/native")
def import_posting_native(payload: NativePostingImport):
    """Raw posting text -> native AI extraction -> typed validation -> existing import path.

    The AI task layer (`app.ai`) never touches storage directly: it returns a
    validated `JobPostingImport`, which is then handed to the same
    `_insert_posting` used by the legacy JSON/bulk paths below. See
    docs/13-ai-task-layer.md for why `result.run` isn't persisted yet.
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
