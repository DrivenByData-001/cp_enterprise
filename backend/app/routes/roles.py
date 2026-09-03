from fastapi import APIRouter, HTTPException, Query

from ..db import db_cursor, flatten_role_instance, upsert_role_instance
from ..embeddings import cosine_similarity, ensure_profile_embedding, get_embedding, get_embeddings
from ..models import JobPostingImport
from .import_routes import posting_columns

router = APIRouter(prefix="/api/roles", tags=["roles"])


def _path_to_target(cur, target_id: str, target_vec: list[float], profile_vec: list[float]) -> dict:
    """Rank real postings as stepping-stones between the profile and a target's embedding."""
    cur.execute(
        "SELECT id, title, organisation, career_track FROM jobber.role_instance "
        "WHERE instance_type = 'observed_posting' AND id != %s",
        (target_id,),
    )
    candidates = cur.fetchall()
    vec_by_id = get_embeddings(cur, "role_instance", [str(c["id"]) for c in candidates])

    stepping_stones = []
    for c in candidates:
        sim_to_target = cosine_similarity(target_vec, vec_by_id.get(str(c["id"]), []))
        if sim_to_target is None:
            continue
        stepping_stones.append(
            {
                "id": str(c["id"]),
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
    concept_id: str | None = None,
    min_similarity: float | None = None,
    sort: str = Query("similarity", pattern="^(similarity|posting_date|captured_at|title)$"),
):
    with db_cursor() as cur:
        _, profile_vec = ensure_profile_embedding(cur)

        filters = ""
        params: list = []
        if career_track:
            filters += " AND ri.career_track = %s"
            params.append(career_track)
        if concept_id is not None:
            filters += " AND ri.id IN (SELECT role_instance_id FROM jobber.role_skill_observation WHERE canonical_concept_id = %s)"
            params.append(concept_id)

        cur.execute(
            "SELECT ri.*, d.url AS url, d.captured_at AS captured_at FROM jobber.role_instance ri "
            "LEFT JOIN jobber.document d ON d.id = ri.document_id "
            "WHERE ri.instance_type = 'observed_posting'" + filters,
            params,
        )
        rows = [flatten_role_instance(r) for r in cur.fetchall()]

        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in rows])

    for r in rows:
        r["similarity"] = cosine_similarity(profile_vec, vec_by_id.get(r["id"], [])) if profile_vec else None

    if min_similarity is not None:
        rows = [r for r in rows if r["similarity"] is not None and r["similarity"] >= min_similarity]

    if sort == "similarity":
        rows.sort(key=lambda r: (r["similarity"] is None, -(r["similarity"] or 0)))
    elif sort in ("posting_date", "captured_at", "title"):
        rows.sort(key=lambda r: (r.get(sort) is None, str(r.get(sort) or "")), reverse=(sort != "title"))

    return rows


@router.get("/{role_id}")
def get_role(role_id: str):
    with db_cursor() as cur:
        cur.execute(
            "SELECT ri.*, d.url AS url, d.captured_at AS captured_at FROM jobber.role_instance ri "
            "LEFT JOIN jobber.document d ON d.id = ri.document_id "
            "WHERE ri.id = %s",
            (role_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "role not found")
        role = flatten_role_instance(row)

        cur.execute(
            "SELECT surface_form AS name, category, importance, requirement_type, canonical_concept_id AS resolved_concept_id "
            "FROM jobber.role_skill_observation WHERE role_instance_id = %s",
            (role_id,),
        )
        role["skills"] = [
            {**s, "resolved_concept_id": str(s["resolved_concept_id"]) if s["resolved_concept_id"] else None}
            for s in cur.fetchall()
        ]

        _, profile_vec = ensure_profile_embedding(cur)
        role_vec = get_embedding(cur, "role_instance", role_id)

        if role["node_type"] != "posting" and role_vec:
            role["path"] = _path_to_target(cur, role_id, role_vec, profile_vec)

    role["similarity"] = cosine_similarity(profile_vec, role_vec) if profile_vec else None
    return role


@router.put("/{role_id}")
def update_role(role_id: str, payload: JobPostingImport):
    with db_cursor() as cur:
        cur.execute("SELECT instance_type FROM jobber.role_instance WHERE id = %s", (role_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "role not found")
    if row["instance_type"] != "observed_posting":
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
def delete_role(role_id: str):
    with db_cursor() as cur:
        cur.execute("DELETE FROM jobber.role_instance WHERE id = %s", (role_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "role not found")
        cur.execute(
            "DELETE FROM jobber.d_embedding WHERE owner_kind = 'role_instance' AND owner_id = %s",
            (role_id,),
        )
    return {"status": "deleted"}
