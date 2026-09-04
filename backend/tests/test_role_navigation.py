"""Role navigation regression coverage (docs/18 §4): "Dashboard role cards
-> /roles/:id" and "Space node click -> /roles/:id" are both frontend
routing (React Router `Link`/`navigate`, verified visually against a real
browser during this pass — see the deliverable report) built on exactly one
contract this backend must hold: *every id either listing endpoint returns
must resolve via GET /api/roles/{id}*. That contract is what these tests
prove, at a corpus size (50+ roles, paginated) large enough to catch a
pagination/filtering regression that a handful of roles wouldn't."""

from datetime import date, timedelta

from app import db, embeddings


def _role(title, posting_date, node_type="posting"):
    kind_map = {"posting": ("observed_posting", None), "target_real": ("user_defined_target", "real_role")}
    instance_type, target_basis = kind_map[node_type]
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(
            cur, None,
            {"instance_type": instance_type, "target_basis": target_basis, "title": title, "posting_date": posting_date},
            skills=[],
        )
        vec = embeddings.embed_text(title)
        if vec:
            embeddings.set_embedding(cur, "role_instance", role_id, vec)
    return role_id


def test_every_dashboard_listed_role_id_resolves_via_detail_endpoint(client):
    recent = (date.today() - timedelta(days=10)).isoformat()
    ids = {_role(f"Nav role {i}", posting_date=recent) for i in range(55)}

    seen = set()
    offset = 0
    while True:
        page = client.get("/api/roles", params={"period": "all", "limit": 20, "offset": offset}).json()
        if not page["items"]:
            break
        for row in page["items"]:
            detail = client.get(f"/api/roles/{row['id']}")
            assert detail.status_code == 200, f"role {row['id']} listed on Dashboard but not resolvable"
            assert detail.json()["title"] == row["title"]
            seen.add(row["id"])
        offset += 20
        if offset > page["total"] + 20:
            break  # safety valve against an infinite loop on a broken total

    assert ids <= seen  # every seeded role was both listed and independently resolvable


def test_dashboard_pagination_does_not_corrupt_or_duplicate_ids_across_pages(client):
    recent = (date.today() - timedelta(days=5)).isoformat()
    for i in range(45):
        _role(f"Paged role {i}", posting_date=recent)

    all_ids = []
    offset = 0
    while True:
        page = client.get("/api/roles", params={"period": "all", "limit": 20, "offset": offset}).json()
        if not page["items"]:
            break
        all_ids.extend(r["id"] for r in page["items"])
        offset += 20

    assert len(all_ids) == len(set(all_ids))  # no id repeated across pages
    for role_id in all_ids:
        assert client.get(f"/api/roles/{role_id}").status_code == 200


def test_every_space_point_id_resolves_via_detail_endpoint_postings_and_targets(client):
    posting_ids = {_role(f"Space nav posting {i}", posting_date="2020-01-01") for i in range(10)}
    target_ids = {_role(f"Space nav target {i}", posting_date=None, node_type="target_real") for i in range(3)}

    body = client.get("/api/space").json()
    point_ids = {p["id"] for p in body["points"]}
    assert posting_ids <= point_ids
    assert target_ids <= point_ids

    for point_id in point_ids:
        detail = client.get(f"/api/roles/{point_id}")
        assert detail.status_code == 200, f"Space point {point_id} not resolvable via role detail"


def test_space_point_id_resolves_correctly_under_a_temporal_filter(client):
    # A PCA projection needs >= 2 embedded points (pre-existing rule,
    # unrelated to navigation) — a second in-year role keeps the filtered
    # set above that floor.
    role_id = _role("Filtered space nav role", posting_date="2012-01-01")
    _role("Filtered space nav role sibling", posting_date="2012-06-01")

    body = client.get("/api/space", params={"year": 2012}).json()
    ids = {p["id"] for p in body["points"]}
    assert role_id in ids
    assert client.get(f"/api/roles/{role_id}").json()["title"] == "Filtered space nav role"
