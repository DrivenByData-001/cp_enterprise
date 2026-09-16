"""Regressions for the six review findings on the economic-pathways build.

Each test here names the defect it prevents coming back, because every one of
them was a case of the system presenting something as more certain than the
evidence supported.
"""

import uuid
from datetime import date, timedelta

import pytest

from app import compensation_resolver as resolver
from app import pathways
from app import personal_earnings as earnings
from app import posting_compensation
from app.economics_freshness import economics_freshness, record_rebuild
from app.db import create_document, db_cursor, to_json_param, upsert_role_instance


TODAY = date(2026, 9, 16)


# --- shared fixtures --------------------------------------------------------

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


def _capability(cur, name):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('capability', %s, 'active', 'curator', now()) RETURNING id",
        (name,),
    )
    concept_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) "
        "VALUES (%s, %s, 'owned')",
        (concept_id, f"Demonstrate {name}"),
    )
    return concept_id


def _document(cur, text, *, provenance="original"):
    document_id, _dup = create_document(
        cur, kind="job_posting", content_text=text, provenance_quality=provenance,
        title="Posting", source="user_paste", source_date=date(2026, 1, 15),
    )
    return document_id


def _role(cur, *, title="Head of Capital", document_id=None, archetype_concept_id=None,
          country="United Kingdom", currency=None, instance_type="observed_posting"):
    role_id = str(
        upsert_role_instance(
            cur, None,
            {"instance_type": instance_type, "title": title, "country": country,
             "document_id": document_id, "currency": currency, "description": f"{title} description."},
            skills=[],
        )
    )
    if archetype_concept_id:
        cur.execute(
            "UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s",
            (archetype_concept_id, role_id),
        )
    return role_id


def _archetype_comp(cur, archetype_id, market_id, *, reference=132000, currency="GBP"):
    cur.execute(
        """
        INSERT INTO jobber.d_archetype_comp
            (archetype_concept_id, market_id, period_start, period_end, currency, component, pay_period,
             n_observations, n_posting_stated, n_posting_estimated, n_survey_sources,
             posting_p25, posting_p50, posting_p75, survey_benchmarks,
             reference_comp, reference_source, reference_basis_detail, trace, engine_version)
        VALUES (%s, %s, '2000-01-01', '2026-09-16', %s, 'base', 'annual', 12, 12, 0, 0,
                120000, %s, 145000, '[]'::jsonb, %s, 'posting', NULL, '{}'::jsonb, 'test-engine')
        """,
        (archetype_id, market_id, currency, reference, reference),
    )


def _personal(cur, *, component="annual_base", amount=105000, currency="GBP", unit="annual",
              employment_basis="paye", period_start=None, period_end=None, pay_date=None,
              episode_id=None):
    cur.execute(
        """
        INSERT INTO profile360.compensation_observation
            (source_key, episode_id, source_kind, employment_basis, component,
             period_start, period_end, pay_date, amount, currency, unit, review_status)
        VALUES (%s, %s, 'payslip', %s, %s, %s, %s, %s, %s, %s, %s, 'accepted')
        RETURNING id
        """,
        (f"test:{uuid.uuid4()}", episode_id, employment_basis, component,
         period_start, period_end, pay_date, amount, currency, unit),
    )
    return str(cur.fetchone()["id"])


def _episode(cur, *, start_date, end_date=None, status="active", title="Actuary", organisation="An insurer"):
    cur.execute(
        "INSERT INTO profile360.episodes (start_date, end_date, status, title, organisation) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (start_date, end_date, status, title, organisation),
    )
    return str(cur.fetchone()["id"])


# --- P1: the full requirement-review gate -----------------------------------

def _target_with_requirement(cur):
    capability_id = _capability(cur, "Capital management")
    document_id = _document(cur, "Target narrative.")
    target_id = _role(cur, title="Head of Capital", document_id=document_id,
                      instance_type="user_defined_target")
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
        "VALUES (%s, %s, 'required', 'user_asserted', 'accepted')",
        (target_id, capability_id),
    )
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, canonical_concept_id, "
        "requirement_type, observation_basis) VALUES (%s, 'Capital management', %s, 'required', 'app_capture')",
        (target_id, capability_id),
    )
    return target_id, capability_id, document_id


def _unresolved_proposal(cur, role_id, document_id, surface_form="ORSA reporting"):
    """A pending vocabulary proposal produced from this role's own source —
    zero unreviewed claims, but review is genuinely not finished."""
    cur.execute(
        "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, status) "
        "VALUES (%s, 1, %s, 'pending') RETURNING id",
        (surface_form, document_id),
    )
    proposal_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id) "
        "VALUES (%s, %s, %s)",
        (proposal_id, role_id, document_id),
    )
    return proposal_id


def test_an_unresolved_vocabulary_proposal_blocks_the_pathways_review_gate():
    """Zero unreviewed claims is not the same as reviewed. Checking only the
    unreviewed count let a target with a dangling vocabulary term be assessed
    as though its requirements were final."""
    with db_cursor() as cur:
        target_id, _capability_id, document_id = _target_with_requirement(cur)
        # Sanity: with nothing outstanding the target passes the gate.
        assert pathways.pathways_for_target(cur, target_id)["gates"]["target_requirements_reviewed"] is True

        _unresolved_proposal(cur, target_id, document_id)
        result = pathways.pathways_for_target(cur, target_id)

    assert result["gates"]["target_requirements_reviewed"] is False
    assert result["direct_route"]["state"] == "review_incomplete"
    assert "vocabulary proposals still unresolved" in result["direct_route"]["state_reason"]
    assert result["direct_route"]["fit"]["unreviewed_requirement_claims"] == 0
    assert result["direct_route"]["fit"]["unresolved_vocabulary_proposals"] == 1
    assert [b["kind"] for b in result["review_blockers"]["target"]] == ["unresolved_proposals"]


def test_the_review_gate_reasons_are_carried_through_to_the_caller():
    with db_cursor() as cur:
        target_id, _capability_id, document_id = _target_with_requirement(cur)
        _unresolved_proposal(cur, target_id, document_id)
        result = pathways.pathways_for_target(cur, target_id)

    blockers = result["direct_route"]["fit"]["review_blockers"]
    assert blockers and blockers[0]["count"] == 1
    assert blockers[0]["label"] == "vocabulary proposals still unresolved"


def test_candidate_review_completeness_ignores_postings_that_support_nothing():
    """An unrelated posting elsewhere in the corpus needing review is not a
    reason to tell the user their Pathways candidates are unreviewed."""
    with db_cursor() as cur:
        target_id, capability_id, _document_id = _target_with_requirement(cur)

        # A posting that has nothing to do with this target, with an
        # unreviewed claim of its own.
        unrelated = _role(cur, title="Unrelated role", document_id=_document(cur, "Unrelated advert."))
        other_capability = _capability(cur, "Something else entirely")
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
            "VALUES (%s, %s, 'required', 'user_asserted', 'unreviewed')",
            (unrelated, other_capability),
        )
        result = pathways.pathways_for_target(cur, target_id)

    assert result["intermediate_archetypes"] == []
    assert result["unclassified_supporting_postings"] == []
    # Nothing supports a route here, so there is nothing outstanding to report.
    assert result["gates"]["candidate_review_complete"] is True
    assert result["review_blockers"]["supporting_candidates"] == []


# --- P1: historic earnings must not read as current -------------------------

def test_an_open_ended_observation_on_an_ended_episode_is_historical():
    """The defect this prevents: an open-ended 2019 salary attached to an
    employment that ended in 2022, reported as your current pay in 2026."""
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2019, 1, 1), end_date=date(2022, 6, 30),
                              title="Actuarial Manager", organisation="Former Insurer")
        _personal(cur, amount=88000, period_start=date(2019, 1, 1), period_end=None, episode_id=episode_id)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "historical"
    baseline = state["baselines"][0]
    assert baseline["evidence_status"] == "historical"
    assert baseline["label"] == "Latest known earnings"
    assert "ended on 2022-06-30" in baseline["evidence_status_reason"]
    assert any("employment that has ended" in note for note in state["notes"])


def test_an_open_ended_observation_on_an_ongoing_episode_is_still_current():
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2025, 7, 1), end_date=None, status="active")
        _personal(cur, amount=105000, period_start=date(2025, 7, 1), episode_id=episode_id)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "current"
    assert state["baselines"][0]["evidence_status"] == "current"
    assert state["baselines"][0]["evidence_status_reason"] is None


def test_an_episode_whose_status_is_not_ongoing_makes_its_pay_historical():
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2019, 1, 1), end_date=None, status="ended")
        _personal(cur, amount=88000, period_start=date(2019, 1, 1), episode_id=episode_id)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "historical"
    assert "no longer recorded as ongoing" in state["baselines"][0]["evidence_status_reason"]


def test_an_entirely_undated_observation_is_not_called_current():
    """No period, no pay date and no episode establishes nothing. Absence of
    evidence is not evidence of currency."""
    with db_cursor() as cur:
        _personal(cur, amount=95000, period_start=None, period_end=None, pay_date=None, episode_id=None)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "historical"
    assert state["baselines"][0]["evidence_status"] == "historical"
    assert "No period, pay date or ongoing employment episode" in state["baselines"][0]["evidence_status_reason"]
    assert any("carries no period, pay date or employment episode" in note for note in state["notes"])


def test_an_undated_observation_on_an_ongoing_episode_is_current():
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2025, 1, 1), end_date=None, status="active")
        _personal(cur, amount=105000, period_start=None, period_end=None, pay_date=None, episode_id=episode_id)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "current"


def test_a_historic_baseline_is_not_offered_as_your_current_pay_in_a_comparison():
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2019, 1, 1), end_date=date(2022, 6, 30))
        _personal(cur, amount=88000, period_start=date(2019, 1, 1), episode_id=episode_id)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(
        {
            "basis": resolver.BASIS_ADVERT_STATED, "basis_label": "Advert salary", "currency": "GBP",
            "amount_min": 120000, "amount_reference": 132000, "amount_max": 145000,
            "component": "base", "pay_period": "annual", "employment_basis": None, "as_of": date(2026, 1, 1),
        },
        state,
    )
    assert comparison["comparable"] is True
    assert comparison["baseline"]["evidence_status"] == "historical"
    assert any("historical evidence" in limitation for limitation in comparison["limitations"])


# --- P1: multi-component advert compensation --------------------------------

_MULTI_COMPONENT_POSTING = (
    "Head of Capital, London.\n\n"
    "Base salary: £120,000 - £145,000 per annum.\n"
    "Plus an annual bonus of up to 15%.\n"
    "Total package up to £180,000.\n"
)


def _accept(cur, role_id, **item):
    payload = {
        "amount_min": None, "amount_max": None, "bonus_pct": None, "currency": None,
        "component": "base", "pay_period": "annual", "employment_basis": None,
        "evidence_span": "", "note": None,
    }
    payload.update(item)
    return posting_compensation.accept_posting_compensation(cur, role_id, payload)


def test_a_bonus_is_never_the_headline_advert_salary():
    """The defect: the resolver took the first accepted row by date, so a
    bonus or a package could become the role's 'Advert salary'."""
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        _accept(cur, role_id, component="base", amount_min=120000, amount_max=145000, currency="GBP",
                evidence_span="Base salary: £120,000 - £145,000 per annum.")
        _accept(cur, role_id, component="bonus_pct", bonus_pct=15,
                evidence_span="Plus an annual bonus of up to 15%.")
        _accept(cur, role_id, component="total_package", amount_max=180000, currency="GBP",
                evidence_span="Total package up to £180,000.")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_ADVERT_STATED
    assert resolved["component"] == "base"
    assert resolved["component_label"] == "base salary"
    assert resolved["amount_min"] == 120000
    assert resolved["amount_max"] == 145000
    # The other stated figures are reported, not discarded and not merged.
    labels = {s["label"] for s in resolved["supplementary"]}
    assert labels == {"bonus", "total package"}
    bonus = next(s for s in resolved["supplementary"] if s["label"] == "bonus")
    assert bonus["bonus_pct"] == 15
    assert bonus["amount_min"] is None


def test_a_total_package_is_labelled_a_package_not_a_base_salary():
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        _accept(cur, role_id, component="total_package", amount_max=180000, currency="GBP",
                evidence_span="Total package up to £180,000.")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["component"] == "total_package"
    assert resolved["component_label"] == "total package"
    assert "total package" in resolved["reason"]


def test_a_day_rate_is_the_headline_for_a_contract_role():
    posting = "Interim Capital Actuary.\nRate: £650 per day, outside IR35.\n"
    with db_cursor() as cur:
        document_id = _document(cur, posting)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        _accept(cur, role_id, component="day_rate", pay_period="daily", amount_min=650, currency="GBP",
                employment_basis="contract", evidence_span="Rate: £650 per day, outside IR35.")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["component_label"] == "day rate"
    assert resolved["amount_reference"] == 650
    assert resolved["pay_period"] == "daily"


def test_a_posting_stating_only_a_bonus_has_no_headline_pay_figure():
    """Inventing a headline out of a bonus percentage is exactly the error
    the primary-component rule exists to prevent."""
    posting = "Head of Capital.\nAn annual bonus of up to 15% is offered.\n"
    with db_cursor() as cur:
        document_id = _document(cur, posting)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        _accept(cur, role_id, component="bonus_pct", bonus_pct=15,
                evidence_span="An annual bonus of up to 15% is offered.")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_INSUFFICIENT
    assert resolved["amount_reference"] is None
    assert [s["label"] for s in resolved["supplementary"]] == ["bonus"]


def test_a_bonus_percentage_is_stored_as_a_percentage_not_a_currency_amount():
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        result = _accept(cur, role_id, component="bonus_pct", bonus_pct=15,
                         evidence_span="Plus an annual bonus of up to 15%.")
        cur.execute(
            "SELECT bonus_pct, amount_min, amount_max, currency FROM jobber.compensation_observation WHERE id = %s",
            (result["id"],),
        )
        row = cur.fetchone()

    assert row["bonus_pct"] == 15
    assert row["amount_min"] is None and row["amount_max"] is None
    # NOT NULL on the column; the currency is the one the bonus applies to,
    # taken from the role rather than invented.
    assert row["currency"] == "GBP"


def test_a_bonus_carrying_a_cash_amount_is_refused():
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="bonus_pct", bonus_pct=15, amount_min=10000, currency="GBP",
                    evidence_span="Plus an annual bonus of up to 15%.")
    assert "must not also carry a cash amount" in str(excinfo.value)


@pytest.mark.parametrize("percent", [0, -5, 150])
def test_an_impossible_bonus_percentage_is_refused(percent):
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="bonus_pct", bonus_pct=percent,
                    evidence_span="Plus an annual bonus of up to 15%.")
    assert "percentage between 0 and 100" in str(excinfo.value)


def test_a_bonus_on_a_role_with_no_currency_anywhere_is_refused_not_guessed():
    posting = "Head of Capital.\nAn annual bonus of up to 15% is offered.\n"
    with db_cursor() as cur:
        document_id = _document(cur, posting)
        role_id = _role(cur, document_id=document_id, currency=None)
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="bonus_pct", bonus_pct=15,
                    evidence_span="An annual bonus of up to 15% is offered.")
    assert "currency of the pay it applies to" in str(excinfo.value)


def test_a_non_bonus_component_may_not_carry_a_percentage():
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", amount_min=120000, currency="GBP", bonus_pct=15,
                    evidence_span="Base salary: £120,000 - £145,000 per annum.")
    assert "only applies to a bonus_pct component" in str(excinfo.value)


# --- P2: correcting an accepted figure --------------------------------------

def test_accepting_a_corrected_figure_retires_the_one_it_corrects():
    """Two accepted posting_stated rows for the same component both fed the
    archetype benchmark, so a corrected mistake kept influencing the market.

    The mistake here is one that is still reachable: the range's upper bound
    missed, so the role reads as paying a flat £120,000. A mistyped number is
    not, nor is the package figure taken for the base — each would have to be
    stated, as that kind of pay, by the quote backing it."""
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        wrong = _accept(cur, role_id, component="base", amount_min=120000, currency="GBP",
                        evidence_span="Base salary: £120,000 - £145,000 per annum.")
        corrected = _accept(cur, role_id, component="base", amount_min=120000, amount_max=145000, currency="GBP",
                            evidence_span="Base salary: £120,000 - £145,000 per annum.")

        cur.execute(
            "SELECT id, review_status, source_note FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s ORDER BY created_at",
            (role_id,),
        )
        rows = {str(r["id"]): r for r in cur.fetchall()}
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert corrected["superseded_observation_ids"] == [wrong["id"]]
    assert rows[wrong["id"]]["review_status"] == "rejected"
    assert f"superseded by reviewed observation {corrected['id']}" in rows[wrong["id"]]["source_note"]
    assert rows[corrected["id"]]["review_status"] == "accepted"
    # Exactly one accepted base figure remains, and it is the corrected one.
    assert resolved["amount_min"] == 120000
    assert resolved["amount_max"] == 145000


def test_accepting_a_reviewed_figure_retires_the_unquoted_backfill_projection():
    with db_cursor() as cur:
        market_id = _market(cur)
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, role_instance_id, market_id, component, pay_period, currency,
                 amount_min, amount_max, basis, review_status)
            VALUES (%s, %s, %s, 'base', 'annual', 'GBP', 100000, 110000, 'posting_stated', 'accepted')
            RETURNING id
            """,
            (f"posting_stated:{role_id}", role_id, market_id),
        )
        backfilled = str(cur.fetchone()["id"])
        _accept(cur, role_id, component="base", amount_min=120000, amount_max=145000, currency="GBP",
                evidence_span="Base salary: £120,000 - £145,000 per annum.")
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s AND review_status = 'accepted' AND component = 'base'",
            (role_id,),
        )
        accepted = cur.fetchone()["n"]
        cur.execute("SELECT review_status FROM jobber.compensation_observation WHERE id = %s", (backfilled,))
        backfill_status = cur.fetchone()["review_status"]

    assert accepted == 1
    assert backfill_status == "rejected"


def test_a_bonus_is_not_retired_by_accepting_a_base_salary():
    """Supersession is per (component, pay_period): correcting the salary
    must not silently discard a separately stated bonus."""
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        bonus = _accept(cur, role_id, component="bonus_pct", bonus_pct=15,
                        evidence_span="Plus an annual bonus of up to 15%.")
        _accept(cur, role_id, component="base", amount_min=120000, amount_max=145000, currency="GBP",
                evidence_span="Base salary: £120,000 - £145,000 per annum.")
        cur.execute("SELECT review_status FROM jobber.compensation_observation WHERE id = %s", (bonus["id"],))
        assert cur.fetchone()["review_status"] == "accepted"


def test_the_headline_does_not_depend_on_row_order_when_two_figures_compete():
    """Both accepted, same component — the most recent human review decision
    wins, deterministically, not whichever row the database returns first."""
    with db_cursor() as cur:
        market_id = _market(cur)
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        for amount, reviewed_at in ((100000, "2026-01-01"), (120000, "2026-06-01")):
            cur.execute(
                """
                INSERT INTO jobber.compensation_observation
                    (source_key, role_instance_id, market_id, component, pay_period, currency,
                     amount_min, amount_max, basis, review_status, reviewed_at, evidence_span)
                VALUES (%s, %s, %s, 'base', 'annual', 'GBP', %s, %s, 'posting_stated', 'accepted', %s, %s)
                """,
                (f"test:{uuid.uuid4()}", role_id, market_id, amount, amount + 10000, reviewed_at,
                 "Base salary: £120,000 - £145,000 per annum."),
            )
        first = resolver.resolve_role_compensation(cur, role_id)
        second = resolver.resolve_role_compensation(cur, role_id)

    assert first["amount_min"] == 120000, "the later review decision should win"
    assert first == second


# --- P1: derived economics freshness ----------------------------------------

def test_a_never_rebuilt_derivation_is_not_presented_as_current():
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id)
        freshness = economics_freshness(cur)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert freshness["state"] == "never_rebuilt"
    assert resolved["basis"] == resolver.BASIS_INSUFFICIENT
    assert "never been rebuilt" in resolved["reason"]


def test_a_fresh_rebuild_makes_the_benchmark_usable():
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id)
        record_rebuild(cur, "test-engine")
        assert economics_freshness(cur)["state"] == "fresh"
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["basis"] == resolver.BASIS_MARKET_ESTIMATE
    assert resolved["amount_reference"] == 132000


def test_accepting_compensation_after_a_rebuild_makes_the_benchmark_stale():
    """The core defect: invalidating the Pathways cache only forces
    recomposition *from* the derived tables; it never rebuilds them."""
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id)
        record_rebuild(cur, "test-engine")
        assert resolver.resolve_role_compensation(cur, role_id)["basis"] == resolver.BASIS_MARKET_ESTIMATE

        # New compensation evidence arrives; d_archetype_comp still holds the
        # figure computed before it.
        other_role = _role(cur, title="Another role", archetype_concept_id=archetype_id)
        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, role_instance_id, market_id, component, pay_period, currency,
                 amount_min, amount_max, basis, review_status)
            VALUES (%s, %s, %s, 'base', 'annual', 'GBP', 200000, 250000, 'posting_stated', 'accepted')
            """,
            (f"test:{uuid.uuid4()}", other_role, market_id),
        )
        freshness = economics_freshness(cur)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert freshness["state"] == "stale"
    assert "economics" in freshness["stale_inputs"]
    assert resolved["basis"] == resolver.BASIS_INSUFFICIENT
    assert "withheld" in resolved["reason"]
    assert "Rebuild economics" in resolved["reason"]
    assert resolved["trace"]["withheld"] == "stale_derived_economics"


def test_a_capability_model_change_makes_gap_value_stale_too():
    """Gap value is a counterfactual over the current structural model, so a
    capability change invalidates it just as a compensation change does."""
    with db_cursor() as cur:
        record_rebuild(cur, "test-engine")
        assert economics_freshness(cur)["state"] == "fresh"
        _capability(cur, "A brand new capability")
        freshness = economics_freshness(cur)

    assert freshness["state"] == "stale"
    assert "evidence" in freshness["stale_inputs"]


def test_an_actual_rebuild_records_its_source_state_and_clears_staleness(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id)
        assert economics_freshness(cur)["fresh"] is False

    response = client.post("/api/economics/rebuild")
    assert response.status_code == 200
    assert response.json()["rebuild_state"]["engine_version"]

    with db_cursor() as cur:
        assert economics_freshness(cur)["state"] == "fresh"


def test_pathways_withholds_stale_market_estimates_and_says_so():
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        target_id, capability_id, _document_id = _target_with_requirement(cur)
        cur.execute(
            "UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s",
            (archetype_id, target_id),
        )
        _archetype_comp(cur, archetype_id, market_id)
        cur.execute(
            """
            INSERT INTO jobber.d_gap_value
                (capability_concept_id, market_id, period_start, period_end, currency,
                 archetypes_unlocked, archetypes_improved, roles_unlocked, roles_improved,
                 reference_comp_unlocked, comp_delta_vs_best_current_reachable, n_comp_observations,
                 evidence_quality, rank, trace, engine_version)
            VALUES (%s, %s, '2000-01-01', '2026-09-16', 'GBP', 2, 1, 3, 2, 145000, 40000, 14,
                    'moderate', 1, %s, 'test-engine')
            """,
            (capability_id, market_id, to_json_param({})),
        )
        result = pathways.pathways_for_target(cur, target_id, market_id=market_id, currency="GBP")

    assert result["gates"]["derived_economics_fresh"] is False
    # Either non-fresh state is correct here (seeding the derived rows itself
    # bumps the economics counter); what matters is that it is not fresh.
    assert result["economics_freshness"]["state"] in ("never_rebuilt", "stale")
    assert result["direct_route"]["compensation"]["basis"] == "insufficient_evidence"
    # Gap value's market option value is withheld rather than shown as current.
    gap = result["gap_value"][0]
    assert gap["market_option_value"]["available"] is False
    assert gap["market_option_value"]["withheld_as_stale"] is True
    assert "derived_economics_fresh" in result["incomplete"]


# --- Second review round ----------------------------------------------------

def test_the_earnings_fingerprint_notices_an_episode_ending():
    """`_is_current` consults the employment episode, so the fingerprint that
    claims to cover the earnings state has to as well. (The Pathways cache is
    also protected by target_cache's own episodes hash — but this fingerprint
    is what the earnings state publishes as its own cache key, and it has to
    be right on its own terms.)"""
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2019, 1, 1), end_date=None, status="active")
        _personal(cur, amount=105000, period_start=date(2019, 1, 1), episode_id=episode_id)
        before = earnings.personal_compensation_fingerprint(cur)
        assert earnings.personal_earnings_state(cur, today=TODAY)["status"] == "current"

        # The salary row is untouched; only the episode ends.
        cur.execute(
            "UPDATE profile360.episodes SET end_date = %s WHERE id = %s", (date(2022, 6, 30), episode_id)
        )
        after = earnings.personal_compensation_fingerprint(cur)
        assert earnings.personal_earnings_state(cur, today=TODAY)["status"] == "historical"

    assert before != after


def test_the_earnings_fingerprint_notices_an_episode_status_change():
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2019, 1, 1), end_date=None, status="active")
        _personal(cur, amount=105000, period_start=date(2019, 1, 1), episode_id=episode_id)
        before = earnings.personal_compensation_fingerprint(cur)
        cur.execute("UPDATE profile360.episodes SET status = 'ended' WHERE id = %s", (episode_id,))
        after = earnings.personal_compensation_fingerprint(cur)
    assert before != after


def test_an_unlinked_episode_does_not_churn_the_earnings_fingerprint():
    """Only episodes a compensation observation points at can change an
    earnings answer; hashing the rest would invalidate caches for nothing."""
    with db_cursor() as cur:
        episode_id = _episode(cur, start_date=date(2025, 1, 1), end_date=None)
        _personal(cur, amount=105000, period_start=date(2025, 1, 1), episode_id=episode_id)
        before = earnings.personal_compensation_fingerprint(cur)
        _episode(cur, start_date=date(2010, 1, 1), end_date=date(2012, 1, 1), title="Unrelated")
        after = earnings.personal_compensation_fingerprint(cur)
    assert before == after


def test_changing_the_planning_assumption_does_not_make_market_benchmarks_stale():
    """Billable days affect only the personal comparison. Marking every
    derived economics figure stale for a change that cannot alter one would
    withhold benchmarks for no reason."""
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _archetype_comp(cur, archetype_id, market_id)
        record_rebuild(cur, "test-engine")
        assert economics_freshness(cur)["state"] == "fresh"

        earnings.save_planning_assumption(cur, contract_billable_days_per_year=215, note=None)

        assert economics_freshness(cur)["state"] == "fresh"
        assert resolver.resolve_role_compensation(cur, role_id)["basis"] == resolver.BASIS_MARKET_ESTIMATE


def test_changing_the_planning_assumption_still_invalidates_the_pathways_cache():
    """It has to reach Pathways some way — through the personal fingerprint,
    not by falsely claiming the derived tables need rebuilding."""
    with db_cursor() as cur:
        before = earnings.personal_compensation_fingerprint(cur)
        earnings.save_planning_assumption(cur, contract_billable_days_per_year=215, note=None)
        after = earnings.personal_compensation_fingerprint(cur)
    assert before != after


# --- The HTTP accept contract -----------------------------------------------

# Every figure a correction test corrects *to* has to be a figure this advert
# states, because a posting-stated observation may only carry numbers its own
# quote contains. So the advert states two base ranges and two bonus rates,
# exactly as a real posting with a senior band and a service-related bonus
# does — and a correction between them re-quotes the passage it moves to.
_BONUS_POSTING = (
    "Head of Capital, London.\n\n"
    "Base salary: £120,000 - £145,000 per annum.\n"
    "Exceptional candidates will be considered at £125,000 - £150,000.\n"
    "Plus an annual bonus of up to 15%, rising to 20% after two years.\n"
    "Total package, including bonus, up to £165,000.\n"
)
_SENIOR_BAND = "Exceptional candidates will be considered at £125,000 - £150,000."
_PACKAGE_LINE = "Total package, including bonus, up to £165,000."


def test_the_accept_endpoint_takes_the_bonus_shape_the_prompt_returns(client):
    """The extraction prompt returns a bonus with `bonus_pct` set and
    `currency: null`. A required `currency: str` on the route model rejected
    exactly that with a 422 before the service layer ever saw it."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")

    response = client.post(
        f"/api/role-instances/{role_id}/compensation/accept",
        json={
            "amount_min": None, "amount_max": None, "bonus_pct": 15, "currency": None,
            "component": "bonus_pct", "pay_period": "annual",
            "evidence_span": "Plus an annual bonus of up to 15%",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "accepted"

    with db_cursor() as cur:
        cur.execute(
            "SELECT bonus_pct, amount_min, currency FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s",
            (role_id,),
        )
        row = cur.fetchone()
    assert row["bonus_pct"] == 15
    assert row["amount_min"] is None
    assert row["currency"] == "GBP"  # borrowed from the pay it applies to


def test_the_accept_endpoint_still_refuses_a_bonus_with_no_percentage(client):
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")

    response = client.post(
        f"/api/role-instances/{role_id}/compensation/accept",
        json={"amount_min": None, "amount_max": None, "bonus_pct": None, "currency": None,
              "component": "bonus_pct", "pay_period": "annual",
              "evidence_span": "Plus an annual bonus of up to 15%"},
    )
    assert response.status_code == 400
    assert "percentage" in response.json()["detail"]


def test_the_accept_endpoint_still_requires_a_currency_for_a_salary(client):
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency=None)

    response = client.post(
        f"/api/role-instances/{role_id}/compensation/accept",
        json={"amount_min": 120000, "amount_max": 145000, "currency": None,
              "component": "base", "pay_period": "annual",
              "evidence_span": "Base salary: £120,000 - £145,000 per annum."},
    )
    assert response.status_code == 400
    assert "currency" in response.json()["detail"]


# --- Role-aware correction / reject / re-accept -----------------------------

def _accepted_base(cur, role_id):
    return _accept(cur, role_id, component="base", amount_min=120000, amount_max=145000, currency="GBP",
                   evidence_span="Base salary: £120,000 - £145,000 per annum.")


def test_re_accepting_a_retired_figure_retires_its_replacement(client):
    """The generic review endpoint just flips review_status, which would put
    the role back to two accepted base salaries — both feeding the archetype
    benchmark. Re-accept has to supersede, exactly like an acceptance."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        first = _accepted_base(cur, role_id)
        second = _accept(cur, role_id, component="base", amount_min=125000, amount_max=150000, currency="GBP",
                         evidence_span=_SENIOR_BAND)
        assert second["superseded_observation_ids"] == [first["id"]]

    response = client.post(f"/api/role-instances/{role_id}/compensation/{first['id']}/reaccept")
    assert response.status_code == 200, response.text
    assert response.json()["superseded_observation_ids"] == [second["id"]]

    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s AND review_status = 'accepted' AND component = 'base'",
            (role_id,),
        )
        assert cur.fetchone()["n"] == 1
        assert resolver.resolve_role_compensation(cur, role_id)["amount_min"] == 120000


def test_correcting_an_accepted_figure_revalidates_against_the_source(client):
    """The generic PATCH sets columns without re-reading the document, so it
    could detach a stated fact from the quote that justifies it."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    bad_span = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"evidence_span": "a quote that is not in the advert"},
    )
    assert bad_span.status_code == 400
    assert "not an exact match" in bad_span.json()["detail"]

    bad_shape = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"bonus_pct": 15},
    )
    assert bad_shape.status_code == 400
    assert "only applies to a bonus_pct component" in bad_shape.json()["detail"]


def test_correcting_an_accepted_figure_preserves_the_value_it_replaces(client):
    """A correction must not rewrite history. The prior accepted figure is
    source-backed financial evidence: it stays, retired and annotated, so the
    record reads "£120k-£145k was accepted, then corrected to £125k-£150k"."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    response = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"amount_min": 125000, "amount_max": 150000, "evidence_span": _SENIOR_BAND},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "corrected"
    assert body["corrected_from_observation_id"] == accepted["id"]
    assert body["id"] != accepted["id"], "a correction creates a new observation"

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, review_status, amount_min, amount_max, source_note "
            "FROM jobber.compensation_observation WHERE role_instance_id = %s ORDER BY created_at",
            (role_id,),
        )
        rows = {str(r["id"]): r for r in cur.fetchall()}
        resolved = resolver.resolve_role_compensation(cur, role_id)

    # Both figures survive; exactly one is accepted.
    assert len(rows) == 2
    original, corrected = rows[accepted["id"]], rows[body["id"]]
    assert original["review_status"] == "rejected"
    assert original["amount_min"] == 120000 and original["amount_max"] == 145000
    assert f"superseded by reviewed observation {body['id']}" in original["source_note"]
    assert corrected["review_status"] == "accepted"
    assert corrected["amount_min"] == 125000 and corrected["amount_max"] == 150000

    assert sum(r["review_status"] == "accepted" for r in rows.values()) == 1
    assert resolved["amount_min"] == 125000
    assert resolved["amount_max"] == 150000


def test_each_figure_keeps_its_own_content_addressed_source_key(client):
    """The key hashes the figures and quote. With a correction creating a new
    row, each row's key describes its own content — the original's is not
    rewritten to describe a figure it never held."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)
        cur.execute("SELECT source_key FROM jobber.compensation_observation WHERE id = %s", (accepted["id"],))
        original_key = cur.fetchone()["source_key"]

    corrected_id = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"amount_min": 125000, "amount_max": 150000, "evidence_span": _SENIOR_BAND},
    ).json()["id"]

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, source_key FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,)
        )
        keys = {str(r["id"]): r["source_key"] for r in cur.fetchall()}

    assert keys[accepted["id"]] == original_key, "the original's key still describes the original"
    assert keys[corrected_id] != original_key
    assert len(set(keys.values())) == 2


def test_a_correction_that_changes_nothing_is_a_no_op(client):
    """Content-addressed keys mean re-submitting identical figures lands on
    the same row — no spurious history entry for a non-correction."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    response = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}", json={"amount_min": 120000}
    )
    assert response.status_code == 200
    assert response.json()["id"] == accepted["id"]
    assert response.json()["status"] == "unchanged"

    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,)
        )
        assert cur.fetchone()["n"] == 1


def test_a_correction_that_changes_the_component_still_retires_the_original(client):
    """Supersession keys on the new component, so a base -> total_package
    correction would otherwise leave the original accepted alongside it.

    Changing the component re-anchors the whole item: a total package has to
    be quoted from wording that says it is one, so the correction moves to the
    advert's package line and the figure that line gives."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    response = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"component": "total_package", "amount_min": None, "amount_max": 165000,
              "evidence_span": _PACKAGE_LINE},
    )
    assert response.status_code == 200, response.text

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, component, review_status FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s",
            (role_id,),
        )
        rows = {str(r["id"]): r for r in cur.fetchall()}

    assert rows[accepted["id"]]["review_status"] == "rejected"
    assert sum(r["review_status"] == "accepted" for r in rows.values()) == 1
    assert next(r["component"] for r in rows.values() if r["review_status"] == "accepted") == "total_package"


def test_a_bonus_can_be_corrected_as_a_percentage(client):
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        bonus = _accept(cur, role_id, component="bonus_pct", bonus_pct=15,
                        evidence_span="Plus an annual bonus of up to 15%")

    response = client.patch(
        f"/api/role-instances/{role_id}/compensation/{bonus['id']}",
        json={"bonus_pct": 20, "evidence_span": "rising to 20% after two years"},
    )
    assert response.status_code == 200, response.text
    corrected_id = response.json()["id"]

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, bonus_pct, amount_min, review_status FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s",
            (role_id,),
        )
        rows = {str(r["id"]): r for r in cur.fetchall()}

    assert rows[bonus["id"]]["bonus_pct"] == 15 and rows[bonus["id"]]["review_status"] == "rejected"
    assert rows[corrected_id]["bonus_pct"] == 20 and rows[corrected_id]["review_status"] == "accepted"
    assert rows[corrected_id]["amount_min"] is None


# --- The generic market-data endpoints must not reach posting-stated rows ---

def test_the_generic_review_endpoint_cannot_reaccept_a_posting_stated_row(client):
    """Its listing is survey-only, but it updated by id alone — so naming a
    posting-stated id directly would flip it back to accepted with no
    supersession, recreating two accepted base salaries."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        first = _accepted_base(cur, role_id)
        second = _accept(cur, role_id, component="base", amount_min=125000, amount_max=150000, currency="GBP",
                         evidence_span=_SENIOR_BAND)
        assert second["superseded_observation_ids"] == [first["id"]]

    response = client.post(
        f"/api/market-data/compensation-observations/{first['id']}/review", json={"action": "accept"}
    )
    assert response.status_code == 400
    assert "posting-stated observation" in response.json()["detail"]
    assert "/api/role-instances/" in response.json()["detail"]

    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s AND review_status = 'accepted' AND component = 'base'",
            (role_id,),
        )
        assert cur.fetchone()["n"] == 1, "the bypass must not have recreated a second accepted base salary"


def test_the_generic_patch_cannot_mutate_a_posting_stated_row(client):
    """It sets columns without re-reading the source, so it could move a
    stated figure away from the quote that justifies it."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    response = client.patch(
        f"/api/market-data/compensation-observations/{accepted['id']}", json={"amount_min": 999999}
    )
    assert response.status_code == 400
    assert "posting-stated observation" in response.json()["detail"]

    with db_cursor() as cur:
        cur.execute(
            "SELECT amount_min FROM jobber.compensation_observation WHERE id = %s", (accepted["id"],)
        )
        assert cur.fetchone()["amount_min"] == 120000


def test_the_generic_endpoints_still_work_for_survey_rows(client):
    """Closing the loophole must not break the lifecycle those endpoints
    actually exist for."""
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, archetype_concept_id, market_id, component, pay_period, currency,
                 amount_min, amount_max, basis, review_status)
            VALUES (%s, %s, %s, 'base', 'annual', 'GBP', 100000, 120000, 'survey', 'unreviewed')
            RETURNING id
            """,
            (f"test:{uuid.uuid4()}", archetype_id, market_id),
        )
        survey_id = str(cur.fetchone()["id"])

    assert client.post(
        f"/api/market-data/compensation-observations/{survey_id}/review", json={"action": "accept"}
    ).status_code == 200
    assert client.patch(
        f"/api/market-data/compensation-observations/{survey_id}", json={"amount_min": 105000}
    ).status_code == 200

    with db_cursor() as cur:
        cur.execute(
            "SELECT review_status, amount_min FROM jobber.compensation_observation WHERE id = %s", (survey_id,)
        )
        row = cur.fetchone()
    assert row["review_status"] == "accepted"
    assert row["amount_min"] == 105000


def test_rejecting_through_the_role_endpoint_retires_without_deleting(client):
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    response = client.post(f"/api/role-instances/{role_id}/compensation/{accepted['id']}/reject")
    assert response.status_code == 200
    assert response.json()["review_status"] == "rejected"

    with db_cursor() as cur:
        cur.execute("SELECT review_status FROM jobber.compensation_observation WHERE id = %s", (accepted["id"],))
        assert cur.fetchone()["review_status"] == "rejected"
        assert resolver.resolve_role_compensation(cur, role_id)["basis"] == resolver.BASIS_INSUFFICIENT


def test_the_role_correction_endpoints_refuse_a_foreign_or_non_posting_observation(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur)
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        other_role = _role(cur, title="Another role", document_id=_document(cur, "Another advert."))
        accepted = _accepted_base(cur, role_id)
        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, role_instance_id, archetype_concept_id, market_id, component, pay_period,
                 currency, amount_min, amount_max, basis, review_status)
            VALUES (%s, %s, %s, %s, 'base', 'annual', 'GBP', 100000, 120000, 'survey', 'accepted')
            RETURNING id
            """,
            (f"test:{uuid.uuid4()}", role_id, archetype_id, market_id),
        )
        survey_id = str(cur.fetchone()["id"])

    # Belongs to a different role.
    assert client.post(
        f"/api/role-instances/{other_role}/compensation/{accepted['id']}/reject"
    ).status_code == 404
    # Not a posting-stated row this flow owns.
    refused = client.post(f"/api/role-instances/{role_id}/compensation/{survey_id}/reject")
    assert refused.status_code == 404
    assert "posting-stated" in refused.json()["detail"]


def test_the_content_key_is_stable_across_number_spellings():
    """The same figure arrives as an int from JSON, a float from Pydantic and
    a Decimal from the database. If those hashed differently, re-accepting an
    unchanged figure would create a duplicate accepted row."""
    from decimal import Decimal

    from app.posting_compensation import _source_key

    def key(amount_min, amount_max, bonus_pct=None):
        return _source_key(
            "role-1",
            {"component": "base", "pay_period": "annual", "currency": "GBP",
             "amount_min": amount_min, "amount_max": amount_max, "bonus_pct": bonus_pct,
             "evidence_span": "£120,000 - £145,000"},
        )

    assert key(120000, 145000) == key(120000.0, 145000.0) == key(Decimal("120000"), Decimal("145000"))
    assert key(120000, 145000) != key(125000, 150000)


# --- A stated figure must be a figure the advert states ---------------------
#
# Proving the *quote* is real said nothing about whether the numbers submitted
# beside it were the numbers that quote gives. Everything below feeds the
# resolver's advert-stated tier, the archetype benchmark and the personal
# comparison, so a figure the source never states must not be able to enter it
# wearing basis='posting_stated'.

def test_a_figure_the_quote_does_not_state_is_refused_even_with_a_valid_span():
    """The exact hole: a genuine, verbatim "£120,000 - £145,000 per annum"
    span carrying £999,999. Both the span check and the shape check pass."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", amount_min=999999, currency="GBP",
                    evidence_span="Base salary: £120,000 - £145,000 per annum.")

        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,)
        )
        assert cur.fetchone()["n"] == 0, "nothing may be written when corroboration fails"

    message = str(excinfo.value)
    assert "not stated by the quoted evidence span" in message
    assert "120000, 145000" in message, "the refusal names what the span does state"
    assert "curator-asserted" in message, "and names where a reviewer's own figure belongs"


def test_a_figure_that_is_off_by_a_digit_is_refused():
    """The typo this rule exists for — £12,000 is not £120,000, and the quote
    justifying it says so."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", amount_min=12000, amount_max=14500, currency="GBP",
                    evidence_span="Base salary: £120,000 - £145,000 per annum.")
    assert "amount_min 12000 is not stated" in str(excinfo.value)


def test_a_bonus_percentage_the_quote_does_not_state_is_refused():
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="bonus_pct", bonus_pct=25,
                    evidence_span="Plus an annual bonus of up to 15%")
    assert "bonus_pct 25% is not stated" in str(excinfo.value)
    assert "15%" in str(excinfo.value)


def test_a_correction_cannot_move_a_figure_away_from_its_quote(client):
    """A PATCH that changes the amounts but not the span is refused unless the
    span already states them — so correcting to a figure from another passage
    means quoting that passage, and the stored row never drifts from its
    evidence."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    drifting = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"amount_min": 125000, "amount_max": 150000},
    )
    assert drifting.status_code == 400
    assert "not stated by the quoted evidence span" in drifting.json()["detail"]

    # Quoting the passage that does state them is accepted.
    re_anchored = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"amount_min": 125000, "amount_max": 150000, "evidence_span": _SENIOR_BAND},
    )
    assert re_anchored.status_code == 200, re_anchored.text

    with db_cursor() as cur:
        cur.execute(
            "SELECT amount_min, amount_max, evidence_span FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s AND review_status = 'accepted'",
            (role_id,),
        )
        row = cur.fetchone()
    assert row["amount_min"] == 125000 and row["amount_max"] == 150000
    assert row["evidence_span"] == _SENIOR_BAND


def test_a_re_accept_will_not_reinstate_an_uncorroborated_figure(client):
    """Re-accept re-validates in full, so a row whose figures its own quote
    does not state cannot be brought back as a stated fact — including a row
    written before this rule existed."""
    with db_cursor() as cur:
        market_id = _market(cur)
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, role_instance_id, document_id, market_id, component, pay_period, currency,
                 amount_min, amount_max, basis, review_status, evidence_span)
            VALUES (%s, %s, %s, %s, 'base', 'annual', 'GBP', 999999, NULL, 'posting_stated', 'rejected', %s)
            RETURNING id
            """,
            (f"test:{uuid.uuid4()}", role_id, document_id, market_id,
             "Base salary: £120,000 - £145,000 per annum."),
        )
        legacy_id = str(cur.fetchone()["id"])

    response = client.post(f"/api/role-instances/{role_id}/compensation/{legacy_id}/reaccept")
    assert response.status_code == 400
    assert "not stated by the quoted evidence span" in response.json()["detail"]

    with db_cursor() as cur:
        cur.execute("SELECT review_status FROM jobber.compensation_observation WHERE id = %s", (legacy_id,))
        assert cur.fetchone()["review_status"] == "rejected"


def test_the_proposal_screen_flags_a_figure_the_quote_does_not_state(monkeypatch):
    """The review screen must never offer an Accept the server will refuse, so
    the same rule annotates the proposal."""
    from app import ai
    from app.posting_compensation import PostingCompensationProposal

    def fake_run(**kwargs):
        output = PostingCompensationProposal.model_validate(
            {
                "items": [
                    {"amount_min": 120000, "amount_max": 145000, "currency": "GBP", "component": "base",
                     "pay_period": "annual",
                     "evidence_span": "Base salary: £120,000 - £145,000 per annum."},
                    {"amount_min": 160000, "currency": "GBP", "component": "base", "pay_period": "annual",
                     "evidence_span": "Base salary: £120,000 - £145,000 per annum."},
                ],
                "no_compensation_stated": False,
            }
        )
        run = ai.AITaskRun("posting_compensation_extract", "test-model",
                           "extract_posting_compensation.md", "v1",
                           "2026-09-16", "2026-09-16", "ok", 100, 50)
        return ai.AITaskResult(output, run)

    monkeypatch.setattr(posting_compensation, "run_json_task", fake_run)

    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        items = posting_compensation.propose_posting_compensation(cur, role_id)["proposal"]["items"]

    assert items[0]["acceptable"] is True and items[0]["problems"] == []
    assert items[1]["acceptable"] is False
    assert any("not stated by the quoted evidence span" in p for p in items[1]["problems"])


@pytest.mark.parametrize(
    "span,amount,component,period",
    [
        ("Base salary: £120,000 - £145,000 per annum.", 120000, "base", "annual"),   # grouped
        ("Salary 120000 to 145000", 145000, "base", "annual"),                       # bare
        ("Paying £120k - £145k", 120000, "base", "annual"),                          # k suffix
        ("Paying £120-145k", 120000, "base", "annual"),                              # shared suffix
        ("Up to £1.2m in total compensation", 1200000, "total_package", "annual"),   # m suffix
        ("Rate: £650 per day, outside IR35.", 650, "day_rate", "daily"),             # day rate
    ],
)
def test_the_normalisation_reads_the_ways_adverts_write_money(span, amount, component, period):
    """Sensible normalisation, not string equality: an advert writing £120k
    and a reviewer entering 120000 mean the same figure."""
    assert posting_compensation._corroboration_problems(
        {"component": component, "pay_period": period, "currency": "GBP", "amount_min": amount}, span
    ) == []


def test_a_shared_scale_suffix_does_not_also_corroborate_the_bare_number():
    """"£120-145k" states £120,000, not £120 — reading the lower bound without
    the suffix would corroborate a figure a thousand times too small."""
    problems = posting_compensation._corroboration_problems(
        {"component": "base", "pay_period": "annual", "amount_min": 120}, "Paying £120-145k"
    )
    assert problems and "not stated" in problems[0]


def test_a_percentage_in_the_quote_is_not_an_amount():
    """"a bonus of up to 15%" states no cash, so 15 is not an amount the span
    corroborates — otherwise any percentage in a quote would license a
    matching salary figure."""
    problems = posting_compensation._corroboration_problems(
        {"component": "base", "pay_period": "annual", "amount_min": 15}, "Plus an annual bonus of up to 15%"
    )
    assert problems and "no such figure" in problems[0]


# --- ...and must mean what the advert says it means -------------------------
#
# Matching the number is half of source fidelity. Component and pay period are
# how the resolver interprets that number, and currency is what it is
# denominated in, so a figure quoted correctly but labelled wrongly is still a
# fabricated fact.

def test_a_day_rate_cannot_be_recorded_as_an_annual_salary():
    """The exact hole: "Rate: £650 per day" submitted as base/annual/GBP. The
    component is valid, annual is a valid period, GBP is the right currency
    and 650 is in the quote — and the stored fact would be a £650 salary."""
    posting = "Interim Capital Actuary.\nRate: £650 per day, outside IR35.\n"
    with db_cursor() as cur:
        document_id = _document(cur, posting)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", pay_period="annual", amount_min=650, currency="GBP",
                    evidence_span="Rate: £650 per day, outside IR35.")

        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,)
        )
        assert cur.fetchone()["n"] == 0

    message = str(excinfo.value)
    assert "states a rate per day" in message
    assert "'day_rate'" in message and "'daily'" in message, "the refusal names the labels that would be right"


def test_the_same_quote_is_accepted_as_the_day_rate_it_states():
    """The rule refuses the mislabel, not the figure."""
    posting = "Interim Capital Actuary.\nRate: £650 per day, outside IR35.\n"
    with db_cursor() as cur:
        document_id = _document(cur, posting)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        _accept(cur, role_id, component="day_rate", pay_period="daily", amount_min=650, currency="GBP",
                employment_basis="contract", evidence_span="Rate: £650 per day, outside IR35.")
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert resolved["component"] == "day_rate"
    assert resolved["pay_period"] == "daily"
    assert resolved["amount_reference"] == 650


def test_a_total_package_cannot_be_recorded_as_a_base_salary():
    """"Total package up to £180,000" as base would offer a base salary the
    advert never did — and it is the figure the archetype benchmark would
    then aggregate as a salary."""
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", amount_max=180000, currency="GBP",
                    evidence_span="Total package up to £180,000.")
    assert "states a total package" in str(excinfo.value)
    assert "'total_package'" in str(excinfo.value)


def test_a_total_package_must_be_quoted_from_wording_that_says_it_is_one():
    """The other direction: claiming a package needs more than the same
    number appearing somewhere."""
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="total_package", amount_min=120000, amount_max=145000,
                    currency="GBP", evidence_span="Base salary: £120,000 - £145,000 per annum.")
    assert "without saying it is the whole package" in str(excinfo.value)


def test_a_benefits_package_mentioned_beside_a_salary_does_not_veto_a_base_figure():
    """The asymmetry is deliberate. Bare "package" supports a package claim,
    but only wording that explicitly totals may overrule a base one —
    otherwise "plus a generous benefits package" would refuse an ordinary
    base salary."""
    posting = "Head of Capital.\nBase salary £120,000 plus a generous benefits package.\n"
    with db_cursor() as cur:
        document_id = _document(cur, posting)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        result = _accept(cur, role_id, component="base", amount_min=120000, currency="GBP",
                         evidence_span="Base salary £120,000 plus a generous benefits package.")
    assert result["review_status"] == "accepted"


@pytest.mark.parametrize(
    "component,pay_period,expected",
    [
        ("day_rate", "annual", "pay_period must be 'daily'"),
        ("base", "daily", "pay_period must be 'annual'"),
        ("total_package", "daily", "pay_period must be 'annual'"),
    ],
)
def test_an_incoherent_component_and_pay_period_is_refused(component, pay_period, expected):
    """Checked without needing a span at all: these pairs are wrong on their
    own terms, whatever the advert says."""
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component=component, pay_period=pay_period, amount_min=120000,
                    currency="GBP", evidence_span="Base salary: £120,000 - £145,000 per annum.")
    assert expected in str(excinfo.value)


def test_a_bonus_percentage_keeps_its_period_unconstrained():
    """A percentage of pay carries no period of its own and nothing reads one
    off it, so the coherence rule deliberately leaves it alone rather than
    inventing a constraint."""
    assert posting_compensation._value_problems(
        {"component": "bonus_pct", "pay_period": "daily", "bonus_pct": 15, "currency": "GBP"},
        fallback_currency="GBP",
    ) == []


def test_a_currency_the_quote_contradicts_is_refused():
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", amount_min=120000, amount_max=145000, currency="USD",
                    evidence_span="Base salary: £120,000 - £145,000 per annum.")
    assert "currency USD is not the currency the quoted evidence span states (GBP)" in str(excinfo.value)


@pytest.mark.parametrize(
    "span,currency,accepted",
    [
        ("Salary GBP 120,000 per annum", "GBP", True),    # ISO code agrees
        ("Salary GBP 120,000 per annum", "EUR", False),   # ISO code disagrees
        ("Salary €120,000 per annum", "EUR", True),       # sign agrees
        ("Salary 120,000 per annum", "USD", True),        # no signal, no opinion
        ("Salary $120,000 per annum", "CAD", True),       # "$" is ambiguous, so it rules nothing out
    ],
)
def test_currency_is_corroborated_only_where_the_quote_is_unambiguous(span, currency, accepted):
    problems = posting_compensation._corroboration_problems(
        {"component": "base", "pay_period": "annual", "currency": currency, "amount_min": 120000}, span
    )
    assert (problems == []) is accepted


def test_a_quote_stating_both_bases_leaves_the_judgement_to_the_reviewer():
    """A passage giving a day rate and its annual equivalent does not settle
    which the figure is, so it does not get to overrule the reviewer."""
    span = "£650 per day, c. £150,000 per annum"
    for component, period, amount in (("day_rate", "daily", 650), ("base", "annual", 150000)):
        assert posting_compensation._corroboration_problems(
            {"component": component, "pay_period": period, "currency": "GBP", "amount_min": amount}, span
        ) == []


def test_the_proposal_screen_flags_a_mislabelled_day_rate(monkeypatch):
    """The same rules annotate the proposal, so a model that reads the right
    quote but labels it annual base is caught on the review screen."""
    from app import ai
    from app.posting_compensation import PostingCompensationProposal

    def fake_run(**kwargs):
        output = PostingCompensationProposal.model_validate(
            {
                "items": [
                    {"amount_min": 650, "currency": "GBP", "component": "base", "pay_period": "annual",
                     "evidence_span": "Rate: £650 per day, outside IR35."},
                ],
                "no_compensation_stated": False,
            }
        )
        run = ai.AITaskRun("posting_compensation_extract", "test-model",
                           "extract_posting_compensation.md", "v1",
                           "2026-09-16", "2026-09-16", "ok", 100, 50)
        return ai.AITaskResult(output, run)

    monkeypatch.setattr(posting_compensation, "run_json_task", fake_run)

    with db_cursor() as cur:
        document_id = _document(cur, "Interim Capital Actuary.\nRate: £650 per day, outside IR35.\n")
        role_id = _role(cur, document_id=document_id, currency="GBP")
        items = posting_compensation.propose_posting_compensation(cur, role_id)["proposal"]["items"]

    assert items[0]["acceptable"] is False
    assert any("states a rate per day" in p for p in items[0]["problems"])


# --- A period the schema cannot hold is refused, never annualised -----------
#
# `compensation_observation.pay_period` is CHECK (pay_period IN ('annual',
# 'daily')) (migration 0013), so a monthly or hourly figure has no
# representation under any basis — not even curator_asserted. Recognising only
# daily and annual wording made "no period stated" and "a period we cannot
# store" the same silent case.

@pytest.mark.parametrize(
    "span,amount,named",
    [
        ("Salary: £8,000 per month.", 8000, "a monthly"),
        ("Salary: £8,000 pcm.", 8000, "a monthly"),
        ("Rate: £85 per hour.", 85, "an hourly"),
        ("Paying £1,600 a week.", 1600, "a weekly"),
    ],
)
def test_a_period_the_schema_cannot_hold_is_refused(span, amount, named):
    """£8,000 per month recorded as annual base pay is an £8,000 salary. The
    prompt already tells the model not to convert; the server has to enforce
    it, because an edited item takes the same path as a proposed one."""
    posting = f"Head of Capital, London.\n{span}\n"
    with db_cursor() as cur:
        document_id = _document(cur, posting)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", amount_min=amount, currency="GBP", evidence_span=span)

        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,)
        )
        assert cur.fetchone()["n"] == 0

    message = str(excinfo.value)
    assert f"states {named} figure" in message
    assert "only be annual or daily" in message
    # It must not offer the curator-asserted route: the CHECK constraint
    # forbids a monthly period there too, so that would be wrong advice.
    assert "curator-asserted" not in message


def test_an_hourly_rate_cannot_be_recorded_as_a_day_rate_either():
    """The refusal is about the period the advert states, not about which
    component the reviewer picked."""
    span = "Rate: £85 per hour."
    with db_cursor() as cur:
        document_id = _document(cur, f"Interim Actuary.\n{span}\n")
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="day_rate", pay_period="daily", amount_min=85, currency="GBP",
                    employment_basis="contract", evidence_span=span)
    assert "only be annual or daily" in str(excinfo.value)


def test_an_advert_that_annualises_its_own_monthly_figure_is_still_usable():
    """The rule refuses *our* converting, not the advert's. Where the posting
    states the annual equivalent itself, that figure is exactly what the
    reviewer should take."""
    span = "£8,000 per month (£96,000 per annum)"
    with db_cursor() as cur:
        document_id = _document(cur, f"Head of Capital.\nSalary {span}.\n")
        role_id = _role(cur, document_id=document_id, currency="GBP")
        result = _accept(cur, role_id, component="base", amount_min=96000, currency="GBP",
                         evidence_span=span)
        resolved = resolver.resolve_role_compensation(cur, role_id)

    assert result["review_status"] == "accepted"
    assert resolved["amount_min"] == 96000


def test_the_monthly_figure_in_that_same_span_is_still_refused(client):
    """The span states an annual figure, so the whole-span rule passes it —
    but £8,000 is not that figure. The period written beside the submitted
    number settles it."""
    span = "£8,000 per month (£96,000 per annum)"
    with db_cursor() as cur:
        document_id = _document(cur, f"Head of Capital.\nSalary {span}.\n")
        role_id = _role(cur, document_id=document_id, currency="GBP")
        with pytest.raises(posting_compensation.PostingCompensationValidationError) as excinfo:
            _accept(cur, role_id, component="base", amount_min=8000, currency="GBP", evidence_span=span)
    assert "amount_min 8000 is written in the quoted evidence span as a monthly figure" in str(excinfo.value)


def test_an_annual_figure_paid_in_monthly_instalments_is_not_refused():
    """"£120,000 per annum, paid monthly" states an annual salary. Refusing it
    would be the rule misfiring on how the money arrives."""
    assert posting_compensation._corroboration_problems(
        {"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 120000},
        "£120,000 per annum, paid monthly",
    ) == []


def test_the_proposal_screen_flags_an_unsupported_period(monkeypatch):
    from app import ai
    from app.posting_compensation import PostingCompensationProposal

    def fake_run(**kwargs):
        output = PostingCompensationProposal.model_validate(
            {
                "items": [
                    {"amount_min": 8000, "currency": "GBP", "component": "base", "pay_period": "annual",
                     "evidence_span": "Salary: £8,000 per month."},
                ],
                "no_compensation_stated": False,
            }
        )
        run = ai.AITaskRun("posting_compensation_extract", "test-model",
                           "extract_posting_compensation.md", "v1",
                           "2026-09-16", "2026-09-16", "ok", 100, 50)
        return ai.AITaskResult(output, run)

    monkeypatch.setattr(posting_compensation, "run_json_task", fake_run)

    with db_cursor() as cur:
        document_id = _document(cur, "Head of Capital.\nSalary: £8,000 per month.\n")
        role_id = _role(cur, document_id=document_id, currency="GBP")
        items = posting_compensation.propose_posting_compensation(cur, role_id)["proposal"]["items"]

    assert items[0]["acceptable"] is False
    assert any("only be annual or daily" in p for p in items[0]["problems"])
