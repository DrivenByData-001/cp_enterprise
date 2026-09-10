"""Role archetype curation/assignment (Phase 4 prompt §3).

Reuses `concept(type_code='role_archetype')`, `jobber.role_archetype_detail`
and `role_instance.archetype_concept_id` — all three already existed
(migration 0002) but were completely unused in production (0 active
role_archetype concepts, 0 assigned roles) before this build. No parallel
role-family table is created here.

Title grouping is a curation aid only: it is computed live from
`role_instance.title`, never persisted, and never mutates the raw posting
title. Creating or assigning an archetype is always an explicit curator
action — there is no AI-driven auto-creation/auto-assignment path.
"""

import re

import psycopg
from fastapi import APIRouter, HTTPException

from ..db import db_cursor
from ..models import ArchetypeAssign, ArchetypeCreate, ArchetypeUpdate

router = APIRouter(prefix="/api/archetypes", tags=["archetypes"])


def _normalize_title(title: str | None) -> str:
    """Curation-aid-only normalisation: lowercase, collapse whitespace,
    strip punctuation noise. Never written back to role_instance.title."""
    if not title:
        return ""
    value = title.strip().lower()
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _row_to_archetype(row: dict) -> dict:
    row = dict(row)
    row["id"] = str(row["id"])
    if row.get("primary_function_concept_id"):
        row["primary_function_concept_id"] = str(row["primary_function_concept_id"])
    return row


def _role_counts(cur, archetype_ids: list[str]) -> dict[str, int]:
    if not archetype_ids:
        return {}
    cur.execute(
        "SELECT archetype_concept_id, COUNT(*) AS n FROM jobber.role_instance "
        "WHERE archetype_concept_id = ANY(%s::uuid[]) GROUP BY archetype_concept_id",
        (archetype_ids,),
    )
    return {str(r["archetype_concept_id"]): r["n"] for r in cur.fetchall()}


def _accepted_comp_counts(cur, archetype_ids: list[str]) -> dict[str, int]:
    """Accepted compensation observations touching each archetype — either
    linked directly (archetype_concept_id) or via one of its assigned roles.
    A simple curation-facing signal, not the benchmark-selection rule itself
    (that lives in economics_engine.py)."""
    if not archetype_ids:
        return {}
    cur.execute(
        """
        SELECT a.id AS archetype_concept_id, COUNT(DISTINCT co.id) AS n
        FROM unnest(%s::uuid[]) AS a(id)
        LEFT JOIN jobber.role_instance ri ON ri.archetype_concept_id = a.id
        LEFT JOIN jobber.compensation_observation co
            ON co.review_status = 'accepted'
           AND (co.archetype_concept_id = a.id OR co.role_instance_id = ri.id)
        GROUP BY a.id
        """,
        (archetype_ids,),
    )
    return {str(r["archetype_concept_id"]): r["n"] for r in cur.fetchall()}


@router.get("/title-groups")
def list_title_groups():
    """Unassigned observed roles grouped by normalised title (prompt §3).
    Declared before /{archetype_id} so it is never swallowed by that dynamic
    route (same convention as routes/capabilities.py's /coverage)."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, organisation, country, seniority_level, instance_type "
            "FROM jobber.role_instance "
            "WHERE archetype_concept_id IS NULL AND instance_type = 'observed_posting' "
            "AND title IS NOT NULL AND title != ''"
        )
        rows = [dict(r) for r in cur.fetchall()]

    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = _normalize_title(r["title"])
        if not key:
            continue
        groups.setdefault(key, []).append(r)

    result = []
    for key, roles in groups.items():
        result.append(
            {
                "normalized_title": key,
                "sample_title": roles[0]["title"],
                "role_count": len(roles),
                "roles": [
                    {
                        "id": str(r["id"]),
                        "title": r["title"],
                        "organisation": r["organisation"],
                        "country": r["country"],
                        "seniority_level": r["seniority_level"],
                    }
                    for r in roles
                ],
            }
        )
    result.sort(key=lambda g: (-g["role_count"], g["normalized_title"]))
    return result


@router.get("")
def list_archetypes(status: str = "active"):
    query = """
        SELECT c.id, c.canonical_name, c.status, c.created_at, c.reviewed_at,
               rad.seniority_band, rad.primary_function_concept_id, rad.typical_market, rad.notes
        FROM jobber.concept c
        JOIN jobber.role_archetype_detail rad ON rad.concept_id = c.id
        WHERE c.type_code = 'role_archetype'
    """
    params: list = []
    if status != "all":
        query += " AND c.status = %s"
        params.append(status)
    query += " ORDER BY c.canonical_name"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = [_row_to_archetype(r) for r in cur.fetchall()]
        ids = [r["id"] for r in rows]
        role_counts = _role_counts(cur, ids)
        comp_counts = _accepted_comp_counts(cur, ids)
    for row in rows:
        row["role_count"] = role_counts.get(row["id"], 0)
        row["accepted_compensation_observation_count"] = comp_counts.get(row["id"], 0)
    return rows


@router.post("")
def create_archetype(payload: ArchetypeCreate):
    with db_cursor() as cur:
        role_ids = _validate_role_ids(cur, payload.role_instance_ids)

        if payload.primary_function_concept_id:
            _validate_active_concept(cur, payload.primary_function_concept_id)

        try:
            cur.execute(
                "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at, reviewed_at) "
                "VALUES ('role_archetype', %s, 'active', 'curator', now(), now()) RETURNING id",
                (payload.canonical_name,),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(400, "a role_archetype with this canonical name already exists")
        concept_id = str(cur.fetchone()["id"])

        cur.execute(
            "INSERT INTO jobber.role_archetype_detail "
            "(concept_id, seniority_band, primary_function_concept_id, typical_market, notes) "
            "VALUES (%s, %s, %s, %s, %s)",
            (concept_id, payload.seniority_band, payload.primary_function_concept_id, payload.typical_market, payload.notes),
        )

        if role_ids:
            cur.execute(
                "UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = ANY(%s::uuid[])",
                (concept_id, role_ids),
            )
    return {"id": concept_id, "status": "created", "assigned_count": len(role_ids)}


def _validate_role_ids(cur, role_instance_ids: list[str]) -> list[str]:
    if not role_instance_ids:
        return []
    cur.execute("SELECT id FROM jobber.role_instance WHERE id = ANY(%s::uuid[])", (role_instance_ids,))
    found = {str(r["id"]) for r in cur.fetchall()}
    missing = [rid for rid in role_instance_ids if rid not in found]
    if missing:
        raise HTTPException(400, f"role_instance ids not found: {missing}")
    return list(role_instance_ids)


def _validate_active_concept(cur, concept_id: str) -> None:
    cur.execute("SELECT 1 FROM jobber.concept WHERE id = %s AND status = 'active'", (concept_id,))
    if not cur.fetchone():
        raise HTTPException(400, "primary_function_concept_id does not exist or is not active")


def _get_archetype_or_404(cur, archetype_id: str, *, require_active: bool = False) -> dict:
    cur.execute(
        "SELECT id, canonical_name, status FROM jobber.concept WHERE id = %s AND type_code = 'role_archetype'",
        (archetype_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "role_archetype not found")
    row = dict(row)
    if require_active and row["status"] != "active":
        raise HTTPException(400, "role_archetype is not active — reactivate it before assigning new roles")
    return row


@router.get("/{archetype_id}")
def get_archetype(archetype_id: str):
    with db_cursor() as cur:
        archetype = _get_archetype_or_404(cur, archetype_id)
        cur.execute(
            """
            SELECT c.id, c.canonical_name, c.status, c.created_at, c.reviewed_at,
                   rad.seniority_band, rad.primary_function_concept_id, rad.typical_market, rad.notes
            FROM jobber.concept c
            JOIN jobber.role_archetype_detail rad ON rad.concept_id = c.id
            WHERE c.id = %s
            """,
            (archetype_id,),
        )
        detail = _row_to_archetype(cur.fetchone())
        cur.execute(
            "SELECT id, title, organisation, country, seniority_level, instance_type "
            "FROM jobber.role_instance WHERE archetype_concept_id = %s ORDER BY title",
            (archetype_id,),
        )
        roles = [
            {
                "id": str(r["id"]),
                "title": r["title"],
                "organisation": r["organisation"],
                "country": r["country"],
                "seniority_level": r["seniority_level"],
                "instance_type": r["instance_type"],
            }
            for r in cur.fetchall()
        ]
    detail["roles"] = roles
    detail["role_count"] = len(roles)
    return detail


@router.put("/{archetype_id}")
def update_archetype(archetype_id: str, payload: ArchetypeUpdate):
    with db_cursor() as cur:
        _get_archetype_or_404(cur, archetype_id)

        concept_fields = {k: v for k, v in (("canonical_name", payload.canonical_name), ("status", payload.status)) if v is not None}
        if concept_fields:
            set_clause = ", ".join(f"{k} = %s" for k in concept_fields)
            try:
                cur.execute(
                    f"UPDATE jobber.concept SET {set_clause}, reviewed_at = now() WHERE id = %s",
                    [*concept_fields.values(), archetype_id],
                )
            except psycopg.errors.UniqueViolation:
                raise HTTPException(400, "a role_archetype with this canonical name already exists")

        if payload.primary_function_concept_id:
            _validate_active_concept(cur, payload.primary_function_concept_id)

        detail_fields = {
            k: v
            for k, v in (
                ("seniority_band", payload.seniority_band),
                ("primary_function_concept_id", payload.primary_function_concept_id),
                ("typical_market", payload.typical_market),
                ("notes", payload.notes),
            )
            if v is not None
        }
        if detail_fields:
            set_clause = ", ".join(f"{k} = %s" for k in detail_fields)
            cur.execute(
                f"UPDATE jobber.role_archetype_detail SET {set_clause} WHERE concept_id = %s",
                [*detail_fields.values(), archetype_id],
            )
    return {"id": archetype_id, "status": "updated"}


@router.post("/{archetype_id}/assign")
def assign_roles(archetype_id: str, payload: ArchetypeAssign):
    """Assigns (or re-assigns, from a different archetype) the given roles to
    this archetype. Rejects a target that is not an active role_archetype
    concept — assignment onto a non-role_archetype concept, or a deprecated
    one, is never silently accepted."""
    with db_cursor() as cur:
        _get_archetype_or_404(cur, archetype_id, require_active=True)
        role_ids = _validate_role_ids(cur, payload.role_instance_ids)
        cur.execute(
            "UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = ANY(%s::uuid[])",
            (archetype_id, role_ids),
        )
    return {"id": archetype_id, "status": "assigned", "assigned_count": len(role_ids)}
