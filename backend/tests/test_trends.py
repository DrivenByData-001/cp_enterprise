"""Corpus trend analytics (docs/18 §7/§8, app/trends.py + routes/trends.py).
Real Postgres test database throughout."""

from app import db, trends


def _concept(cur, name, type_code="tool"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _role(cur, title, *, posting_date, country=None, seniority_level=None, career_track=None, skills=None):
    return db.upsert_role_instance(
        cur, None,
        {
            "instance_type": "observed_posting", "title": title, "posting_date": posting_date,
            "country": country, "seniority_level": seniority_level, "career_track": career_track,
        },
        skills=skills or [],
    )


def _skill(name, requirement_type="required"):
    return {"name": name, "requirement_type": requirement_type, "importance": 3}


# --- classify_trend (pure function, no DB) ----------------------------------

def _series(*proportions, sample_size=10):
    return [{"period": 2000 + i, "role_count": int(p * sample_size), "total_roles": sample_size, "proportion": p, "sample_size": sample_size} for i, p in enumerate(proportions)]


def test_classify_trend_sparse_below_two_usable_periods():
    result = trends.classify_trend(_series(0.5, sample_size=2))  # only 1 period, and below the sample floor
    assert result["label"] == "sparse_insufficient_evidence"


def test_classify_trend_emerging():
    result = trends.classify_trend(_series(0.0, 0.0, 0.4, 0.5))
    assert result["label"] == "emerging"


def test_classify_trend_increasing():
    result = trends.classify_trend(_series(0.10, 0.12, 0.30, 0.35))
    assert result["label"] == "increasing"


def test_classify_trend_declining():
    result = trends.classify_trend(_series(0.40, 0.38, 0.15, 0.10))
    assert result["label"] == "declining"


def test_classify_trend_persistent_when_stable():
    result = trends.classify_trend(_series(0.30, 0.32, 0.29, 0.31))
    assert result["label"] == "persistent"


def test_classify_trend_flat_zero_is_persistent_not_emerging_or_sparse():
    result = trends.classify_trend(_series(0.0, 0.0, 0.0, 0.0))
    assert result["label"] == "persistent"


def test_classify_trend_ignores_periods_below_sample_floor():
    series = _series(0.1, 0.1, sample_size=10) + [
        {"period": 2010, "role_count": 4, "total_roles": 4, "proportion": 1.0, "sample_size": 4},  # below default floor of 5
    ]
    result = trends.classify_trend(series, min_sample_size=trends.SPARSE_MIN_SAMPLE)
    assert result["usable_periods"] == 2  # the 1.0-proportion period never counted


# --- region derivation --------------------------------------------------------

def test_region_for_known_and_unknown_country():
    assert trends.region_for_country("Ireland") == "Europe"
    assert trends.region_for_country("United States") == "North America"
    assert trends.region_for_country("Atlantis") is None
    assert trends.region_for_country(None) is None


# --- corpus_overview -----------------------------------------------------------

def test_corpus_overview_breakdowns_and_sample_size(client):
    with db.db_cursor() as cur:
        _role(cur, "R1", posting_date="2020-01-01", country="Ireland", seniority_level="senior", career_track="actuarial")
        _role(cur, "R2", posting_date="2020-06-01", country="United Kingdom", seniority_level="junior", career_track="actuarial")
        _role(cur, "R3", posting_date="2021-01-01", country="Ireland", seniority_level="senior", career_track="risk")

        overview = trends.corpus_overview(cur, trends.TrendFilters())

    assert overview["sample_size"] == 3
    assert {b["value"]: b["role_count"] for b in overview["by_year"]} == {2020: 2, 2021: 1}
    assert {b["value"]: b["role_count"] for b in overview["by_country"]} == {"Ireland": 2, "United Kingdom": 1}
    assert {b["value"]: b["role_count"] for b in overview["by_region"]} == {"Europe": 3}
    assert {b["value"]: b["role_count"] for b in overview["by_seniority"]} == {"senior": 2, "junior": 1}


def test_corpus_overview_respects_filters(client):
    with db.db_cursor() as cur:
        _role(cur, "R1", posting_date="2020-01-01", country="Ireland")
        _role(cur, "R2", posting_date="2021-01-01", country="United Kingdom")

        filtered = trends.corpus_overview(cur, trends.TrendFilters(country="Ireland"))

    assert filtered["sample_size"] == 1


# --- top_requirements -----------------------------------------------------------

def test_top_requirements_groups_canonical_and_surface_form_separately(client):
    with db.db_cursor() as cur:
        python_id = _concept(cur, "Python")
        _role(cur, "R1", posting_date="2020-01-01", skills=[_skill("Python", "required")])
        _role(cur, "R2", posting_date="2020-01-01", skills=[_skill("Python", "preferred")])
        _role(cur, "R3", posting_date="2020-01-01", skills=[_skill("Some Unresolved Skill")])

        result = trends.top_requirements(cur, trends.TrendFilters(), min_sample_size=1)

    assert result["sample_size"] == 3
    assert result["insufficient_sample"] is False
    python_row = next(i for i in result["items"] if i["concept_id"] == python_id)
    assert python_row["role_count"] == 2
    assert python_row["is_canonical"] is True
    assert python_row["by_requirement_type"] == {"required": 1, "preferred": 1, "inferred": 0}
    assert python_row["proportion"] == round(2 / 3, 4)

    unresolved_row = next(i for i in result["items"] if i["label"] == "some unresolved skill")
    assert unresolved_row["is_canonical"] is False
    assert unresolved_row["concept_id"] is None


def test_top_requirements_flags_insufficient_sample(client):
    with db.db_cursor() as cur:
        _role(cur, "R1", posting_date="2020-01-01", skills=[_skill("X")])
        result = trends.top_requirements(cur, trends.TrendFilters(), min_sample_size=5)
    assert result["insufficient_sample"] is True


# --- requirement_trend -----------------------------------------------------------

def test_requirement_trend_denominator_is_all_roles_not_just_matching_ones(client):
    # Deliberately no matching concept pre-created — this test targets the
    # unresolved-surface_form path (canonical_concept_id IS NULL); a
    # pre-existing exact-name concept would auto-resolve the observation on
    # insert (db.upsert_role_instance's exact-match cascade) and this
    # requirement_key would then match nothing.
    with db.db_cursor() as cur:
        _role(cur, "With skill", posting_date="2020-01-01", skills=[_skill("Chain ladder Trend")])
        _role(cur, "Without skill", posting_date="2020-06-01", skills=[])

        result = trends.requirement_trend(cur, {"surface_form": "Chain ladder Trend"}, trends.TrendFilters(), min_sample_size=1)

    period_2020 = next(p for p in result["series"] if p["period"] == 2020)
    assert period_2020["total_roles"] == 2
    assert period_2020["role_count"] == 1
    assert period_2020["proportion"] == 0.5


def test_requirement_trend_classification_end_to_end(client):
    with db.db_cursor() as cur:
        for i in range(6):
            _role(cur, f"Old {i}", posting_date="2015-01-01", skills=[])
        for i in range(6):
            _role(cur, f"New {i}", posting_date="2023-01-01", skills=[_skill("Emerging Tool")])

        result = trends.requirement_trend(cur, {"surface_form": "Emerging Tool"}, trends.TrendFilters(), min_sample_size=5)

    assert result["classification"]["label"] == "emerging"


# --- cooccurring_requirements -----------------------------------------------------------

def test_cooccurring_requirements_ranks_by_shared_role_count(client):
    with db.db_cursor() as cur:
        chain_ladder = _concept(cur, "Chain ladder Co", type_code="method")
        reserving = _concept(cur, "Reserving Co", type_code="function")
        rare = _concept(cur, "Rare Co", type_code="tool")
        for i in range(4):
            skills = [_skill("Chain ladder Co"), _skill("Reserving Co")]
            if i == 0:
                skills.append(_skill("Rare Co"))
            _role(cur, f"R{i}", posting_date="2020-01-01", skills=skills)

        result = trends.cooccurring_requirements(cur, {"concept_id": chain_ladder}, trends.TrendFilters(), min_count=1)

    by_id = {i["concept_id"]: i for i in result["items"]}
    assert result["sample_size"] == 4
    assert by_id[reserving]["co_count"] == 4
    assert by_id[rare]["co_count"] == 1
    assert chain_ladder not in by_id  # never co-occurs with itself


# --- compare_dimension -----------------------------------------------------------

def test_compare_dimension_by_country(client):
    # No pre-created concept — see the note in the requirement_trend
    # denominator test above for why that would defeat this surface_form key.
    with db.db_cursor() as cur:
        _role(cur, "IE role", posting_date="2020-01-01", country="Ireland", skills=[_skill("IFRS 17 Compare")])
        _role(cur, "IE role 2", posting_date="2020-01-01", country="Ireland", skills=[])
        _role(cur, "UK role", posting_date="2020-01-01", country="United Kingdom", skills=[_skill("IFRS 17 Compare")])

        result = trends.compare_dimension(cur, {"surface_form": "IFRS 17 Compare"}, trends.TrendFilters(), dimension="country")

    by_country = {i["value"]: i for i in result["items"]}
    assert by_country["Ireland"]["sample_size"] == 2
    assert by_country["Ireland"]["role_count"] == 1
    assert by_country["United Kingdom"]["proportion"] == 1.0


# --- route smoke tests -----------------------------------------------------------

def test_trends_routes_smoke(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, "Route Concept", type_code="tool")
        _role(cur, "R1", posting_date="2020-01-01", country="Ireland", skills=[_skill("Route Concept")])

    assert client.get("/api/trends/overview").status_code == 200
    assert client.get("/api/trends/top-requirements").status_code == 200
    assert client.get("/api/trends/requirement-trend", params={"concept_id": concept_id}).status_code == 200
    assert client.get("/api/trends/cooccurrence", params={"concept_id": concept_id}).status_code == 200
    assert client.get("/api/trends/compare", params={"concept_id": concept_id, "dimension": "country"}).status_code == 200
    methodology = client.get("/api/trends/methodology")
    assert methodology.status_code == 200
    assert "sparse_insufficient_evidence" in methodology.json()["text"] or "sparse" in methodology.json()["text"]


def test_trends_requirement_endpoints_require_exactly_one_key(client):
    resp = client.get("/api/trends/requirement-trend")
    assert resp.status_code == 400
    resp2 = client.get("/api/trends/requirement-trend", params={"concept_id": "x", "surface_form": "y"})
    assert resp2.status_code == 400
