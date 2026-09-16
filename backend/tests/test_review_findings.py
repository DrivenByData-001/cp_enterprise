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
    archetype benchmark, so a corrected mistake kept influencing the market."""
    with db_cursor() as cur:
        document_id = _document(cur, _MULTI_COMPONENT_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        wrong = _accept(cur, role_id, component="base", amount_min=12000, amount_max=14500, currency="GBP",
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

_BONUS_POSTING = (
    "Head of Capital, London.\n\n"
    "Base salary: £120,000 - £145,000 per annum.\n"
    "Plus an annual bonus of up to 15%.\n"
)


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
            "evidence_span": "Plus an annual bonus of up to 15%.",
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
              "evidence_span": "Plus an annual bonus of up to 15%."},
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
                         evidence_span="Base salary: £120,000 - £145,000 per annum.")
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


def test_correcting_an_accepted_figure_updates_it_and_keeps_one_accepted(client):
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)

    response = client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"amount_min": 125000, "amount_max": 150000},
    )
    assert response.status_code == 200, response.text

    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s AND review_status = 'accepted'",
            (role_id,),
        )
        assert cur.fetchone()["n"] == 1
        resolved = resolver.resolve_role_compensation(cur, role_id)
    assert resolved["amount_min"] == 125000
    assert resolved["amount_max"] == 150000


def test_a_correction_keeps_the_content_addressed_source_key_true(client):
    """The key is a hash of the figures and quote, so a correction has to
    recompute it — otherwise a later acceptance of the corrected figures
    would collide with a row whose key no longer described it."""
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        accepted = _accepted_base(cur, role_id)
        cur.execute("SELECT source_key FROM jobber.compensation_observation WHERE id = %s", (accepted["id"],))
        before = cur.fetchone()["source_key"]

    client.patch(
        f"/api/role-instances/{role_id}/compensation/{accepted['id']}",
        json={"amount_min": 125000, "amount_max": 150000},
    )

    with db_cursor() as cur:
        cur.execute("SELECT source_key FROM jobber.compensation_observation WHERE id = %s", (accepted["id"],))
        after = cur.fetchone()["source_key"]
    assert before != after


def test_a_bonus_can_be_corrected_as_a_percentage(client):
    with db_cursor() as cur:
        document_id = _document(cur, _BONUS_POSTING)
        role_id = _role(cur, document_id=document_id, currency="GBP")
        bonus = _accept(cur, role_id, component="bonus_pct", bonus_pct=15,
                        evidence_span="Plus an annual bonus of up to 15%.")

    response = client.patch(
        f"/api/role-instances/{role_id}/compensation/{bonus['id']}", json={"bonus_pct": 20}
    )
    assert response.status_code == 200, response.text

    with db_cursor() as cur:
        cur.execute(
            "SELECT bonus_pct, amount_min FROM jobber.compensation_observation WHERE id = %s", (bonus["id"],)
        )
        row = cur.fetchone()
    assert row["bonus_pct"] == 20
    assert row["amount_min"] is None


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
