"""Raw-document processing API (docs/17-document-processing-pipeline.md,
brief §18/§19). Deliberately thin: all the actual logic lives in
`app.document_processing`, so this module stays a route layer, not a second
place business rules could drift from the CLI (`scripts/process_job_documents.py`)
that shares the same service functions.
"""

from fastapi import APIRouter, HTTPException

from ..db import db_cursor
from ..document_processing import (
    DocumentNotFoundError,
    DocumentNotProcessableError,
    document_processing_state,
    job_posting_processing_counts,
    process_job_posting_document,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/{document_id}/analyse")
def analyse_document(document_id: str):
    """One document, synchronously (brief §18) — never the batch corpus; that
    stays a CLI/script operation (`scripts/process_job_documents.py`)."""
    try:
        result = process_job_posting_document(document_id)
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except DocumentNotProcessableError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "document_id": result["document_id"],
        "extraction_run_id": result["extraction_run_id"],
        "status": result["status"],
        "role_instance_id": result["role_instance_id"],
        "error": result["error"],
    }


@router.get("/{document_id}/status")
def document_status(document_id: str):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.document WHERE id = %s", (document_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="document not found")
        state = document_processing_state(cur, document_id)
    return {"document_id": document_id, "state": state}


@router.get("/processing-status")
def processing_status(source_prefix: str | None = None):
    """Counts sufficient for a dashboard (brief §19) — derived live from
    extraction_run, never stored redundantly on document."""
    with db_cursor() as cur:
        counts = job_posting_processing_counts(cur, source_prefix=source_prefix)
    return counts
