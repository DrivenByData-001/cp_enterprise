"""Pure, DB-free tests for the deterministic scoring/banding/noise-flag
methodology (app/vocabulary_priority.py). No Postgres fixtures needed here —
see test_vocabulary_curation.py for the DB-backed aggregation/API tests."""

from app.vocabulary_priority import (
    BAND_HIGH,
    BAND_LOW,
    BAND_MEDIUM,
    BAND_SPARSE,
    NOISE_EMPLOYER_SPECIFIC,
    NOISE_LONG_PHRASE,
    NOISE_MALFORMED,
    NOISE_POSSIBLE_FRAGMENT,
    WEIGHT_ROLE_COUNT,
    ClusterSignals,
    assign_priority_band,
    compute_priority_score,
    noise_flags,
    recency_factor,
    sort_key,
)


def _signals(**overrides):
    base = dict(
        role_count=1, observation_count=1, year_count=1, seniority_count=1,
        country_count=1, career_track_count=1, most_recent_year=2026, current_year=2026,
    )
    base.update(overrides)
    return ClusterSignals(**base)


# --- anti-dominance: distinct-role breadth beats raw mention count --------

def test_broad_cluster_outranks_narrow_high_frequency_cluster():
    """The brief's core anti-dominance requirement: a term mentioned many
    times in one role/period must not outrank one seen widely."""
    narrow_but_loud = _signals(role_count=1, observation_count=200, year_count=1, seniority_count=1, country_count=1, career_track_count=1)
    broad_but_quiet = _signals(role_count=15, observation_count=15, year_count=6, seniority_count=3, country_count=2, career_track_count=2)
    assert compute_priority_score(broad_but_quiet) > compute_priority_score(narrow_but_loud)


def test_observation_count_alone_never_determines_ranking():
    """Two clusters with identical role/year/seniority/country breadth but
    wildly different raw observation counts must score close together
    (log-dampened, smallest weight) — never in proportion to the raw count."""
    few_mentions = _signals(role_count=5, observation_count=5, year_count=2, seniority_count=1, country_count=1, career_track_count=1)
    many_mentions = _signals(role_count=5, observation_count=500, year_count=2, seniority_count=1, country_count=1, career_track_count=1)
    diff = compute_priority_score(many_mentions) - compute_priority_score(few_mentions)
    # log1p(500) - log1p(5) ~= 4.6, weighted at 0.5 -> ~2.3 max contribution,
    # far less than a single extra distinct role (weight 3.0) would add.
    assert 0 < diff < WEIGHT_ROLE_COUNT


def test_recency_is_secondary_and_never_dominates_breadth():
    old_but_broad = _signals(role_count=10, observation_count=10, year_count=5, seniority_count=2, country_count=2, career_track_count=2, most_recent_year=2010, current_year=2026)
    recent_but_sparse = _signals(role_count=1, observation_count=1, year_count=1, seniority_count=1, country_count=1, career_track_count=1, most_recent_year=2026, current_year=2026)
    assert compute_priority_score(old_but_broad) > compute_priority_score(recent_but_sparse)


def test_recency_factor_decays_and_is_bounded():
    this_year = _signals(most_recent_year=2026, current_year=2026)
    half_life_ago = _signals(most_recent_year=2021, current_year=2026)
    unknown = _signals(most_recent_year=None, current_year=2026)
    assert recency_factor(this_year) == 1.0
    assert abs(recency_factor(half_life_ago) - 0.5) < 1e-9
    assert recency_factor(unknown) == 0.0


# --- priority bands ---------------------------------------------------------

def test_band_sparse_is_single_role():
    assert assign_priority_band(_signals(role_count=1)) == BAND_SPARSE
    assert assign_priority_band(_signals(role_count=0)) == BAND_SPARSE


def test_band_low_is_role_count_two_without_breadth():
    assert assign_priority_band(_signals(role_count=2)) == BAND_LOW


def test_band_medium_requires_three_roles():
    assert assign_priority_band(_signals(role_count=3, year_count=1, seniority_count=1, country_count=1, career_track_count=1)) == BAND_MEDIUM


def test_band_high_requires_role_count_and_cross_dimension_breadth():
    # role_count high but no cross-cutting breadth -> medium, not high.
    narrow_breadth = _signals(role_count=10, year_count=1, seniority_count=1, country_count=1, career_track_count=1)
    assert assign_priority_band(narrow_breadth) == BAND_MEDIUM

    wide_breadth = _signals(role_count=10, year_count=3, seniority_count=2, country_count=1, career_track_count=1)
    assert assign_priority_band(wide_breadth) == BAND_HIGH


def test_bands_are_deterministic_pure_functions_of_evidence():
    s = _signals(role_count=6, year_count=2, seniority_count=2, country_count=1, career_track_count=1)
    assert assign_priority_band(s) == assign_priority_band(s) == BAND_HIGH


# --- noise/sparse flags ------------------------------------------------------

def test_noise_flags_catch_fragments_and_malformed_and_long_phrases():
    assert NOISE_POSSIBLE_FRAGMENT in noise_flags(["and reporting"])
    assert NOISE_POSSIBLE_FRAGMENT in noise_flags(["excel, word, powerpoint"])
    assert NOISE_MALFORMED in noise_flags([""])
    assert NOISE_MALFORMED in noise_flags(["12345"])
    assert NOISE_LONG_PHRASE in noise_flags(["ability to work independently under significant time pressure daily"])
    assert NOISE_EMPLOYER_SPECIFIC in noise_flags(["our in-house proprietary reporting tool"])


def test_noise_flags_do_not_fire_on_ordinary_terms():
    assert noise_flags(["Solvency II", "SII"]) == []
    assert noise_flags(["Python"]) == []
    assert noise_flags(["Stakeholder management"]) == []


def test_noise_flags_never_raises_on_edge_input():
    assert noise_flags([]) == []
    assert noise_flags(["   "]) == [NOISE_MALFORMED]


# --- sort_key determinism ----------------------------------------------------

def test_sort_key_breaks_ties_deterministically():
    a = sort_key("beta", 5.0, role_count=3, observation_count=3)
    b = sort_key("alpha", 5.0, role_count=3, observation_count=3)
    # Same score/role_count/observation_count -> alphabetical cluster_key tiebreak.
    assert b < a


def test_sort_key_orders_by_score_first():
    high = sort_key("z", 10.0, role_count=1, observation_count=1)
    low = sort_key("a", 1.0, role_count=100, observation_count=100)
    assert high < low
