import numpy as np
from fastapi import APIRouter
from sklearn.decomposition import PCA

from ..db import db_cursor
from ..embeddings import get_embedding, get_embeddings

router = APIRouter(prefix="/api/space", tags=["space"])


@router.get("")
def get_space():
    with db_cursor() as cur:
        cur.execute(
            "SELECT ri.id, ri.title, ri.organisation, ri.career_track, ri.kind, lra.is_plausible "
            "FROM jobber.role_instance ri "
            "LEFT JOIN jobber.legacy_role_analysis lra ON lra.role_instance_id = ri.id"
        )
        all_roles = cur.fetchall()
        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in all_roles])
        roles = [r for r in all_roles if r["id"] in vec_by_id]

        cur.execute(
            "SELECT id FROM jobber.profile_snapshots WHERE is_current = TRUE ORDER BY created_at DESC LIMIT 1"
        )
        prow = cur.fetchone()
        profile_vec = get_embedding(cur, "profile_snapshot", prow["id"]) if prow else []

    vectors = [vec_by_id[r["id"]] for r in roles]
    if profile_vec:
        vectors.append(profile_vec)

    if len(vectors) < 2:
        return {"points": [], "profile": None, "note": "Need at least 2 embedded points to project."}

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
                "node_type": role["kind"],
                "is_plausible": bool(role["is_plausible"]) if role["is_plausible"] is not None else None,
                "x": float(x),
                "y": float(y),
                "z": float(z),
            }
        )

    profile_point = None
    if profile_vec:
        px, py, pz = coords[-1]
        profile_point = {"x": float(px), "y": float(py), "z": float(pz)}

    return {"points": points, "profile": profile_point}
