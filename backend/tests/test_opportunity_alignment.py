"""Phase 7: `You -> Opportunity -> Target` opportunity alignment (docs/38).

Real Postgres, same fixture style as test_pathways.py (raw SQL helpers, no
ORM/factory library). No AI anywhere in this module or in what it exercises.
"""

import uuid
from datetime import date

from app import career_directions, opportunity_alignment as alignment
from app.db import create_document, db_cursor, upsert_role_instance
from app.economics_freshness import record_rebuild
from app.models import CareerDirectionCompensationFloor, CareerDirectionConstraints, CareerDirectionCreate, CareerDirectionDimensionInput
from query_counter import count_queries


# --- Fixtures (mirrors backend/tests/test_pathways.py) ----------------------

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


def _role(cur, *, title, instance_type="observed_posting", archetype_concept_id=None, country="United Kingdom",
          location=None, remote_type=None, employment_type=None, seniority_level=None):
    document_id, _dup = create_document(
        cur, kind="job_posting" if instance_type == "observed_posting" else "narrative",
        content_text=f"{title} — source text for this role.", provenance_quality="original",
        title=title, source="user_paste",
    )
    columns = {"instance_type": instance_type, "title": title, "country": country,
               "document_id": document_id, "description": f"{title} description."}
    if location is not None:
        columns["location"] = location
    if remote_type is not None:
        columns["remote_type"] = remote_type
    if employment_type is not None:
        columns["employment_type"] = employment_type
    if seniority_level is not None:
        columns["seniority_level"] = seniority_level
    role_id = upsert_role_instance(cur, None, columns, skills=[])
    if archetype_concept_id:
        cur.execute("UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s",
                    (archetype_concept_id, role_id))
    return str(role_id)


def _requirement(cur, role_id, concept_id, *, requirement_type="required", review_status="accepted"):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
        "VALUES (%s, %s, %s, 'user_asserted', %s)",
        (role_id, concept_id, requirement_type, review_status),
    )


def _mapped_observation(cur, role_id, concept_id, name, *, requirement_type="required"):
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, canonical_concept_id, "
        "requirement_type, observation_basis) VALUES (%s, %s, %s, %s, 'app_capture')",
        (role_id, name, concept_id, requirement_type),
    )


def _unmapped_observation(cur, role_id, name):
    """An observation with no resolved concept — the shape that makes a
    target's mapping *incomplete* (`target_mapping_summary`'s `unresolved`
    count), independent of whether its requirement_claim-derived
    requirements are otherwise usable."""
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, canonical_concept_id, "
        "requirement_type, observation_basis) VALUES (%s, %s, NULL, 'required', 'app_capture')",
        (role_id, name),
    )


def _evidence_for(cur, concept_id, *, name="capability"):
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


def _stated_compensation(cur, role_id, *, amount_min=90000, amount_max=110000, currency="GBP",
                         component="base", pay_period="annual", employment_basis=None):
    cur.execute(
        """
        INSERT INTO jobber.compensation_observation
            (source_key, role_instance_id, component, pay_period, employment_basis, currency,
             amount_min, amount_max, basis, review_status, evidence_span, observed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'posting_stated', 'accepted', 'quoted from the advert', now())
        """,
        (f"test:{uuid.uuid4()}", role_id, component, pay_period, employment_basis, currency, amount_min, amount_max),
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


def _selected_direction(cur, *, target_id=None, constraints=None, dimensions=None, name="My Direction"):
    payload = CareerDirectionCreate(
        name=name, target_role_instance_id=target_id,
        constraints=constraints or CareerDirectionConstraints(),
        dimensions=dimensions or [],
    )
    saved = career_directions.create_direction(cur, payload)
    career_directions.select_direction(cur, saved["id"])
    return saved["id"]


def _base_scenario(cur, *, opportunity_kwargs=None, opportunity_archetype=None, target_archetype=None,
                   gap_required_on_opportunity=False, name_suffix=""):
    """Target requires two capabilities: `evidenced` (the user already has
    accepted evidence for) and `gap` (a genuine outstanding requirement).
    The opportunity requires `evidenced` (required) and `gap` — preferred by
    default, which is the exact shape backend/tests/test_pathways.py's own
    `_scenario` uses to reach `potential_step` (a candidate whose own
    *required* set is already fully evidenced is structurally easier than a
    target that still has a required gap). `name_suffix` lets one test build
    two independent scenarios without colliding on `jobber.concept`'s unique
    (type_code, canonical_name) constraint."""
    evidenced = _capability(cur, f"Reserving{name_suffix}")
    gap = _capability(cur, f"Capital management{name_suffix}")
    _evidence_for(cur, evidenced, name="Reserving")

    target_id = _role(cur, title="Head of Capital", instance_type="user_defined_target",
                      archetype_concept_id=target_archetype)
    for concept_id, name in ((evidenced, "Reserving"), (gap, "Capital management")):
        _requirement(cur, target_id, concept_id)
        _mapped_observation(cur, target_id, concept_id, name)

    opportunity_id = _role(cur, title="Capital Analyst", archetype_concept_id=opportunity_archetype,
                           **(opportunity_kwargs or {}))
    _requirement(cur, opportunity_id, evidenced)
    _requirement(cur, opportunity_id, gap, requirement_type="required" if gap_required_on_opportunity else "preferred")

    return {"target_id": target_id, "opportunity_id": opportunity_id, "evidenced": evidenced, "gap": gap}


# --- Subject validation (build §4/§28) --------------------------------------

def test_alignment_requires_observed_posting_not_target(client):
    with db_cursor() as cur:
        target_id = _role(cur, title="A Target", instance_type="user_defined_target")
    resp = client.get(f"/api/roles/{target_id}/career-alignment")
    assert resp.status_code == 400


def test_alignment_404_for_missing_role(client):
    resp = client.get(f"/api/roles/{uuid.uuid4()}/career-alignment")
    assert resp.status_code == 404


def test_target_id_override_rejects_an_observed_posting(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")
        other_posting = _role(cur, title="Another posting")
    resp = client.get(f"/api/roles/{opportunity_id}/career-alignment", params={"target_id": other_posting})
    assert resp.status_code == 400


def test_target_id_override_404_for_missing_target(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")
    resp = client.get(f"/api/roles/{opportunity_id}/career-alignment", params={"target_id": str(uuid.uuid4())})
    assert resp.status_code == 404


# --- Selected-direction states (build §3/§28) -------------------------------

def test_no_selected_direction_state(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["state"] == "no_selected_direction"
    assert body["direction"] is None
    assert body["target"] is None


def test_exploring_direction_is_not_treated_as_selected(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")
        career_directions.create_direction(cur, CareerDirectionCreate(name="Exploring only"))  # never selected
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["state"] == "no_selected_direction"


def test_archived_direction_is_not_treated_as_selected(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")
        saved = career_directions.create_direction(cur, CareerDirectionCreate(name="Archived one"))
        career_directions.select_direction(cur, saved["id"])
        career_directions.archive_direction(cur, saved["id"])
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["state"] == "no_selected_direction"


def test_direction_without_target_state(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity", location="London")
        _selected_direction(cur, constraints=CareerDirectionConstraints(locations=["London"]))
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["state"] == "direction_without_target"
    assert body["direction"] is not None
    assert body["target"] is None
    assert "opportunity_to_target" not in body
    locations = next(c for c in body["direction_constraints"] if c["constraint"] == "locations")
    assert locations["status"] == "matches"


def test_insufficient_target_evidence_when_target_has_no_requirements(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")
        target_id = _role(cur, title="Empty Target", instance_type="user_defined_target")
        _selected_direction(cur, target_id=target_id)
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["state"] == "insufficient_target_evidence"
    assert body["target"]["id"] == target_id
    assert "you_to_opportunity" in body  # independent facts still shown (build §3)
    assert "economics" in body


def test_target_available_full_analysis(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["state"] == "target_available"
    assert body["relationship"]["state"] == "potential_step"
    assert body["target"]["id"] == scenario["target_id"]
    assert set(body["opportunity_to_target"]) >= {
        "target_gaps_involved", "target_gaps_not_touched", "additional_opportunity_demands",
    }


# --- Explicit target_id override (Pathways overlay, build §17) -------------

def test_target_id_override_works_without_any_selected_direction(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
    body = client.get(
        f"/api/roles/{scenario['opportunity_id']}/career-alignment",
        params={"target_id": scenario["target_id"]},
    ).json()
    assert body["state"] == "target_available"
    assert body["direction"] is None
    assert body["direction_constraints"] == []
    assert body["relationship"]["state"] == "potential_step"


def test_target_id_override_agrees_with_direction_derived_target(client):
    """Reaching the same (opportunity, target) pair via the selected
    Direction, or via an explicit target_id, must never disagree — Role
    Detail and Pathways call the same underlying composition (build §17)."""
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _selected_direction(cur, target_id=scenario["target_id"])
    via_direction = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    via_override = client.get(
        f"/api/roles/{scenario['opportunity_id']}/career-alignment",
        params={"target_id": scenario["target_id"]},
    ).json()
    assert via_direction["relationship"] == via_override["relationship"]
    assert via_direction["opportunity_to_target"] == via_override["opportunity_to_target"]


# --- Bulk vs specific-candidate parity (build §5) ---------------------------

def test_bulk_and_specific_candidate_assessment_agree():
    from app.embeddings import ensure_profile_embedding, get_embedding
    from app.stepping_stones import assess_all_candidates, assess_specific_candidate
    from app.target_cache import revisions

    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        # A second, unrelated posting so assess_all_candidates has more than
        # one candidate to rank, and the parity check is not trivial.
        _role(cur, title="Unrelated posting")

        _, profile_vec = ensure_profile_embedding(cur)
        target_vec = get_embedding(cur, "role_instance", scenario["target_id"])
        evidence_revision, _ = revisions(cur, target_vec, profile_vec)

        bulk = assess_all_candidates(cur, scenario["target_id"], target_vec, profile_vec, evidence_revision)
        bulk_entry = next(r for r in bulk["ranked"] if r["id"] == scenario["opportunity_id"])

        specific = assess_specific_candidate(
            cur, scenario["target_id"], scenario["opportunity_id"], target_vec, profile_vec, evidence_revision,
        )
        step = specific["step"]

    for key in ("assessment", "explanation", "ranking_score", "evidenced_requirements", "requirements_total",
                "evidence_coverage", "target_gap_coverage", "missing_required", "unverified_required",
                "target_gaps_addressed", "similarity_to_target", "similarity_to_profile"):
        assert bulk_entry[key] == step[key], key


def test_specific_candidate_assessment_does_not_scan_the_corpus():
    """The whole reason assess_specific_candidate exists (build §5/§27): its
    query count must not grow with how many other observed postings exist."""
    from app.embeddings import ensure_profile_embedding, get_embedding
    from app.stepping_stones import assess_specific_candidate
    from app.target_cache import revisions

    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _, profile_vec = ensure_profile_embedding(cur)
        target_vec = get_embedding(cur, "role_instance", scenario["target_id"])
        evidence_revision, _ = revisions(cur, target_vec, profile_vec)
        with count_queries() as counted_small:
            assess_specific_candidate(cur, scenario["target_id"], scenario["opportunity_id"],
                                      target_vec, profile_vec, evidence_revision)

    with db_cursor() as cur:
        scenario2 = _base_scenario(cur, name_suffix=" 2")
        for i in range(15):
            _role(cur, title=f"Noise posting {i}")
        _, profile_vec = ensure_profile_embedding(cur)
        target_vec = get_embedding(cur, "role_instance", scenario2["target_id"])
        evidence_revision, _ = revisions(cur, target_vec, profile_vec)
        with count_queries() as counted_large:
            assess_specific_candidate(cur, scenario2["target_id"], scenario2["opportunity_id"],
                                      target_vec, profile_vec, evidence_revision)

    assert counted_large.count == counted_small.count


# --- Relationship states (build §8/§28) -------------------------------------

def test_relationship_potential_step(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["relationship"]["state"] == "potential_step"
    involved_names = {g["canonical_name"] for g in body["opportunity_to_target"]["target_gaps_involved"]}
    assert involved_names == {"Capital management"}


def test_relationship_no_identified_target_progress(client):
    with db_cursor() as cur:
        gap = _capability(cur, "Capital management")
        unrelated = _capability(cur, "Something else entirely")
        target_id = _role(cur, title="Head of Capital", instance_type="user_defined_target")
        _requirement(cur, target_id, gap)
        _mapped_observation(cur, target_id, gap, "Capital management")
        opportunity_id = _role(cur, title="Unrelated Opportunity")
        _requirement(cur, opportunity_id, unrelated)
        _selected_direction(cur, target_id=target_id)
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["relationship"]["state"] == "no_identified_target_progress"
    assert body["opportunity_to_target"]["target_gaps_involved"] == []
    assert len(body["opportunity_to_target"]["target_gaps_not_touched"]) == 1


def test_relationship_not_more_reachable_than_target(client):
    with db_cursor() as cur:
        evidenced = _capability(cur, "Reserving")
        gap = _capability(cur, "Capital management")
        _evidence_for(cur, evidenced, name="Reserving")
        target_id = _role(cur, title="Head of Capital", instance_type="user_defined_target")
        for concept_id, name in ((evidenced, "Reserving"), (gap, "Capital management")):
            _requirement(cur, target_id, concept_id)
            _mapped_observation(cur, target_id, concept_id, name)
        # Opportunity requires only the unevidenced gap, and nothing else —
        # zero evidence coverage, so it is not more reachable than the target.
        opportunity_id = _role(cur, title="Just The Gap")
        _requirement(cur, opportunity_id, gap)
        _selected_direction(cur, target_id=target_id)
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["relationship"]["state"] == "not_more_reachable_than_target"


def test_relationship_target_already_evidenced(client):
    with db_cursor() as cur:
        evidenced = _capability(cur, "Reserving")
        _evidence_for(cur, evidenced, name="Reserving")
        target_id = _role(cur, title="Head of Capital", instance_type="user_defined_target")
        _requirement(cur, target_id, evidenced)
        _mapped_observation(cur, target_id, evidenced, "Reserving")
        opportunity_id = _role(cur, title="Some Opportunity")
        _requirement(cur, opportunity_id, evidenced)
        _selected_direction(cur, target_id=target_id)
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["relationship"]["state"] == "target_already_evidenced"


def test_relationship_unclear_when_candidate_review_incomplete(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        other = _capability(cur, "Some other unreviewed thing")
        _requirement(cur, scenario["opportunity_id"], other, review_status="unreviewed")
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["relationship"]["state"] == "relationship_unclear"
    assert body["opportunity_to_target"]["candidate_review_complete"] is False


def test_relationship_unclear_when_target_review_incomplete(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        other = _capability(cur, "Some other unreviewed target thing")
        _requirement(cur, scenario["target_id"], other, review_status="unreviewed")
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["relationship"]["state"] == "relationship_unclear"
    assert body["opportunity_to_target"]["target_review_complete"] is False


def test_relationship_unclear_when_target_mapping_incomplete(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _unmapped_observation(cur, scenario["target_id"], "Some unmapped surface form")
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["relationship"]["state"] == "relationship_unclear"
    assert body["opportunity_to_target"]["target_mapping_complete"] is False


def test_same_reviewed_archetype_gives_same_destination_family(client):
    with db_cursor() as cur:
        shared_archetype = _archetype(cur, "Capital archetype")
        scenario = _base_scenario(cur, opportunity_archetype=shared_archetype, target_archetype=shared_archetype)
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["relationship"]["state"] == "same_destination_family"
    # Both facts remain visible (build §9): the gap-movement data is not erased.
    involved_names = {g["canonical_name"] for g in body["opportunity_to_target"]["target_gaps_involved"]}
    assert involved_names == {"Capital management"}
    assert body["archetype_relationship"]["same_as_target_archetype"] is True


def test_no_archetype_never_infers_same_destination_family(client):
    with db_cursor() as cur:
        target_archetype = _archetype(cur, "Capital archetype")
        scenario = _base_scenario(cur, target_archetype=target_archetype)  # opportunity has no archetype
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["relationship"]["state"] == "potential_step"
    assert body["archetype_relationship"]["opportunity_archetype"] is None
    assert body["archetype_relationship"]["note"] == "No reviewed archetype is assigned to this posting."


def test_semantic_similarity_alone_never_creates_a_relationship_state():
    """Direct unit check of the classifier's contract: it takes an
    `archetype_match` boolean and the stepping-stone verdict, never a
    similarity figure — so no similarity value, however extreme, can move
    the outcome (build §22)."""
    step = {
        "assessment": "no_target_progress", "explanation": "no shared gaps",
        "similarity_to_target": 0.999999, "similarity_to_profile": 0.999999,
    }
    result = alignment._classify_relationship(step, archetype_match=False)
    assert result["state"] == "no_identified_target_progress"


# --- Gap movement (build §10/§29) -------------------------------------------

def test_gap_movement_sections_are_correct(client):
    with db_cursor() as cur:
        evidenced = _capability(cur, "Reserving")
        touched_gap = _capability(cur, "Capital management")
        untouched_gap = _capability(cur, "Regulatory reporting")
        extra_demand = _capability(cur, "Cloud infrastructure")
        _evidence_for(cur, evidenced, name="Reserving")

        target_id = _role(cur, title="Head of Capital", instance_type="user_defined_target")
        for concept_id, name in ((evidenced, "Reserving"), (touched_gap, "Capital management"),
                                 (untouched_gap, "Regulatory reporting")):
            _requirement(cur, target_id, concept_id)
            _mapped_observation(cur, target_id, concept_id, name)

        opportunity_id = _role(cur, title="Capital Analyst")
        _requirement(cur, opportunity_id, evidenced)
        _requirement(cur, opportunity_id, touched_gap, requirement_type="preferred")
        _requirement(cur, opportunity_id, extra_demand, requirement_type="required")

        _selected_direction(cur, target_id=target_id)

    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    o2t = body["opportunity_to_target"]

    involved = {g["canonical_name"]: g for g in o2t["target_gaps_involved"]}
    assert set(involved) == {"Capital management"}
    assert involved["Capital management"]["target_requirement_type"] == "required"
    assert involved["Capital management"]["opportunity_requirement_type"] == "preferred"
    assert involved["Capital management"]["person_evidence_status"] == "not_found"

    not_touched = {g["canonical_name"] for g in o2t["target_gaps_not_touched"]}
    assert not_touched == {"Regulatory reporting"}

    additional = {g["canonical_name"] for g in o2t["additional_opportunity_demands"]}
    assert additional == {"Cloud infrastructure"}


def test_alignment_never_writes_to_profile360_review_or_direction(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _selected_direction(cur, target_id=scenario["target_id"])

    def _counts():
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
            claims = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM jobber.profile360_claim_mapping")
            mappings = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
            requirement_claims = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM jobber.career_direction")
            directions = cur.fetchone()["n"]
        return claims, mappings, requirement_claims, directions

    before = _counts()
    resp = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment")
    assert resp.status_code == 200
    after = _counts()
    assert before == after


# --- Direction constraints (build §11/§30) ----------------------------------

def test_direct_constraints_match_conflict_unknown_not_specified(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity", location="London", remote_type="hybrid",
                               employment_type="permanent")
        _selected_direction(cur, constraints=CareerDirectionConstraints(
            locations=["London"], remote_types=["remote"], employment_types=["permanent"],
        ))
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    by_key = {c["constraint"]: c for c in body["direction_constraints"]}
    assert by_key["locations"]["status"] == "matches"
    assert by_key["remote_types"]["status"] == "conflicts"
    assert by_key["employment_types"]["status"] == "matches"
    assert by_key["seniority_levels"]["status"] == "not_specified"


def test_direct_constraint_unknown_when_opportunity_field_missing(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")  # no remote_type set
        _selected_direction(cur, constraints=CareerDirectionConstraints(remote_types=["remote"]))
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    by_key = {c["constraint"]: c for c in body["direction_constraints"]}
    assert by_key["remote_types"]["status"] == "unknown"


def test_compensation_floor_matches_and_conflicts(client):
    with db_cursor() as cur:
        under = _role(cur, title="Underpaying role")
        _stated_compensation(cur, under, amount_min=60000, amount_max=60000, currency="GBP")
        over = _role(cur, title="Well paying role")
        _stated_compensation(cur, over, amount_min=150000, amount_max=150000, currency="GBP")
        _selected_direction(cur, name="Floor direction",
                            constraints=CareerDirectionConstraints(
                                compensation_floor=CareerDirectionCompensationFloor(amount=100000, currency="GBP"),
                            ))
    under_body = client.get(f"/api/roles/{under}/career-alignment").json()
    over_body = client.get(f"/api/roles/{over}/career-alignment").json()
    under_floor = next(c for c in under_body["direction_constraints"] if c["constraint"] == "compensation_floor")
    over_floor = next(c for c in over_body["direction_constraints"] if c["constraint"] == "compensation_floor")
    assert under_floor["status"] == "conflicts"
    assert over_floor["status"] == "matches"


def test_compensation_floor_incompatible_currency_is_unknown_not_converted(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="A US role")
        _stated_compensation(cur, opportunity_id, amount_min=200000, amount_max=200000, currency="USD")
        _selected_direction(cur, constraints=CareerDirectionConstraints(
            compensation_floor=CareerDirectionCompensationFloor(amount=100000, currency="GBP"),
        ))
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    floor = next(c for c in body["direction_constraints"] if c["constraint"] == "compensation_floor")
    assert floor["status"] == "unknown"
    assert "never converted" in floor["reason"]


def test_compensation_floor_daily_contract_vs_annual_floor_is_unknown(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="A contract role")
        _stated_compensation(cur, opportunity_id, amount_min=600, amount_max=600, currency="GBP",
                             component="day_rate", pay_period="daily", employment_basis="contract")
        _selected_direction(cur, constraints=CareerDirectionConstraints(
            compensation_floor=CareerDirectionCompensationFloor(amount=100000, currency="GBP", pay_period="annual"),
        ))
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    floor = next(c for c in body["direction_constraints"] if c["constraint"] == "compensation_floor")
    assert floor["status"] == "unknown"
    assert "No annualisation is assumed" in floor["reason"]


def test_qualitative_dimensions_are_never_structurally_assessed(client):
    with db_cursor() as cur:
        opportunity_id = _role(cur, title="An Opportunity")
        _selected_direction(cur, dimensions=[
            CareerDirectionDimensionInput(dimension_code="autonomy", desired_direction="toward", importance=2),
        ])
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert len(body["direction_dimensions"]) == 1
    assert body["direction_dimensions"][0]["assessment"] == "not_structurally_assessed"
    assert body["direction_dimensions"][0]["dimension_code"] == "autonomy"


# --- Economics / review / freshness (build §13/§23/§24/§31) ----------------

def test_advert_stated_compensation_in_economics_block(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _stated_compensation(cur, scenario["opportunity_id"], amount_min=100000, amount_max=120000)
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["economics"]["opportunity"]["basis"] == "advert_stated"


def test_archetype_benchmark_compensation_retains_its_basis(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur, "Capital archetype")
        scenario = _base_scenario(cur, opportunity_archetype=archetype_id)
        _archetype_comp(cur, archetype_id, market_id, reference=132000)
        record_rebuild(cur, "test-engine")
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["economics"]["opportunity"]["basis"] == "market_estimate"


def test_insufficient_compensation_stays_insufficient(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["economics"]["opportunity"]["basis"] == "insufficient_evidence"


def test_stale_economics_is_disclosed_never_silently_rebuilt(client):
    with db_cursor() as cur:
        market_id = _market(cur)
        archetype_id = _archetype(cur, "Capital archetype")
        scenario = _base_scenario(cur, opportunity_archetype=archetype_id)
        _archetype_comp(cur, archetype_id, market_id, reference=132000)
        # Deliberately no record_rebuild() call — derived economics are
        # "never_rebuilt" from this connection's point of view.
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["economics"]["opportunity"]["basis"] == "insufficient_evidence"
    assert "rebuild" in body["economics"]["opportunity"]["reason"].lower()


def test_get_alignment_never_calls_ai(client, monkeypatch):
    from app import ai

    def _boom(**kwargs):
        raise AssertionError("GET career-alignment must never call the AI provider")

    monkeypatch.setattr(ai, "run_json_task", _boom)
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _selected_direction(cur, target_id=scenario["target_id"])
    resp = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment")
    assert resp.status_code == 200


def test_legacy_requirements_are_labelled_not_treated_as_reviewed(client):
    with db_cursor() as cur:
        gap = _capability(cur, "Capital management")
        target_id = _role(cur, title="Head of Capital", instance_type="user_defined_target")
        _requirement(cur, target_id, gap)
        _mapped_observation(cur, target_id, gap, "Capital management")
        # The opportunity's only evidence for this concept is a legacy
        # role_skill_observation, never a reviewed requirement_claim.
        opportunity_id = _role(cur, title="Legacy Sourced Posting")
        _mapped_observation(cur, opportunity_id, gap, "Capital management", requirement_type="required")
        _selected_direction(cur, target_id=target_id)
    body = client.get(f"/api/roles/{opportunity_id}/career-alignment").json()
    assert body["you_to_opportunity"]["legacy_requirement_count"] >= 1
    involved = body["opportunity_to_target"]["target_gaps_involved"]
    assert any(g["opportunity_requirement_source"] == "role_skill_observation" for g in involved)
    assert body["opportunity_to_target"]["legacy_requirements_involved"] >= 1


def test_review_blockers_are_returned(client):
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        other = _capability(cur, "Some other unreviewed thing")
        _requirement(cur, scenario["opportunity_id"], other, review_status="unreviewed")
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert body["review"]["opportunity"]["complete"] is False
    assert body["review"]["opportunity"]["blockers"]


def test_fit_score_is_never_exposed_as_the_decision_signal(client):
    """build §6: `fit_score` must never appear as the Phase 7 alignment
    signal — the structural counts/statuses are used instead."""
    with db_cursor() as cur:
        scenario = _base_scenario(cur)
        _selected_direction(cur, target_id=scenario["target_id"])
    body = client.get(f"/api/roles/{scenario['opportunity_id']}/career-alignment").json()
    assert "fit_score" not in body["you_to_opportunity"]
