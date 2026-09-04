import json

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import create_document, db_cursor, upsert_role_instance
from ..document_processing import DocumentNotProcessableError, process_job_posting_document
from ..embeddings import embed_text, role_embedding_text, set_embedding
from ..models import JobPostingImport
from ..posting_persistence import posting_role_columns

router = APIRouter(prefix="/api/import", tags=["import"])


class NativePostingImport(BaseModel):
    text: str
    source_url: str | None = None
    known_posting_date: str | None = None


def compose_role_text(job, analysis) -> str:
    """Thin wrapper around the canonical `embeddings.role_embedding_text` —
    kept as its own named function since callers here work with the
    not-yet-stored `Job`/`Analysis` payload objects, not a stored row."""
    return role_embedding_text(
        {
            "node_type": "posting",
            "title": job.title,
            "description": job.description,
            "requirements": job.requirements,
            "responsibilities": job.responsibilities,
            "key_skills_summary": analysis.key_skills_summary if analysis else None,
        }
    )


def posting_columns(cur, payload: JobPostingImport) -> dict:
    """Builds the flat column dict `upsert_role_instance` expects, using
    jobber.role_instance's real production columns (docs/14 §5) — including
    packing the pre-capability-model scores/analysis fields into
    `legacy_scores`/`legacy_analysis` JSONB, since production has no
    individual column per score. Also creates a `jobber.document` row
    backing the posting whenever there is real posting text to capture
    (brief §4: "retain whenever available: original advert text...").
    `provenance_quality='original'`: this path is always a fresh user paste
    of a real posting, never a reconstruction.
    """
    job, meta, analysis = payload.job, payload.metadata, payload.analysis
    text = compose_role_text(job, analysis)

    document_id = None
    if text.strip():
        document_id, _duplicate_of = create_document(
            cur,
            kind="job_posting",
            content_text=text,
            provenance_quality="original",
            title=job.title,
            source=meta.source,
            url=meta.url,
            source_date=job.posting_date,
        )

    columns = posting_role_columns(payload, document_id)
    columns["_embedding_text"] = text
    return columns


def _insert_posting(payload: JobPostingImport) -> str:
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


_NATIVE_ERROR_STATUS = {
    "AIConfigError": 503,
    "AIProviderError": 502,
    "AIResponseFormatError": 422,
    "AISchemaValidationError": 422,
}


@router.post("/native")
def import_posting_native(payload: NativePostingImport):
    """Raw-first (docs/17 §10/§20): the actual pasted text becomes the
    immutable `jobber.document.content_text` *before* any AI call, and the
    same document-processing service the historical corpus uses
    (`app.document_processing.process_job_posting_document`) turns that
    document into a role — rather than the previous flow, which ran
    extraction first and only ever captured the model's *composed* summary
    text as the document. No insertion or extraction-run-recording logic is
    duplicated between this path and that service.
    """
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Posting text is required")

    with db_cursor() as cur:
        document_id, _duplicate_of = create_document(
            cur,
            kind="job_posting",
            content_text=payload.text.strip(),
            provenance_quality="original",
            source="user_paste",
            url=payload.source_url,
            source_date=payload.known_posting_date,
        )

    try:
        result = process_job_posting_document(document_id)
    except DocumentNotProcessableError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if result["status"] == "failed":
        code = _NATIVE_ERROR_STATUS.get(result["error_type"], 502)
        raise HTTPException(status_code=code, detail=result["error"]) from None

    return {
        "id": result["role_instance_id"],
        "status": "imported",
        "extraction_run_id": result["extraction_run_id"],
        "extraction": result["output_payload"],
        "run": {
            "task": "job_posting_extract",
            "model": result["model"],
            "prompt_name": result["prompt_name"],
            "prompt_version": result["prompt_version"],
            "status": result["status"],
        },
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
