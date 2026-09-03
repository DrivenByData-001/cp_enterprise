from fastapi import APIRouter, HTTPException, Query

from ..db import db_cursor, row_to_dict, upsert_role_instance
from ..embeddings import cosine_similarity, get_embedding, get_embeddings
from ..models import JobPostingImport
from .import_routes import posting_columns

router = APIRouter(prefix="/api/roles", tags=["roles"])

_ROLE_JSON_COLUMNS = ("top_adjacent_roles", "typical_tasks", "skill_decomposition", "technical_subjects", "raw_json")


def _current_profile_vector(cur) -> list[float]:
    cur.execute(
        "SELECT id FROM jobber.profile_snapshots WHERE is_current = TRUE ORDER BY created_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    return get_embedding(cur, "profile_snapshot", row["id"]) if row else []


def _path_to_target(cur, target_id: int, target_vec: list[float], profile_vec: list[float]) -> dict:
    """Rank real postings as stepping-stones between the profile and a target's embedding."""
    cur.execute(
        "SELECT id, title, organisation, career_track FROM jobber.role_instance "
        "WHERE kind = 'posting' AND id != %s",
        (target_id,),
    )
    candidates = cur.fetchall()
    vec_by_id = get_embeddings(cur, "role_instance", [c["id"] for c in candidates])

    stepping_stones = []
    for c in candidates:
        sim_to_target = cosine_similarity(target_vec, vec_by_id.get(c["id"], []))
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
    concept_id: int | None = None,
    min_similarity: float | None = None,
    sort: str = Query("similarity", pattern="^(similarity|posting_date|captured_at|title)$"),
):
    with db_cursor() as cur:
        profile_vec = _current_profile_vector(cur)

        filters = ""
        params: list = []
        if career_track:
            filters += " AND ri.career_track = %s"
            params.append(career_track)
        if concept_id is not None:
            filters += " AND ri.id IN (SELECT role_instance_id FROM jobber.role_skill_observation WHERE resolved_concept_id = %s)"
            params.append(concept_id)

        cur.execute(
            "SELECT ri.*, lra.* FROM jobber.role_instance ri "
            "LEFT JOIN jobber.legacy_role_analysis lra ON lra.role_instance_id = ri.id "
            "WHERE ri.kind = 'posting'" + filters,
            params,
        )
        rows = [row_to_dict(r, _ROLE_JSON_COLUMNS) for r in cur.fetchall()]
        for r in rows:
            r["node_type"] = r["kind"]

        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in rows])

    for r in rows:
        r["similarity"] = cosine_similarity(profile_vec, vec_by_id.get(r["id"], [])) if profile_vec else None

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
        cur.execute(
            "SELECT ri.*, lra.* FROM jobber.role_instance ri "
            "LEFT JOIN jobber.legacy_role_analysis lra ON lra.role_instance_id = ri.id "
            "WHERE ri.id = %s",
            (role_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "role not found")
        role = row_to_dict(row, _ROLE_JSON_COLUMNS)
        role["node_type"] = role["kind"]

        cur.execute(
            "SELECT name, category, importance, requirement_type, resolved_concept_id "
            "FROM jobber.role_skill_observation WHERE role_instance_id = %s",
            (role_id,),
        )
        role["skills"] = cur.fetchall()

        profile_vec = _current_profile_vector(cur)
        role_vec = get_embedding(cur, "role_instance", role_id)

        if role["kind"] != "posting" and role_vec:
            role["path"] = _path_to_target(cur, role_id, role_vec, profile_vec)

    role["similarity"] = cosine_similarity(profile_vec, role_vec) if profile_vec else None
    return role


@router.put("/{role_id}")
def update_role(role_id: int, payload: JobPostingImport):
    with db_cursor() as cur:
        cur.execute("SELECT kind FROM jobber.role_instance WHERE id = %s", (role_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "role not found")
    if row["kind"] != "posting":
        raise HTTPException(400, "this is a target role — edit it via PUT /api/targets/{id}")

    skills = [s.model_dump() for s in payload.skills]
    with db_cursor() as cur:
        columns = posting_columns(cur, payload)
        embedding_text = columns.pop("_embedding_text")
        upsert_role_instance(cur, role_id, columns, skills)
        if embedding_text:
            from ..embeddings import embed_text, set_embedding

            vector = embed_text(embedding_text)
            if vector:
                set_embedding(cur, "role_instance", role_id, vector)
    return {"id": role_id, "status": "updated"}


@router.delete("/{role_id}")
def delete_role(role_id: int):
    with db_cursor() as cur:
        cur.execute("DELETE FROM jobber.role_instance WHERE id = %s", (role_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "role not found")
        cur.execute(
            "DELETE FROM jobber.d_embedding WHERE owner_kind = 'role_instance' AND owner_id = %s",
            (role_id,),
        )
    return {"status": "deleted"}
