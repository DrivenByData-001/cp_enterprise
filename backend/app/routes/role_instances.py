import io

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import (
    app_kind_to_instance_type,
    create_document,
    db_cursor,
    find_document_duplicates,
    update_role_metadata,
    upsert_role_instance,
)
from ..embeddings import embed_text, set_embedding
from ..extraction import ExtractionSubjectError, extract_role_requirements
from ..metadata_enrichment import MetadataEnrichmentSubjectError, propose_role_metadata
from ..models import RoleMetadataUpdate

router = APIRouter(prefix="/api/role-instances", tags=["role-instances"])


class RawIngest(BaseModel):
    text: str
    kind: str = "posting"  # posting | target_real | target_imagined | synthetic_reference
    title: str | None = None
    organisation: str | None = None
    location: str | None = None
    country: str | None = None
    source_url: str | None = None
    source: str | None = None
    posting_date: str | None = None


class DuplicateCheckRequest(BaseModel):
    text: str
    kind: str = "posting"


def _document_kind(role_kind: str) -> str:
    return "job_posting" if role_kind == "posting" else "narrative"


def _derive_title(text: str, given: str | None) -> str:
    if given and given.strip():
        return given.strip()
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    return (first_line[:120] or "Untitled posting")


def _ingest_raw(payload: RawIngest) -> dict:
    """The minimal, source-aware capture path (brief §4/§13): raw text ->
    immutable document (provenance_quality='original' — this is always a
    fresh capture, never a reconstruction) -> a bare role_instance. Deliberately
    does not run AI extraction inline — ingestion and extraction are separate
    steps (§13: "normalize ingestion... before running AI extraction"), so a
    slow/unavailable AI provider never blocks capturing the source text."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    try:
        instance_type, target_basis = app_kind_to_instance_type(payload.kind)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    document_kind = _document_kind(payload.kind)
    with db_cursor() as cur:
        # Computed against whatever documents already exist *before* this
        # capture's own document is inserted below — informational,
        # defense-in-depth alongside the duplicate-check preflight endpoint
        # the frontend calls first (never blocks the capture that follows).
        duplicate_info = find_document_duplicates(cur, text, kind=document_kind)

        document_id, duplicate_of = create_document(
            cur,
            kind=document_kind,
            content_text=text,
            provenance_quality="original",
            title=payload.title,
            source=payload.source or "user_paste",
            url=payload.source_url,
            source_date=payload.posting_date,
        )
        role_id = upsert_role_instance(
            cur,
            None,
            {
                "instance_type": instance_type,
                "target_basis": target_basis,
                "document_id": document_id,
                "title": _derive_title(text, payload.title),
                "organisation": payload.organisation,
                "location": payload.location,
                "country": payload.country,
                "posting_date": payload.posting_date,
            },
            skills=[],
        )
        vector = embed_text(text)
        if vector:
            set_embedding(cur, "role_instance", role_id, vector)

    return {
        "id": role_id,
        "document_id": document_id,
        "duplicate_of_document_id": duplicate_of,
        "duplicate": duplicate_info,
        "status": "ingested",
    }


async def _extract_pdf_text(file: UploadFile) -> str:
    """Selectable-text PDF extraction (brief §13 priority 2), shared by the
    real ingest endpoint and the text-only preflight endpoint below. No OCR:
    if the PDF has no extractable text layer (a scan/photo), this raises a
    clear 422 rather than silently producing empty text — OCR is explicitly
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
    return text


@router.post("/ingest")
def ingest_text(payload: RawIngest):
    return _ingest_raw(payload)


@router.post("/ingest/pdf")
async def ingest_pdf(
    file: UploadFile,
    kind: str = "posting",
    title: str | None = None,
    organisation: str | None = None,
    location: str | None = None,
    country: str | None = None,
    posting_date: str | None = None,
    source_url: str | None = None,
):
    text = await _extract_pdf_text(file)
    return _ingest_raw(
        RawIngest(
            text=text, kind=kind, title=title, organisation=organisation, location=location,
            country=country, posting_date=posting_date, source_url=source_url, source="pdf",
        )
    )


@router.post("/pdf/extract-text")
async def extract_pdf_text_preview(file: UploadFile):
    """Text extraction only — never persists a document or role. Lets the
    frontend run the duplicate-check preflight (POST .../duplicate-check)
    against a PDF's content *before* anything is captured (source-aware
    ingest cleanup, problem #3): extract here, check-duplicate with the
    returned text, then either open the existing role or call the real
    ingest endpoint with that same text."""
    text = await _extract_pdf_text(file)
    return {"text": text}


@router.post("/duplicate-check")
def duplicate_check(payload: DuplicateCheckRequest):
    """Pre-persistence duplicate preflight (source-aware ingest cleanup,
    problem #3): given raw text the user is about to capture (pasted
    directly, or already extracted from a PDF via .../pdf/extract-text),
    reports whether an identical or whitespace-normalised-identical document
    already exists — without creating anything. Never blocks the capture
    that follows; the caller decides whether to open the existing role or
    capture anyway."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    with db_cursor() as cur:
        return find_document_duplicates(cur, text, kind=_document_kind(payload.kind))


@router.post("/{role_id}/extract-requirements")
def extract_requirements(role_id: str):
    with db_cursor() as cur:
        try:
            return extract_role_requirements(cur, role_id)
        except ExtractionSubjectError as e:
            raise HTTPException(404, str(e)) from e


_METADATA_ERROR_STATUS = {
    "AIConfigError": 503,
    "AIProviderError": 502,
    "AIResponseFormatError": 422,
    "AISchemaValidationError": 422,
}


@router.post("/{role_id}/metadata/propose")
def propose_metadata(role_id: str):
    """Runs the lightweight metadata-enrichment task against this role's
    linked source document and returns a proposal for the user to review —
    never writes anything (source-aware ingest cleanup, problem #5). Accept
    via PATCH /api/role-instances/{role_id}/metadata once reviewed/edited."""
    with db_cursor() as cur:
        try:
            result = propose_role_metadata(cur, role_id)
        except MetadataEnrichmentSubjectError as e:
            raise HTTPException(404, str(e)) from e
    if result["status"] == "failed":
        code = _METADATA_ERROR_STATUS.get(result["error_type"], 502)
        raise HTTPException(status_code=code, detail=result["error"])
    return result


@router.patch("/{role_id}/metadata")
def update_metadata(role_id: str, payload: RoleMetadataUpdate):
    """Metadata-only update for a role_instance — the source-aware
    counterpart to the legacy full-JobPostingImport overwrite (PUT
    /api/roles/{id}): used both for manual Edit on a role with no `raw_json`
    and for 'Accept metadata' after a proposed enrichment (problems #5/#7).
    Never touches skills, the linked document, or requirement claims."""
    with db_cursor() as cur:
        cur.execute("SELECT instance_type FROM jobber.role_instance WHERE id = %s", (role_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "role not found")
        if row["instance_type"] != "observed_posting":
            raise HTTPException(400, "this is a target role — edit it via PUT /api/targets/{id}")
        role = update_role_metadata(cur, role_id, payload.model_dump(exclude_unset=True))
    return role


@router.get("/{role_id}/requirements")
def list_requirements(role_id: str):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "role_instance not found")
        cur.execute(
            """
            SELECT rc.id, rc.requirement_type, rc.importance, rc.basis, rc.evidence_span,
                   rc.review_status, rc.created_at, rc.extraction_run_id,
                   c.id AS concept_id, c.canonical_name, c.type_code,
                   d.id AS document_id, d.title AS document_title, d.provenance_quality AS document_provenance
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
def review_requirement(role_id: str, claim_id: str, payload: ReviewAction):
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
