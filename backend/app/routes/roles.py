from fastapi import APIRouter, HTTPException, Query

from ..db import db_cursor, flatten_role_instance, upsert_role_instance
from ..document_processing import role_extraction_quality, role_extraction_quality_bulk
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


# docs/18 §3 (Dashboard temporal filter): the historical corpus (~2008-2025)
# must not drown out recent/current roles in the view checked day to day, but
# must remain explicitly, fully browsable — never hidden at the persistence
# layer (README's own pre-existing note on this, now implemented). "Recent"
# is a named, documented window, not a guess: the last DEFAULT_RECENT_YEARS
# calendar years, plus every role with no known posting_date at all (a
# freshly captured role with unknown/unset posting date is exactly the kind
# of "current" role this default must not hide).
DEFAULT_RECENT_YEARS = 3


@router.get("")
def list_roles(
    career_track: str | None = None,
    concept_id: str | None = None,
    min_similarity: float | None = None,
    sort: str = Query("similarity", pattern="^(similarity|posting_date|captured_at|title)$"),
    period: str = Query("recent", pattern="^(recent|all)$"),
    year: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Server-side filtered, sorted, and paginated (brief: "every role loaded
    into the browser" must not be required). Temporal precedence: an
    explicit `year` or `date_from`/`date_to` always wins; otherwise `period`
    decides ('recent' — the default — or 'all'). Pagination is applied last,
    after similarity is computed and the full matching set is sorted — see
    the comment above the slice below for why that's still "server-side"
    pagination in the sense that matters (the browser never receives
    unpaginated rows), even though the DB query itself isn't LIMIT/OFFSET'd:
    at this corpus's scale (~300 roles) an in-memory sort after a SQL-side
    similarity-independent filter is simpler and no less correct than
    pushing cosine ranking into SQL, and every filter that *can* run in SQL
    (track, concept, temporal) already does.
    """
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

        applied_period = "all"
        if year is not None:
            filters += " AND EXTRACT(YEAR FROM ri.posting_date) = %s"
            params.append(year)
            applied_period = "year"
        elif date_from or date_to:
            if date_from:
                filters += " AND ri.posting_date >= %s"
                params.append(date_from)
            if date_to:
                filters += " AND ri.posting_date <= %s"
                params.append(date_to)
            applied_period = "range"
        elif period == "recent":
            filters += " AND (ri.posting_date IS NULL OR ri.posting_date >= (CURRENT_DATE - (%s || ' years')::interval))"
            params.append(DEFAULT_RECENT_YEARS)
            applied_period = "recent"

        cur.execute(
            "SELECT ri.*, d.url AS url, d.captured_at AS captured_at FROM jobber.role_instance ri "
            "LEFT JOIN jobber.document d ON d.id = ri.document_id "
            "WHERE ri.instance_type = 'observed_posting'" + filters,
            params,
        )
        rows = [flatten_role_instance(r) for r in cur.fetchall()]

        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in rows])
        quality_by_id = role_extraction_quality_bulk(cur, [r["id"] for r in rows])

        cur.execute(
            "SELECT MIN(EXTRACT(YEAR FROM posting_date)) AS min_year, MAX(EXTRACT(YEAR FROM posting_date)) AS max_year "
            "FROM jobber.role_instance WHERE instance_type = 'observed_posting' AND posting_date IS NOT NULL"
        )
        year_bounds = cur.fetchone()

    for r in rows:
        r["similarity"] = cosine_similarity(profile_vec, vec_by_id.get(r["id"], [])) if profile_vec else None
        r["extraction_quality"] = quality_by_id.get(r["id"])

    if min_similarity is not None:
        rows = [r for r in rows if r["similarity"] is not None and r["similarity"] >= min_similarity]

    if sort == "similarity":
        rows.sort(key=lambda r: (r["similarity"] is None, -(r["similarity"] or 0)))
    elif sort in ("posting_date", "captured_at", "title"):
        rows.sort(key=lambda r: (r.get(sort) is None, str(r.get(sort) or "")), reverse=(sort != "title"))

    total = len(rows)
    page = rows[offset : offset + limit]

    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "period": applied_period,
        "year_range": (
            {"min": int(year_bounds["min_year"]), "max": int(year_bounds["max_year"])}
            if year_bounds and year_bounds["min_year"] is not None
            else None
        ),
    }


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

        # The authoritative ok/partial signal (docs/18 §5) — None for a role
        # never processed through this pipeline (legacy/bulk import, hand
        # edit); the frontend falls back to role['extraction_status'] then.
        role["extraction_quality"] = role_extraction_quality(cur, role_id)

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
