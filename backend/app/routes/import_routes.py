import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile

from ..db import db_cursor
from ..embeddings import dumps_vec, embed_text, embedding_model_name
from ..models import JobPostingImport

router = APIRouter(prefix="/api/import", tags=["import"])


def _compose_role_text(job, analysis) -> str:
    parts = [
        job.title,
        job.description,
        job.requirements,
        job.responsibilities,
        analysis.key_skills_summary if analysis else None,
    ]
    return "\n\n".join(p for p in parts if p)


def _insert_posting(payload: JobPostingImport) -> int:
    job, meta, analysis = payload.job, payload.metadata, payload.analysis
    text = _compose_role_text(job, analysis)
    vector = embed_text(text) if text else []

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO job_roles (
                title, organisation, location, country, remote_type, employment_type,
                seniority_level, posting_date, captured_at, source, url,
                salary_min, salary_max, salary_estimate_min, salary_estimate_max, currency,
                description, requirements, responsibilities, summary, key_skills_summary, notes,
                career_track, seniority_score, complexity_score, specialisation_score,
                transferability_score, market_demand_score, rarity_score, automation_risk_score,
                top_adjacent_roles, extraction_status, extraction_notes, raw_json,
                embedding, embedding_model, embedded_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                job.title, job.organisation, job.location, job.country, job.remote_type,
                job.employment_type, job.seniority_level, job.posting_date,
                meta.captured_at or datetime.now(timezone.utc).isoformat(), meta.source, meta.url,
                job.salary_min, job.salary_max, analysis.salary_estimate_min,
                analysis.salary_estimate_max, job.currency,
                job.description, job.requirements, job.responsibilities, analysis.summary,
                analysis.key_skills_summary, analysis.notes,
                analysis.career_track, analysis.seniority_score, analysis.complexity_score,
                analysis.specialisation_score, analysis.transferability_score,
                analysis.market_demand_score, analysis.rarity_score,
                analysis.automation_risk_score,
                json.dumps(analysis.top_adjacent_roles) if analysis.top_adjacent_roles else None,
                meta.extraction_status, meta.notes_for_user, payload.model_dump_json(),
                dumps_vec(vector) if vector else None,
                embedding_model_name() if vector else None,
                datetime.now(timezone.utc).isoformat() if vector else None,
            ),
        )
        role_id = cur.lastrowid

        for skill in payload.skills:
            cur.execute(
                """
                INSERT INTO job_role_skills (job_role_id, name, category, importance, requirement_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (role_id, skill.name, skill.category, skill.importance, skill.requirement_type),
            )

    return role_id


@router.post("")
def import_posting(payload: JobPostingImport):
    role_id = _insert_posting(payload)
    return {"id": role_id, "status": "imported"}


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
