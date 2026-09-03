"""Phase 3 deterministic capability-coverage / role-fit engine (brief §36).
Runs `app.capability_engine` directly against the real Postgres test
database — the engine never calls AI, so nothing here is mocked."""

from datetime import date

import pytest

from app import capability_engine as engine
from app import db, embeddings


def _concept(cur, name, type_code="tool", status="active"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, name, status),
    )
    return str(cur.fetchone()["id"])


def _capability(cur, name, *, min_depth="owned", min_autonomy=None, requires_all_core=True, min_core_required=None):
    cap_id = _concept(cur, name, type_code="capability")
    cur.execute(
        "INSERT INTO jobber.capability_detail "
        "(concept_id, demonstration_standard, min_depth, min_autonomy, requires_all_core, min_core_required) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (cap_id, f"Demonstration standard for {name}", min_depth, min_autonomy, requires_all_core, min_core_required),
    )
    return cap_id


def _component_edge(cur, atomic_id, capability_id, necessity="core"):
    cur.execute(
        "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, necessity, origin, status) "
        "VALUES (%s, %s, 'component_of', %s, 'curator', 'accepted')",
        (atomic_id, capability_id, necessity),
    )


def _episode(cur, *, start_date=None, end_date=None, autonomy=None, status="active"):
    cur.execute(
        "INSERT INTO profile360.episodes (start_date, end_date, autonomy, status) VALUES (%s, %s, %s, %s) RETURNING id",
        (start_date, end_date, autonomy, status),
    )
    return str(cur.fetchone()["id"])


def _claim(cur, text, *, episode_id=None, depth=None):
    cur.execute(
        "INSERT INTO profile360.claims (claim_text, episode_id, depth) VALUES (%s, %s, %s) RETURNING id",
        (text, episode_id, depth),
    )
    return str(cur.fetchone()["id"])


def _claim_mapping(cur, claim_id, concept_id, *, review_status="accepted"):
    cur.execute(
        "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
        "VALUES (%s, %s, 'ai_suggested', %s)",
        (claim_id, concept_id, review_status),
    )


def _p360_capability(cur, name):
    cur.execute("INSERT INTO profile360.capabilities (name) VALUES (%s) RETURNING id", (name,))
    return str(cur.fetchone()["id"])


def _capability_mapping(cur, capability_row_id, capability_concept_id, *, review_status="accepted"):
    cur.execute(
        "INSERT INTO jobber.profile360_capability_mapping (profile360_capability_id, jobber_capability_concept_id, mapping_basis, review_status) "
        "VALUES (%s, %s, 'ai_suggested', %s)",
        (capability_row_id, capability_concept_id, review_status),
    )


def _assert_capability(cur, concept_id, note=None):
    cur.execute(
        "INSERT INTO jobber.person_capability_assertion (jobber_concept_id, asserted, note) VALUES (%s, TRUE, %s)",
        (concept_id, note),
    )


# --- ordinal comparison (brief §7) ------------------------------------------

def test_depth_meets_exact_threshold():
    assert engine.depth_meets("owned", "owned") is True


def test_depth_meets_below_threshold():
    assert engine.depth_meets("applied", "owned") is False


def test_depth_meets_above_threshold():
    assert engine.depth_meets("set_standard", "owned") is True


def test_autonomy_meets_exact_threshold():
    assert engine.autonomy_meets("independent", "independent") is True


def test_autonomy_meets_below_threshold():
    assert engine.autonomy_meets("assisted", "independent") is False


def test_no_minimum_is_always_satisfied():
    assert engine.depth_meets(None, None) is True
    assert engine.autonomy_meets(None, None) is True


def test_unknown_value_never_satisfies_even_the_lowest_minimum():
    assert engine.depth_meets(None, "exposed") is False


def test_normalize_depth_is_case_insensitive_and_rejects_unknown_vocabulary():
    assert engine.normalize_depth("Owned") == "owned"
    assert engine.normalize_depth("expert-level") is None
    assert engine.normalize_depth(None) is None


# --- temporal derivation (brief §13/§36) ------------------------------------

def test_union_years_active_single_episode():
    episodes = [{"start_date": date(2019, 1, 1), "end_date": date(2020, 1, 1)}]
    assert engine.union_years_active(episodes) == pytest.approx(1.0, abs=0.05)


def test_union_years_active_overlapping_episodes_not_summed():
    """brief §13's own worked example: two overlapping 2-year spans = 3 years, not 4."""
    episodes = [
        {"start_date": date(2019, 1, 1), "end_date": date(2021, 1, 1)},
        {"start_date": date(2020, 1, 1), "end_date": date(2022, 1, 1)},
    ]
    assert engine.union_years_active(episodes) == pytest.approx(3.0, abs=0.05)


def test_union_years_active_adjacent_episodes_merge_with_no_gap():
    episodes = [
        {"start_date": date(2019, 1, 1), "end_date": date(2020, 1, 1)},
        {"start_date": date(2020, 1, 1), "end_date": date(2021, 1, 1)},
    ]
    assert engine.union_years_active(episodes) == pytest.approx(2.0, abs=0.05)


def test_union_years_active_open_episode_uses_today_as_end():
    start = date.today().replace(year=date.today().year - 2)
    assert engine.union_years_active([{"start_date": start, "end_date": None}]) == pytest.approx(2.0, abs=0.05)


def test_union_years_active_excludes_episode_with_no_start_date():
    assert engine.union_years_active([{"start_date": None, "end_date": date(2020, 1, 1)}]) is None


def test_union_years_active_no_qualifying_episodes_returns_none():
    assert engine.union_years_active([]) is None


def test_last_demonstrated_picks_most_recent_end():
    episodes = [
        {"start_date": date(2019, 1, 1), "end_date": date(2020, 6, 1)},
        {"start_date": date(2021, 1, 1), "end_date": date(2022, 3, 15)},
    ]
    assert engine.last_demonstrated(episodes) == date(2022, 3, 15)


def test_last_demonstrated_open_episode_is_today():
    assert engine.last_demonstrated([{"start_date": date(2020, 1, 1), "end_date": None}]) == date.today()


def test_last_demonstrated_excludes_episode_with_no_dates_at_all():
    assert engine.last_demonstrated([{"start_date": None, "end_date": None}]) is None


# --- capability composition (brief §36) -------------------------------------

def test_all_core_components_found_in_same_episode_is_meaningful_partial(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Own a production model", requires_all_core=True)
        python_id = _concept(cur, "Python")
        reserving_id = _concept(cur, "Reserving", type_code="function")
        _component_edge(cur, python_id, cap_id, "core")
        _component_edge(cur, reserving_id, cap_id, "core")

        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "Used Python", episode_id=episode_id, depth="owned"), python_id)
        _claim_mapping(cur, _claim(cur, "Did reserving", episode_id=episode_id, depth="owned"), reserving_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"
    assert coverage["core_components_met"] == 2
    assert coverage["core_components_total"] == 2
    assert coverage["trace"]["compositional"]["core_complete"] is True


def test_components_split_across_episodes_never_credited_together(client):
    """The central Phase 3 invariant (brief §10): using Python in one job and
    doing reserving separately, years later, must not be credited as if both
    happened together."""
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Own a production model", requires_all_core=True)
        python_id = _concept(cur, "Python")
        reserving_id = _concept(cur, "Reserving", type_code="function")
        _component_edge(cur, python_id, cap_id, "core")
        _component_edge(cur, reserving_id, cap_id, "core")

        episode_a = _episode(cur, start_date=date(2015, 1, 1), end_date=date(2016, 1, 1))
        episode_b = _episode(cur, start_date=date(2021, 1, 1), end_date=date(2022, 1, 1))
        _claim_mapping(cur, _claim(cur, "Used Python", episode_id=episode_a, depth="owned"), python_id)
        _claim_mapping(cur, _claim(cur, "Did reserving", episode_id=episode_b, depth="owned"), reserving_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"
    assert coverage["core_components_met"] == 1  # best single episode has only one of the two
    assert coverage["trace"]["compositional"]["core_complete"] is False


def test_missing_core_component_is_reported(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Own a production model", requires_all_core=True)
        python_id = _concept(cur, "Python")
        reserving_id = _concept(cur, "Reserving", type_code="function")
        _component_edge(cur, python_id, cap_id, "core")
        _component_edge(cur, reserving_id, cap_id, "core")

        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "Used Python", episode_id=episode_id, depth="owned"), python_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"
    assert coverage["trace"]["compositional"]["best_episode"]["core_missing"] == ["Reserving"]


def test_supporting_component_missing_still_meaningful(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Own a production model")
        python_id = _concept(cur, "Python")
        docs_id = _concept(cur, "Documentation practice", type_code="method")
        _component_edge(cur, python_id, cap_id, "core")
        _component_edge(cur, docs_id, cap_id, "supporting")

        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "Used Python", episode_id=episode_id, depth="owned"), python_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"
    assert coverage["trace"]["compositional"]["best_episode"]["supporting_missing"] == ["Documentation practice"]


def test_contextual_component_missing_still_meaningful(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Own a production model")
        python_id = _concept(cur, "Python")
        domain_id = _concept(cur, "Life insurance", type_code="domain")
        _component_edge(cur, python_id, cap_id, "core")
        _component_edge(cur, domain_id, cap_id, "contextual")

        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "Used Python", episode_id=episode_id, depth="owned"), python_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"
    assert coverage["trace"]["compositional"]["best_episode"]["contextual_missing"] == ["Life insurance"]


def test_no_components_configured_cannot_produce_compositional_partial(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Undecomposed capability")
        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "not_found"
    assert coverage["trace"]["compositional"]["note"] == "no components are curated for this capability"


def test_requires_all_core_true_needs_every_core_component(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Strict capability", requires_all_core=True)
        a_id, b_id = _concept(cur, "AtomA"), _concept(cur, "AtomB")
        _component_edge(cur, a_id, cap_id, "core")
        _component_edge(cur, b_id, cap_id, "core")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "used A", episode_id=episode_id, depth="owned"), a_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["trace"]["compositional"]["core_complete"] is False


def test_requires_all_core_false_uses_min_core_required(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Lenient capability", requires_all_core=False, min_core_required=1)
        a_id, b_id = _concept(cur, "AtomA"), _concept(cur, "AtomB")
        _component_edge(cur, a_id, cap_id, "core")
        _component_edge(cur, b_id, cap_id, "core")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "used A", episode_id=episode_id, depth="owned"), a_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    # same underlying evidence as the strict test above, but this capability
    # only curated a minimum of 1 core component -> now "complete"
    assert coverage["trace"]["compositional"]["core_complete"] is True


def test_invalid_component_edge_grammar_rejected(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability")
        role_archetype_id = _concept(cur, "Chief Actuary", type_code="role_archetype")

    resp = client.post(f"/api/capabilities/{cap_id}/components", json={"concept_id": role_archetype_id, "necessity": "core"})
    assert resp.status_code == 400


# --- direct evidence (brief §36) --------------------------------------------

def test_direct_capability_claim_mapping_meeting_threshold_is_evidenced(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Lead a reserving process", min_depth="owned", min_autonomy="directed_others")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1), autonomy="directed_others")
        claim_id = _claim(cur, "Led the quarterly reserving process end to end.", episode_id=episode_id, depth="owned")
        _claim_mapping(cur, claim_id, cap_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "evidenced"
    assert coverage["directly_claimed"] is True
    assert coverage["strongest_depth"] == "owned"
    assert coverage["strongest_autonomy"] == "directed_others"


def test_direct_profile360_capability_mapping_alone_is_partial_not_evidenced(client):
    """profile360.capabilities carries no depth/autonomy at all — a mapping
    from it can never alone satisfy a modifier threshold (brief §7's "do not
    fabricate missing modifiers"), so it is capped at partial."""
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Own a production model")  # min_depth defaults to 'owned'
        p360_cap_id = _p360_capability(cur, "Owns production models end to end")
        _capability_mapping(cur, p360_cap_id, cap_id, review_status="accepted")

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"
    assert coverage["directly_claimed"] is True


def test_rejected_mapping_is_ignored(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        claim_id = _claim(cur, "Not relevant.", episode_id=episode_id, depth="owned")
        _claim_mapping(cur, claim_id, cap_id, review_status="rejected")

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "not_found"
    assert coverage["directly_claimed"] is False


def test_unreviewed_mapping_cannot_produce_evidenced(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="owned")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        claim_id = _claim(cur, "States the capability.", episode_id=episode_id, depth="set_standard")
        _claim_mapping(cur, claim_id, cap_id, review_status="unreviewed")

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"


# --- modifier thresholds (brief §36) ----------------------------------------

def test_below_depth_threshold_is_partial(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="owned")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        claim_id = _claim(cur, "States the capability.", episode_id=episode_id, depth="applied")
        _claim_mapping(cur, claim_id, cap_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["status"] == "partial"


def test_above_depth_threshold_is_evidenced(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="applied")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        claim_id = _claim(cur, "States the capability.", episode_id=episode_id, depth="set_standard")
        _claim_mapping(cur, claim_id, cap_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["status"] == "evidenced"


def test_below_autonomy_threshold_is_partial(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="owned", min_autonomy="accountable")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1), autonomy="independent")
        claim_id = _claim(cur, "States the capability.", episode_id=episode_id, depth="owned")
        _claim_mapping(cur, claim_id, cap_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["status"] == "partial"
    assert "accountable" in coverage["trace"]["status_reason"]["message"]


def test_null_modifier_where_threshold_required_is_not_promoted(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="owned", min_autonomy="independent")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1), autonomy=None)
        claim_id = _claim(cur, "States the capability.", episode_id=episode_id, depth="owned")
        _claim_mapping(cur, claim_id, cap_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["status"] == "partial"  # autonomy unknown -> never promoted to satisfy the threshold


# --- status precedence (brief §36) ------------------------------------------

def test_evidenced_beats_assertion(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="owned")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        claim_id = _claim(cur, "States the capability.", episode_id=episode_id, depth="owned")
        _claim_mapping(cur, claim_id, cap_id)
        _assert_capability(cur, cap_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["status"] == "evidenced"


def test_partial_beats_assertion(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="set_standard")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        claim_id = _claim(cur, "States the capability.", episode_id=episode_id, depth="owned")
        _claim_mapping(cur, claim_id, cap_id)
        _assert_capability(cur, cap_id)

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["status"] == "partial"


def test_assertion_beats_not_found(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability")
        _assert_capability(cur, cap_id, note="I have done this")

        coverage = engine.derive_capability_coverage(cur, cap_id)
    assert coverage["status"] == "user_asserted"


def test_composition_alone_cannot_produce_evidenced(client):
    """The absolute invariant (brief §10): even when every core, supporting,
    and contextual component is fully evidenced at the highest depth, in one
    episode, with no direct evidence for the capability itself, the status
    must stay `partial`."""
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Own a production model", min_depth="owned", requires_all_core=True)
        python_id = _concept(cur, "Python")
        reserving_id = _concept(cur, "Reserving", type_code="function")
        docs_id = _concept(cur, "Documentation practice", type_code="method")
        domain_id = _concept(cur, "Life insurance", type_code="domain")
        _component_edge(cur, python_id, cap_id, "core")
        _component_edge(cur, reserving_id, cap_id, "core")
        _component_edge(cur, docs_id, cap_id, "supporting")
        _component_edge(cur, domain_id, cap_id, "contextual")

        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1), autonomy="accountable")
        for atom, text in ((python_id, "Python"), (reserving_id, "reserving"), (docs_id, "docs"), (domain_id, "life insurance")):
            _claim_mapping(cur, _claim(cur, f"Used {text}", episode_id=episode_id, depth="set_standard"), atom)

        coverage = engine.derive_capability_coverage(cur, cap_id)

    assert coverage["status"] == "partial"
    assert coverage["trace"]["compositional"]["core_complete"] is True
    assert coverage["directly_claimed"] is False


# --- rebuild (brief §36) -----------------------------------------------------

def test_rebuild_is_idempotent(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Idempotent capability")
        first = engine.rebuild_capability_coverage(cur)
        cur.execute("SELECT status, trace FROM jobber.d_capability_coverage WHERE capability_concept_id = %s", (cap_id,))
        row1 = dict(cur.fetchone())
        second = engine.rebuild_capability_coverage(cur)
        cur.execute("SELECT status, trace FROM jobber.d_capability_coverage WHERE capability_concept_id = %s", (cap_id,))
        row2 = dict(cur.fetchone())
    assert row1 == row2
    assert first["computed"] == second["computed"]


def test_rebuild_removes_stale_rows_for_deactivated_capability(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Temp capability")
        engine.rebuild_capability_coverage(cur)
        cur.execute("SELECT 1 FROM jobber.d_capability_coverage WHERE capability_concept_id = %s", (cap_id,))
        assert cur.fetchone() is not None

        cur.execute("UPDATE jobber.concept SET status = 'deprecated' WHERE id = %s", (cap_id,))
        engine.rebuild_capability_coverage(cur)
        cur.execute("SELECT 1 FROM jobber.d_capability_coverage WHERE capability_concept_id = %s", (cap_id,))
        assert cur.fetchone() is None


def test_rebuild_stamps_engine_version_and_vocabulary_version(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Versioned capability")
        engine.rebuild_capability_coverage(cur)
        cur.execute(
            "SELECT engine_version, vocabulary_version_id FROM jobber.d_capability_coverage WHERE capability_concept_id = %s",
            (cap_id,),
        )
        row = cur.fetchone()
    assert row["engine_version"] == engine.ENGINE_VERSION
    assert row["vocabulary_version_id"] is not None


def test_rebuild_persists_a_capability_with_an_assertion(client):
    """Regression: person_capability_assertion.created_at is a TIMESTAMPTZ
    embedded verbatim into the trace dict — persisting it as JSONB must not
    raise (caught by backend/scripts/seed_phase3_eval_sample.py, not by the
    narrower derive_capability_coverage-only tests above)."""
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Asserted-only capability")
        _assert_capability(cur, cap_id, note="illustrative")

        summary = engine.rebuild_capability_coverage(cur)
        cur.execute("SELECT status, trace FROM jobber.d_capability_coverage WHERE capability_concept_id = %s", (cap_id,))
        row = cur.fetchone()

    assert summary["computed"] >= 1
    assert row["status"] == "user_asserted"
    assert row["trace"]["assertion"]["note"] == "illustrative"
    assert isinstance(row["trace"]["assertion"]["created_at"], str)


def test_rebuild_does_not_modify_source_tables(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Source-stable capability")
        tool_id = _concept(cur, "SourceTool")
        _component_edge(cur, tool_id, cap_id, "core")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        claim_id = _claim(cur, "Used SourceTool", episode_id=episode_id, depth="owned")
        _claim_mapping(cur, claim_id, tool_id)

        cur.execute("SELECT claim_text, depth, episode_id FROM profile360.claims WHERE id = %s", (claim_id,))
        before = dict(cur.fetchone())

        engine.rebuild_phase3_derivations(cur)

        cur.execute("SELECT claim_text, depth, episode_id FROM profile360.claims WHERE id = %s", (claim_id,))
        after = dict(cur.fetchone())
    assert before == after


# --- role fit (brief §36) ----------------------------------------------------

def _role(cur, title="Test role"):
    return db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": title}, skills=[])


def _requirement(cur, role_id, concept_id, requirement_type="required", basis="user_asserted"):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis) VALUES (%s, %s, %s, %s)",
        (role_id, concept_id, requirement_type, basis),
    )


def test_role_fit_required_evidenced(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="owned")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "text", episode_id=episode_id, depth="owned"), cap_id)
        role_id = _role(cur)
        _requirement(cur, role_id, cap_id)

        fit = engine.derive_role_fit(cur, role_id)
    assert fit["n_evidenced"] == 1
    assert fit["blocking_gaps"] == []


def test_role_fit_required_partial(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability", min_depth="set_standard")
        episode_id = _episode(cur, start_date=date(2020, 1, 1), end_date=date(2021, 1, 1))
        _claim_mapping(cur, _claim(cur, "text", episode_id=episode_id, depth="owned"), cap_id)
        role_id = _role(cur)
        _requirement(cur, role_id, cap_id)

        fit = engine.derive_role_fit(cur, role_id)
    assert fit["n_partial"] == 1
    assert len(fit["unverified_required"]) == 1
    assert fit["blocking_gaps"] == []


def test_role_fit_required_user_asserted(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability")
        _assert_capability(cur, cap_id)
        role_id = _role(cur)
        _requirement(cur, role_id, cap_id)

        fit = engine.derive_role_fit(cur, role_id)
    assert fit["n_asserted"] == 1
    assert len(fit["unverified_required"]) == 1
    assert fit["blocking_gaps"] == []


def test_role_fit_required_not_found_is_blocking(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability")
        role_id = _role(cur)
        _requirement(cur, role_id, cap_id)

        fit = engine.derive_role_fit(cur, role_id)
    assert fit["n_not_found"] == 1
    assert len(fit["blocking_gaps"]) == 1
    assert fit["blocking_gaps"][0]["id"] == cap_id


def test_role_fit_preferred_gap_is_not_blocking(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Some capability")
        role_id = _role(cur)
        _requirement(cur, role_id, cap_id, requirement_type="preferred")

        fit = engine.derive_role_fit(cur, role_id)
    assert fit["n_not_found"] == 1
    assert fit["blocking_gaps"] == []
    assert fit["unverified_required"] == []


def test_role_fit_atomic_concept_requirement_uses_direct_concept_evidence(client):
    """brief §16: an atomic (non-capability) requirement must not be
    silently treated as a capability requirement."""
    with db.db_cursor() as cur:
        python_id = _concept(cur, "Python")
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Used Python daily.') RETURNING id")
        claim_id = str(cur.fetchone()["id"])
        _claim_mapping(cur, claim_id, python_id)
        role_id = _role(cur)
        _requirement(cur, role_id, python_id)

        fit = engine.derive_role_fit(cur, role_id)
    assert fit["n_evidenced"] == 1
    assert fit["trace"]["items"][0]["detail"]["kind"] == "concept"


def test_embedding_similarity_cannot_alter_structural_status(client):
    with db.db_cursor() as cur:
        cur.execute("INSERT INTO profile360.snapshots (summary) VALUES ('Actuarial professional with Python experience.')")
        cap_id = _capability(cur, "Some capability")
        role_id = _role(cur)
        _requirement(cur, role_id, cap_id)
        vector = embeddings.embed_text("Actuarial professional with Python experience.")
        embeddings.set_embedding(cur, "role_instance", role_id, vector)

        fit = engine.derive_role_fit(cur, role_id)

    assert fit["n_not_found"] == 1  # no evidence at all for the capability -> not_found regardless of similarity
    assert len(fit["blocking_gaps"]) == 1
    assert fit["embedding_similarity"] is not None
