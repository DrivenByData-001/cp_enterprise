from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import profile360_reader as p360
from ..db import db_cursor
from ..extraction import (
    ExtractionSubjectError,
    map_profile360_capability,
    map_profile360_claim,
    map_profile360_claim_to_capability,
    run_pass_c,
)

router = APIRouter(prefix="/api/profile360", tags=["profile360"])


def _row_with_display(row: dict) -> dict:
    return {**row, "_display": p360.display_text(row)}


@router.get("/claims")
def list_claims(limit: int = 50, offset: int = 0):
    with db_cursor() as cur:
        try:
            rows = p360.list_claims(cur, limit=limit, offset=offset)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    return [_row_with_display(r) for r in rows]


@router.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    with db_cursor() as cur:
        try:
            row = p360.get_claim(cur, claim_id)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    if row is None:
        raise HTTPException(404, "profile360 claim not found")
    return _row_with_display(row)


@router.get("/capabilities")
def list_capabilities(limit: int = 50, offset: int = 0):
    with db_cursor() as cur:
        try:
            rows = p360.list_capabilities(cur, limit=limit, offset=offset)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    return [_row_with_display(r) for r in rows]


@router.get("/capabilities/{capability_id}")
def get_capability(capability_id: str):
    with db_cursor() as cur:
        try:
            row = p360.get_capability(cur, capability_id)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    if row is None:
        raise HTTPException(404, "profile360 capability not found")
    return _row_with_display(row)


@router.post("/claims/{claim_id}/map")
def map_claim(claim_id: str):
    with db_cursor() as cur:
        try:
            return map_profile360_claim(cur, claim_id)
        except ExtractionSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e


@router.post("/capabilities/{capability_id}/map")
def map_capability(capability_id: str):
    with db_cursor() as cur:
        try:
            return map_profile360_capability(cur, capability_id)
        except ExtractionSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e


@router.post("/claims/{claim_id}/map-capability")
def map_claim_to_capability(claim_id: str):
    """Phase 3 Pass C (brief §23): try to attribute a profile360 claim
    directly to a curated capability, not just an atomic concept."""
    with db_cursor() as cur:
        try:
            return map_profile360_claim_to_capability(cur, claim_id)
        except ExtractionSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e


@router.post("/pass-c/run")
def run_pass_c_route(limit: int = 25):
    with db_cursor() as cur:
        return run_pass_c(cur, limit=limit)


def _mapping_rows(cur, table: str, id_column: str, concept_column: str, review_status: str | None) -> list[dict]:
    query = f"""
        SELECT m.id, m.{id_column} AS profile360_id, m.mapping_basis, m.review_status,
               m.reviewed_at, m.created_at, m.extraction_run_id,
               c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.{table} m
        JOIN jobber.concept c ON c.id = m.{concept_column}
    """
    params: list = []
    if review_status:
        query += " WHERE m.review_status = %s"
        params.append(review_status)
    query += " ORDER BY m.created_at DESC"
    cur.execute(query, params)
    rows = cur.fetchall()
    for row in rows:
        try:
            source = p360.get_claim(cur, row["profile360_id"]) if table == "profile360_claim_mapping" else p360.get_capability(cur, row["profile360_id"])
            row["_display"] = p360.display_text(source) if source else None
        except p360.Profile360UnavailableError:
            row["_display"] = None
    return rows


@router.get("/mappings")
def list_mappings(kind: str = "claim", review_status: str | None = None):
    if kind not in ("claim", "capability"):
        raise HTTPException(400, "kind must be 'claim' or 'capability'")
    table, id_col, concept_col = (
        ("profile360_claim_mapping", "profile360_claim_id", "jobber_concept_id")
        if kind == "claim"
        else ("profile360_capability_mapping", "profile360_capability_id", "jobber_capability_concept_id")
    )
    with db_cursor() as cur:
        return _mapping_rows(cur, table, id_col, concept_col, review_status)


class MappingReview(BaseModel):
    kind: str  # claim | capability
    action: str  # accept | reject


@router.post("/mappings/{mapping_id}/review")
def review_mapping(mapping_id: str, payload: MappingReview):
    if payload.kind not in ("claim", "capability"):
        raise HTTPException(400, "kind must be 'claim' or 'capability'")
    if payload.action not in ("accept", "reject"):
        raise HTTPException(400, "action must be 'accept' or 'reject'")
    table = "profile360_claim_mapping" if payload.kind == "claim" else "profile360_capability_mapping"
    new_status = "accepted" if payload.action == "accept" else "rejected"
    with db_cursor() as cur:
        cur.execute(
            f"UPDATE jobber.{table} SET review_status = %s, reviewed_at = now() WHERE id = %s",
            (new_status, mapping_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "mapping not found")
    return {"id": mapping_id, "review_status": new_status}
