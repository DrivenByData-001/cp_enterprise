import numpy as np
from fastapi import APIRouter
from sklearn.decomposition import PCA

from ..db import db_cursor, instance_type_to_app_kind
from ..embeddings import embedding_model_name, ensure_profile_embedding, get_embeddings, rebuild_role_embeddings

router = APIRouter(prefix="/api/space", tags=["space"])


@router.get("")
def get_space(year: int | None = None, date_from: str | None = None, date_to: str | None = None):
    """Temporal filtering (docs/18 §2): unlike Dashboard, Space defaults to
    **all-time** — the historical cloud's shape is itself analytically
    useful, so nothing is hidden unless the caller asks. `year` /
    `date_from`/`date_to` scope which *postings* are selected before the PCA
    fit runs; targets/synthetic references never carry a posting_date and
    are never excluded by a temporal filter (the WHERE clause below).
    Deliberately the same param names/semantics as GET /api/roles, so a
    future client-side "time travel"/animation step can re-request this
    endpoint across a year range with no server-side redesign — see
    Space.tsx's TemporalControls."""
    filters = ""
    params: list = []
    if year is not None:
        filters += " AND EXTRACT(YEAR FROM posting_date) = %s"
        params.append(year)
    else:
        if date_from:
            filters += " AND posting_date >= %s"
            params.append(date_from)
        if date_to:
            filters += " AND posting_date <= %s"
            params.append(date_to)

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, organisation, career_track, instance_type, target_basis, posting_date, "
            "(legacy_analysis->>'is_plausible')::boolean AS is_plausible "
            "FROM jobber.role_instance "
            "WHERE instance_type != 'observed_posting' OR posting_date IS NULL OR (TRUE" + filters + ")",
            params,
        )
        all_roles = [dict(r, id=str(r["id"])) for r in cur.fetchall()]
        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in all_roles])
        roles = [r for r in all_roles if r["id"] in vec_by_id]

        _, profile_vec = ensure_profile_embedding(cur)

        cur.execute(
            "SELECT MIN(EXTRACT(YEAR FROM posting_date)) AS min_year, MAX(EXTRACT(YEAR FROM posting_date)) AS max_year "
            "FROM jobber.role_instance WHERE instance_type = 'observed_posting' AND posting_date IS NOT NULL"
        )
        year_bounds = cur.fetchone()

    # Diagnostics (brief: distinguish "23 roles loaded, 0 embedded for the
    # current model" from "only one role exists") — included on every
    # response, not only the too-few-points case, so the UI can always show
    # an embedded/total count. year_range reflects the *whole* corpus
    # (unfiltered) regardless of the active filter, so the UI can build a
    # year picker without a second request.
    diagnostics = {
        "role_count": len(all_roles),
        "embedded_role_count": len(roles),
        "embedding_model": embedding_model_name(),
        "year_range": (
            {"min": int(year_bounds["min_year"]), "max": int(year_bounds["max_year"])}
            if year_bounds and year_bounds["min_year"] is not None
            else None
        ),
    }

    vectors = [vec_by_id[r["id"]] for r in roles]
    if profile_vec:
        vectors.append(profile_vec)

    if len(vectors) < 2:
        return {"points": [], "profile": None, "note": "Need at least 2 embedded points to project.", **diagnostics}

    matrix = np.array(vectors)
    n_components = min(3, matrix.shape[0], matrix.shape[1])
    coords = PCA(n_components=n_components).fit_transform(matrix)
    if n_components < 3:
        coords = np.hstack([coords, np.zeros((coords.shape[0], 3 - n_components))])

    points = []
    for role, (x, y, z) in zip(roles, coords[: len(roles)]):
        points.append(
            {
                "id": role["id"],
                "title": role["title"],
                "organisation": role["organisation"],
                "career_track": role["career_track"],
                "node_type": instance_type_to_app_kind(role["instance_type"], role["target_basis"]),
                "is_plausible": role["is_plausible"],
                "posting_date": str(role["posting_date"]) if role["posting_date"] else None,
                "x": float(x),
                "y": float(y),
                "z": float(z),
            }
        )

    profile_point = None
    if profile_vec:
        px, py, pz = coords[-1]
        profile_point = {"x": float(px), "y": float(py), "z": float(pz)}

    return {"points": points, "profile": profile_point, **diagnostics}


@router.post("/rebuild-role-embeddings")
def rebuild_role_embeddings_route(force: bool = False):
    """Explicit, deterministic backfill/rebuild of role embeddings (brief:
    the Space regression's actual fix) — never invoked implicitly by GET
    /api/space itself, which stays a pure read/project operation. `force`
    (default false) only recomputes roles missing a *current-model*
    embedding; pass true to recompute every role's current-model embedding
    from its canonical text."""
    with db_cursor() as cur:
        return rebuild_role_embeddings(cur, missing_only=not force)
