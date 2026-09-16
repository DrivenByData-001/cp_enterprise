"""Evidence-based role compensation resolution (build §3) and the
source-backed posting compensation review flow (build §2).

Every tier of the precedence rule is exercised in isolation and in
combination, and every server-side validation rule on acceptance has a test
that proves it refuses rather than silently upgrading weak evidence.
"""

import uuid
from datetime import date

import pytest

from app import compensation_resolver as resolver
from app import posting_compensation
from app.economics_freshness import record_rebuild
from app.db import create_document, db_cursor, upsert_role_instance


# --- Fixtures ---------------------------------------------------------------

def _market(cur, code="country-united-kingdom", label="United Kingdom"):
    cur.execute(
        "INSERT INTO jobber.market (code, label, country, geography) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label RETURNING id",
        (code, label, label, label),
    )
    return str(cur.fetchone()["id"])


def _archetype(cur, name="Senior Life Actuary"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('role_archetype', %s, 'active', 'curator', now()) RETURNING id",
        (name,),
    )
    concept_id = str(cur.fetchone()["id"])
    cur.execute("INSERT INTO jobber.role_archetype_detail (concept_id) VALUES (%s)", (concept_id,))
    return concept_id


def _role(cur, *, title="Head of Capital", country="United Kingdom", archetype_concept_id=None,
          document_id=None, currency=None, salary_min=None, salary_max=None,
          salary_estimate_min=None, salary_estimate_max=None):
    role_id = upsert_role_instance(
        cur, None,
        {
            "instance_type": "observed_posting", "title": title, "country": country,
            "document_id": document_id, "currency": currency,
            "salary_min": salary_min, "salary_max": salary_max,
            "salary_estimate_min": salary_estimate_min, "salary_estimate_max": salary_estimate_max,
        },
        skills=[],
    )
    if archetype_concept_id:
        cur.execute(
            "UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s",
            (archetype_concept_id, role_id),
        )
    return str(role_id)


def _document(cur, text, *, provenance="original", kind="job_posting"):
    document_id, _duplicate = create_document(
        cur, kind=kind, content_text=text, provenance_quality=provenance,
        title="Posting", source="user_paste", source_date=date(2026, 1, 15),
    )
    return document_id


def _observation(cur, *, role_id=None, archetype_id=None, market_id, basis, amount_min=None,
                 amount_max=None, currency="GBP", component="base", pay_period="annual",
                 review_status="accepted", evidence_span=None, observed_at=None, employment_basis=None):
    cur.execute(
        """
        INSERT INTO jobber.compensation_observation
            (source_key, role_instance_id, archetype_concept_id, market_id, component, pay_period,
             employment_basis, currency, amount_min, amount_max, basis, review_status, evidence_span, observed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (f"test:{uuid.uuid4()}", role_id, archetype_id, market_id, component, pay_period,
         employment_basis, currency, amount_min, amount_max, basis, review_status, evidence_span, observed_at),
    )
    return str(cur.fetchone()["id"])


def _mark_economics_fresh(cur):
    """Stand in for an explicit `POST /api/economics/rebuild`.

    These tests insert `d_archetype_comp` rows directly, which a real rebuild
    would also have recorded its source state for. Without that record the
    resolver correctly treats the derived tables as never-rebuilt and
    withholds every benchmark — so a test that wants to exercise the market
    tier has to say that a rebuild happened, exactly as production does."""
    record_rebuild(cur, "test-engine")


def _archetype_comp(cur, archetype_id, market_id, *, reference=132000, currency="GBP",
                    p25=120000, p75=145000, n_observations=12, n_posting_stated=12,
                    reference_source="posting", n_survey_sources=0, basis_detail=None):
    from app.db import to_json_param
    cur.execute(
        """
        INSERT INTO jobber.d_archetype_comp
            (archetype_concept_id, market_id, period_start, period_end, currency, component, pay_period,
             n_observations, n_posting_stated, n_posting_estimated, n_survey_sources,
             posting_p25, posting_p50, posting_p75, survey_benchmarks,
             reference_comp, reference_source, reference_basis_detail, trace, engine_version)
        VALUES (%s, %s, '2000-01-01', '2026-09-16', %s, 'base', 'annual', %s, %s, 0, %s,
                %s, %s, %s, '[]'::jsonb, %s, %s, %s, '{}'::jsonb, 'test-engine')
        """,
        (archetype_id, market_id, currency, n_observations, n_posting_stated, n_survey_sources,
         p25, reference, p75, reference, reference_source, to_json_param(basis_detail)),
    )


# --- Precedence (build §3) --------------------------------------------------

def test_stated_salary_wins_over_the_archetype_benchmark():
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id, reference=132000)
        _observation(cur, role_id=role_id, market_id=market_id, basis="posting_stated",
                     amount_min=150000, amount_max=170000, evidence_span="£150,000 - £170,000")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_ADVERT_STATED
    assert resolved["basis_label"] == "Advert salary"
    assert resolved["amount_min"] == 150000
    assert resolved["amount_reference"] == 160000
    assert resolved["amount_max"] == 170000
    assert resolved["trace"]["tier"] == 1
    assert resolved["trace"]["source_quoted"] is True


def test_a_source_quoted_observation_outranks_the_backfill_projection():
    """Both are genuinely 'stated on the advert'; the quoted one simply has
    better provenance, so it is the one shown."""
    with db_cursor() as cur:
        market_id = _market(cur)
        role_id = _role(cur)
        _observation(cur, role_id=role_id, market_id=market_id, basis="posting_stated",
                     amount_min=100000, amount_max=110000, evidence_span=None)
        _observation(cur, role_id=role_id, market_id=market_id, basis="posting_stated",
                     amount_min=120000, amount_max=130000, evidence_span="£120,000 to £130,000")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["amount_min"] == 120000
    assert resolved["trace"]["source_quoted"] is True
    assert resolved["evidence_quality"] == "good"


def test_archetype_benchmark_is_the_fallback_when_nothing_is_stated():
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id, reference=132000, p25=120000, p75=145000)
        _mark_economics_fresh(cur)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_MARKET_ESTIMATE
    assert resolved["basis_label"] == "Market estimate"
    assert resolved["amount_reference"] == 132000
    assert resolved["amount_min"] == 120000
    assert resolved["amount_max"] == 145000
    assert resolved["archetype"]["name"] == "Senior Life Actuary"
    assert resolved["market"]["label"] == "United Kingdom"
    assert resolved["trace"]["source"] == "jobber.d_archetype_comp"
    assert resolved["trace"]["tier"] == 2


def test_legacy_estimate_is_the_last_resort_and_is_labelled_as_such():
    with db_cursor() as cur:
        _market(cur)
        role_id = _role(cur, currency="GBP", salary_estimate_min=90000, salary_estimate_max=110000)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_LEGACY_ESTIMATE
    assert resolved["basis_label"] == "Legacy estimate"
    assert resolved["amount_reference"] == 100000
    assert resolved["evidence_quality"] == "thin"
    assert "legacy estimate" in resolved["reason"]
    assert resolved["trace"]["tier"] == 3


def test_a_legacy_estimate_with_no_currency_is_not_used():
    """A number with no currency is not a usable economic fact, and the
    resolver never guesses one from the role's country."""
    with db_cursor() as cur:
        role_id = _role(cur, currency=None, salary_estimate_min=90000, salary_estimate_max=110000)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_INSUFFICIENT


def test_insufficient_evidence_is_an_explicit_state_with_a_reason():
    with db_cursor() as cur:
        role_id = _role(cur)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_INSUFFICIENT
    assert resolved["basis_label"] == "Insufficient compensation evidence"
    assert resolved["amount_reference"] is None
    assert "no reviewed archetype" in resolved["reason"]
    assert resolved["evidence_quality"] == "insufficient"


def test_archetype_with_no_qualifying_benchmark_says_so_specifically():
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id, reference=None, p25=None, p75=None,
                        n_observations=2, n_posting_stated=2, reference_source=None)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_INSUFFICIENT
    assert "not enough for a reference benchmark" in resolved["reason"]
    assert resolved["trace"]["has_archetype_comp_row"] is True


def test_unaccepted_observations_never_reach_the_resolver():
    with db_cursor() as cur:
        market_id = _market(cur)
        role_id = _role(cur)
        _observation(cur, role_id=role_id, market_id=market_id, basis="posting_stated",
                     amount_min=150000, amount_max=170000, review_status="unreviewed")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_INSUFFICIENT


def test_market_estimate_evidence_quality_reflects_the_underlying_source():
    with db_cursor() as cur:
        market_id = _market(cur)
        thin = _archetype(cur, "Thin archetype")
        good = _archetype(cur, "Survey archetype")
        _archetype_comp(cur, thin, market_id, reference=100000, n_posting_stated=6)
        _archetype_comp(cur, good, market_id, reference=140000, reference_source="survey",
                        n_survey_sources=1, basis_detail={"quality_tier": "explicit_sample_n_ge_5"})
        thin_role = _role(cur, archetype_concept_id=thin, title="Thin")
        good_role = _role(cur, archetype_concept_id=good, title="Good")
        _mark_economics_fresh(cur)
        resolved = resolver.resolve_role_compensation_bulk(cur, [thin_role, good_role])

    assert resolved[thin_role]["evidence_quality"] == "thin"
    assert resolved[good_role]["evidence_quality"] == "good"


def test_bulk_resolution_is_a_fixed_number_of_queries_regardless_of_role_count():
    """Avoiding N+1 is a stated design constraint, so it is asserted rather
    than assumed: resolving 12 roles must not cost more queries than 2."""
    from query_counter import count_queries

    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        _archetype_comp(cur, archetype_id, market_id)
        _mark_economics_fresh(cur)
        two = [_role(cur, archetype_concept_id=archetype_id, title=f"Role {i}") for i in range(2)]
        twelve = two + [_role(cur, archetype_concept_id=archetype_id, title=f"Role {i}") for i in range(2, 12)]

    with count_queries() as small:
        with db_cursor() as cur:
            resolver.resolve_role_compensation_bulk(cur, two)
    with count_queries() as large:
        with db_cursor() as cur:
            resolver.resolve_role_compensation_bulk(cur, twelve)

    assert large.count == small.count, f"query count grew with role count: {small.count} -> {large.count}"


def test_archetype_compensation_resolution_starts_at_the_market_tier():
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        _archetype_comp(cur, archetype_id, market_id, reference=132000)
        _mark_economics_fresh(cur)
        resolved = resolver.resolve_archetype_compensation(cur, archetype_id)

    assert resolved["basis"] == resolver.BASIS_MARKET_ESTIMATE
    assert resolved["amount_reference"] == 132000


# --- Posting compensation review (build §2) --------------------------------

_POSTING_TEXT = (
    "Head of Capital, London.\n\n"
    "We are seeking a qualified actuary to lead our capital function.\n"
    "Package: £120,000 - £145,000 per annum plus bonus.\n"
)


def test_accepting_a_reviewed_stated_figure_creates_accepted_posting_stated_evidence():
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)
        result = posting_compensation.accept_posting_compensation(
            cur, role_id,
            {
                "amount_min": 120000, "amount_max": 145000, "currency": "gbp",
                "component": "base", "pay_period": "annual", "employment_basis": "permanent",
                "evidence_span": "£120,000 - £145,000 per annum", "note": "Package section",
            },
        )
        assert result["created"] is True
        cur.execute(
            "SELECT basis, review_status, currency, amount_min, amount_max, evidence_span, market_id "
            "FROM jobber.compensation_observation WHERE id = %s",
            (result["id"],),
        )
        row = cur.fetchone()

    assert row["basis"] == "posting_stated"
    assert row["review_status"] == "accepted"
    assert row["currency"] == "GBP"
    assert row["amount_min"] == 120000
    assert row["evidence_span"] == "£120,000 - £145,000 per annum"
    assert row["market_id"] is not None  # derived from the role's country


def test_accepted_observation_immediately_feeds_the_resolver():
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)
        posting_compensation.accept_posting_compensation(
            cur, role_id,
            {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
             "pay_period": "annual", "employment_basis": None,
             "evidence_span": "£120,000 - £145,000 per annum", "note": None},
        )
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_ADVERT_STATED
    assert resolved["amount_reference"] == 132500


def test_accepting_the_same_reviewed_figure_twice_is_idempotent():
    item = {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
            "pay_period": "annual", "employment_basis": None,
            "evidence_span": "£120,000 - £145,000 per annum", "note": None}
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)
        first = posting_compensation.accept_posting_compensation(cur, role_id, item)
        second = posting_compensation.accept_posting_compensation(cur, role_id, item)
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,)
        )
        count = cur.fetchone()["n"]

    assert first["id"] == second["id"]
    assert second["created"] is False
    assert count == 1


def test_a_span_that_is_not_verbatim_in_the_source_is_refused():
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            posting_compensation.accept_posting_compensation(
                cur, role_id,
                {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
                 "pay_period": "annual", "employment_basis": None,
                 "evidence_span": "£120,000 to £145,000 per year", "note": None},
            )
    assert "not an exact match" in str(excinfo.value)


def test_weak_provenance_is_never_upgraded_into_a_stated_fact():
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT, provenance="reconstructed")
        role_id = _role(cur, document_id=document_id)
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            posting_compensation.accept_posting_compensation(
                cur, role_id,
                {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
                 "pay_period": "annual", "employment_basis": None,
                 "evidence_span": "£120,000 - £145,000 per annum", "note": None},
            )
    assert "original provenance" in str(excinfo.value)


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"currency": "pounds"}, "is not a three-letter currency code"),
        ({"component": "equity"}, "component must be one of"),
        ({"pay_period": "monthly"}, "pay_period must be one of"),
        ({"amount_min": None, "amount_max": None}, "no amount was stated"),
        ({"amount_min": 200000, "amount_max": 100000}, "cannot be greater than"),
        ({"amount_min": -5}, "cannot be negative"),
        ({"evidence_span": "   "}, "exact evidence span"),
    ],
)
def test_server_side_validation_refuses_malformed_acceptances(overrides, expected):
    item = {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
            "pay_period": "annual", "employment_basis": None,
            "evidence_span": "£120,000 - £145,000 per annum", "note": None}
    item.update(overrides)
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            posting_compensation.accept_posting_compensation(cur, role_id, item)
    assert expected in str(excinfo.value)


def test_a_role_with_no_source_document_cannot_have_stated_compensation_reviewed():
    with db_cursor() as cur:
        role_id = _role(cur, document_id=None)
        with pytest.raises(posting_compensation.PostingCompensationSubjectError):
            posting_compensation.accept_posting_compensation(
                cur, role_id,
                {"amount_min": 1, "currency": "GBP", "component": "base", "pay_period": "annual",
                 "evidence_span": "x", "amount_max": None, "employment_basis": None, "note": None},
            )


def test_proposal_items_carry_the_servers_own_verdict(monkeypatch):
    """The review screen must never offer an Accept the server will refuse,
    so each proposed item is annotated with the same rules acceptance
    applies."""
    from app import ai
    from app.posting_compensation import PostingCompensationProposal

    def fake_run(**kwargs):
        output = PostingCompensationProposal.model_validate(
            {
                "items": [
                    {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
                     "pay_period": "annual", "evidence_span": "£120,000 - £145,000 per annum"},
                    {"amount_min": 99999, "currency": "GBP", "component": "base", "pay_period": "annual",
                     "evidence_span": "a quote that is not in the advert"},
                ],
                "no_compensation_stated": False,
            }
        )
        run = ai.AITaskRun(
            "posting_compensation_extract", "test-model", "extract_posting_compensation.md", "v1",
            "2026-09-16", "2026-09-16", "ok", 100, 50,
        )
        return ai.AITaskResult(output, run)

    monkeypatch.setattr(posting_compensation, "run_json_task", fake_run)

    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)
        result = posting_compensation.propose_posting_compensation(cur, role_id)

    assert result["status"] == "ok"
    items = result["proposal"]["items"]
    assert items[0]["acceptable"] is True and items[0]["problems"] == []
    assert items[1]["acceptable"] is False
    assert any("does not appear verbatim" in p for p in items[1]["problems"])

    # A proposal is never persisted as an economic fact.
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.compensation_observation")
        assert cur.fetchone()["n"] == 0


def test_a_no_salary_posting_produces_no_items_and_no_observation(monkeypatch):
    from app import ai
    from app.posting_compensation import PostingCompensationProposal

    def fake_run(**kwargs):
        output = PostingCompensationProposal.model_validate(
            {"items": [], "no_compensation_stated": True, "notes": "states 'competitive salary' only"}
        )
        run = ai.AITaskRun("posting_compensation_extract", "test-model", "p.md", "v1",
                           "2026-09-16", "2026-09-16", "ok", 10, 5)
        return ai.AITaskResult(output, run)

    monkeypatch.setattr(posting_compensation, "run_json_task", fake_run)
    with db_cursor() as cur:
        document_id = _document(cur, "Head of Capital. Competitive salary.")
        role_id = _role(cur, document_id=document_id)
        result = posting_compensation.propose_posting_compensation(cur, role_id)
        cur.execute("SELECT COUNT(*) AS n FROM jobber.compensation_observation")
        count = cur.fetchone()["n"]

    assert result["proposal"]["no_compensation_stated"] is True
    assert result["proposal"]["items"] == []
    assert count == 0


def test_a_failed_proposal_records_the_run_and_writes_no_observation(monkeypatch):
    from app.ai import AIProviderError

    def fake_run(**kwargs):
        raise AIProviderError("provider down")

    monkeypatch.setattr(posting_compensation, "run_json_task", fake_run)
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)
        result = posting_compensation.propose_posting_compensation(cur, role_id)
        cur.execute(
            "SELECT status, error_type FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],)
        )
        run = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM jobber.compensation_observation")
        count = cur.fetchone()["n"]

    assert result["status"] == "failed"
    assert run["status"] == "failed"
    assert run["error_type"] == "AIProviderError"
    assert count == 0


def test_legacy_salary_columns_are_never_modified_by_the_review_flow():
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id, currency="GBP",
                        salary_min=100000, salary_max=110000)
        posting_compensation.accept_posting_compensation(
            cur, role_id,
            {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
             "pay_period": "annual", "employment_basis": None,
             "evidence_span": "£120,000 - £145,000 per annum", "note": None},
        )
        cur.execute(
            "SELECT salary_min, salary_max FROM jobber.role_instance WHERE id = %s", (role_id,)
        )
        row = cur.fetchone()

    assert row["salary_min"] == 100000
    assert row["salary_max"] == 110000


def test_raw_capture_makes_no_ai_call_and_creates_no_compensation(client, monkeypatch):
    """Capture must stay fast and AI-independent — the whole reason
    compensation review is a separate, explicit step."""
    from app import ai

    def explode(**kwargs):
        raise AssertionError("raw capture must never call the AI provider")

    monkeypatch.setattr(ai, "run_json_task", explode)
    monkeypatch.setattr(posting_compensation, "run_json_task", explode)

    response = client.post(
        "/api/role-instances/ingest",
        json={"text": _POSTING_TEXT, "kind": "posting", "country": "United Kingdom"},
    )
    assert response.status_code == 200
    role_id = response.json()["id"]

    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,)
        )
        assert cur.fetchone()["n"] == 0


# --- API surface ------------------------------------------------------------

def test_role_compensation_endpoint_returns_figure_comparison_and_observations(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        role_id = _role(cur)
        _observation(cur, role_id=role_id, market_id=market_id, basis="posting_stated",
                     amount_min=120000, amount_max=145000, evidence_span="£120,000 - £145,000")

    body = client.get(f"/api/role-instances/{role_id}/compensation").json()
    assert body["compensation"]["basis"] == "advert_stated"
    assert body["personal_comparison"]["comparable"] is False
    assert len(body["observations"]) == 1
    assert body["observations"][0]["evidence_span"] == "£120,000 - £145,000"


def test_accept_endpoint_rejects_a_bad_span_with_a_readable_error(client):
    with db_cursor() as cur:
        document_id = _document(cur, _POSTING_TEXT)
        role_id = _role(cur, document_id=document_id)

    response = client.post(
        f"/api/role-instances/{role_id}/compensation/accept",
        json={"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
              "pay_period": "annual", "evidence_span": "not in the source"},
    )
    assert response.status_code == 400
    assert "not an exact match" in response.json()["detail"]
