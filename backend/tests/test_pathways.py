"""Pathways composition (build §7), the gap-value personal overlay (§8) and
conservative transition cost (§9).

No AI anywhere in this module or in what it exercises — Pathways composes
already-derived structures, and one of the tests asserts exactly that.
"""

import uuid
from datetime import date

from app import pathways
from app import personal_earnings as earnings
from app.db import create_document, db_cursor, to_json_param, upsert_role_instance
from query_counter import count_queries


# --- Fixtures ---------------------------------------------------------------

def _market(cur, code="country-united-kingdom", label="United Kingdom"):
    cur.execute(
        "INSERT INTO jobber.market (code, label, country, geography) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label RETURNING id",
        (code, label, label, label),
    )
    return str(cur.fetchone()["id"])


def _archetype(cur, name):
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


def _role(cur, *, title, instance_type="observed_posting", archetype_concept_id=None, country="United Kingdom"):
    document_id, _dup = create_document(
        cur, kind="job_posting" if instance_type == "observed_posting" else "narrative",
        content_text=f"{title} — source text for this role.", provenance_quality="original",
        title=title, source="user_paste",
    )
    role_id = upsert_role_instance(
        cur, None,
        {"instance_type": instance_type, "title": title, "country": country,
         "document_id": document_id, "description": f"{title} description."},
        skills=[],
    )
    if archetype_concept_id:
        cur.execute(
            "UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s",
            (archetype_concept_id, role_id),
        )
    return str(role_id)


def _requirement(cur, role_id, concept_id, *, requirement_type="required", review_status="accepted"):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
        "VALUES (%s, %s, %s, 'user_asserted', %s)",
        (role_id, concept_id, requirement_type, review_status),
    )


def _mapped_observation(cur, role_id, concept_id, name):
    """A target's requirement mapping reads role_skill_observation, so a
    target whose mapping must come out 'complete' needs one per requirement."""
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, canonical_concept_id, "
        "requirement_type, observation_basis) VALUES (%s, %s, %s, 'required', 'app_capture')",
        (role_id, name, concept_id),
    )


def _evidence_for(cur, concept_id, *, name="capability"):
    """Real, accepted evidence of a capability — an accepted profile360
    claim mapping at the curated depth, which is what actually reaches
    `evidenced` in the capability engine. A bare
    `person_capability_assertion` deliberately only reaches
    `user_asserted`, which Pathways treats as an unverified gap, not as
    evidence."""
    cur.execute(
        "INSERT INTO profile360.episodes (start_date, end_date, autonomy, status) "
        "VALUES (%s, %s, 'directed_others', 'active') RETURNING id",
        (date(2020, 1, 1), date(2023, 1, 1)),
    )
    episode_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO profile360.claims (claim_text, episode_id, depth) VALUES (%s, %s, 'owned') RETURNING id",
        (f"Led work demonstrating {name}.", episode_id),
    )
    claim_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.profile360_claim_mapping "
        "(profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
        "VALUES (%s, %s, 'ai_suggested', 'accepted')",
        (claim_id, concept_id),
    )


def _archetype_comp(cur, archetype_id, market_id, *, reference=132000, currency="GBP",
                    p25=120000, p75=145000, n_observations=12):
    cur.execute(
        """
        INSERT INTO jobber.d_archetype_comp
            (archetype_concept_id, market_id, period_start, period_end, currency, component, pay_period,
             n_observations, n_posting_stated, n_posting_estimated, n_survey_sources,
             posting_p25, posting_p50, posting_p75, survey_benchmarks,
             reference_comp, reference_source, reference_basis_detail, trace, engine_version)
        VALUES (%s, %s, '2000-01-01', '2026-09-16', %s, 'base', 'annual', %s, %s, 0, 0,
                %s, %s, %s, '[]'::jsonb, %s, 'posting', NULL, '{}'::jsonb, 'test-engine')
        """,
        (archetype_id, market_id, currency, n_observations, n_observations, p25, reference, p75, reference),
    )


def _gap_value(cur, capability_id, market_id, *, currency="GBP", archetypes_unlocked=2,
               reference_comp_unlocked=145000, delta=40000, quality="moderate", rank=1):
    cur.execute(
        """
        INSERT INTO jobber.d_gap_value
            (capability_concept_id, market_id, period_start, period_end, currency,
             archetypes_unlocked, archetypes_improved, roles_unlocked, roles_improved,
             reference_comp_unlocked, comp_delta_vs_best_current_reachable, n_comp_observations,
             evidence_quality, rank, trace, engine_version)
        VALUES (%s, %s, '2000-01-01', '2026-09-16', %s, %s, 1, 3, 2, %s, %s, 14, %s, %s, %s, 'test-engine')
        """,
        (capability_id, market_id, currency, archetypes_unlocked, reference_comp_unlocked, delta,
         quality, rank, to_json_param({})),
    )


def _personal_observation(cur, *, component="annual_base", amount=105000, currency="GBP",
                          unit="annual", employment_basis="paye"):
    cur.execute(
        """
        INSERT INTO profile360.compensation_observation
            (source_key, source_kind, employment_basis, component, period_start, amount, currency, unit, review_status)
        VALUES (%s, 'payslip', %s, %s, %s, %s, %s, %s, 'accepted')
        """,
        (f"test:{uuid.uuid4()}", employment_basis, component, date(2025, 7, 1), amount, currency, unit),
    )


def _fully_reviewed_target(cur, *, name="Head of Capital", archetype_concept_id=None):
    """A target whose requirement review and vocabulary mapping are both
    complete — the precondition for Pathways producing anything other than a
    gated state."""
    target_id = _role(cur, title=name, instance_type="user_defined_target",
                      archetype_concept_id=archetype_concept_id)
    return target_id


# --- Scenario ---------------------------------------------------------------

def _scenario(cur, *, with_intermediate=True, with_compensation=True, with_personal=True):
    """The canonical shape these tests reason about:

    - a target requiring two capabilities, one of which the user already has
      evidence for and one of which is a genuine blocking gap;
    - an intermediate archetype whose supporting postings involve that gap
      and are more reachable than the target itself;
    - compensation for both, and the user's own baseline.
    """
    market_id = _market(cur)
    evidenced = _capability(cur, "Reserving")
    gap = _capability(cur, "Capital management")
    _evidence_for(cur, evidenced, name="Reserving")

    target_archetype = _archetype(cur, "Head of Capital archetype")
    target_id = _fully_reviewed_target(cur, archetype_concept_id=target_archetype)
    for concept_id, name in ((evidenced, "Reserving"), (gap, "Capital management")):
        _requirement(cur, target_id, concept_id)
        _mapped_observation(cur, target_id, concept_id, name)

    intermediate_id = None
    supporting = []
    if with_intermediate:
        intermediate_id = _archetype(cur, "Capital Analyst archetype")
        for i in range(2):
            posting = _role(cur, title=f"Capital Analyst {i}", archetype_concept_id=intermediate_id)
            _requirement(cur, posting, evidenced)
            _requirement(cur, posting, gap, requirement_type="preferred")
            supporting.append(posting)

    if with_compensation:
        _archetype_comp(cur, target_archetype, market_id, reference=132000)
        if intermediate_id:
            _archetype_comp(cur, intermediate_id, market_id, reference=110000, p25=100000, p75=120000)
        _gap_value(cur, gap, market_id)

    if with_personal:
        _personal_observation(cur)

    return {
        "market_id": market_id, "target_id": target_id, "target_archetype": target_archetype,
        "intermediate_id": intermediate_id, "supporting": supporting,
        "evidenced": evidenced, "gap": gap,
    }


# --- Direct route -----------------------------------------------------------

def test_direct_route_reports_structural_fit_and_the_blocking_gap():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    direct = result["direct_route"]
    assert direct["kind"] == "direct"
    assert direct["state"] == "blocking_gaps"
    assert direct["fit"]["blocking_required_gaps"] == ["Capital management"]
    assert direct["fit"]["evidenced_requirements"] == 1
    assert direct["fit"]["requirements_total"] == 2
    assert direct["fit"]["review_complete"] is True
    assert direct["fit"]["mapping_complete"] is True


def test_direct_route_reports_structural_fit_when_every_requirement_is_evidenced():
    with db_cursor() as cur:
        scenario = _scenario(cur, with_intermediate=False)
        _evidence_for(cur, scenario["gap"], name="Capital management")
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["direct_route"]["state"] == "structurally_evidenced"
    assert "not a prediction about hiring" in result["direct_route"]["state_reason"]


def test_incomplete_requirement_review_gates_the_direct_route():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        _requirement(cur, scenario["target_id"], _capability(cur, "Unreviewed thing"),
                     review_status="unreviewed")
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["direct_route"]["state"] == "review_incomplete"
    assert result["gates"]["target_requirements_reviewed"] is False
    assert "target_requirements_reviewed" in result["incomplete"]


def test_incomplete_target_mapping_gates_the_direct_route():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        # An unmapped observation on the target makes the vocabulary mapping
        # incomplete without touching requirement review.
        cur.execute(
            "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, observation_basis) "
            "VALUES (%s, 'Something unmapped', 'app_capture')",
            (scenario["target_id"],),
        )
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["direct_route"]["state"] == "mapping_incomplete"
    assert result["gates"]["target_mapping_complete"] is False


def test_a_target_with_no_requirements_is_insufficient_evidence_not_a_perfect_fit():
    with db_cursor() as cur:
        target_id = _fully_reviewed_target(cur, name="Bare target")
        result = pathways.pathways_for_target(cur, target_id)

    assert result["direct_route"]["state"] == "insufficient_evidence"
    assert result["gates"]["target_has_requirements"] is False


# --- Intermediate archetypes ------------------------------------------------

def test_supporting_postings_are_grouped_under_their_reviewed_archetype():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert len(result["intermediate_archetypes"]) == 1
    node = result["intermediate_archetypes"][0]
    assert node["archetype_concept_id"] == scenario["intermediate_id"]
    assert node["archetype_name"] == "Capital Analyst archetype"
    assert node["supporting_posting_count"] == 2
    assert sorted(node["supporting_posting_ids"]) == sorted(scenario["supporting"])
    # The supporting postings are preserved underneath, inspectable.
    assert {p["title"] for p in node["supporting_postings"]} == {"Capital Analyst 0", "Capital Analyst 1"}
    assert node["target_gaps_addressed"] == ["Capital management"]
    assert node["state"] == "useful_intermediate"


def test_a_posting_without_a_reviewed_archetype_cannot_become_an_intermediate_node():
    with db_cursor() as cur:
        scenario = _scenario(cur, with_intermediate=False)
        orphan = _role(cur, title="Unclassified capital role")
        _requirement(cur, orphan, scenario["evidenced"])
        _requirement(cur, orphan, scenario["gap"], requirement_type="preferred")
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["intermediate_archetypes"] == []
    # It is reported separately rather than silently dropped, so "assign an
    # archetype to unlock this" is visible.
    assert [p["id"] for p in result["unclassified_supporting_postings"]] == [orphan]
    assert result["unclassified_supporting_postings"][0]["target_gaps_addressed"] == ["Capital management"]


def test_an_intermediate_without_compensation_is_still_a_route_but_says_so():
    with db_cursor() as cur:
        scenario = _scenario(cur, with_compensation=False)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    node = result["intermediate_archetypes"][0]
    assert node["state"] == "route_without_compensation"
    assert node["compensation"]["basis"] == "insufficient_evidence"
    assert node["compensation"]["amount_reference"] is None
    assert node["evidence_quality"] == "insufficient"


def test_missing_compensation_never_fabricates_a_value():
    with db_cursor() as cur:
        scenario = _scenario(cur, with_compensation=False)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["direct_route"]["compensation"]["basis"] == "insufficient_evidence"
    assert result["direct_route"]["compensation"]["amount_reference"] is None
    assert result["direct_route"]["personal_comparison"]["comparable"] is False
    assert result["gates"]["target_compensation_available"] is False
    for node in result["intermediate_archetypes"]:
        assert node["compensation"]["amount_reference"] is None


# --- Compensation and the personal overlay ----------------------------------

def test_compensation_and_personal_comparison_are_included_where_valid():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    direct = result["direct_route"]
    assert direct["compensation"]["basis"] == "market_estimate"
    assert direct["compensation"]["amount_reference"] == 132000
    comparison = direct["personal_comparison"]
    assert comparison["comparable"] is True
    assert comparison["baseline"]["amount"] == 105000
    assert comparison["difference_reference"] == 27000
    assert comparison["difference_min"] == 15000
    assert comparison["difference_max"] == 40000


def test_no_personal_evidence_means_no_comparison_not_a_zero_baseline():
    with db_cursor() as cur:
        scenario = _scenario(cur, with_personal=False)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["personal_earnings"]["status"] == "unavailable"
    assert result["direct_route"]["personal_comparison"]["comparable"] is False
    assert result["gates"]["personal_earnings_available"] is False


def test_market_context_is_chosen_from_evidence_and_can_be_overridden():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        auto = pathways.pathways_for_target(cur, scenario["target_id"])
        explicit = pathways.pathways_for_target(
            cur, scenario["target_id"], market_id=scenario["market_id"], currency="GBP"
        )

    assert auto["market_context"]["selected_by"] == "target_archetype_benchmark"
    assert auto["market_context"]["currency"] == "GBP"
    assert explicit["market_context"]["selected_by"] == "explicit"


def test_with_no_compensation_evidence_at_all_the_market_context_is_honestly_empty():
    with db_cursor() as cur:
        scenario = _scenario(cur, with_compensation=False)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["market_context"]["selected_by"] == "none_available"
    assert result["market_context"]["market_id"] is None
    assert result["gates"]["compensation_context_available"] is False


# --- Gap value overlay (build §8) -------------------------------------------

def test_gap_value_separates_target_value_from_market_option_value():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert len(result["gap_value"]) == 1
    gap = result["gap_value"][0]
    assert gap["canonical_name"] == "Capital management"

    assert gap["target_relevance"]["required_by_target"] is True
    assert gap["target_relevance"]["addresses_target_gaps"] == 1
    assert [a["archetype_name"] for a in gap["target_relevance"]["intermediate_archetypes_involving_it"]] == [
        "Capital Analyst archetype"
    ]

    assert gap["market_option_value"]["available"] is True
    assert gap["market_option_value"]["archetypes_unlocked"] == 2
    assert gap["market_option_value"]["highest_qualifying_reference_compensation"] == 145000

    assert gap["your_context"]["comparable"] is True
    assert gap["your_context"]["difference_reference"] == 40000
    assert gap["evidence_quality"] == "moderate"


def test_the_personal_overlay_is_never_written_back_into_d_gap_value():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        cur.execute(
            "SELECT reference_comp_unlocked, comp_delta_vs_best_current_reachable FROM jobber.d_gap_value "
            "WHERE capability_concept_id = %s",
            (scenario["gap"],),
        )
        before = dict(cur.fetchone())
        pathways.pathways_for_target(cur, scenario["target_id"])
        cur.execute(
            "SELECT reference_comp_unlocked, comp_delta_vs_best_current_reachable FROM jobber.d_gap_value "
            "WHERE capability_concept_id = %s",
            (scenario["gap"],),
        )
        after = dict(cur.fetchone())
        # No personal column was added to the market-level table either.
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'd_gap_value'"
        )
        columns = {r["column_name"] for r in cur.fetchall()}

    assert before == after
    assert not any("personal" in c or "your" in c for c in columns)


def test_a_gap_with_no_market_gap_value_row_says_so_rather_than_guessing():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        cur.execute("DELETE FROM jobber.d_gap_value")
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    gap = result["gap_value"][0]
    assert gap["market_option_value"]["available"] is False
    assert "Rebuild economics" in gap["market_option_value"]["reason"]
    assert gap["your_context"]["comparable"] is False
    assert gap["evidence_quality"] == "insufficient"


def test_an_evidenced_requirement_is_not_listed_as_a_gap():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert "Reserving" not in [g["canonical_name"] for g in result["gap_value"]]


# --- Transition cost (build §9) ---------------------------------------------

def test_transition_reports_only_what_the_system_knows_and_names_what_it_does_not():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    transition = result["direct_route"]["transition"]
    assert transition["blocking_required_gaps"] == 1
    assert transition["blocking_required_gap_names"] == ["Capital management"]
    assert transition["evidence_coverage"] == 0.5
    assert transition["development_actions"]["open"] == 0
    joined = " ".join(transition["not_estimated"])
    assert "No learning duration is estimated" in joined
    assert "No hiring or success probability" in joined
    assert "No universal learning-difficulty rating" in joined


def test_user_entered_development_action_planning_fields_appear_in_transition(client):
    with db_cursor() as cur:
        scenario = _scenario(cur)

    created = client.post(
        f"/api/comparison/role/{scenario['target_id']}/actions",
        json={"concept_id": scenario["gap"], "title": "Take the capital course", "note": "",
              "due_date": None, "planned_start_date": "2026-11-01", "estimated_effort_hours": 40},
    )
    assert created.status_code == 200
    assert created.json()["planned_start_date"] == "2026-11-01"

    with db_cursor() as cur:
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    actions = result["direct_route"]["transition"]["development_actions"]
    assert actions["open"] == 1
    assert actions["earliest_planned_start"] == "2026-11-01"
    assert actions["estimated_effort_hours"] == 40


def test_a_development_action_never_becomes_capability_evidence(client):
    """Recording and completing a plan changes nothing about what the user
    can evidence."""
    with db_cursor() as cur:
        scenario = _scenario(cur)
        before = pathways.pathways_for_target(cur, scenario["target_id"])

    action = client.post(
        f"/api/comparison/role/{scenario['target_id']}/actions",
        json={"concept_id": scenario["gap"], "title": "Take the capital course", "note": "",
              "due_date": None, "estimated_effort_hours": 40},
    ).json()
    client.patch(
        f"/api/comparison/role/{scenario['target_id']}/actions/{action['id']}", json={"status": "done"}
    )

    with db_cursor() as cur:
        after = pathways.pathways_for_target(cur, scenario["target_id"])

    assert before["direct_route"]["fit"]["blocking_required_gaps"] == ["Capital management"]
    assert after["direct_route"]["fit"]["blocking_required_gaps"] == ["Capital management"]
    assert after["direct_route"]["state"] == "blocking_gaps"


def test_a_status_only_patch_still_works_unchanged(client):
    with db_cursor() as cur:
        scenario = _scenario(cur)
    action = client.post(
        f"/api/comparison/role/{scenario['target_id']}/actions",
        json={"concept_id": scenario["gap"], "title": "Plan", "note": "", "due_date": None},
    ).json()
    patched = client.patch(
        f"/api/comparison/role/{scenario['target_id']}/actions/{action['id']}", json={"status": "done"}
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "done"


# --- Route depth, method, caching, performance ------------------------------

def test_only_direct_and_one_intermediate_hop_are_produced():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert set(result.keys()) >= {"direct_route", "intermediate_archetypes"}
    assert result["direct_route"]["kind"] == "direct"
    for node in result["intermediate_archetypes"]:
        assert node["kind"] == "intermediate_archetype"
        # No chaining: a node carries postings, never further route nodes.
        assert "intermediate_archetypes" not in node
        assert "next" not in node
    assert "one intermediate archetype" in result["method"]["route_depth"]
    assert "Multi-step chains are not searched" in result["method"]["route_depth_limitation"]


def test_the_warm_path_is_served_from_cache_and_matches_the_cold_result():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        cold = pathways.pathways_for_target(cur, scenario["target_id"])
        warm = pathways.pathways_for_target(cur, scenario["target_id"])

    assert cold["metrics"]["cache_hit"] is False
    assert warm["metrics"]["cache_hit"] is True
    cold.pop("metrics"), warm.pop("metrics")
    assert cold == warm


def test_the_warm_path_is_substantially_cheaper_in_queries():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        pathways.pathways_for_target(cur, scenario["target_id"])  # prime

    with count_queries() as warm:
        with db_cursor() as cur:
            result = pathways.pathways_for_target(cur, scenario["target_id"])
    assert result["metrics"]["cache_hit"] is True

    with db_cursor() as cur:
        cur.execute("DELETE FROM jobber.d_pathways")
    with count_queries() as cold:
        with db_cursor() as cur:
            pathways.pathways_for_target(cur, scenario["target_id"])

    assert warm.count < cold.count


def test_accepting_compensation_evidence_invalidates_the_pathways_cache():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        pathways.pathways_for_target(cur, scenario["target_id"])
        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, archetype_concept_id, market_id, component, pay_period, currency,
                 amount_min, amount_max, basis, review_status)
            VALUES (%s, %s, %s, 'base', 'annual', 'GBP', 150000, 170000, 'curator_asserted', 'accepted')
            """,
            (f"test:{uuid.uuid4()}", scenario["target_archetype"], scenario["market_id"]),
        )
        after = pathways.pathways_for_target(cur, scenario["target_id"])

    assert after["metrics"]["cache_hit"] is False


def test_changing_personal_compensation_invalidates_the_pathways_cache():
    """profile360 is externally owned, so no trigger can cover it — the
    fingerprint in the cache key is what notices."""
    with db_cursor() as cur:
        scenario = _scenario(cur, with_personal=False)
        pathways.pathways_for_target(cur, scenario["target_id"])
        _personal_observation(cur)
        after = pathways.pathways_for_target(cur, scenario["target_id"])

    assert after["metrics"]["cache_hit"] is False
    assert after["personal_earnings"]["status"] == "current"


def test_changing_the_planning_assumption_invalidates_the_pathways_cache():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        pathways.pathways_for_target(cur, scenario["target_id"])
        earnings.save_planning_assumption(cur, contract_billable_days_per_year=215, note=None)
        after = pathways.pathways_for_target(cur, scenario["target_id"])

    assert after["metrics"]["cache_hit"] is False


def test_a_different_market_context_gets_its_own_cache_entry():
    with db_cursor() as cur:
        scenario = _scenario(cur)
        other_market = _market(cur, code="country-ireland", label="Ireland")
        pathways.pathways_for_target(cur, scenario["target_id"],
                                     market_id=scenario["market_id"], currency="GBP")
        other = pathways.pathways_for_target(cur, scenario["target_id"],
                                             market_id=other_market, currency="EUR")
        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_pathways WHERE target_role_id = %s",
                    (scenario["target_id"],))
        entries = cur.fetchone()["n"]

    assert other["metrics"]["cache_hit"] is False
    assert entries == 2


def test_cold_pathways_does_not_scale_query_count_with_candidate_count():
    """The N+1 guard: ten more candidate postings must not mean ten more
    rounds of per-role loading."""
    with db_cursor() as cur:
        scenario = _scenario(cur)
        cur.execute("DELETE FROM jobber.d_pathways")
        cur.execute("DELETE FROM jobber.d_target_evidence")

    with count_queries() as small:
        with db_cursor() as cur:
            pathways.pathways_for_target(cur, scenario["target_id"])

    with db_cursor() as cur:
        for i in range(10):
            posting = _role(cur, title=f"Extra capital role {i}", archetype_concept_id=scenario["intermediate_id"])
            _requirement(cur, posting, scenario["evidenced"])
            _requirement(cur, posting, scenario["gap"], requirement_type="preferred")
        cur.execute("DELETE FROM jobber.d_pathways")

    with count_queries() as large:
        with db_cursor() as cur:
            result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["candidates_assessed"] >= 12
    # A strict equality would be brittle against the evidence-status cache
    # warming differently between runs; the point is that cost is driven by
    # distinct concepts, not by candidate count.
    assert large.count <= small.count + 2, f"query count grew with candidates: {small.count} -> {large.count}"


def test_pathways_never_calls_the_ai_provider(monkeypatch):
    from app import ai

    def explode(*args, **kwargs):
        raise AssertionError("Pathways must never call the AI provider")

    monkeypatch.setattr(ai, "run_json_task", explode)
    monkeypatch.setattr(ai, "_client", explode)

    with db_cursor() as cur:
        scenario = _scenario(cur)
        result = pathways.pathways_for_target(cur, scenario["target_id"])

    assert result["direct_route"]["state"] == "blocking_gaps"


def test_metrics_are_reported_in_the_same_vocabulary_as_target_path(record_property):
    with db_cursor() as cur:
        scenario = _scenario(cur)
        cold = pathways.pathways_for_target(cur, scenario["target_id"])
        warm = pathways.pathways_for_target(cur, scenario["target_id"])

    assert set(cold["metrics"]) == {
        "cache_hit", "candidates", "archetypes_assessed", "distinct_concepts",
        "concepts_evaluated", "elapsed_ms",
    }
    record_property(
        "pathways_benchmark",
        f"cold_ms={cold['metrics']['elapsed_ms']} warm_ms={warm['metrics']['elapsed_ms']} "
        f"candidates={cold['metrics']['candidates']}",
    )


# --- API surface ------------------------------------------------------------

def test_pathways_endpoint_returns_the_composition(client):
    with db_cursor() as cur:
        scenario = _scenario(cur)

    response = client.get(f"/api/pathways/{scenario['target_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["target"]["title"] == "Head of Capital"
    assert body["direct_route"]["state"] == "blocking_gaps"
    assert len(body["intermediate_archetypes"]) == 1
    assert body["gap_value"][0]["canonical_name"] == "Capital management"


def test_pathways_endpoint_404s_for_an_unknown_target(client):
    assert client.get("/api/pathways/00000000-0000-0000-0000-000000000000").status_code == 404


def test_market_contexts_endpoint_lists_only_evidenced_pairs(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur, "Some archetype")
        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, archetype_concept_id, market_id, component, pay_period, currency,
                 amount_min, amount_max, basis, review_status)
            VALUES (%s, %s, %s, 'base', 'annual', 'GBP', 100000, 120000, 'curator_asserted', 'accepted')
            """,
            (f"test:{uuid.uuid4()}", archetype_id, market_id),
        )

    contexts = client.get("/api/pathways/market-contexts").json()
    assert contexts == [
        {"market_id": market_id, "market_label": "United Kingdom", "market_code": "country-united-kingdom",
         "currency": "GBP", "accepted_observations": 1}
    ]


def test_stepping_stones_still_work_unchanged_alongside_pathways(client):
    """The existing target-path analysis is refactored, not replaced — its
    wire shape must be unchanged."""
    with db_cursor() as cur:
        scenario = _scenario(cur)

    body = client.get(f"/api/roles/{scenario['target_id']}").json()
    assert "path" in body
    assert set(body["path"]) >= {
        "profile_to_target_similarity", "stepping_stones", "candidates_assessed",
        "target_mapping", "method", "metrics",
    }
    assert len(body["path"]["stepping_stones"]) <= 5
