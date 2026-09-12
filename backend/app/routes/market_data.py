"""Market Data: recruiter/market salary survey capture + AI extraction +
review (Phase 4 prompt §8). Reuses the existing immutable `jobber.document`
approach and the existing `app.ai` task layer exactly as
`document_processing.py` established for job postings — paste raw text, or
upload a selectable-text PDF (no OCR), stored as an immutable document
*before* anything is extracted from it.
"""

import io
from datetime import date

from fastapi import APIRouter, HTTPException, UploadFile

from .. import market_analytics
from ..db import create_document, db_cursor
from ..market_data_processing import (
    DOCUMENT_KIND,
    DocumentNotFoundError,
    DocumentNotProcessableError,
    process_market_data_document,
    source_quality_for_sample_size,
)
from ..models import CompensationObservationCorrect, CompensationObservationReview, MarketDataIngest

router = APIRouter(prefix="/api/market-data", tags=["market-data"])


def _parse_date_param(value: str | None, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{field_name} must be an ISO 8601 date (YYYY-MM-DD), got {value!r}") from None


def _document_row(row) -> dict:
    row = dict(row)
    row["id"] = str(row["id"])
    return row


def _observation_row(row) -> dict:
    row = dict(row)
    for key in ("id", "market_id", "archetype_concept_id", "role_instance_id", "document_id"):
        if key in row:
            row[key] = str(row[key]) if row[key] else None
    return row


@router.get("/documents")
def list_documents():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, source, url, source_date, captured_at, source_payload, created_at "
            "FROM jobber.document WHERE kind = %s ORDER BY created_at DESC",
            (DOCUMENT_KIND,),
        )
        rows = [_document_row(r) for r in cur.fetchall()]
    return rows


@router.get("/documents/{document_id}")
def get_document(document_id: str):
    """Viewing a report never calls AI (prompt §8) — a pure read."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, source, url, source_date, captured_at, content_text, source_payload, created_at "
            "FROM jobber.document WHERE id = %s AND kind = %s",
            (document_id, DOCUMENT_KIND),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "market survey document not found")
        document = _document_row(row)

        cur.execute(
            "SELECT id, raw_role_label, market_id, archetype_concept_id, component, pay_period, employment_basis, "
            "amount_min, amount_mid, amount_max, currency, reported_p25, reported_p50, reported_p75, reported_mean, bonus_pct, "
            "reported_sample_size, source_quality, source_kind, geography_reported, domain_or_practice_area, seniority_band_reported, "
            "experience_band, pqe_band, page_reference, table_reference, source_note, review_status, observed_at, "
            "period_end, created_at, reviewed_at "
            "FROM jobber.compensation_observation WHERE document_id = %s ORDER BY created_at",
            (document_id,),
        )
        document["observations"] = [_observation_row(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT id, status, started_at, finished_at, error_message, output_payload FROM jobber.extraction_run "
            "WHERE task = 'compensation_extract' AND document_id = %s ORDER BY started_at DESC",
            (document_id,),
        )
        document["extraction_runs"] = [{**dict(r), "id": str(r["id"])} for r in cur.fetchall()]
    return document


@router.post("/documents/ingest")
def ingest_text(payload: MarketDataIngest):
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    with db_cursor() as cur:
        document_id, duplicate_of = create_document(
            cur,
            kind=DOCUMENT_KIND,
            content_text=text,
            provenance_quality="original",
            title=payload.report_title,
            source=payload.publisher,
            url=payload.source_url,
            source_date=payload.report_date,
            source_payload={
                "publisher": payload.publisher,
                "report_title": payload.report_title,
                "report_date": payload.report_date,
                "methodology_notes": payload.methodology_notes,
            },
        )
    return {"id": document_id, "duplicate_of_document_id": duplicate_of, "status": "ingested"}


@router.post("/documents/ingest/pdf")
async def ingest_pdf(
    file: UploadFile,
    publisher: str | None = None,
    report_title: str | None = None,
    report_date: str | None = None,
    source_url: str | None = None,
    methodology_notes: str | None = None,
):
    """Selectable-text PDF only — no OCR, same posture as
    `routes/role_instances.py::ingest_pdf`."""
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

    return ingest_text(
        MarketDataIngest(
            text=text, publisher=publisher, report_title=report_title,
            report_date=report_date, source_url=source_url, methodology_notes=methodology_notes,
        )
    )


@router.post("/documents/{document_id}/extract")
def extract(document_id: str):
    """Explicit, on-demand AI extraction only — never triggered by a GET.
    Produces draft/unreviewed compensation_observation rows, never accepted
    economic facts."""
    try:
        return process_market_data_document(document_id)
    except DocumentNotFoundError:
        raise HTTPException(404, "market survey document not found")
    except DocumentNotProcessableError as e:
        raise HTTPException(422, str(e)) from e


# --- Draft compensation observation review (prompt §8) ---------------------

@router.get("/compensation-observations")
def list_draft_observations(review_status: str = "unreviewed"):
    with db_cursor() as cur:
        cur.execute(
            "SELECT co.id, raw_role_label, market_id, archetype_concept_id, component, pay_period, "
            "employment_basis, amount_min, amount_mid, amount_max, currency, reported_p25, "
            "reported_p50, reported_p75, reported_mean, bonus_pct, reported_sample_size, source_quality, source_kind, "
            "geography_reported, domain_or_practice_area, seniority_band_reported, experience_band, pqe_band, page_reference, "
            "table_reference, source_note, review_status, co.document_id, co.observed_at, co.period_end, "
            "co.created_at, co.reviewed_at, d.source AS publisher, d.title AS document_title, d.source_date AS report_date "
            "FROM jobber.compensation_observation co LEFT JOIN jobber.document d ON d.id = co.document_id "
            "WHERE co.basis = 'survey' AND co.review_status = %s "
            "ORDER BY co.created_at DESC",
            (review_status,),
        )
        rows = [_observation_row(r) for r in cur.fetchall()]
    return rows


@router.post("/compensation-observations/{observation_id}/review")
def review_observation(observation_id: str, payload: CompensationObservationReview):
    new_status = "accepted" if payload.action == "accept" else "rejected"
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.compensation_observation SET review_status = %s, reviewed_at = now() WHERE id = %s",
            (new_status, observation_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "compensation observation not found")
    return {"id": observation_id, "review_status": new_status}


@router.patch("/compensation-observations/{observation_id}")
def correct_observation(observation_id: str, payload: CompensationObservationCorrect):
    """Correct extracted numeric fields, or assign/change role archetype/
    market (prompt §8) — before or after review. Only supplied, non-null
    fields change."""
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        return {"id": observation_id, "status": "unchanged"}

    if "reported_sample_size" in fields:
        fields["source_quality"] = source_quality_for_sample_size(fields["reported_sample_size"])

    with db_cursor() as cur:
        if "market_id" in fields:
            cur.execute("SELECT 1 FROM jobber.market WHERE id = %s", (fields["market_id"],))
            if not cur.fetchone():
                raise HTTPException(400, "market_id does not exist")
        if "archetype_concept_id" in fields:
            cur.execute(
                "SELECT 1 FROM jobber.concept WHERE id = %s AND type_code = 'role_archetype' AND status = 'active'",
                (fields["archetype_concept_id"],),
            )
            if not cur.fetchone():
                raise HTTPException(400, "archetype_concept_id is not an active role_archetype")

        set_clause = ", ".join(f"{k} = %s" for k in fields)
        cur.execute(f"UPDATE jobber.compensation_observation SET {set_clause} WHERE id = %s", [*fields.values(), observation_id])
        if cur.rowcount == 0:
            raise HTTPException(404, "compensation observation not found")
    return {"id": observation_id, "status": "updated"}


# --- Market analytics (docs/25) ---------------------------------------------
#
# A read-only analytical layer over *every* accepted compensation
# observation, whether or not it carries an archetype assignment — see
# app/market_analytics.py's module docstring. Never rebuilds anything, never
# writes, and never requires an archetype filter.

@router.get("/analytics/summary")
def market_analytics_summary(
    market_id: str | None = None,
    currency: str | None = None,
    component: str | None = None,
    pay_period: str | None = None,
    practice_group: str | None = None,
    provider: str | None = None,
    source_kind: str | None = None,
    employment_basis: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    evidence_limit: int = 100,
    evidence_offset: int = 0,
):
    filters = market_analytics.MarketAnalyticsFilters(
        market_id=market_id or None,
        currency=currency or None,
        component=component or None,
        pay_period=pay_period or None,
        practice_group=practice_group or None,
        provider=provider or None,
        source_kind=source_kind or None,
        employment_basis=employment_basis or None,
        period_from=_parse_date_param(period_from, "period_from"),
        period_to=_parse_date_param(period_to, "period_to"),
    )
    with db_cursor() as cur:
        return market_analytics.build_market_analytics_summary(
            cur, filters, evidence_limit=evidence_limit, evidence_offset=evidence_offset
        )
