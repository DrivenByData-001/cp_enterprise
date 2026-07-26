import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..concept_linking import normalize_name
from ..db import db_cursor
from ..models import ConceptCreate, ProposalResolve

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


# --- Proposal grouping -----------------------------------------------------
#
# concept_proposal rows are per normalized surface form (§8.1: "a term appearing
# in thirty postings is one decision"). occurrence_count for *pending* groups is
# recomputed live from job_role_skills rather than trusted from the stored
# column, so the queue never looks stale between Pass B runs (see docs/11 Phase 1
# build notes).

def _group_proposals(cur: sqlite3.Cursor, status: str) -> list[dict]:
    cur.execute("SELECT * FROM concept_proposal WHERE status = ? ORDER BY surface_form", (status,))
    rows = [dict(r) for r in cur.fetchall()]

    live_counts: dict[str, int] = {}
    if status == "pending":
        cur.execute("SELECT name FROM job_role_skills WHERE resolved_concept_id IS NULL")
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
        cur.execute("SELECT * FROM concept_type ORDER BY sort_order")
        return [dict(r) for r in cur.fetchall()]


@router.get("/facets")
def get_facets(type_code: str):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.canonical_name, COUNT(DISTINCT jrs.job_role_id) AS role_count
            FROM concept c
            JOIN job_role_skills jrs ON jrs.resolved_concept_id = c.id
            WHERE c.status = 'active' AND c.type_code = ?
            GROUP BY c.id
            ORDER BY role_count DESC, c.canonical_name
            """,
            (type_code,),
        )
        return [dict(r) for r in cur.fetchall()]


@router.get("/proposals")
def list_proposals(status: str = "pending"):
    with db_cursor() as cur:
        return _group_proposals(cur, status)


@router.get("/proposals/stats")
def proposal_stats():
    with db_cursor() as cur:
        pending_groups = len(_group_proposals(cur, "pending"))
        cur.execute("SELECT COUNT(*) AS n FROM document WHERE kind = 'job_posting'")
        total_documents = cur.fetchone()["n"]
    return {
        "pending_groups": pending_groups,
        "total_documents": total_documents,
        "proposals_per_document": round(pending_groups / total_documents, 2) if total_documents else None,
    }


@router.post("/proposals/resolve")
def resolve_proposal(payload: ProposalResolve):
    surface_form = normalize_name(payload.surface_form)
    now = datetime.utcnow().isoformat()

    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM concept_proposal WHERE surface_form = ? AND status = 'pending'",
            (surface_form,),
        )
        proposal_ids = [r["id"] for r in cur.fetchall()]
        if not proposal_ids:
            raise HTTPException(404, "no pending proposals for this surface form")

        resolved_concept_id = None

        if payload.action == "accept_new":
            try:
                cur.execute(
                    "INSERT INTO concept (type_code, canonical_name, definition, status, origin, created_at, reviewed_at) "
                    "VALUES (?, ?, ?, 'active', 'extraction_proposal', ?, ?)",
                    (payload.type_code, payload.canonical_name, payload.definition, now, now),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(400, "a concept with this type and canonical name already exists")
            resolved_concept_id = cur.lastrowid
            new_status = "accepted_new"

        elif payload.action == "accept_alias":
            cur.execute("SELECT id FROM concept WHERE id = ? AND status = 'active'", (payload.concept_id,))
            if not cur.fetchone():
                raise HTTPException(400, "concept_id does not exist or is not active")
            cur.execute(
                "INSERT OR IGNORE INTO concept_alias (concept_id, alias, origin, created_at) "
                "VALUES (?, ?, 'extraction_proposal', ?)",
                (payload.concept_id, surface_form, now),
            )
            resolved_concept_id = payload.concept_id
            new_status = "accepted_alias"

        elif payload.action == "reject":
            new_status = "rejected"
        else:
            new_status = "deferred"

        placeholders = ",".join("?" * len(proposal_ids))
        cur.execute(
            f"UPDATE concept_proposal SET status = ?, resolved_concept_id = ?, resolved_at = ? "
            f"WHERE id IN ({placeholders})",
            (new_status, resolved_concept_id, now, *proposal_ids),
        )

        if resolved_concept_id is not None:
            cur.execute("SELECT id, name FROM job_role_skills WHERE resolved_concept_id IS NULL")
            matching_ids = [r["id"] for r in cur.fetchall() if normalize_name(r["name"]) == surface_form]
            if matching_ids:
                match_placeholders = ",".join("?" * len(matching_ids))
                cur.execute(
                    f"UPDATE job_role_skills SET resolved_concept_id = ? WHERE id IN ({match_placeholders})",
                    (resolved_concept_id, *matching_ids),
                )

    return {"surface_form": surface_form, "status": new_status, "resolved_concept_id": resolved_concept_id}


# --- Concept CRUD ------------------------------------------------------------

@router.get("")
def list_concepts(type_code: str | None = None, status: str = "active", q: str | None = None):
    query = "SELECT * FROM concept WHERE status = ?"
    params: list = [status]
    if type_code:
        query += " AND type_code = ?"
        params.append(type_code)
    if q:
        query += " AND canonical_name LIKE ?"
        params.append(f"%{q}%")
    query += " ORDER BY canonical_name"
    with db_cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


@router.post("")
def create_concept(payload: ConceptCreate):
    with db_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO concept (type_code, canonical_name, definition, status, origin, created_at) "
                "VALUES (?, ?, ?, ?, 'curator', ?)",
                (payload.type_code, payload.canonical_name, payload.definition, payload.status, datetime.utcnow().isoformat()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(400, "a concept with this type and canonical name already exists")
        concept_id = cur.lastrowid
    return {"id": concept_id, "status": "created"}


@router.get("/{concept_id}")
def get_concept(concept_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM concept WHERE id = ?", (concept_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "concept not found")
        concept = dict(row)
        cur.execute("SELECT id, alias, origin FROM concept_alias WHERE concept_id = ?", (concept_id,))
        concept["aliases"] = [dict(r) for r in cur.fetchall()]
    return concept
