"""GET /api/roles temporal filtering + server-side pagination (docs/18 §3/§5).
Real Postgres test database throughout — no mocking beyond conftest's
deterministic embedding stub."""

from datetime import date, timedelta

from app import db


def _role(cur, title, posting_date=None, career_track="actuarial"):
    return db.upsert_role_instance(
        cur, None,
        {"instance_type": "observed_posting", "title": title, "posting_date": posting_date, "career_track": career_track},
        skills=[],
    )


def test_default_period_is_recent_and_includes_null_dates(client):
    today = date.today()
    old_date = (today - timedelta(days=365 * 6)).isoformat()  # well outside the recent window
    recent_date = (today - timedelta(days=30)).isoformat()

    with db.db_cursor() as cur:
        old_id = _role(cur, "Old role", posting_date=old_date)
        recent_id = _role(cur, "Recent role", posting_date=recent_date)
        undated_id = _role(cur, "Undated role", posting_date=None)

    body = client.get("/api/roles").json()
    ids = {r["id"] for r in body["items"]}
    assert body["period"] == "recent"
    assert recent_id in ids
    assert undated_id in ids  # an unknown posting date must never be treated as "old"
    assert old_id not in ids


def test_period_all_includes_every_year(client):
    with db.db_cursor() as cur:
        old_id = _role(cur, "Very old role", posting_date="2008-01-01")

    body = client.get("/api/roles", params={"period": "all"}).json()
    assert body["period"] == "all"
    assert old_id in {r["id"] for r in body["items"]}


def test_explicit_year_filter(client):
    with db.db_cursor() as cur:
        y2015 = _role(cur, "2015 role", posting_date="2015-06-01")
        y2020 = _role(cur, "2020 role", posting_date="2020-06-01")

    body = client.get("/api/roles", params={"year": 2015}).json()
    ids = {r["id"] for r in body["items"]}
    assert body["period"] == "year"
    assert y2015 in ids
    assert y2020 not in ids


def test_explicit_date_range_filter(client):
    with db.db_cursor() as cur:
        _role(cur, "Before range", posting_date="2011-01-01")
        in_range = _role(cur, "In range", posting_date="2012-06-01")
        _role(cur, "After range", posting_date="2014-01-01")

    body = client.get("/api/roles", params={"date_from": "2012-01-01", "date_to": "2013-01-01"}).json()
    ids = {r["id"] for r in body["items"]}
    assert body["period"] == "range"
    assert ids == {in_range}


def test_year_range_reflects_full_corpus_regardless_of_active_filter(client):
    with db.db_cursor() as cur:
        _role(cur, "Oldest", posting_date="2009-01-01")
        _role(cur, "Newest", posting_date="2024-01-01")

    body = client.get("/api/roles", params={"period": "all"}).json()
    assert body["year_range"] == {"min": 2009, "max": 2024}


def test_pagination_limits_response_and_reports_total(client):
    with db.db_cursor() as cur:
        for i in range(25):
            _role(cur, f"Role {i}", posting_date="2024-01-01")

    page1 = client.get("/api/roles", params={"period": "all", "limit": 10, "offset": 0}).json()
    assert len(page1["items"]) == 10
    assert page1["total"] == 25
    assert page1["limit"] == 10
    assert page1["offset"] == 0

    page3 = client.get("/api/roles", params={"period": "all", "limit": 10, "offset": 20}).json()
    assert len(page3["items"]) == 5

    page1_ids = {r["id"] for r in page1["items"]}
    page3_ids = {r["id"] for r in page3["items"]}
    assert page1_ids.isdisjoint(page3_ids)


def test_pagination_rejects_oversized_limit(client):
    resp = client.get("/api/roles", params={"limit": 10000})
    assert resp.status_code == 422


def test_list_roles_reports_extraction_quality_none_for_non_pipeline_roles(client):
    with db.db_cursor() as cur:
        _role(cur, "Manually captured role", posting_date="2024-01-01")

    body = client.get("/api/roles", params={"period": "all"}).json()
    assert all(r["extraction_quality"] is None for r in body["items"])
