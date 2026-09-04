from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException

from ..concept_linking import normalize_name
from ..db import db_cursor
from ..models import ClusterProposalResolve, ConceptCreate, ProposalResolve

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


# --- Proposal grouping -----------------------------------------------------
#
# concept_proposal rows are per normalized surface form (§8.1: "a term appearing
# in thirty postings is one decision"). occurrence_count for *pending* groups is
# recomputed live from role_skill_observation rather than trusted from the
# stored column, so the queue never looks stale between Pass B runs.
#
# Groups are keyed by COALESCE(cluster_key, surface_form) — docs/18 §3's
# deterministic clustering (app/vocabulary_bootstrap.py) — so e.g. "Solvency
# II" and "SII" render as one reviewable card (`surface_forms` lists every
# exact form the cluster covers) instead of two, while a proposal with no
# cluster_key yet (predates the bootstrap pass, or Pass B ran without it)
# still renders correctly as its own single-member cluster — this is a pure
# display/grouping change, `surface_form` itself is untouched everywhere
# else (exact-match auto-link on resolution, `nearest_concept`, ...).

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
        cluster_key = row["cluster_key"] or row["surface_form"]
        g = groups.setdefault(
            cluster_key,
            {
                "cluster_key": cluster_key,
                "surface_form": row["surface_form"],  # the first/representative exact form — back-compat for existing callers
                "surface_forms": [],
                "proposal_ids": [],
                "suggested_type": None,
                "nearest_concept_id": None,
                "nearest_similarity": None,
                "occurrence_count": 0,
            },
        )
        if row["surface_form"] not in g["surface_forms"]:
            g["surface_forms"].append(row["surface_form"])
        g["proposal_ids"].append(row["id"])
        g["suggested_type"] = g["suggested_type"] or row["suggested_type"]
        if row["nearest_concept_id"] is not None and g["nearest_concept_id"] is None:
            g["nearest_concept_id"] = row["nearest_concept_id"]
            g["nearest_similarity"] = row["nearest_similarity"]
        g["occurrence_count"] += live_counts.get(row["surface_form"], row["occurrence_count"])

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


def _resolve_surface_form_group(cur, surface_forms: list[str], payload, now) -> tuple[str, str | None]:
    """Shared core of both `resolve_proposal` (one exact surface form — the
    original, unchanged single-item contract) and `resolve_cluster` (every
    exact surface form sharing a cluster_key, docs/18 §3). For a cluster with
    more than one member, exactly one concept is ever created/chosen — every
    *other* member surface form in the group becomes an alias of it, which is
    the whole point of clustering "Solvency II"/"SII" together: accepting the
    group links both surface forms to one concept, one an alias of the
    other, rather than requiring two separate curator decisions.

    Returns (status, resolved_concept_id). Raises HTTPException on the same
    conditions the original single-surface-form endpoint did (404 no pending
    proposals, 400 bad concept_id/duplicate name) — untouched behaviour for
    the n=1 case.
    """
    all_proposal_ids: list[str] = []
    ids_by_surface_form: dict[str, list[str]] = {}
    for surface_form in surface_forms:
        cur.execute(
            "SELECT id FROM jobber.concept_proposal WHERE surface_form = %s AND status = 'pending'",
            (surface_form,),
        )
        ids = [r["id"] for r in cur.fetchall()]
        ids_by_surface_form[surface_form] = ids
        all_proposal_ids.extend(ids)

    if not all_proposal_ids:
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
        canonical_normalized = normalize_name(payload.canonical_name)
        alias_targets = [sf for sf in surface_forms if sf != canonical_normalized]

    elif payload.action == "accept_alias":
        cur.execute("SELECT id FROM jobber.concept WHERE id = %s AND status = 'active'", (payload.concept_id,))
        if not cur.fetchone():
            raise HTTPException(400, "concept_id does not exist or is not active")
        resolved_concept_id = payload.concept_id
        new_status = "accepted_alias"
        alias_targets = list(surface_forms)

    elif payload.action == "reject":
        new_status = "rejected"
        alias_targets = []
    else:
        new_status = "deferred"
        alias_targets = []

    for surface_form in alias_targets:
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) "
            "VALUES (%s, %s, 'extraction_proposal', %s) ON CONFLICT (alias, concept_id) DO NOTHING",
            (resolved_concept_id, surface_form, now),
        )

    for surface_form, ids in ids_by_surface_form.items():
        if not ids:
            continue
        cur.execute(
            "UPDATE jobber.concept_proposal SET status = %s, resolved_concept_id = %s, resolved_at = %s "
            "WHERE id = ANY(%s::uuid[])",
            (new_status, resolved_concept_id, now, ids),
        )
        if resolved_concept_id is not None:
            cur.execute(
                "SELECT id, surface_form AS name FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL"
            )
            matching_ids = [r["id"] for r in cur.fetchall() if normalize_name(r["name"]) == surface_form]
            if matching_ids:
                cur.execute(
                    "UPDATE jobber.role_skill_observation SET canonical_concept_id = %s WHERE id = ANY(%s::uuid[])",
                    (resolved_concept_id, matching_ids),
                )

    return new_status, (str(resolved_concept_id) if resolved_concept_id is not None else None)


@router.post("/proposals/resolve")
def resolve_proposal(payload: ProposalResolve):
    surface_form = normalize_name(payload.surface_form)
    now = datetime.now(timezone.utc)
    with db_cursor() as cur:
        status, resolved_concept_id = _resolve_surface_form_group(cur, [surface_form], payload, now)
    return {"surface_form": surface_form, "status": status, "resolved_concept_id": resolved_concept_id}


@router.post("/proposals/resolve-cluster")
def resolve_cluster(payload: ClusterProposalResolve):
    """The clustered counterpart of resolve_proposal (docs/18 §3): resolves
    every pending proposal whose cluster_key (or, for a legacy/unclustered
    proposal, whose own surface_form) equals `cluster_key`, in one action."""
    cluster_key = normalize_name(payload.cluster_key)
    now = datetime.now(timezone.utc)

    with db_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT surface_form FROM jobber.concept_proposal "
            "WHERE status = 'pending' AND COALESCE(cluster_key, surface_form) = %s",
            (cluster_key,),
        )
        surface_forms = [r["surface_form"] for r in cur.fetchall()]
        if not surface_forms:
            raise HTTPException(404, "no pending proposals for this cluster")
        status, resolved_concept_id = _resolve_surface_form_group(cur, surface_forms, payload, now)

    return {
        "cluster_key": cluster_key,
        "surface_forms": surface_forms,
        "status": status,
        "resolved_concept_id": resolved_concept_id,
    }


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
