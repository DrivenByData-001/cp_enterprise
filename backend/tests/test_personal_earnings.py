"""Personal current / latest-known earnings state (build §1) and the
current-vs-opportunity comparison's compatibility rules (build §4).

Runs `app.personal_earnings` and `app.compensation_resolver` directly
against the real Postgres test database. Nothing here calls AI; nothing here
writes to profile360 except the test fixtures' own seeding of the accepted
observations the reader is supposed to read.
"""

import uuid
from datetime import date

import pytest

from app import personal_earnings as earnings
from app import compensation_resolver as resolver
from app.db import db_cursor


def _observation(
    cur, *, component, amount, currency="GBP", unit="annual", employment_basis="paye",
    source_kind="payslip", period_start=None, period_end=None, pay_date=None,
    review_status="accepted",
):
    cur.execute(
        """
        INSERT INTO profile360.compensation_observation
            (source_key, source_kind, employment_basis, component, period_start, period_end, pay_date,
             amount, currency, unit, review_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (f"test:{uuid.uuid4()}", source_kind, employment_basis, component, period_start, period_end,
         pay_date, amount, currency, unit, review_status),
    )
    return str(cur.fetchone()["id"])


TODAY = date(2026, 9, 16)


# --- Earnings state ---------------------------------------------------------

def test_accepted_paye_annual_base_resolves_as_current_earnings():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, currency="EUR",
                     period_start=date(2025, 7, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "current"
    assert len(state["baselines"]) == 1
    baseline = state["baselines"][0]
    assert baseline["component"] == "annual_base"
    assert baseline["amount"] == 105000
    assert baseline["currency"] == "EUR"
    assert baseline["employment_basis"] == "paye"
    assert baseline["unit"] == "annual"
    assert baseline["effective_from"] == date(2025, 7, 1)
    assert baseline["evidence_status"] == "current"
    assert baseline["label"] == "Current earnings"
    # Never annualised into something else, and no planning equivalent on a
    # figure that is already annual.
    assert baseline["planning_equivalent"] is None


def test_historical_only_evidence_becomes_latest_known_not_current():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=88000,
                     period_start=date(2023, 1, 1), period_end=date(2023, 12, 31))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "historical"
    baseline = state["baselines"][0]
    assert baseline["evidence_status"] == "historical"
    assert baseline["label"] == "Latest known earnings"
    assert baseline["evidence_period"] == "2023-01-01 to 2023-12-31"
    assert any("No current compensation evidence" in note for note in state["notes"])


def test_unreviewed_and_rejected_observations_are_excluded():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=200000, review_status="unreviewed")
        _observation(cur, component="annual_base", amount=300000, review_status="rejected")
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "unavailable"
    assert state["baselines"] == []


def test_no_compensation_evidence_returns_unavailable_state():
    with db_cursor() as cur:
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["status"] == "unavailable"
    assert state["baselines"] == []
    assert state["other_components"] == []
    assert any("No accepted personal compensation evidence" in n for n in state["notes"])


def test_contract_day_rate_stays_daily_without_a_planning_assumption():
    with db_cursor() as cur:
        _observation(cur, component="day_rate", amount=650, currency="GBP", unit="daily",
                     employment_basis="contract", source_kind="invoice", period_start=date(2025, 4, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    baseline = state["baselines"][0]
    assert baseline["component"] == "day_rate"
    assert baseline["amount"] == 650
    assert baseline["unit"] == "daily"
    # The critical assertion: no silent annualisation anywhere.
    assert baseline["planning_equivalent"] is None
    assert any("billable-days-per-year assumption" in n for n in state["notes"])


def test_planning_equivalent_uses_the_explicit_billable_days_assumption():
    with db_cursor() as cur:
        _observation(cur, component="day_rate", amount=650, currency="GBP", unit="daily",
                     employment_basis="contract", source_kind="invoice", period_start=date(2025, 4, 1))
        earnings.save_planning_assumption(cur, contract_billable_days_per_year=215, note="my own estimate")
        state = earnings.personal_earnings_state(cur, today=TODAY)

    equivalent = state["baselines"][0]["planning_equivalent"]
    assert equivalent["amount"] == 650 * 215
    assert equivalent["unit"] == "annual"
    assert equivalent["basis"] == "planning_equivalent"
    assert equivalent["assumption"]["billable_days_per_year"] == 215
    assert equivalent["caveat"] == "This is a planning equivalent, not salary."
    # The source amount is preserved untouched alongside it.
    assert state["baselines"][0]["amount"] == 650


def test_clearing_the_planning_assumption_withdraws_the_equivalent():
    with db_cursor() as cur:
        _observation(cur, component="day_rate", amount=650, unit="daily", employment_basis="contract")
        earnings.save_planning_assumption(cur, contract_billable_days_per_year=215, note=None)
        assert earnings.personal_earnings_state(cur, today=TODAY)["baselines"][0]["planning_equivalent"]
        earnings.save_planning_assumption(cur, contract_billable_days_per_year=None, note=None)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert state["baselines"][0]["planning_equivalent"] is None


def test_bonus_does_not_replace_the_base_baseline():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, period_start=date(2025, 7, 1))
        _observation(cur, component="bonus", amount=20000, unit="one_off", pay_date=date(2026, 3, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert len(state["baselines"]) == 1
    assert state["baselines"][0]["component"] == "annual_base"
    assert state["baselines"][0]["amount"] == 105000
    # The bonus is preserved and separately identifiable, never folded in.
    assert [o["component"] for o in state["other_components"]] == ["bonus"]
    assert state["other_components"][0]["amount"] == 20000


def test_multiple_currencies_are_reported_separately_never_combined():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, currency="GBP", period_start=date(2025, 1, 1))
        _observation(cur, component="annual_base", amount=120000, currency="EUR", period_start=date(2025, 1, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    assert sorted(state["currencies"]) == ["EUR", "GBP"]
    assert len(state["baselines"]) == 2
    assert {b["amount"] for b in state["baselines"]} == {105000, 120000}
    assert any("never converted or combined" in n for n in state["notes"])


def test_paye_and_contract_are_kept_distinct():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, employment_basis="paye",
                     period_start=date(2025, 1, 1))
        _observation(cur, component="day_rate", amount=650, unit="daily", employment_basis="contract",
                     period_start=date(2025, 1, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    bases = {b["employment_basis"] for b in state["baselines"]}
    assert bases == {"paye", "contract"}
    assert any("more than one employment basis" in n for n in state["notes"])


def test_current_evidence_is_preferred_over_historical_in_the_same_group():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=88000,
                     period_start=date(2023, 1, 1), period_end=date(2023, 12, 31))
        _observation(cur, component="annual_base", amount=105000, period_start=date(2025, 7, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    baseline = state["baselines"][0]
    assert baseline["amount"] == 105000
    assert baseline["evidence_status"] == "current"
    # The superseded figure is still visible, not discarded.
    assert [o["amount"] for o in baseline["other_evidence_in_group"]] == [88000]


def test_fingerprint_changes_when_evidence_or_assumption_changes():
    with db_cursor() as cur:
        first = earnings.personal_compensation_fingerprint(cur)
        _observation(cur, component="annual_base", amount=105000)
        second = earnings.personal_compensation_fingerprint(cur)
        earnings.save_planning_assumption(cur, contract_billable_days_per_year=215, note=None)
        third = earnings.personal_compensation_fingerprint(cur)

    assert first != second != third
    assert first != third


def test_fingerprint_changes_when_an_observation_is_merely_reviewed():
    """Accepting a row changes the answer even though no compensation field
    moved — the cache key has to notice."""
    with db_cursor() as cur:
        observation_id = _observation(cur, component="annual_base", amount=105000, review_status="unreviewed")
        before = earnings.personal_compensation_fingerprint(cur)
        cur.execute(
            "UPDATE profile360.compensation_observation SET review_status = 'accepted' WHERE id = %s",
            (observation_id,),
        )
        after = earnings.personal_compensation_fingerprint(cur)

    assert before != after


# --- Comparison compatibility (build §4) -----------------------------------

def _opportunity(**overrides):
    base = {
        "basis": resolver.BASIS_ADVERT_STATED,
        "basis_label": resolver.BASIS_LABELS[resolver.BASIS_ADVERT_STATED],
        "currency": "GBP",
        "amount_min": 120000,
        "amount_reference": 132000,
        "amount_max": 145000,
        "component": "base",
        "pay_period": "annual",
        "employment_basis": None,
        "as_of": date(2026, 1, 1),
    }
    return {**base, **overrides}


def test_compatible_comparison_produces_the_expected_differences():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, currency="GBP", period_start=date(2025, 7, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(_opportunity(), state)
    assert comparison["comparable"] is True
    assert comparison["difference_min"] == 15000
    assert comparison["difference_reference"] == 27000
    assert comparison["difference_max"] == 40000
    assert comparison["uses_planning_equivalent"] is False
    assert comparison["baseline"]["amount"] == 105000


def test_incompatible_currencies_produce_no_delta_and_say_why():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, currency="EUR", period_start=date(2025, 7, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(_opportunity(currency="GBP"), state)
    assert comparison["comparable"] is False
    assert comparison["difference_reference"] is None
    assert "GBP" in comparison["reason"] and "EUR" in comparison["reason"]
    assert "never converted" in comparison["reason"]


def test_contract_day_rate_versus_annual_salary_is_not_compared_without_an_assumption():
    with db_cursor() as cur:
        _observation(cur, component="day_rate", amount=650, currency="GBP", unit="daily",
                     employment_basis="contract", period_start=date(2025, 4, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(_opportunity(), state)
    assert comparison["comparable"] is False
    assert comparison["difference_reference"] is None
    assert "billable-days-per-year planning assumption" in comparison["reason"]


def test_contract_day_rate_compares_through_a_stated_planning_equivalent_and_flags_it():
    with db_cursor() as cur:
        _observation(cur, component="day_rate", amount=650, currency="GBP", unit="daily",
                     employment_basis="contract", period_start=date(2025, 4, 1))
        earnings.save_planning_assumption(cur, contract_billable_days_per_year=215, note=None)
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(_opportunity(), state)
    assert comparison["comparable"] is True
    assert comparison["uses_planning_equivalent"] is True
    assert comparison["baseline"]["amount"] == 650 * 215
    assert comparison["difference_reference"] == 132000 - 650 * 215
    assert any("planning equivalent" in limitation for limitation in comparison["limitations"])


def test_mismatched_employment_basis_is_not_silently_equated():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, currency="GBP",
                     employment_basis="paye", period_start=date(2025, 7, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(
        _opportunity(employment_basis="contract"), state
    )
    assert comparison["comparable"] is False
    assert "never treated as equivalent" in comparison["reason"]


def test_no_personal_evidence_means_no_comparison_rather_than_a_zero_baseline():
    with db_cursor() as cur:
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(_opportunity(), state)
    assert comparison["comparable"] is False
    assert comparison["difference_reference"] is None
    assert "No accepted personal compensation evidence" in comparison["reason"]


def test_insufficient_opportunity_evidence_produces_no_comparison():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, period_start=date(2025, 7, 1))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(
        resolver.insufficient_compensation("nothing known"), state
    )
    assert comparison["comparable"] is False
    assert "no compensation figure" in comparison["reason"].lower()


def test_historical_baseline_comparison_is_flagged_as_a_limitation():
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=88000, currency="GBP",
                     period_start=date(2023, 1, 1), period_end=date(2023, 12, 31))
        state = earnings.personal_earnings_state(cur, today=TODAY)

    comparison = resolver.compare_to_personal_earnings(_opportunity(), state)
    assert comparison["comparable"] is True
    assert any("historical evidence" in limitation for limitation in comparison["limitations"])


# --- API surface ------------------------------------------------------------

def test_personal_earnings_endpoint_returns_the_state(client):
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, currency="GBP",
                     period_start=date(2025, 7, 1))

    response = client.get("/api/pathways/personal-earnings")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "current"
    assert body["baselines"][0]["amount"] == 105000


def test_planning_assumption_round_trips_through_the_api(client):
    assert client.get("/api/pathways/planning-assumptions").json()["contract_billable_days_per_year"] is None

    saved = client.put(
        "/api/pathways/planning-assumptions",
        json={"contract_billable_days_per_year": 215, "note": "my own estimate"},
    )
    assert saved.status_code == 200
    assert saved.json()["contract_billable_days_per_year"] == 215

    assert client.get("/api/pathways/planning-assumptions").json()["note"] == "my own estimate"

    cleared = client.put("/api/pathways/planning-assumptions", json={"contract_billable_days_per_year": None})
    assert cleared.json()["contract_billable_days_per_year"] is None


@pytest.mark.parametrize("days", [0, -5, 400])
def test_planning_assumption_rejects_an_impossible_billable_day_count(client, days):
    response = client.put("/api/pathways/planning-assumptions", json={"contract_billable_days_per_year": days})
    assert response.status_code == 422


def test_personal_compensation_is_never_copied_into_jobber():
    """The boundary rule, asserted rather than only documented: reading the
    personal earnings state writes nothing to any jobber table that could
    hold a person-side compensation fact."""
    with db_cursor() as cur:
        _observation(cur, component="annual_base", amount=105000, currency="GBP")
        earnings.personal_earnings_state(cur, today=TODAY)
        cur.execute("SELECT COUNT(*) AS n FROM jobber.compensation_observation")
        assert cur.fetchone()["n"] == 0
        # The one jobber row this module may write holds an assumption, not money.
        cur.execute(
            "SELECT contract_billable_days_per_year FROM jobber.planning_assumption WHERE singleton"
        )
        assert cur.fetchone()["contract_billable_days_per_year"] is None
