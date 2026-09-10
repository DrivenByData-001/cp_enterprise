"""Phase 4 derivation engine: archetype demand, archetype compensation +
reference-benchmark selection, and capability gap value (prompt §4/§9/§10/
§11). Runs `app.economics_engine` directly against the real Postgres test
database — the engine never calls AI."""

import uuid
from datetime import date

from app import db
from app import economics_engine as engine


def _concept(cur, name, type_code="tool", status="active"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, name, status),
    )
    return str(cur.fetchone()["id"])


def _capability(cur, name, *, min_depth="owned"):
    cap_id = _concept(cur, name, type_code="capability")
    cur.execute(
        "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) VALUES (%s, %s, %s)",
        (cap_id, f"Demonstration standard for {name}", min_depth),
    )
    return cap_id


def _archetype(cur, name):
    concept_id = _concept(cur, name, type_code="role_archetype")
    cur.execute("INSERT INTO jobber.role_archetype_detail (concept_id) VALUES (%s)", (concept_id,))
    return concept_id


def _role(cur, *, archetype_concept_id=None, title="Test role"):
    role_id = db.upsert_role_instance(
        cur, None, {"instance_type": "observed_posting", "title": title}, skills=[]
    )
    if archetype_concept_id:
        cur.execute("UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s", (archetype_concept_id, role_id))
    return str(role_id)


def _claim_requirement(cur, role_id, concept_id, *, requirement_type="required"):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis) "
        "VALUES (%s, %s, %s, 'user_asserted')",
        (role_id, concept_id, requirement_type),
    )


def _assert_capability(cur, concept_id):
    cur.execute("INSERT INTO jobber.person_capability_assertion (jobber_concept_id, asserted) VALUES (%s, TRUE)", (concept_id,))


def _market(cur, code="test-market"):
    cur.execute("INSERT INTO jobber.market (code, label) VALUES (%s, %s) RETURNING id", (code, code))
    return str(cur.fetchone()["id"])


def _document(cur):
    doc_id, _ = db.create_document(cur, kind="market_survey", content_text="a survey report", provenance_quality="original")
    return doc_id


def _observation(
    cur,
    *,
    market_id,
    currency="GBP",
    component="base",
    pay_period="annual",
    basis="posting_stated",
    archetype_concept_id=None,
    role_instance_id=None,
    raw_role_label=None,
    amount_min=None,
    amount_max=None,
    amount_mid=None,
    reported_p25=None,
    reported_p50=None,
    reported_p75=None,
    reported_sample_size=None,
    document_id=None,
    review_status="accepted",
    period_end=None,
):
    cur.execute(
        """
        INSERT INTO jobber.compensation_observation
            (source_key, role_instance_id, archetype_concept_id, raw_role_label, market_id, component, pay_period,
             currency, amount_min, amount_max, amount_mid, reported_p25, reported_p50, reported_p75,
             reported_sample_size, document_id, basis, review_status, period_end)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            f"test:{uuid.uuid4()}", role_instance_id, archetype_concept_id, raw_role_label, market_id, component,
            pay_period, currency, amount_min, amount_max, amount_mid, reported_p25, reported_p50, reported_p75,
            reported_sample_size, document_id, basis, review_status, period_end,
        ),
    )
    return str(cur.fetchone()["id"])


# --- d_archetype_demand (prompt §4) -----------------------------------------

def test_archetype_demand_derived_from_assigned_roles(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Chief Actuary")
        cap_id = _capability(cur, "Lead a reserving process")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, cap_id, requirement_type="required")

        rows = engine.derive_archetype_demand(cur, archetype_id)
    assert len(rows) == 1
    assert rows[0]["capability_concept_id"] == cap_id
    assert rows[0]["roles_in_archetype"] == 1
    assert rows[0]["roles_demanding_capability"] == 1
    assert rows[0]["required_count"] == 1
    assert rows[0]["demand_rate"] == 1.0


def test_archetype_demand_only_counts_capability_typed_concepts(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Pricing Manager")
        atomic_id = _concept(cur, "Python", type_code="tool")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, atomic_id, requirement_type="required")

        rows = engine.derive_archetype_demand(cur, archetype_id)
    assert rows == []


def test_rebuild_archetype_demand_removes_stale_rows_on_reassignment(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Head of Capital")
        cap_id = _capability(cur, "Own a capital model")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, cap_id, requirement_type="required")
        engine.rebuild_archetype_demand(cur)

        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_archetype_demand WHERE archetype_concept_id = %s", (archetype_id,))
        assert cur.fetchone()["n"] == 1

        # Reassign the role away, then rebuild again.
        cur.execute("UPDATE jobber.role_instance SET archetype_concept_id = NULL WHERE id = %s", (role_id,))
        engine.rebuild_archetype_demand(cur)

        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_archetype_demand WHERE archetype_concept_id = %s", (archetype_id,))
        assert cur.fetchone()["n"] == 0


# --- benchmark selection rule (prompt §10) -----------------------------------

def test_fewer_than_five_stated_postings_yield_no_reference(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Reserving Actuary")
        market_id = _market(cur)
        for mid in (50000, 55000, 60000, 65000):
            _observation(cur, archetype_concept_id=archetype_id, market_id=market_id, amount_min=mid, amount_max=mid)

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["n_posting_stated"] == 4
    assert row["reference_comp"] is None
    assert row["reference_source"] is None


def test_five_stated_postings_yield_median_midpoint_reference(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Reserving Actuary II")
        market_id = _market(cur)
        for mid in (50000, 55000, 60000, 65000, 70000):
            _observation(cur, archetype_concept_id=archetype_id, market_id=market_id, amount_min=mid, amount_max=mid)

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["n_posting_stated"] == 5
    assert row["reference_source"] == "posting"
    assert row["reference_comp"] == 60000
    assert row["posting_p50"] == 60000


def test_posting_estimated_never_satisfies_the_sample_gate(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Estimated Only Archetype")
        market_id = _market(cur)
        for mid in (50000, 55000, 60000, 65000, 70000):
            _observation(cur, archetype_concept_id=archetype_id, market_id=market_id, basis="posting_estimated", amount_min=mid, amount_max=mid)

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["n_posting_stated"] == 0
    assert row["n_posting_estimated"] == 5
    assert row["reference_comp"] is None


def test_qualifying_survey_takes_precedence_over_posting(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Survey Preferred Archetype")
        market_id = _market(cur)
        doc_id = _document(cur)
        for mid in (50000, 55000, 60000, 65000, 70000):
            _observation(cur, archetype_concept_id=archetype_id, market_id=market_id, amount_min=mid, amount_max=mid)
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=95000, reported_sample_size=20, document_id=doc_id,
        )

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["reference_source"] == "survey"
    assert row["reference_comp"] == 95000
    # Posting evidence is still summarised, never discarded or pooled in.
    assert row["n_posting_stated"] == 5
    assert row["posting_p50"] == 60000


def test_newer_qualifying_survey_beats_older_survey_with_larger_sample(client):
    """The rule is 'most recent qualifying survey', not 'largest sample' —
    a genuinely newer, smaller-sample survey must win over an older one
    with a bigger sample, exactly per prompt §10's stated precedence."""
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Date Precedence Archetype")
        market_id = _market(cur)
        older_doc = _document(cur)
        newer_doc = _document(cur)
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=80000, reported_sample_size=100, document_id=older_doc, period_end=date(2024, 1, 1),
        )
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=95000, reported_sample_size=10, document_id=newer_doc, period_end=date(2026, 1, 1),
        )

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["reference_source"] == "survey"
    assert row["reference_comp"] == 95000


def test_equal_survey_dates_tiebreak_by_sample_size_then_id(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Tiebreak Archetype")
        market_id = _market(cur)
        same_date = date(2026, 1, 1)
        smaller_doc = _document(cur)
        larger_doc = _document(cur)
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=70000, reported_sample_size=8, document_id=smaller_doc, period_end=same_date,
        )
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=90000, reported_sample_size=25, document_id=larger_doc, period_end=same_date,
        )

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    # Same date -> the larger-sample survey wins the tie.
    assert row["reference_comp"] == 90000


def test_survey_with_insufficient_sample_does_not_qualify(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Thin Survey Archetype")
        market_id = _market(cur)
        doc_id = _document(cur)
        for mid in (50000, 55000, 60000, 65000, 70000):
            _observation(cur, archetype_concept_id=archetype_id, market_id=market_id, amount_min=mid, amount_max=mid)
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=95000, reported_sample_size=3, document_id=doc_id,
        )

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["reference_source"] == "posting"
    assert row["reference_comp"] == 60000


def test_survey_and_posting_never_falsely_pooled(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Separation Archetype")
        market_id = _market(cur)
        doc_id = _document(cur)
        for mid in (50000, 70000):
            _observation(cur, archetype_concept_id=archetype_id, market_id=market_id, amount_min=mid, amount_max=mid)
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p25=80000, reported_p50=90000, reported_p75=100000, reported_sample_size=50, document_id=doc_id,
        )

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    # Posting stats are computed only over the 2 posting rows.
    assert row["posting_p50"] == 60000
    assert row["n_posting_stated"] == 2
    # Survey stats are preserved as their own separate benchmark entry.
    assert len(row["survey_benchmarks"]) == 1
    assert row["survey_benchmarks"][0]["reported_p50"] == 90000


def test_n_survey_sources_counts_distinct_documents_not_rows(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Distinct Sources Archetype")
        market_id = _market(cur)
        one_report = _document(cur)
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=70000, reported_sample_size=10, document_id=one_report,
        )
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=72000, reported_sample_size=12, document_id=one_report,
        )

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["n_survey_sources"] == 1


def test_n_survey_sources_counts_two_reports_as_two(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Two Sources Archetype")
        market_id = _market(cur)
        report_a = _document(cur)
        report_b = _document(cur)
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=70000, reported_sample_size=10, document_id=report_a,
        )
        _observation(
            cur, archetype_concept_id=archetype_id, market_id=market_id, basis="survey",
            reported_p50=72000, reported_sample_size=12, document_id=report_b,
        )

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row["n_survey_sources"] == 2


def test_rejected_observations_never_influence_the_benchmark(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Rejected Evidence Archetype")
        market_id = _market(cur)
        for mid in (50000, 55000, 60000, 65000, 70000):
            _observation(cur, archetype_concept_id=archetype_id, market_id=market_id, amount_min=mid, amount_max=mid, review_status="rejected")

        row = engine.derive_archetype_comp(cur, archetype_id, market_id, "GBP", "base", "annual")
    assert row is None


# --- d_gap_value (prompt §11) -----------------------------------------------

def test_single_blocking_capability_unlocks_role(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Unlockable Archetype")
        cap_id = _capability(cur, "Sign off technical provisions")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, cap_id, requirement_type="required")
        market_id = _market(cur)

        rows = engine.derive_gap_value_for_bucket(cur, market_id, "GBP")
    row = next(r for r in rows if r["capability_concept_id"] == cap_id)
    assert row["roles_unlocked"] == 1
    assert row["roles_improved"] == 0
    assert row["archetypes_unlocked"] == 1
    assert row["archetypes_improved"] == 0


def test_one_of_multiple_gaps_only_improves_role(client):
    with db.db_cursor() as cur:
        archetype_id = _archetype(cur, "Multi Gap Archetype")
        cap1 = _capability(cur, "Gap One")
        cap2 = _capability(cur, "Gap Two")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, cap1, requirement_type="required")
        _claim_requirement(cur, role_id, cap2, requirement_type="required")
        market_id = _market(cur)

        rows = engine.derive_gap_value_for_bucket(cur, market_id, "GBP")
    row = next(r for r in rows if r["capability_concept_id"] == cap1)
    assert row["roles_unlocked"] == 0
    assert row["roles_improved"] == 1
    assert row["archetypes_improved"] == 1
    assert row["archetypes_unlocked"] == 0


def test_distinct_archetype_aggregation_across_unlocked_and_improved(client):
    with db.db_cursor() as cur:
        cap1 = _capability(cur, "Shared Gap")
        cap_other = _capability(cur, "Other Gap")

        unlocked_archetype = _archetype(cur, "Unlocked Archetype")
        role_unlocked = _role(cur, archetype_concept_id=unlocked_archetype)
        _claim_requirement(cur, role_unlocked, cap1, requirement_type="required")

        improved_archetype = _archetype(cur, "Improved Archetype")
        role_improved = _role(cur, archetype_concept_id=improved_archetype)
        _claim_requirement(cur, role_improved, cap1, requirement_type="required")
        _claim_requirement(cur, role_improved, cap_other, requirement_type="required")

        market_id = _market(cur)
        rows = engine.derive_gap_value_for_bucket(cur, market_id, "GBP")
    row = next(r for r in rows if r["capability_concept_id"] == cap1)
    assert row["archetypes_unlocked"] == 1
    assert row["archetypes_improved"] == 1
    assert set(row["trace"]["unlocked_archetype_ids"]) == {unlocked_archetype}
    assert set(row["trace"]["improved_archetype_ids"]) == {improved_archetype}


def test_unassigned_roles_handled_safely(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Unassigned Role Gap")
        role_id = _role(cur, archetype_concept_id=None)
        _claim_requirement(cur, role_id, cap_id, requirement_type="required")
        market_id = _market(cur)

        rows = engine.derive_gap_value_for_bucket(cur, market_id, "GBP")
    row = next(r for r in rows if r["capability_concept_id"] == cap_id)
    assert row["roles_unlocked"] == 1
    assert row["archetypes_unlocked"] == 0  # no archetype to credit


def test_best_current_reachable_benchmark_and_delta(client):
    with db.db_cursor() as cur:
        market_id = _market(cur)

        # Currently-reachable archetype: its one requirement is only
        # user_asserted (never blocking), and it has a seeded reference comp.
        reachable_archetype = _archetype(cur, "Currently Reachable")
        other_cap = _capability(cur, "Already Have This")
        _assert_capability(cur, other_cap)
        reachable_role = _role(cur, archetype_concept_id=reachable_archetype)
        _claim_requirement(cur, reachable_role, other_cap, requirement_type="required")
        _persist_seed_comp(cur, reachable_archetype, market_id, reference_comp=80000.0)

        # Gap capability C unlocks a higher-paying archetype.
        cap_c = _capability(cur, "Gap C")
        unlocked_archetype = _archetype(cur, "Unlocked By C")
        unlocked_role = _role(cur, archetype_concept_id=unlocked_archetype)
        _claim_requirement(cur, unlocked_role, cap_c, requirement_type="required")
        _persist_seed_comp(cur, unlocked_archetype, market_id, reference_comp=120000.0)

        rows = engine.derive_gap_value_for_bucket(cur, market_id, "GBP")
    row = next(r for r in rows if r["capability_concept_id"] == cap_c)
    assert row["reference_comp_unlocked"] == 120000.0
    assert row["comp_delta_vs_best_current_reachable"] == 40000.0
    assert row["trace"]["best_current_reachable"] == 80000.0


def test_no_monetary_delta_without_qualifying_evidence(client):
    with db.db_cursor() as cur:
        market_id = _market(cur)
        cap_id = _capability(cur, "No Evidence Gap")
        archetype_id = _archetype(cur, "No Comp Data Archetype")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, cap_id, requirement_type="required")
        # No d_archetype_comp row seeded at all for this archetype/bucket.

        rows = engine.derive_gap_value_for_bucket(cur, market_id, "GBP")
    row = next(r for r in rows if r["capability_concept_id"] == cap_id)
    assert row["reference_comp_unlocked"] is None
    assert row["comp_delta_vs_best_current_reachable"] is None
    assert row["evidence_quality"] == "insufficient"
    # Structural counts remain, per prompt §11: "retain structural
    # unlocked/improved counts; do not manufacture a currency delta."
    assert row["roles_unlocked"] == 1
    assert row["archetypes_unlocked"] == 1


def test_markets_and_currencies_isolated(client):
    with db.db_cursor() as cur:
        market_gbp = _market(cur, "market-gbp")
        market_eur = _market(cur, "market-eur")
        cap_id = _capability(cur, "Cross Market Gap")
        archetype_id = _archetype(cur, "Cross Market Archetype")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, cap_id, requirement_type="required")
        _persist_seed_comp(cur, archetype_id, market_gbp, reference_comp=100000.0, currency="GBP")

        rows_eur = engine.derive_gap_value_for_bucket(cur, market_eur, "EUR")
        rows_gbp = engine.derive_gap_value_for_bucket(cur, market_gbp, "GBP")

    row_eur = next(r for r in rows_eur if r["capability_concept_id"] == cap_id)
    row_gbp = next(r for r in rows_gbp if r["capability_concept_id"] == cap_id)
    assert row_eur["reference_comp_unlocked"] is None  # GBP market's comp data never leaks into the EUR bucket
    assert row_gbp["reference_comp_unlocked"] == 100000.0


def test_gap_value_rebuild_is_deterministic(client):
    with db.db_cursor() as cur:
        market_id = _market(cur)
        cap_id = _capability(cur, "Deterministic Gap")
        archetype_id = _archetype(cur, "Deterministic Archetype")
        role_id = _role(cur, archetype_concept_id=archetype_id)
        _claim_requirement(cur, role_id, cap_id, requirement_type="required")

        first = engine.rebuild_gap_value(cur, market_id, "GBP")
        cur.execute(
            "SELECT archetypes_unlocked, roles_unlocked, evidence_quality, rank FROM jobber.d_gap_value "
            "WHERE capability_concept_id = %s AND market_id = %s AND currency = 'GBP'",
            (cap_id, market_id),
        )
        first_row = dict(cur.fetchone())

        second = engine.rebuild_gap_value(cur, market_id, "GBP")
        cur.execute(
            "SELECT archetypes_unlocked, roles_unlocked, evidence_quality, rank FROM jobber.d_gap_value "
            "WHERE capability_concept_id = %s AND market_id = %s AND currency = 'GBP'",
            (cap_id, market_id),
        )
        second_row = dict(cur.fetchone())
    assert first["computed"] == second["computed"] == 1
    assert first_row == second_row


def _persist_seed_comp(cur, archetype_id, market_id, *, reference_comp, currency="GBP"):
    """Directly seeds a d_archetype_comp row — isolates gap-value tests from
    needing 5+ real postings just to get a reference figure to read."""
    cur.execute(
        """
        INSERT INTO jobber.d_archetype_comp
            (archetype_concept_id, market_id, period_start, period_end, currency, component, pay_period,
             n_observations, reference_comp, reference_source, engine_version)
        VALUES (%s, %s, %s, %s, %s, 'base', 'annual', 5, %s, 'posting', %s)
        """,
        (archetype_id, market_id, date(2000, 1, 1), date.today(), currency, reference_comp, engine.ENGINE_VERSION),
    )
