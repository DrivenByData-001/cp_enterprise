"""Market analytics read model (docs/25 §11.1) — the market-summary layer
over *every accepted* compensation observation, independent of archetype
assignment. Most of these are pure-function tests against `app.market_analytics`
directly (no database needed, per docs/25 §4's "keep analytics/transformation
logic out of the FastAPI route... so it can be unit-tested as pure/
deterministic logic"); a handful exercise the real HTTP route/database to
prove the accepted-only filter and facet stability actually hold end-to-end.
Every fixture here is synthetic — nothing depends on the live production row
count (docs/25 §11.1)."""

import uuid
from datetime import date

from app import db
from app import market_analytics as ma


def _row(**overrides) -> dict:
    """A minimal, fully-specified evidence row matching the shape
    `fetch_accepted_evidence_rows` produces — every pure function in
    `market_analytics.py` is tested against dicts built by this factory."""
    base = {
        "id": str(uuid.uuid4()),
        "raw_role_label": "Test Role",
        "market_id": None,
        "market_label": None,
        "archetype_concept_id": None,
        "component": "base",
        "pay_period": "annual",
        "employment_basis": None,
        "currency": "GBP",
        "amount_min": None,
        "amount_mid": None,
        "amount_max": None,
        "reported_p25": None,
        "reported_p50": None,
        "reported_p75": None,
        "reported_mean": None,
        "bonus_pct": None,
        "reported_sample_size": None,
        "source_quality": None,
        "source_kind": None,
        "basis": "survey",
        "geography_reported": None,
        "domain_or_practice_area": None,
        "seniority_band_reported": None,
        "experience_band": None,
        "pqe_band": None,
        "document_id": None,
        "page_reference": None,
        "table_reference": None,
        "source_note": None,
        "observed_at": None,
        "period_end": None,
        "created_at": None,
        "provider": None,
        "document_title": None,
        "report_date": None,
    }
    base.update(overrides)
    if "practice_group" not in overrides:
        base["practice_group"] = ma.group_practice_area(base["domain_or_practice_area"])
    if "period_key" not in overrides:
        base["period_key"] = base["period_end"] or base["observed_at"]
    return base


def _market(cur, code="test-analytics-market"):
    cur.execute("INSERT INTO jobber.market (code, label) VALUES (%s, %s) RETURNING id", (code, code))
    return str(cur.fetchone()["id"])


def _insert_observation(
    cur,
    *,
    document_id=None,
    market_id=None,
    archetype_concept_id=None,
    raw_role_label="Test Role",
    component="base",
    pay_period="annual",
    currency="GBP",
    amount_min=None,
    amount_max=None,
    amount_mid=None,
    reported_p25=None,
    reported_p50=None,
    reported_p75=None,
    reported_mean=None,
    reported_sample_size=None,
    basis="survey",
    review_status="accepted",
    source_kind=None,
    geography_reported=None,
    domain_or_practice_area=None,
    experience_band=None,
    pqe_band=None,
    observed_at=None,
    period_end=None,
) -> str:
    cur.execute(
        """
        INSERT INTO jobber.compensation_observation
            (source_key, document_id, market_id, archetype_concept_id, raw_role_label, component, pay_period,
             currency, amount_min, amount_max, amount_mid, reported_p25, reported_p50, reported_p75, reported_mean,
             reported_sample_size, basis, review_status, source_kind, geography_reported, domain_or_practice_area,
             experience_band, pqe_band, observed_at, period_end)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            f"test-analytics:{uuid.uuid4()}", document_id, market_id, archetype_concept_id, raw_role_label, component,
            pay_period, currency, amount_min, amount_max, amount_mid, reported_p25, reported_p50, reported_p75,
            reported_mean, reported_sample_size, basis, review_status, source_kind, geography_reported,
            domain_or_practice_area, experience_band, pqe_band, observed_at, period_end,
        ),
    )
    return str(cur.fetchone()["id"])


# --- 1/2: accepted-only rule, no archetype requirement (HTTP/DB level) -----


def test_only_accepted_observations_are_included(client):
    with db.db_cursor() as cur:
        doc_id, _ = db.create_document(cur, kind="market_survey", content_text="report", provenance_quality="original")
        _insert_observation(cur, document_id=doc_id, raw_role_label="Accepted Row", review_status="accepted")
        _insert_observation(cur, document_id=doc_id, raw_role_label="Unreviewed Row", review_status="unreviewed")
        _insert_observation(cur, document_id=doc_id, raw_role_label="Rejected Row", review_status="rejected")

    resp = client.get("/api/market-data/analytics/summary")
    assert resp.status_code == 200
    labels = {r["raw_role_label"] for r in resp.json()["evidence_rows"]["items"]}
    assert "Accepted Row" in labels
    assert "Unreviewed Row" not in labels
    assert "Rejected Row" not in labels


def test_no_archetype_requirement_unmapped_evidence_is_first_class(client):
    with db.db_cursor() as cur:
        doc_id, _ = db.create_document(cur, kind="market_survey", content_text="report", provenance_quality="original")
        _insert_observation(cur, document_id=doc_id, raw_role_label="Unmapped Row", archetype_concept_id=None)

    resp = client.get("/api/market-data/analytics/summary")
    body = resp.json()
    labels = {r["raw_role_label"] for r in body["evidence_rows"]["items"]}
    assert "Unmapped Row" in labels
    assert body["coverage"]["not_archetype_linked_count"] >= 1


# --- 3: practice grouping ----------------------------------------------------


def test_group_practice_area_maps_synonyms_and_preserves_unknown_labels():
    assert ma.group_practice_area("Life") == "Life"
    assert ma.group_practice_area("Life Insurance") == "Life"
    assert ma.group_practice_area("Non-Life") == "Non-Life / GI"
    assert ma.group_practice_area("General Insurance") == "Non-Life / GI"
    assert ma.group_practice_area("Pensions") == "Pensions"
    assert ma.group_practice_area("Reinsurance & London Market") == "Reinsurance / London Market"
    assert ma.group_practice_area("All practice areas") == ma.PRACTICE_GROUP_ALL
    assert ma.group_practice_area(None) == ma.PRACTICE_GROUP_UNSPECIFIED
    assert ma.group_practice_area("   ") == ma.PRACTICE_GROUP_UNSPECIFIED
    assert ma.group_practice_area("Something Bespoke") == "Something Bespoke"


def test_practice_grouping_preserves_raw_label_alongside_group():
    rows = [
        _row(id="1", domain_or_practice_area="Life"),
        _row(id="2", domain_or_practice_area="Life Insurance"),
        _row(id="3", domain_or_practice_area="Non-Life"),
        _row(id="4", domain_or_practice_area="General Insurance"),
        _row(id="5", domain_or_practice_area="All practice areas"),
    ]
    items = {r["observation_id"]: r for r in ma.build_evidence_rows(rows)["items"]}
    assert items["1"]["practice_group"] == items["2"]["practice_group"] == "Life"
    assert items["2"]["practice_reported"] == "Life Insurance"  # raw label never overwritten
    assert items["3"]["practice_group"] == items["4"]["practice_group"] == "Non-Life / GI"
    assert items["5"]["practice_group"] == ma.PRACTICE_GROUP_ALL


# --- 4: statistics remain distinct -------------------------------------------


def test_median_mean_and_range_remain_statistically_distinct():
    median_row = _row(id="m", pqe_band="5-8", reported_p50=100000)
    mean_row = _row(id="a", pqe_band="5-8", reported_mean=90000)
    range_only_row = _row(id="r", pqe_band="5-8", amount_min=80000, amount_max=120000)

    points = {p["observation_id"]: p for p in ma.build_pqe_series([median_row, mean_row, range_only_row])}
    assert points["m"]["reported_p50"] == 100000
    assert points["m"]["reported_mean"] is None
    assert points["a"]["reported_mean"] == 90000
    assert points["a"]["reported_p50"] is None
    assert points["r"]["reported_p50"] is None and points["r"]["reported_mean"] is None
    assert points["r"]["amount_min"] == 80000 and points["r"]["amount_max"] == 120000


# --- 5: range-only recruiter evidence ----------------------------------------


def test_range_only_recruiter_row_appears_in_role_ranges_not_median_series():
    row = _row(id="cavehill", raw_role_label="Actuarial Manager", amount_min=92000, amount_max=128000, source_kind="recruiter_benchmark")

    ranges = ma.build_role_ranges([row])
    assert len(ranges) == 1
    assert ranges[0]["derived_range_midpoint"] == 110000
    assert ranges[0]["reported_p50"] is None  # never fabricated from the range

    # No PQE/experience band on this row -> correctly absent from both series,
    # and cannot contaminate a neighbouring row's median either.
    other = _row(id="other", pqe_band="9-12", reported_p50=100000)
    pqe_points = {p["observation_id"]: p for p in ma.build_pqe_series([row, other])}
    assert "cavehill" not in pqe_points
    assert pqe_points["other"]["reported_p50"] == 100000


# --- 6: PQE vs experience separation -----------------------------------------


def test_pqe_and_experience_rows_do_not_cross_contaminate():
    pqe_row = _row(id="p", pqe_band="9-12 years")
    exp_row = _row(id="e", experience_band="10-15 years")

    pqe_ids = {p["observation_id"] for p in ma.build_pqe_series([pqe_row, exp_row])}
    exp_ids = {p["observation_id"] for p in ma.build_experience_series([pqe_row, exp_row])}
    assert pqe_ids == {"p"}
    assert exp_ids == {"e"}


# --- 7: component separation --------------------------------------------------


def test_component_comparisons_separate_base_and_total_and_exclude_day_rate():
    doc_id = "doc-1"
    base_row = _row(id="b", document_id=doc_id, component="base", pqe_band="9-12", reported_p50=100000)
    total_row = _row(id="t", document_id=doc_id, component="total_package", pqe_band="9-12", reported_p50=120000)
    day_rate_row = _row(id="d", document_id=doc_id, component="day_rate", pay_period="daily", pqe_band="9-12", amount_mid=500)

    comparisons = ma.build_component_comparisons([base_row, total_row, day_rate_row])
    assert len(comparisons) == 1
    components = {c["component"] for c in comparisons[0]["components"]}
    assert components == {"base", "total_package"}


# --- 8: geography/currency filtering ------------------------------------------


def test_filters_do_not_mix_markets_or_currencies():
    gbp_row = _row(id="g", currency="GBP", market_id="market-uk")
    eur_row = _row(id="e", currency="EUR", market_id="market-ie")
    rows = [gbp_row, eur_row]

    only_gbp = ma.apply_filters(rows, ma.MarketAnalyticsFilters(currency="GBP"))
    assert {r["id"] for r in only_gbp} == {"g"}

    only_ie = ma.apply_filters(rows, ma.MarketAnalyticsFilters(market_id="market-ie"))
    assert {r["id"] for r in only_ie} == {"e"}


# --- 9: comparison-set integrity ---------------------------------------------


def test_practice_comparisons_require_shared_document_and_context():
    doc1, doc2 = "doc-1", "doc-2"
    life = _row(id="life", document_id=doc1, domain_or_practice_area="Life", pqe_band="9-12", reported_p50=100000)
    nonlife_same_doc = _row(id="nonlife", document_id=doc1, domain_or_practice_area="Non-Life", pqe_band="9-12", reported_p50=110000)
    life_other_doc = _row(id="life2", document_id=doc2, domain_or_practice_area="Life", pqe_band="9-12", reported_p50=90000)
    mismatched_band = _row(id="life3", document_id=doc1, domain_or_practice_area="Pensions", pqe_band="1-4", reported_p50=70000)

    comparisons = ma.build_practice_comparisons([life, nonlife_same_doc, life_other_doc, mismatched_band])
    assert len(comparisons) == 1
    ids_in_set = {p["observation_id"] for p in comparisons[0]["practices"]}
    assert ids_in_set == {"life", "nonlife"}


def test_practice_comparisons_exclude_all_practice_areas_and_unspecified():
    doc1 = "doc-1"
    life = _row(id="life", document_id=doc1, domain_or_practice_area="Life", pqe_band="9-12", reported_p50=100000)
    all_areas = _row(id="all", document_id=doc1, domain_or_practice_area="All practice areas", pqe_band="9-12", reported_p50=95000)
    unspecified = _row(id="unspec", document_id=doc1, domain_or_practice_area=None, pqe_band="9-12", reported_p50=80000)

    assert ma.build_practice_comparisons([life, all_areas, unspecified]) == []


# --- 10: trend strictness -----------------------------------------------------


def test_trend_requires_at_least_two_comparable_periods():
    row = _row(id="1", provider="Acme", pqe_band="9-12", reported_p50=100000, period_end=date(2025, 6, 30))
    assert ma.build_trends([row]) == []


def test_trend_emitted_for_two_exact_like_for_like_periods():
    common = dict(provider="Acme", market_id="m1", currency="GBP", component="base", pay_period="annual", domain_or_practice_area="Life")
    r1 = _row(id="1", **common, pqe_band="9-12", reported_p50=100000, period_end=date(2025, 6, 30))
    r2 = _row(id="2", **common, pqe_band="9-12", reported_p50=110000, period_end=date(2026, 6, 30))

    series = ma.build_trends([r1, r2])
    assert len(series) == 1
    assert series[0]["context"]["statistic"] == "median"
    assert [p["value"] for p in series[0]["points"]] == [100000, 110000]


def test_trend_never_falsely_combines_mismatched_band_component_geography_or_statistic():
    common = dict(provider="Acme", market_id="m1", currency="GBP", component="base", pay_period="annual", domain_or_practice_area="Life")
    r1 = _row(id="1", **common, pqe_band="9-12", reported_p50=100000, period_end=date(2025, 6, 30))
    r2_diff_band = _row(id="2", **common, pqe_band="1-4", reported_p50=60000, period_end=date(2026, 6, 30))
    r3_diff_component = _row(id="3", **{**common, "component": "total_package"}, pqe_band="9-12", reported_p50=130000, period_end=date(2026, 6, 30))
    r4_diff_geo = _row(id="4", **{**common, "market_id": "m2"}, pqe_band="9-12", reported_p50=105000, period_end=date(2026, 6, 30))
    r5_mean_not_median = _row(id="5", **common, pqe_band="9-12", reported_mean=99000, period_end=date(2026, 6, 30))

    series = ma.build_trends([r1, r2_diff_band, r3_diff_component, r4_diff_geo, r5_mean_not_median])
    assert series == []  # every candidate differs on at least one held-constant dimension


# --- 11: source counts ---------------------------------------------------------


def test_source_counts_are_distinct_documents_not_rows():
    doc1, doc2 = "doc-1", "doc-2"
    rows = [
        _row(id="1", document_id=doc1, provider="Acumen"),
        _row(id="2", document_id=doc1, provider="Acumen"),  # same document/provider, second row
        _row(id="3", document_id=doc2, provider="Raretec"),
    ]
    coverage = ma.build_coverage(rows)
    assert coverage["accepted_observation_count"] == 3
    assert coverage["distinct_source_document_count"] == 2
    assert coverage["distinct_provider_count"] == 2


# --- 12: extraction-button support data (existing document detail behaviour) -


def test_curated_document_without_extraction_run_still_exposes_observations(client):
    resp = client.post(
        "/api/market-data/documents/ingest",
        json={"text": "Manually curated report.", "publisher": "Acumen Resources"},
    )
    document_id = resp.json()["id"]
    with db.db_cursor() as cur:
        _insert_observation(cur, document_id=document_id, raw_role_label="Curated Row", review_status="accepted")

    detail = client.get(f"/api/market-data/documents/{document_id}").json()
    assert len(detail["observations"]) == 1
    assert detail["observations"][0]["review_status"] == "accepted"
    assert detail["extraction_runs"] == []


# --- End-to-end route wiring: filters narrow the view, facets stay stable ----


def test_analytics_summary_endpoint_filters_narrow_view_while_facets_stay_stable(client):
    with db.db_cursor() as cur:
        market_id = _market(cur, "market-ie-analytics")
        doc_id, _ = db.create_document(cur, kind="market_survey", content_text="report", provenance_quality="original")
        _insert_observation(cur, document_id=doc_id, market_id=market_id, currency="EUR", raw_role_label="Row A", pqe_band="9-12", reported_p50=100000)
        _insert_observation(cur, document_id=doc_id, market_id=None, currency="GBP", raw_role_label="Row B", pqe_band="5-8", reported_p50=70000)

    all_resp = client.get("/api/market-data/analytics/summary").json()
    assert all_resp["coverage"]["accepted_observation_count"] == 2
    assert {f["value"] for f in all_resp["facets"]["currencies"]} == {"EUR", "GBP"}

    eur_resp = client.get("/api/market-data/analytics/summary", params={"currency": "EUR"}).json()
    assert eur_resp["coverage"]["accepted_observation_count"] == 1
    assert eur_resp["evidence_rows"]["items"][0]["raw_role_label"] == "Row A"
    # Facets reflect the full accepted corpus, not the current filter selection.
    assert {f["value"] for f in eur_resp["facets"]["currencies"]} == {"EUR", "GBP"}


def test_analytics_summary_rejects_invalid_period_filter(client):
    resp = client.get("/api/market-data/analytics/summary", params={"period_from": "not-a-date"})
    assert resp.status_code == 422
