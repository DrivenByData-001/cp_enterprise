"""GET /api/roles temporal filtering + server-side pagination (docs/18 §3/§5;
default period + sort per the Explicit Role Save / Current Roles brief §6/§13).
Real Postgres test database throughout — no mocking beyond conftest's
deterministic embedding stub."""

from datetime import date, datetime, timedelta, timezone

from app import db


def _role(cur, title, posting_date=None, career_track="actuarial"):
    return db.upsert_role_instance(
        cur, None,
        {"instance_type": "observed_posting", "title": title, "posting_date": posting_date, "career_track": career_track},
        skills=[],
    )


def _role_with_document(cur, title, posting_date=None, captured_at=None, career_track="actuarial"):
    """Like `_role`, but backed by a real linked document — needed whenever a
    test cares about `captured_at` (an undated role's only "current" signal,
    and Current's own default sort key), since a bare role_instance with no
    document_id carries no captured_at at all."""
    document_id, _ = db.create_document(cur, kind="job_posting", content_text=f"{title} posting text.", provenance_quality="original")
    if captured_at is not None:
        cur.execute("UPDATE jobber.document SET captured_at = %s WHERE id = %s", (captured_at, document_id))
    return db.upsert_role_instance(
        cur, None,
        {
            "instance_type": "observed_posting", "title": title, "posting_date": posting_date,
            "career_track": career_track, "document_id": document_id,
        },
        skills=[],
    )


def test_default_period_is_current_and_includes_undated_recently_captured_roles(client):
    """Brief §6.1/§13: current-year posting date is included; a prior-year
    posting date is excluded even from the default view; an undated role is
    included only when its own source document was captured this calendar
    year, never merely because it has no posting date at all (that's
    'recent'/'unknown_date's job, not Current's)."""
    today = date.today()
    last_year = today.year - 1

    with db.db_cursor() as cur:
        current_dated = _role_with_document(cur, "Current dated role", posting_date=today.isoformat())
        old_dated = _role_with_document(cur, "Old dated role", posting_date=date(last_year, 6, 15).isoformat())
        current_undated = _role_with_document(
            cur, "Newly captured undated role", posting_date=None, captured_at=datetime.now(timezone.utc),
        )
        stale_undated = _role_with_document(
            cur, "Old capture undated role", posting_date=None, captured_at=datetime(last_year, 6, 15, tzinfo=timezone.utc),
        )

    body = client.get("/api/roles").json()
    ids = {r["id"] for r in body["items"]}
    assert body["period"] == "current"
    assert current_dated in ids
    assert current_undated in ids
    assert old_dated not in ids  # a dated-but-old role is never pulled in by Current
    assert stale_undated not in ids  # an undated role's stale capture doesn't count as current either


def test_current_period_defaults_to_newest_captured_first(client):
    """Brief §6.3: Current's own default sort is newest/recently-captured
    first, not similarity — a role the user just saved must be immediately
    visible near the top."""
    now = datetime.now(timezone.utc)
    with db.db_cursor() as cur:
        oldest = _role_with_document(cur, "Oldest captured", captured_at=now - timedelta(hours=2))
        middle = _role_with_document(cur, "Middle captured", captured_at=now - timedelta(hours=1))
        newest = _role_with_document(cur, "Newest captured", captured_at=now)

    body = client.get("/api/roles").json()
    ids_in_order = [r["id"] for r in body["items"]]
    assert ids_in_order.index(newest) < ids_in_order.index(middle) < ids_in_order.index(oldest)


def test_current_period_falls_back_to_posting_date_when_captured_at_is_missing(client):
    """A role with no linked document (so no captured_at at all — e.g. a
    legacy/bulk-imported role) still participates in Current's default
    newest-first sort via its own posting_date, compared chronologically
    against another role's real captured_at, rather than being stranded at
    a meaningless position for lack of a captured_at to compare."""
    today = date.today()
    with db.db_cursor() as cur:
        earlier_posting = _role(cur, "Legacy role, no document", posting_date=(today - timedelta(days=1)).isoformat())
        later_capture = _role_with_document(cur, "Freshly captured role", captured_at=datetime.now(timezone.utc))

    body = client.get("/api/roles").json()
    ids_in_order = [r["id"] for r in body["items"]]
    assert set(ids_in_order) == {earlier_posting, later_capture}
    assert ids_in_order.index(later_capture) < ids_in_order.index(earlier_posting)


def test_explicit_similarity_sort_still_works_under_default_current_period(client):
    """Explicit query parameters win over Current's own default (brief
    §6.3/§13) — similarity sorting must remain fully available, it merely
    stops being the default."""
    today = date.today()
    with db.db_cursor() as cur:
        role_a = _role_with_document(cur, "Role A", posting_date=today.isoformat())
        role_b = _role_with_document(cur, "Role B", posting_date=today.isoformat())

    body = client.get("/api/roles", params={"sort": "similarity"}).json()
    assert body["period"] == "current"
    assert {r["id"] for r in body["items"]} == {role_a, role_b}


def test_explicit_captured_at_sort_matches_current_default_semantics(client):
    """An explicit `sort=captured_at` must behave identically to Current's
    own default sort — one comparator (posting_date fallback, null-last),
    not two subtly different ones depending on how you reached it. Checked
    under `period=all` specifically, so this isn't just re-testing the
    default-period case."""
    today = date.today()
    with db.db_cursor() as cur:
        earlier_posting = _role(cur, "Legacy role, no document", posting_date=(today - timedelta(days=1)).isoformat())
        later_capture = _role_with_document(cur, "Freshly captured role", captured_at=datetime.now(timezone.utc))

    body = client.get("/api/roles", params={"period": "all", "sort": "captured_at"}).json()
    ids_in_order = [r["id"] for r in body["items"]]
    assert ids_in_order.index(later_capture) < ids_in_order.index(earlier_posting)


def test_posting_date_sort_puts_undated_roles_last(client):
    """Regression: folding "is the value missing" into the same tuple as the
    value and reversing the *whole* tuple for descending order used to put
    missing values first, the opposite of the intended null-last ordering."""
    with db.db_cursor() as cur:
        dated = _role(cur, "Dated role", posting_date="2020-06-01")
        undated = _role(cur, "Undated role", posting_date=None)

    body = client.get("/api/roles", params={"period": "all", "sort": "posting_date"}).json()
    ids_in_order = [r["id"] for r in body["items"]]
    assert ids_in_order.index(dated) < ids_in_order.index(undated)


def test_period_recent_still_works(client):
    today = date.today()
    old_date = (today - timedelta(days=365 * 6)).isoformat()  # well outside the recent window
    recent_date = (today - timedelta(days=30)).isoformat()

    with db.db_cursor() as cur:
        old_id = _role(cur, "Old role", posting_date=old_date)
        recent_id = _role(cur, "Recent role", posting_date=recent_date)
        undated_id = _role(cur, "Undated role", posting_date=None)

    body = client.get("/api/roles", params={"period": "recent"}).json()
    ids = {r["id"] for r in body["items"]}
    assert body["period"] == "recent"
    assert recent_id in ids
    assert undated_id in ids  # an unknown posting date must never be treated as "old"
    assert old_id not in ids


def test_period_unknown_date_returns_only_undated_roles(client):
    """Source-aware ingest cleanup, problem #8: unknown posting dates must be
    explicitly findable, distinct from 'recent' (which mixes them in
    alongside genuinely recent dated roles)."""
    with db.db_cursor() as cur:
        dated_id = _role(cur, "Dated role", posting_date="2025-06-13")
        undated_id = _role(cur, "Undated role", posting_date=None)

    body = client.get("/api/roles", params={"period": "unknown_date"}).json()
    ids = {r["id"] for r in body["items"]}
    assert body["period"] == "unknown_date"
    assert undated_id in ids
    assert dated_id not in ids


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
