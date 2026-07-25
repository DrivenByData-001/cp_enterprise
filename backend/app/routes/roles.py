from fastapi import APIRouter, HTTPException, Query

from ..db import db_cursor, row_to_dict, upsert_job_role
from ..embeddings import cosine_similarity, loads_vec
from ..models import JobPostingImport
from .import_routes import posting_columns

router = APIRouter(prefix="/api/roles", tags=["roles"])


def _current_profile_vector(cur) -> list[float]:
    cur.execute(
        "SELECT embedding FROM profile_snapshots WHERE is_current = 1 "
        "ORDER BY created_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    return loads_vec(row["embedding"]) if row else []


def _path_to_target(cur, target_id: int, target_vec: list[float], profile_vec: list[float]) -> dict:
    """Rank real postings as stepping-stones between the profile and a target's embedding."""
    cur.execute(
        "SELECT id, title, organisation, career_track, embedding FROM job_roles "
        "WHERE node_type = 'posting' AND embedding IS NOT NULL AND id != ?",
        (target_id,),
    )
    candidates = [dict(r) for r in cur.fetchall()]

    stepping_stones = []
    for c in candidates:
        sim_to_target = cosine_similarity(target_vec, loads_vec(c["embedding"]))
        if sim_to_target is None:
            continue
        stepping_stones.append(
            {
                "id": c["id"],
                "title": c["title"],
                "organisation": c["organisation"],
                "career_track": c["career_track"],
                "similarity_to_target": sim_to_target,
            }
        )
    stepping_stones.sort(key=lambda s: -s["similarity_to_target"])

    return {
        "profile_to_target_similarity": cosine_similarity(profile_vec, target_vec) if profile_vec else None,
        "stepping_stones": stepping_stones[:5],
    }


@router.get("")
def list_roles(
    career_track: str | None = None,
    min_similarity: float | None = None,
    sort: str = Query("similarity", pattern="^(similarity|posting_date|captured_at|title)$"),
):
    with db_cursor() as cur:
        profile_vec = _current_profile_vector(cur)

        query = "SELECT * FROM job_roles WHERE node_type = 'posting'"
        params = []
        if career_track:
            query += " AND career_track = ?"
            params.append(career_track)
        cur.execute(query, params)
        rows = [row_to_dict(r) for r in cur.fetchall()]

        # embedding column was stripped by row_to_dict; re-fetch raw vectors separately
        cur.execute(
            "SELECT id, embedding FROM job_roles WHERE node_type = 'posting'"
            + (" AND career_track = ?" if career_track else ""),
            params,
        )
        vec_by_id = {r["id"]: loads_vec(r["embedding"]) for r in cur.fetchall()}

    for r in rows:
        r["similarity"] = (
            cosine_similarity(profile_vec, vec_by_id.get(r["id"], [])) if profile_vec else None
        )

    if min_similarity is not None:
        rows = [r for r in rows if r["similarity"] is not None and r["similarity"] >= min_similarity]

    if sort == "similarity":
        rows.sort(key=lambda r: (r["similarity"] is None, -(r["similarity"] or 0)))
    elif sort in ("posting_date", "captured_at", "title"):
        rows.sort(key=lambda r: (r.get(sort) is None, r.get(sort) or ""), reverse=(sort != "title"))

    return rows


@router.get("/{role_id}")
def get_role(role_id: int):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM job_roles WHERE id = ?", (role_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "role not found")
        role = row_to_dict(row)

        cur.execute(
            "SELECT name, category, importance, requirement_type FROM job_role_skills WHERE job_role_id = ?",
            (role_id,),
        )
        role["skills"] = [dict(s) for s in cur.fetchall()]

        profile_vec = _current_profile_vector(cur)
        cur.execute("SELECT embedding FROM job_roles WHERE id = ?", (role_id,))
        role_vec = loads_vec(cur.fetchone()["embedding"])

        if role["node_type"] != "posting" and role_vec:
            role["path"] = _path_to_target(cur, role_id, role_vec, profile_vec)

    role["similarity"] = cosine_similarity(profile_vec, role_vec) if profile_vec else None
    return role


@router.put("/{role_id}")
def update_role(role_id: int, payload: JobPostingImport):
    with db_cursor() as cur:
        cur.execute("SELECT node_type FROM job_roles WHERE id = ?", (role_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "role not found")
    if row["node_type"] != "posting":
        raise HTTPException(400, "this is a target role — edit it via PUT /api/targets/{id}")

    skills = [s.model_dump() for s in payload.skills]
    upsert_job_role(role_id, posting_columns(payload), skills)
    return {"id": role_id, "status": "updated"}


@router.delete("/{role_id}")
def delete_role(role_id: int):
    with db_cursor() as cur:
        cur.execute("DELETE FROM job_roles WHERE id = ?", (role_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "role not found")
    return {"status": "deleted"}
