"""GET /api/space temporal filtering (docs/18 §2). Real Postgres test
database + conftest's deterministic embedding stub (network to
huggingface.co is blocked in this sandbox)."""

from app import db


def _role(title, posting_date=None, node_type="posting", summary=None):
    """Creates a role_instance and gives it a real (stubbed, per conftest)
    embedding — GET /api/space only ever projects embedded roles, so every
    point this module needs to see in a response must have one."""
    from app.embeddings import embed_text, set_embedding

    kind_map = {"posting": ("observed_posting", None), "target_real": ("user_defined_target", "real_role")}
    instance_type, target_basis = kind_map[node_type]
    columns = {"instance_type": instance_type, "target_basis": target_basis, "title": title, "posting_date": posting_date}
    if summary:
        columns["summary"] = summary

    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(cur, None, columns, skills=[])
        vec = embed_text(title + " " + (summary or ""))
        if vec:
            set_embedding(cur, "role_instance", role_id, vec)
    return role_id


def test_space_defaults_to_all_time(client):
    old_id = _role("Old role", posting_date="2009-01-01")
    recent_id = _role("Recent role", posting_date="2024-01-01")

    body = client.get("/api/space").json()
    ids = {p["id"] for p in body["points"]}
    assert old_id in ids
    assert recent_id in ids


def test_space_year_filter_scopes_postings(client):
    # A PCA projection needs >= 2 embedded points (pre-existing rule,
    # unrelated to temporal filtering) — a second 2010 role keeps the
    # post-filter set above that floor so this test isolates the filter
    # itself rather than tripping the "too few points" short-circuit.
    y2010_a = _role("2010 role A", posting_date="2010-01-01")
    y2010_b = _role("2010 role B", posting_date="2010-06-01")
    y2020 = _role("2020 role", posting_date="2020-01-01")

    body = client.get("/api/space", params={"year": 2010}).json()
    ids = {p["id"] for p in body["points"]}
    assert {y2010_a, y2010_b} <= ids
    assert y2020 not in ids


def test_space_date_range_filter(client):
    before = _role("Before", posting_date="2011-01-01")
    inside_a = _role("Inside A", posting_date="2012-06-01")
    inside_b = _role("Inside B", posting_date="2012-09-01")
    after = _role("After", posting_date="2014-01-01")

    body = client.get("/api/space", params={"date_from": "2012-01-01", "date_to": "2013-01-01"}).json()
    ids = {p["id"] for p in body["points"]}
    assert ids == {inside_a, inside_b}
    assert before not in ids and after not in ids


def test_space_temporal_filter_never_excludes_targets(client):
    _role("Some posting", posting_date="2024-01-01")
    target_a = _role("Target role A", node_type="target_real", summary="Target summary A")
    target_b = _role("Target role B", node_type="target_real", summary="Target summary B")

    body = client.get("/api/space", params={"year": 1999}).json()
    ids = {p["id"] for p in body["points"]}
    assert {target_a, target_b} <= ids


def test_space_year_range_diagnostic_reflects_whole_corpus(client):
    _role("Oldest", posting_date="2008-01-01")
    _role("Newest", posting_date="2025-01-01")

    body = client.get("/api/space", params={"year": 2008}).json()
    assert body["year_range"] == {"min": 2008, "max": 2025}


def test_space_points_carry_posting_date(client):
    role_id = _role("Dated role", posting_date="2019-05-01")
    _role("Other role", posting_date="2019-06-01")

    body = client.get("/api/space").json()
    point = next(p for p in body["points"] if p["id"] == role_id)
    assert point["posting_date"] == "2019-05-01"
