import io
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import db_cursor, get_or_create_document, upsert_role_instance
from ..embeddings import embed_text, set_embedding
from ..extraction import ExtractionSubjectError, extract_role_requirements

router = APIRouter(prefix="/api/role-instances", tags=["role-instances"])


class RawIngest(BaseModel):
    text: str
    kind: str = "posting"  # posting | target_real | target_imagined | synthetic_reference
    title: str | None = None
    organisation: str | None = None
    location: str | None = None
    source_url: str | None = None
    source: str | None = None
    posting_date: str | None = None


def _derive_title(text: str, given: str | None) -> str:
    if given and given.strip():
        return given.strip()
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    return (first_line[:120] or "Untitled posting")


def _ingest_raw(payload: RawIngest) -> dict:
    """The minimal, source-aware capture path (brief §4/§13): raw text ->
    immutable document (provenance='original_capture' — this is always a
    fresh capture, never a reconstruction) -> a bare role_instance. Deliberately
    does not run AI extraction inline — ingestion and extraction are separate
    steps (§13: "normalize ingestion... before running AI extraction"), so a
    slow/unavailable AI provider never blocks capturing the source text."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    if payload.kind not in ("posting", "target_real", "target_imagined", "synthetic_reference"):
        raise HTTPException(400, "invalid kind")

    with db_cursor() as cur:
        document_id, created = get_or_create_document(
            cur,
            kind="job_posting" if payload.kind == "posting" else "narrative",
            body=text,
            provenance="original_capture",
            title=payload.title,
            source=payload.source or "user_paste",
            url=payload.source_url,
            document_date=payload.posting_date,
        )
        role_id = upsert_role_instance(
            cur,
            None,
            {
                "kind": payload.kind,
                "document_id": document_id,
                "title": _derive_title(text, payload.title),
                "organisation": payload.organisation,
                "location": payload.location,
                "posting_date": payload.posting_date,
                "url": payload.source_url,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
            skills=[],
        )
        vector = embed_text(text)
        if vector:
            set_embedding(cur, "role_instance", role_id, vector)

    return {"id": role_id, "document_id": document_id, "document_reused": not created, "status": "ingested"}


@router.post("/ingest")
def ingest_text(payload: RawIngest):
    return _ingest_raw(payload)


@router.post("/ingest/pdf")
async def ingest_pdf(
    file: UploadFile,
    kind: str = "posting",
    title: str | None = None,
    organisation: str | None = None,
    source_url: str | None = None,
):
    """Selectable-text PDF ingestion (brief §13 priority 2). No OCR: if the
    PDF has no extractable text layer (a scan/photo), this returns a clear
    422 rather than silently producing an empty document — OCR is explicitly
    deferred, not silently attempted and failed."""
    import pypdf

    raw = await file.read()
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as e:
        raise HTTPException(422, f"could not read this PDF: {e}") from e

    if not text:
        raise HTTPException(
            422,
            "no selectable text found in this PDF — it looks image-only/scanned. "
            "OCR is not supported yet; paste the text manually instead.",
        )

    return _ingest_raw(
        RawIngest(text=text, kind=kind, title=title, organisation=organisation, source_url=source_url, source="pdf")
    )


@router.post("/{role_id}/extract-requirements")
def extract_requirements(role_id: int):
    with db_cursor() as cur:
        try:
            return extract_role_requirements(cur, role_id)
        except ExtractionSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.get("/{role_id}/requirements")
def list_requirements(role_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "role_instance not found")
        cur.execute(
            """
            SELECT rc.id, rc.requirement_type, rc.importance, rc.basis, rc.evidence_span,
                   rc.review_status, rc.created_at, rc.extraction_run_id,
                   c.id AS concept_id, c.canonical_name, c.type_code,
                   d.id AS document_id, d.title AS document_title, d.provenance AS document_provenance
            FROM jobber.requirement_claim rc
            JOIN jobber.concept c ON c.id = rc.concept_id
            LEFT JOIN jobber.document d ON d.id = rc.document_id
            WHERE rc.role_instance_id = %s
            ORDER BY rc.requirement_type, c.canonical_name
            """,
            (role_id,),
        )
        return cur.fetchall()


class ReviewAction(BaseModel):
    action: str  # accept | reject


@router.post("/{role_id}/requirements/{claim_id}/review")
def review_requirement(role_id: int, claim_id: int, payload: ReviewAction):
    if payload.action not in ("accept", "reject"):
        raise HTTPException(400, "action must be 'accept' or 'reject'")
    new_status = "accepted" if payload.action == "accept" else "rejected"
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.requirement_claim SET review_status = %s, reviewed_at = now() "
            "WHERE id = %s AND role_instance_id = %s",
            (new_status, claim_id, role_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "requirement claim not found on this role")
    return {"id": claim_id, "review_status": new_status}
