import io
import uuid
from typing import Optional

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
from ..role_requirements import load_requirement_evidence, load_requirement_evidence_bulk, load_requirement_review_summary
from ..span_validation import validate_span

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


_REQUIREMENT_CLAIM_FIELDS = """
    rc.id, rc.requirement_type, rc.importance, rc.basis, rc.evidence_span,
    rc.review_status, rc.created_at, rc.extraction_run_id, rc.superseded_by,
    c.id AS concept_id, c.canonical_name, c.type_code,
    d.id AS document_id, d.title AS document_title, d.provenance_quality AS document_provenance
"""


@router.get("/{role_id}/requirements")
def list_requirements(role_id: str, history: bool = False):
    """Current requirement claims for this role — the review queue (every
    review_status, since this is the curation gate itself, not the
    analytical loader) plus a review-summary count for the status banner.
    Defaults to *current* rows only (`superseded_by IS NULL`): a corrected
    claim's superseded predecessor must never render as a second, duplicate
    card alongside its replacement. Pass `?history=true` to deliberately see
    the full chain (audit/debugging) instead — never mixed into the default
    list. See role_requirements.py for why review_summary is computed
    separately from the (accepted-only) analytical requirement set."""
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "role_instance not found")
        query = (
            f"SELECT {_REQUIREMENT_CLAIM_FIELDS} FROM jobber.requirement_claim rc "
            "JOIN jobber.concept c ON c.id = rc.concept_id "
            "LEFT JOIN jobber.document d ON d.id = rc.document_id "
            "WHERE rc.role_instance_id = %s"
        )
        if not history:
            query += " AND rc.superseded_by IS NULL"
        query += " ORDER BY rc.requirement_type, c.canonical_name, rc.created_at"
        cur.execute(query, (role_id,))
        items = [dict(row) for row in cur.fetchall()]
        # One current requirement per (role, concept), many supporting
        # requirement_evidence occurrences (migration 0026) — attached in
        # one bulk query for every claim on this page, never one query per
        # card (N+1).
        evidence_by_claim = load_requirement_evidence_bulk(cur, [str(item["id"]) for item in items])
        for item in items:
            item["evidence"] = evidence_by_claim.get(str(item["id"]), [])
        review_summary = load_requirement_review_summary(cur, role_id)
    return {"items": items, "review_summary": review_summary}


def _current_claim(cur, role_id: str, claim_id: str) -> dict:
    cur.execute(
        "SELECT * FROM jobber.requirement_claim WHERE id = %s AND role_instance_id = %s",
        (claim_id, role_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "requirement claim not found on this role")
    return row


def _fetch_claim_view(cur, claim_id: str) -> dict:
    cur.execute(
        f"SELECT {_REQUIREMENT_CLAIM_FIELDS} FROM jobber.requirement_claim rc "
        "JOIN jobber.concept c ON c.id = rc.concept_id "
        "LEFT JOIN jobber.document d ON d.id = rc.document_id "
        "WHERE rc.id = %s",
        (claim_id,),
    )
    row = dict(cur.fetchone())
    row["evidence"] = load_requirement_evidence(cur, claim_id)
    return row


@router.post("/{role_id}/requirements/{claim_id}/accept")
def accept_requirement(role_id: str, claim_id: str):
    """Unchanged Accept (brief §1/§3): marks the original AI claim accepted
    in place — no replacement row, no provenance change. Only valid from
    'unreviewed'; an already-accepted claim is corrected via /edit instead,
    and a rejected one must be /reopen-ed first, so this action's meaning
    never has to be guessed from context."""
    with db_cursor() as cur:
        claim = _current_claim(cur, role_id, claim_id)
        if claim["superseded_by"] is not None:
            raise HTTPException(409, "this claim has been superseded — act on its current replacement instead")
        if claim["review_status"] != "unreviewed":
            raise HTTPException(409, f"only an unreviewed claim can be accepted directly (current status: {claim['review_status']})")
        cur.execute(
            "UPDATE jobber.requirement_claim SET review_status = 'accepted', reviewed_at = now() WHERE id = %s",
            (claim_id,),
        )
        return _fetch_claim_view(cur, claim_id)


@router.post("/{role_id}/requirements/{claim_id}/reject")
def reject_requirement(role_id: str, claim_id: str):
    """Reject an unreviewed claim in place — or un-accept an already-accepted
    one: a review mistake must be recoverable later, not only while still
    unreviewed, and Edit alone cannot express "this isn't a requirement at
    all." Un-accepting never overwrites the accepted decision in place — it
    goes through the same non-destructive supersession `_supersede_with_new_claim`
    edit uses, so the audit trail shows the claim WAS accepted before a
    human changed their mind, exactly as any other correction would."""
    with db_cursor() as cur:
        claim = _current_claim(cur, role_id, claim_id)
        if claim["superseded_by"] is not None:
            raise HTTPException(409, "this claim has been superseded — act on its current replacement instead")
        if claim["review_status"] == "unreviewed":
            cur.execute(
                "UPDATE jobber.requirement_claim SET review_status = 'rejected', reviewed_at = now() WHERE id = %s",
                (claim_id,),
            )
            return _fetch_claim_view(cur, claim_id)
        if claim["review_status"] == "accepted":
            new_id = _supersede_with_new_claim(
                cur, role_id=role_id, old_claim_id=claim_id, concept_id=str(claim["concept_id"]),
                requirement_type=claim["requirement_type"], importance=claim["importance"], basis=claim["basis"],
                document_id=str(claim["document_id"]) if claim["document_id"] else None,
                evidence_span=claim["evidence_span"], new_review_status="rejected",
            )
            return _fetch_claim_view(cur, new_id)
        raise HTTPException(409, f"only an unreviewed or accepted claim can be rejected (current status: {claim['review_status']})")


@router.post("/{role_id}/requirements/{claim_id}/reopen")
def reopen_requirement(role_id: str, claim_id: str):
    """Recovery path for an accidental Reject (brief §4) — never requires
    direct database intervention. Only a *current, rejected* claim can be
    reopened; a superseded row is history and stays that way."""
    with db_cursor() as cur:
        claim = _current_claim(cur, role_id, claim_id)
        if claim["superseded_by"] is not None:
            raise HTTPException(409, "this claim has been superseded — its history cannot be reopened")
        if claim["review_status"] != "rejected":
            raise HTTPException(409, f"only a rejected claim can be reopened (current status: {claim['review_status']})")
        cur.execute(
            "UPDATE jobber.requirement_claim SET review_status = 'unreviewed', reviewed_at = NULL WHERE id = %s",
            (claim_id,),
        )
        return _fetch_claim_view(cur, claim_id)


_REQUIREMENT_TYPES = {"required", "preferred", "contextual"}
_REQUIREMENT_BASES = {"stated", "implied", "inferred", "user_asserted"}


def _validate_requirement_fields(cur, *, concept_id, requirement_type, basis, importance, evidence_span, document_id):
    """Server-side enforcement of requirement_claim's domain rules — the
    frontend's own validation is never trusted alone (brief §5). Raises
    HTTPException(400) on any violation."""
    cur.execute("SELECT status FROM jobber.concept WHERE id = %s", (concept_id,))
    concept = cur.fetchone()
    if not concept:
        raise HTTPException(400, "concept not found")
    if concept["status"] != "active":
        raise HTTPException(400, "choose an active vocabulary concept")

    if requirement_type not in _REQUIREMENT_TYPES:
        raise HTTPException(400, f"requirement_type must be one of {sorted(_REQUIREMENT_TYPES)}")
    if basis not in _REQUIREMENT_BASES:
        raise HTTPException(400, f"basis must be one of {sorted(_REQUIREMENT_BASES)}")
    if importance is not None and importance not in (1, 2, 3, 4, 5):
        raise HTTPException(400, "importance must be between 1 and 5, or blank")

    if basis in ("stated", "implied"):
        if not document_id or not evidence_span:
            raise HTTPException(400, "stated/implied evidence requires a linked document and an evidence span")
        cur.execute("SELECT content_text, provenance_quality FROM jobber.document WHERE id = %s", (document_id,))
        document = cur.fetchone()
        if not document:
            raise HTTPException(400, "linked document not found")
        # A legacy/reconstructed/unknown-provenance document can never back
        # stated/implied evidence, however good the span looks — never
        # promoted to stronger evidence merely because the edit form allows
        # it (brief §5's "never upgrade weak provenance").
        if document["provenance_quality"] != "original":
            raise HTTPException(
                400,
                "this document's provenance cannot support stated/implied evidence — "
                "choose 'inferred' or 'user_asserted' instead",
            )
        if not validate_span(document["content_text"], evidence_span):
            raise HTTPException(400, "evidence_span is not an exact match in the source document")


def _existing_current_claim_id(cur, role_id: str, concept_id: str, *, exclude_claim_id: str | None = None) -> str | None:
    """Is there already a *current* claim for this (role, concept) — the
    condition migration 0020's partial unique index also enforces at the
    database level. Used to turn what would otherwise be a raw integrity-
    error 500 into a clear 409 before Add requirement or a concept remap
    ever reaches the database."""
    query = "SELECT id FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s AND superseded_by IS NULL"
    params: list = [role_id, concept_id]
    if exclude_claim_id is not None:
        query += " AND id != %s"
        params.append(exclude_claim_id)
    cur.execute(query, params)
    row = cur.fetchone()
    return str(row["id"]) if row else None


def _supersede_with_new_claim(cur, *, role_id, old_claim_id, concept_id, requirement_type, importance, basis,
                               document_id, evidence_span, new_review_status: str) -> str:
    """The shared non-destructive-correction mechanism behind both /edit and
    /reject-an-accepted-claim below: the old row is marked 'corrected' and
    pointed at a new row via superseded_by, and the new row (carrying
    `new_review_status` — 'accepted' for a correction, 'rejected' for an
    un-accept) becomes current, with extraction_run_id=NULL — it is not the
    output of any extraction run, it is what a human reviewer determined,
    truthfully recorded as such.

    The new row's id is generated here, in Python, rather than left to the
    table's own DEFAULT gen_random_uuid(), specifically so the old row's
    UPDATE (which frees the (role, concept) slot by clearing its "current"
    status) can run *before* the new row's INSERT (which reclaims that same
    slot). Reversing that order — inserting the replacement first, the way
    an append-only model might suggest — would momentarily leave two current
    rows for the same (role, concept) whenever a correction doesn't change
    the concept (the common case: editing type/basis/span/importance
    without remapping), tripping migration 0020's partial unique index
    inside this same transaction."""
    new_id = str(uuid.uuid4())
    cur.execute(
        "UPDATE jobber.requirement_claim SET review_status = 'corrected', superseded_by = %s, reviewed_at = now() "
        "WHERE id = %s",
        (new_id, old_claim_id),
    )
    cur.execute(
        """
        INSERT INTO jobber.requirement_claim
            (id, role_instance_id, concept_id, requirement_type, importance, basis,
             document_id, evidence_span, extraction_run_id, review_status, reviewed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, now())
        """,
        (new_id, role_id, concept_id, requirement_type, importance, basis, document_id, evidence_span, new_review_status),
    )
    # The old row's already-attached evidence (migration 0026) belongs to
    # this same role/concept requirement still — a correction changes what
    # the requirement *is*, not which occurrences support it — so it moves
    # to the new current row, same as extraction.py's own rerun-supersession
    # and migration 0026's backfill do for older history.
    cur.execute(
        "UPDATE jobber.requirement_evidence SET requirement_claim_id = %s WHERE requirement_claim_id = %s",
        (new_id, old_claim_id),
    )
    # A human correction that supplies its own evidence_span is itself a new
    # occurrence worth recording — never silently dropped just because the
    # claim's own evidence_span column already carries it too.
    if basis in ("stated", "implied") and evidence_span:
        cur.execute(
            """
            INSERT INTO jobber.requirement_evidence
                (requirement_claim_id, document_id, evidence_span, basis, extraction_run_id)
            VALUES (%s, %s, %s, %s, NULL)
            ON CONFLICT DO NOTHING
            """,
            (new_id, document_id, evidence_span, basis),
        )
    return new_id


class RequirementClaimEdit(BaseModel):
    """PATCH-shaped partial edit for /edit below — every field optional; an
    omitted field keeps the original claim's value, matching
    RoleMetadataUpdate's exclude_unset convention. `importance` alone
    supports an explicit `null` to clear it (brief §2: "importance 1-5 or
    blank")."""

    concept_id: Optional[str] = None
    requirement_type: Optional[str] = None
    basis: Optional[str] = None
    importance: Optional[int] = None
    evidence_span: Optional[str] = None


@router.post("/{role_id}/requirements/{claim_id}/edit")
def edit_requirement(role_id: str, claim_id: str, payload: RequirementClaimEdit):
    """'Edit & accept' for an unreviewed claim, and 'Edit' for an already-
    accepted one (brief §2/§4) — both go through the same non-destructive
    supersession path (brief §3, `_supersede_with_new_claim`): the original
    AI claim/extraction_run_id is preserved untouched on the old row, which
    is marked 'corrected' and linked via superseded_by; a *new* row carries
    the corrected data and review_status='accepted'. A rejected claim must
    be /reopen-ed first; a superseded row is immutable history. A field-
    for-field-identical patch is treated as a plain Accept (or a no-op, if
    already accepted) — enforced here, not only by the frontend, so a direct
    API call can't manufacture a pointless replacement/corrected pair
    either. Remapping onto a concept this role already has a separate
    *current* claim for is rejected (409) rather than creating a duplicate."""
    patch = payload.model_dump(exclude_unset=True)
    with db_cursor() as cur:
        claim = _current_claim(cur, role_id, claim_id)
        if claim["superseded_by"] is not None:
            raise HTTPException(409, "this claim has been superseded — act on its current replacement instead")
        if claim["review_status"] not in ("unreviewed", "accepted"):
            raise HTTPException(
                409,
                f"a claim with review_status={claim['review_status']} cannot be edited directly "
                "— reopen a rejected claim first",
            )

        concept_id = patch.get("concept_id", str(claim["concept_id"]))
        requirement_type = patch.get("requirement_type", claim["requirement_type"])
        basis = patch.get("basis", claim["basis"])
        importance = patch["importance"] if "importance" in patch else claim["importance"]
        evidence_span = patch["evidence_span"] if "evidence_span" in patch else claim["evidence_span"]
        document_id = str(claim["document_id"]) if claim["document_id"] else None
        # A span only ever means something alongside stated/implied basis —
        # computed before the no-op/validation checks below so both compare
        # and store the same "effective" value, never a stale quote left
        # over from a stronger basis.
        if basis not in ("stated", "implied"):
            evidence_span = None

        unchanged = (
            concept_id == str(claim["concept_id"])
            and requirement_type == claim["requirement_type"]
            and basis == claim["basis"]
            and importance == claim["importance"]
            and evidence_span == claim["evidence_span"]
        )
        if unchanged:
            if claim["review_status"] == "unreviewed":
                cur.execute(
                    "UPDATE jobber.requirement_claim SET review_status = 'accepted', reviewed_at = now() WHERE id = %s",
                    (claim_id,),
                )
            return _fetch_claim_view(cur, claim_id)

        _validate_requirement_fields(
            cur, concept_id=concept_id, requirement_type=requirement_type, basis=basis,
            importance=importance, evidence_span=evidence_span, document_id=document_id,
        )
        if _existing_current_claim_id(cur, role_id, concept_id, exclude_claim_id=claim_id):
            raise HTTPException(
                409,
                "this role already has a current requirement for that concept — edit or reject the existing one "
                "instead of creating a duplicate",
            )

        new_id = _supersede_with_new_claim(
            cur, role_id=role_id, old_claim_id=claim_id, concept_id=concept_id, requirement_type=requirement_type,
            importance=importance, basis=basis, document_id=document_id, evidence_span=evidence_span,
            new_review_status="accepted",
        )
        return _fetch_claim_view(cur, new_id)


class RequirementClaimCreate(BaseModel):
    """Manual 'Add requirement' (brief §8) — always source-backed: basis is
    restricted to stated/implied (both server-validated against the role's
    own immutable document below) so a user can never casually invent an
    unsupported employer requirement and present it as source-stated."""

    concept_id: str
    requirement_type: str
    basis: str = "stated"
    importance: Optional[int] = None
    evidence_span: str


@router.post("/{role_id}/requirements")
def add_requirement(role_id: str, payload: RequirementClaimCreate):
    if payload.basis not in ("stated", "implied"):
        raise HTTPException(400, "a manually-added requirement must be source-backed — basis must be 'stated' or 'implied'")
    with db_cursor() as cur:
        cur.execute("SELECT document_id FROM jobber.role_instance WHERE id = %s", (role_id,))
        role = cur.fetchone()
        if not role:
            raise HTTPException(404, "role_instance not found")
        document_id = str(role["document_id"]) if role["document_id"] else None

        _validate_requirement_fields(
            cur, concept_id=payload.concept_id, requirement_type=payload.requirement_type, basis=payload.basis,
            importance=payload.importance, evidence_span=payload.evidence_span, document_id=document_id,
        )
        if _existing_current_claim_id(cur, role_id, payload.concept_id):
            raise HTTPException(409, "this requirement already exists — review or edit the existing claim instead")
        cur.execute(
            """
            INSERT INTO jobber.requirement_claim
                (role_instance_id, concept_id, requirement_type, importance, basis,
                 document_id, evidence_span, extraction_run_id, review_status, reviewed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 'accepted', now())
            RETURNING id
            """,
            (role_id, payload.concept_id, payload.requirement_type, payload.importance, payload.basis,
             document_id, payload.evidence_span),
        )
        new_id = str(cur.fetchone()["id"])
        cur.execute(
            """
            INSERT INTO jobber.requirement_evidence
                (requirement_claim_id, document_id, evidence_span, basis, extraction_run_id)
            VALUES (%s, %s, %s, %s, NULL)
            ON CONFLICT DO NOTHING
            """,
            (new_id, document_id, payload.evidence_span, payload.basis),
        )
        return _fetch_claim_view(cur, new_id)
