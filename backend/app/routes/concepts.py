from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException

from ..concept_linking import normalize_name
from ..db import db_cursor
from ..models import ConceptCreate, ProposalResolve

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


# --- Proposal grouping -----------------------------------------------------
#
# concept_proposal rows are per normalized surface form (§8.1: "a term appearing
# in thirty postings is one decision"). occurrence_count for *pending* groups is
# recomputed live from role_skill_observation rather than trusted from the
# stored column, so the queue never looks stale between Pass B runs.

def _group_proposals(cur, status: str) -> list[dict]:
    cur.execute("SELECT * FROM jobber.concept_proposal WHERE status = %s ORDER BY surface_form", (status,))
    rows = cur.fetchall()

    live_counts: dict[str, int] = {}
    if status == "pending":
        cur.execute("SELECT surface_form AS name FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL")
        for r in cur.fetchall():
            key = normalize_name(r["name"])
            live_counts[key] = live_counts.get(key, 0) + 1

    groups: dict[str, dict] = {}
    for row in rows:
        g = groups.setdefault(
            row["surface_form"],
            {
                "surface_form": row["surface_form"],
                "proposal_ids": [],
                "suggested_type": None,
                "nearest_concept_id": None,
                "nearest_similarity": None,
                "occurrence_count": 0,
            },
        )
        g["proposal_ids"].append(row["id"])
        g["suggested_type"] = g["suggested_type"] or row["suggested_type"]
        if row["nearest_concept_id"] is not None:
            g["nearest_concept_id"] = row["nearest_concept_id"]
            g["nearest_similarity"] = row["nearest_similarity"]
        g["occurrence_count"] = live_counts.get(row["surface_form"], row["occurrence_count"])

    return sorted(groups.values(), key=lambda g: -g["occurrence_count"])


# --- Static routes (declared before /{concept_id}) -------------------------

@router.get("/types")
def list_concept_types():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.concept_type ORDER BY sort_order")
        return cur.fetchall()


@router.get("/facets")
def get_facets(type_code: str):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.canonical_name, COUNT(DISTINCT rso.role_instance_id) AS role_count
            FROM jobber.concept c
            JOIN jobber.role_skill_observation rso ON rso.canonical_concept_id = c.id
            WHERE c.status = 'active' AND c.type_code = %s
            GROUP BY c.id
            ORDER BY role_count DESC, c.canonical_name
            """,
            (type_code,),
        )
        return cur.fetchall()


@router.get("/proposals")
def list_proposals(status: str = "pending"):
    with db_cursor() as cur:
        return _group_proposals(cur, status)


@router.get("/proposals/stats")
def proposal_stats():
    with db_cursor() as cur:
        pending_groups = len(_group_proposals(cur, "pending"))
        cur.execute("SELECT COUNT(*) AS n FROM jobber.document WHERE kind = 'job_posting'")
        total_documents = cur.fetchone()["n"]
    return {
        "pending_groups": pending_groups,
        "total_documents": total_documents,
        "proposals_per_document": round(pending_groups / total_documents, 2) if total_documents else None,
    }


@router.post("/proposals/resolve")
def resolve_proposal(payload: ProposalResolve):
    surface_form = normalize_name(payload.surface_form)
    now = datetime.now(timezone.utc)

    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM jobber.concept_proposal WHERE surface_form = %s AND status = 'pending'",
            (surface_form,),
        )
        proposal_ids = [r["id"] for r in cur.fetchall()]
        if not proposal_ids:
            raise HTTPException(404, "no pending proposals for this surface form")

        resolved_concept_id = None

        if payload.action == "accept_new":
            try:
                cur.execute(
                    "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at, reviewed_at) "
                    "VALUES (%s, %s, %s, 'active', 'extraction_proposal', %s, %s) RETURNING id",
                    (payload.type_code, payload.canonical_name, payload.definition, now, now),
                )
            except psycopg.errors.UniqueViolation:
                raise HTTPException(400, "a concept with this type and canonical name already exists")
            resolved_concept_id = cur.fetchone()["id"]
            new_status = "accepted_new"

        elif payload.action == "accept_alias":
            cur.execute("SELECT id FROM jobber.concept WHERE id = %s AND status = 'active'", (payload.concept_id,))
            if not cur.fetchone():
                raise HTTPException(400, "concept_id does not exist or is not active")
            cur.execute(
                "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) "
                "VALUES (%s, %s, 'extraction_proposal', %s) ON CONFLICT (alias, concept_id) DO NOTHING",
                (payload.concept_id, surface_form, now),
            )
            resolved_concept_id = payload.concept_id
            new_status = "accepted_alias"

        elif payload.action == "reject":
            new_status = "rejected"
        else:
            new_status = "deferred"

        cur.execute(
            "UPDATE jobber.concept_proposal SET status = %s, resolved_concept_id = %s, resolved_at = %s "
            "WHERE id = ANY(%s::uuid[])",
            (new_status, resolved_concept_id, now, proposal_ids),
        )

        if resolved_concept_id is not None:
            cur.execute("SELECT id, surface_form AS name FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL")
            matching_ids = [r["id"] for r in cur.fetchall() if normalize_name(r["name"]) == surface_form]
            if matching_ids:
                cur.execute(
                    "UPDATE jobber.role_skill_observation SET canonical_concept_id = %s WHERE id = ANY(%s::uuid[])",
                    (resolved_concept_id, matching_ids),
                )

    return {"surface_form": surface_form, "status": new_status, "resolved_concept_id": resolved_concept_id}


# --- Concept CRUD ------------------------------------------------------------

@router.get("")
def list_concepts(type_code: str | None = None, status: str = "active", q: str | None = None):
    query = "SELECT * FROM jobber.concept WHERE status = %s"
    params: list = [status]
    if type_code:
        query += " AND type_code = %s"
        params.append(type_code)
    if q:
        query += " AND canonical_name ILIKE %s"
        params.append(f"%{q}%")
    query += " ORDER BY canonical_name"
    with db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


@router.post("")
def create_concept(payload: ConceptCreate):
    with db_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at) "
                "VALUES (%s, %s, %s, %s, 'curator', %s) RETURNING id",
                (payload.type_code, payload.canonical_name, payload.definition, payload.status, datetime.now(timezone.utc)),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(400, "a concept with this type and canonical name already exists")
        concept_id = cur.fetchone()["id"]
    return {"id": concept_id, "status": "created"}


@router.get("/{concept_id}")
def get_concept(concept_id: str):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.concept WHERE id = %s", (concept_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "concept not found")
        concept = dict(row)
        cur.execute("SELECT id, alias, origin FROM jobber.concept_alias WHERE concept_id = %s", (concept_id,))
        concept["aliases"] = cur.fetchall()
    return concept
