import numpy as np
from fastapi import APIRouter
from sklearn.decomposition import PCA

from ..db import db_cursor, instance_type_to_app_kind
from ..embeddings import ensure_profile_embedding, get_embeddings

router = APIRouter(prefix="/api/space", tags=["space"])


@router.get("")
def get_space():
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, title, organisation, career_track, instance_type, target_basis, "
            "(legacy_analysis->>'is_plausible')::boolean AS is_plausible "
            "FROM jobber.role_instance"
        )
        all_roles = [dict(r, id=str(r["id"])) for r in cur.fetchall()]
        vec_by_id = get_embeddings(cur, "role_instance", [r["id"] for r in all_roles])
        roles = [r for r in all_roles if r["id"] in vec_by_id]

        _, profile_vec = ensure_profile_embedding(cur)

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
                "node_type": instance_type_to_app_kind(role["instance_type"], role["target_basis"]),
                "is_plausible": role["is_plausible"],
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
