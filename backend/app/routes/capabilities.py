"""Capability catalogue curation + coverage (brief §5/§6/§27).

`jobber.capability_detail` already exists (0002) — this reuses it rather
than creating a second capability model. A capability is one
`jobber.concept` row with `type_code = 'capability'` plus its one
`capability_detail` row, created/edited together so the two can never drift
out of sync in the API surface (even though they are, and stay, two rows).
"""

from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException

from .. import capability_engine as engine
from ..db import db_cursor
from ..models import (
    CapabilityCreate,
    CapabilityMerge,
    CapabilityUpdate,
    ComponentEdgeCreate,
    ComponentEdgeReview,
    ComponentEdgeUpdate,
)

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


def _component_summary(cur, capability_id: str) -> dict:
    components = engine.load_components(cur, capability_id)
    proposed = engine.load_proposed_components(cur, capability_id)
    proposed_count = sum(len(v) for v in proposed.values())
    return {
        "core_component_count": len(components["core"]),
        "supporting_component_count": len(components["supporting"]),
        "contextual_component_count": len(components["contextual"]),
        "proposed_component_count": proposed_count,
    }


def _row_to_capability(row: dict) -> dict:
    row = dict(row)
    row["id"] = str(row["id"])
    return row


@router.get("")
def list_capabilities(status: str = "active", q: str | None = None):
    query = """
        SELECT c.id, c.canonical_name, c.definition, c.status, c.origin, c.created_at, c.reviewed_at,
               cd.demonstration_standard, cd.min_depth, cd.min_autonomy, cd.requires_all_core,
               cd.min_core_required, cd.economic_salience, cd.notes
        FROM jobber.concept c
        JOIN jobber.capability_detail cd ON cd.concept_id = c.id
        WHERE c.type_code = 'capability' AND c.status = %s
    """
    params: list = [status]
    if q:
        query += " AND c.canonical_name ILIKE %s"
        params.append(f"%{q}%")
    query += " ORDER BY c.canonical_name"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = [_row_to_capability(r) for r in cur.fetchall()]
        for row in rows:
            row.update(_component_summary(cur, row["id"]))
    return rows


@router.post("")
def create_capability(payload: CapabilityCreate):
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at, reviewed_at) "
                "VALUES ('capability', %s, %s, %s, 'curator', %s, %s) RETURNING id",
                (payload.canonical_name, payload.definition, payload.status, now, now),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(400, "a capability with this canonical name already exists")
        concept_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO jobber.capability_detail
                (concept_id, demonstration_standard, min_depth, min_autonomy, requires_all_core,
                 min_core_required, economic_salience, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                concept_id, payload.demonstration_standard, payload.min_depth, payload.min_autonomy,
                payload.requires_all_core, payload.min_core_required, payload.economic_salience, payload.notes,
            ),
        )
    return {"id": str(concept_id), "status": "created"}


@router.get("/coverage")
def list_coverage():
    """Bulk coverage for the personal capability-coverage view (brief §30) —
    avoids an N+1 request per capability from the frontend. Declared before
    `/{capability_id}` so it is not swallowed by that dynamic route (same
    convention as routes/concepts.py's static routes)."""
    with db_cursor() as cur:
        cur.execute("SELECT id, canonical_name FROM jobber.concept WHERE type_code = 'capability' AND status = 'active'")
        names = {str(r["id"]): r["canonical_name"] for r in cur.fetchall()}
        rows = engine.derive_all_capability_coverage(cur)
    for row in rows:
        row["canonical_name"] = names.get(row["capability_concept_id"])
    return rows


@router.get("/{capability_id}")
def get_capability(capability_id: str):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.canonical_name, c.definition, c.status, c.origin, c.created_at, c.reviewed_at,
                   cd.demonstration_standard, cd.min_depth, cd.min_autonomy, cd.requires_all_core,
                   cd.min_core_required, cd.economic_salience, cd.notes
            FROM jobber.concept c
            JOIN jobber.capability_detail cd ON cd.concept_id = c.id
            WHERE c.id = %s AND c.type_code = 'capability'
            """,
            (capability_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "capability not found")
        capability = _row_to_capability(row)
        components = engine.load_components(cur, capability_id)
        capability["components"] = components
        # 'proposed' component_of edges (docs/18 §3/§10 — bootstrap or any
        # future proposer) — a separate, clearly-labelled field, never
        # merged with the accepted `components` above; the engine itself
        # never reads this (engine.load_components stays accepted-only).
        capability["components_proposed"] = engine.load_proposed_components(cur, capability_id)

        try:
            coverage = engine.derive_capability_coverage(cur, capability_id)
        except engine.CapabilityNotFoundError:
            coverage = None
    capability["coverage"] = coverage
    return capability


@router.put("/{capability_id}")
def update_capability(capability_id: str, payload: CapabilityUpdate):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.concept WHERE id = %s AND type_code = 'capability'", (capability_id,))
        if not cur.fetchone():
            raise HTTPException(404, "capability not found")

        concept_fields = {k: v for k, v in (("canonical_name", payload.canonical_name), ("definition", payload.definition), ("status", payload.status)) if v is not None}
        if concept_fields:
            set_clause = ", ".join(f"{k} = %s" for k in concept_fields)
            try:
                cur.execute(
                    f"UPDATE jobber.concept SET {set_clause}, reviewed_at = now() WHERE id = %s",
                    [*concept_fields.values(), capability_id],
                )
            except psycopg.errors.UniqueViolation:
                raise HTTPException(400, "a capability with this canonical name already exists")

        detail_fields = {
            k: v
            for k, v in (
                ("demonstration_standard", payload.demonstration_standard),
                ("min_depth", payload.min_depth),
                ("min_autonomy", payload.min_autonomy),
                ("requires_all_core", payload.requires_all_core),
                ("min_core_required", payload.min_core_required),
                ("economic_salience", payload.economic_salience),
                ("notes", payload.notes),
            )
            if v is not None
        }
        if detail_fields:
            set_clause = ", ".join(f"{k} = %s" for k in detail_fields)
            cur.execute(
                f"UPDATE jobber.capability_detail SET {set_clause} WHERE concept_id = %s",
                [*detail_fields.values(), capability_id],
            )
    return {"id": capability_id, "status": "updated"}


@router.get("/{capability_id}/coverage")
def get_coverage(capability_id: str):
    with db_cursor() as cur:
        try:
            return engine.derive_capability_coverage(cur, capability_id)
        except engine.CapabilityNotFoundError:
            raise HTTPException(404, "capability not found")


# --- component_of edges (brief §6) ------------------------------------------

@router.get("/{capability_id}/components")
def list_components(capability_id: str):
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.concept WHERE id = %s AND type_code = 'capability'", (capability_id,))
        if not cur.fetchone():
            raise HTTPException(404, "capability not found")
        return engine.load_components(cur, capability_id)


@router.post("/{capability_id}/components")
def add_component(capability_id: str, payload: ComponentEdgeCreate):
    with db_cursor() as cur:
        cur.execute("SELECT type_code FROM jobber.concept WHERE id = %s AND type_code = 'capability'", (capability_id,))
        if not cur.fetchone():
            raise HTTPException(404, "capability not found")

        cur.execute("SELECT type_code, status FROM jobber.concept WHERE id = %s", (payload.concept_id,))
        component = cur.fetchone()
        if not component:
            raise HTTPException(400, "component concept_id does not exist")
        if component["status"] != "active":
            raise HTTPException(400, "component concept must be active")

        # Server-side grammar check (brief §6) — never trust the frontend alone.
        if not engine.is_valid_edge(cur, "component_of", component["type_code"], "capability"):
            raise HTTPException(
                400,
                f"invalid edge: component_of is not a legal relation from type '{component['type_code']}' to 'capability'",
            )
        if component["type_code"] == "capability" and str(payload.concept_id) == str(capability_id):
            raise HTTPException(400, "a capability cannot be a component of itself")

        try:
            cur.execute(
                """
                INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, necessity, origin, status)
                VALUES (%s, %s, 'component_of', %s, 'curator', 'accepted')
                RETURNING id
                """,
                (payload.concept_id, capability_id, payload.necessity),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(400, "this component edge already exists")
        edge_id = cur.fetchone()["id"]
    return {"id": str(edge_id), "status": "created"}


@router.put("/{capability_id}/components/{edge_id}")
def update_component(capability_id: str, edge_id: str, payload: ComponentEdgeUpdate):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE jobber.concept_edge SET necessity = %s "
            "WHERE id = %s AND to_concept_id = %s AND relation = 'component_of'",
            (payload.necessity, edge_id, capability_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "component edge not found on this capability")
    return {"id": edge_id, "status": "updated"}


@router.delete("/{capability_id}/components/{edge_id}")
def remove_component(capability_id: str, edge_id: str):
    with db_cursor() as cur:
        cur.execute(
            "DELETE FROM jobber.concept_edge WHERE id = %s AND to_concept_id = %s AND relation = 'component_of'",
            (edge_id, capability_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "component edge not found on this capability")
    return {"status": "deleted"}


# --- proposed component_of edge review (docs/18 §10 — bootstrap proposals) --

@router.post("/{capability_id}/components/{edge_id}/review")
def review_component(capability_id: str, edge_id: str, payload: ComponentEdgeReview):
    """Accept or reject one *proposed* component edge (never touches an
    already-accepted one — the WHERE clause below only ever matches
    status='proposed'). Accepting re-runs the same grammar/active-concept
    checks `add_component` applies to a curator-authored edge (defense in
    depth: the underlying atomic concept could have been deprecated in the
    time since the edge was proposed) — an edge that no longer validates is
    rejected outright rather than silently accepted."""
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT ce.from_concept_id, c.type_code, c.status AS concept_status
            FROM jobber.concept_edge ce JOIN jobber.concept c ON c.id = ce.from_concept_id
            WHERE ce.id = %s AND ce.to_concept_id = %s AND ce.relation = 'component_of' AND ce.status = 'proposed'
            """,
            (edge_id, capability_id),
        )
        edge = cur.fetchone()
        if not edge:
            raise HTTPException(404, "proposed component edge not found on this capability")

        if payload.action == "reject":
            cur.execute("UPDATE jobber.concept_edge SET status = 'rejected' WHERE id = %s", (edge_id,))
            return {"id": edge_id, "status": "rejected"}

        if edge["concept_status"] != "active" or not engine.is_valid_edge(cur, "component_of", edge["type_code"], "capability"):
            cur.execute("UPDATE jobber.concept_edge SET status = 'rejected' WHERE id = %s", (edge_id,))
            raise HTTPException(
                400,
                "component concept is no longer active or no longer a valid component_of source — edge rejected automatically",
            )

        cur.execute("UPDATE jobber.concept_edge SET status = 'accepted', origin = 'curator' WHERE id = %s", (edge_id,))
    return {"id": edge_id, "status": "accepted"}


# --- capability merge (docs/18 §10 — "merge where supported by the existing model") --

@router.post("/{capability_id}/merge")
def merge_capability(capability_id: str, payload: CapabilityMerge):
    """Merges `capability_id` into `payload.merge_into_id`: the source
    concept is marked `status='merged'`/`merged_into=<target>` (both
    existing columns/values — jobber.concept.merged_into has existed since
    Phase 1, docs/11 §7.3), and every one of the source's own component_of
    edges (proposed or accepted) is re-parented onto the target, duplicate
    edges dropped rather than erroring. The source's `d_capability_coverage`/
    `d_role_fit` rows, if any, are left for the next rebuild to clean up
    (rebuild_phase3_derivations already removes derived rows for a
    concept that no longer qualifies as an active capability)."""
    if capability_id == payload.merge_into_id:
        raise HTTPException(400, "cannot merge a capability into itself")

    with db_cursor() as cur:
        cur.execute("SELECT id, status FROM jobber.concept WHERE id = %s AND type_code = 'capability'", (capability_id,))
        source = cur.fetchone()
        if not source:
            raise HTTPException(404, "capability not found")
        cur.execute("SELECT id, status FROM jobber.concept WHERE id = %s AND type_code = 'capability'", (payload.merge_into_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(400, "merge_into_id is not an existing capability")
        if target["status"] == "merged":
            raise HTTPException(400, "cannot merge into a capability that has itself been merged elsewhere")

        cur.execute(
            "SELECT id, from_concept_id, necessity, status FROM jobber.concept_edge "
            "WHERE to_concept_id = %s AND relation = 'component_of'",
            (capability_id,),
        )
        source_edges = cur.fetchall()
        for edge in source_edges:
            cur.execute(
                """
                INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, necessity, origin, status)
                VALUES (%s, %s, 'component_of', %s, 'curator', %s)
                ON CONFLICT (from_concept_id, to_concept_id, relation) DO NOTHING
                """,
                (edge["from_concept_id"], payload.merge_into_id, edge["necessity"], edge["status"]),
            )
            cur.execute("DELETE FROM jobber.concept_edge WHERE id = %s", (edge["id"],))

        cur.execute(
            "UPDATE jobber.concept SET status = 'merged', merged_into = %s, reviewed_at = now() WHERE id = %s",
            (payload.merge_into_id, capability_id),
        )
    return {"id": capability_id, "status": "merged", "merged_into": payload.merge_into_id}


# --- rebuild (brief §20/§27) -------------------------------------------------

@router.post("/rebuild")
def rebuild():
    """Recomputes d_capability_coverage and d_role_fit from source rows.
    Deliberately destructive-safe, not destructive: every derived row is
    replaced by a fresh deterministic computation, never emptied first (brief
    §14/§20) — an interrupted rebuild leaves the previous derived rows in
    place rather than a half-truncated table. No auth layer exists yet in
    this local/trusted single-operator build (docs/15 §1) — consistent with
    every other mutating endpoint in this codebase, not a new gap."""
    with db_cursor() as cur:
        return engine.rebuild_phase3_derivations(cur)
