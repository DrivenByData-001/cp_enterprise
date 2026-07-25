import numpy as np
from fastapi import APIRouter
from sklearn.decomposition import PCA

from ..db import db_cursor
from ..embeddings import loads_vec

router = APIRouter(prefix="/api/space", tags=["space"])


@router.get("")
def get_space():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, organisation, career_track, node_type, is_plausible, embedding "
            "FROM job_roles WHERE embedding IS NOT NULL"
        )
        roles = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT embedding FROM profile_snapshots WHERE is_current = 1 "
            "ORDER BY created_at DESC LIMIT 1"
        )
        prow = cur.fetchone()

    profile_vec = loads_vec(prow["embedding"]) if prow else []

    vectors = [loads_vec(r["embedding"]) for r in roles]
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
                "node_type": role["node_type"],
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
