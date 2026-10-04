from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import profile360_mapping as p360map
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


def _resolve_state(unmapped: bool, mapping_state: str | None) -> str:
    """`unmapped=true` (legacy flag) is the same as mapping_state=unmapped;
    an explicit mapping_state wins. 'Mapped' rows stay browsable here so they
    can still be inspected and given additional mappings."""
    state = mapping_state or ("unmapped" if unmapped else "all")
    if state not in p360map.MAPPING_STATES:
        raise HTTPException(400, f"mapping_state must be one of {p360map.MAPPING_STATES}")
    return state


def _rows_with_mapping_state(cur, kind: str, rows: list[dict]) -> list[dict]:
    ids = [str(r["id"]) for r in rows]
    states = p360map.mapping_states_for(cur, kind, ids)
    dispositions = {}
    if ids:
        cur.execute(
            "SELECT profile360_id, disposition, reason FROM jobber.profile360_disposition WHERE kind = %s AND profile360_id = ANY(%s::uuid[])",
            (kind, ids),
        )
        dispositions = {str(d["profile360_id"]): d for d in cur.fetchall()}
    result = []
    for r in rows:
        rid = str(r["id"])
        disposition = dispositions.get(rid)
        mapping_state = "boundary" if disposition and disposition["disposition"] == "boundary" else states[rid]["state"]
        result.append({
            **_row_with_display(r), "_mapping_state": mapping_state,
            "_mapping_counts": states[rid]["counts"],
            "_disposition": disposition["disposition"] if disposition else "mappable",
            "_disposition_reason": disposition["reason"] if disposition else None,
        })
    return result


@router.get("/claims")
def list_claims(limit: int = 50, offset: int = 0, unmapped: bool = False, mapping_state: str | None = None):
    state = _resolve_state(unmapped, mapping_state)
    with db_cursor() as cur:
        try:
            rows = p360.list_claims(cur, limit=limit, offset=offset, state=state)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
        return _rows_with_mapping_state(cur, "claim", rows)


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
def list_capabilities(limit: int = 50, offset: int = 0, unmapped: bool = False, mapping_state: str | None = None):
    state = _resolve_state(unmapped, mapping_state)
    with db_cursor() as cur:
        try:
            rows = p360.list_capabilities(cur, limit=limit, offset=offset, state=state)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
        return _rows_with_mapping_state(cur, "capability", rows)


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


# --- curation workbench (claims and capabilities share one implementation) ---

_KIND_BY_PATH = {"claims": "claim", "capabilities": "capability"}


def _kind(kind_path: str) -> str:
    if kind_path not in _KIND_BY_PATH:
        raise HTTPException(404, "not found")
    return _KIND_BY_PATH[kind_path]


def _guard(fn):
    try:
        return fn()
    except p360map.MappingError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except p360.Profile360UnavailableError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/vocabulary/search")
def search_vocabulary(q: str, kind: str | None = None, limit: int = 25):
    """Search the whole active canonical vocabulary (not just the AI's top
    candidates). `kind=capability` restricts to capability concepts, matching
    what a capability can be mapped to."""
    if kind is not None and kind not in p360map.KINDS:
        raise HTTPException(400, "kind must be 'claim' or 'capability'")
    with db_cursor() as cur:
        return p360map.search_vocabulary(cur, q, kind, limit=max(1, min(limit, 100)))


@router.get("/{kind_path}/{source_id}/mapping")
def get_mapping_workbench(kind_path: str, source_id: str):
    """Everything needed to curate one item: its text, every existing mapping
    (any status), the candidates the latest AI run considered (<=10, with
    definitions/similarity and which the AI recommended), and any vocabulary
    proposals raised from it."""
    kind = _kind(kind_path)
    with db_cursor() as cur:
        return _guard(lambda: p360map.get_workbench(cur, kind, source_id))


class CuratorMapping(BaseModel):
    concept_id: str


@router.post("/{kind_path}/{source_id}/mappings")
def add_curator_mapping(kind_path: str, source_id: str, payload: CuratorMapping):
    """Human-selected mapping (a candidate, a search result, or an additional
    mapping on an already-mapped item): curator_asserted + accepted. A
    previously rejected pair is re-opened."""
    kind = _kind(kind_path)
    with db_cursor() as cur:
        return _guard(lambda: p360map.curator_map(cur, kind, source_id, payload.concept_id))


class VocabularyProposal(BaseModel):
    canonical_name: str
    type_code: str | None = None
    definition: str | None = None


@router.post("/{kind_path}/{source_id}/propose-vocabulary")
def propose_vocabulary(kind_path: str, source_id: str, payload: VocabularyProposal):
    """"None of these — suggest new vocabulary": records a *pending*
    jobber.concept_proposal (the same queue job-posting vocabulary uses,
    reviewed in Vocabulary) with provenance back to this item. Creates no
    concept and no mapping; the mapping is made when the proposal is accepted."""
    kind = _kind(kind_path)

    def _go():
        row = p360map.cfg(kind)["get"](cur, source_id)
        if row is None:
            raise p360map.MappingError(f"profile360 {kind} not found", 404)
        return p360map.record_proposal(
            cur, kind, source_id, p360.display_text(row), canonical_name=payload.canonical_name,
            type_code=payload.type_code, definition=payload.definition, origin="curator",
        )

    with db_cursor() as cur:
        return _guard(_go)


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
