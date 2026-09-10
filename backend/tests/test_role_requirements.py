"""Canonical role requirement evidence loader (Phase 4 prompt §2). Runs
`app.role_requirements` directly against the real Postgres test database,
plus integration checks through `capability_engine.derive_role_fit`."""

from app import capability_engine as engine
from app import db
from app.role_requirements import load_role_requirements


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


def _role(cur, title="Test role"):
    return db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": title}, skills=[])


def _claim_requirement(cur, role_id, concept_id, *, requirement_type="required", basis="user_asserted", review_status="unreviewed", superseded_by=None):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status, superseded_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (role_id, concept_id, requirement_type, basis, review_status, superseded_by),
    )
    return str(cur.fetchone()["id"])


def _observation(cur, role_id, *, surface_form, canonical_concept_id=None, requirement_type=None, importance=None):
    cur.execute(
        "INSERT INTO jobber.role_skill_observation "
        "(role_instance_id, surface_form, requirement_type, importance, observation_basis, canonical_concept_id) "
        "VALUES (%s, %s, %s, %s, 'legacy_extraction', %s) RETURNING id",
        (role_id, surface_form, requirement_type, importance, canonical_concept_id),
    )
    return str(cur.fetchone()["id"])


def test_requirement_claim_authoritative_when_present(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        _claim_requirement(cur, role_id, concept_id, requirement_type="required", basis="user_asserted")
        # Also add an observation for the SAME role, which must be ignored
        # entirely once a usable claim exists — the two sources are never
        # merged.
        other_concept_id = _concept(cur, "SQL")
        _observation(cur, role_id, surface_form="SQL", canonical_concept_id=other_concept_id)

        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["concept_id"] == concept_id
    assert items[0]["source"] == "claim"


def test_role_skill_observation_fallback_when_no_claims(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=concept_id, requirement_type="required")

        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["concept_id"] == concept_id
    assert items[0]["source"] == "role_skill_observation"
    assert items[0]["requirement_type"] == "required"
    # Never fabricated as stated evidence merely because it exists.
    assert items[0]["basis"] is None
    assert items[0]["review_status"] is None
    assert items[0]["evidence_span"] is None


def test_mapped_observation_produces_real_structural_fit(client):
    with db.db_cursor() as cur:
        cap_id = _capability(cur, "Lead a reserving process")
        role_id = _role(cur)
        _observation(cur, role_id, surface_form="Lead a reserving process", canonical_concept_id=cap_id, requirement_type="required")

        fit = engine.derive_role_fit(cur, role_id)
    assert fit["capabilities_required"] == 1
    assert fit["n_not_found"] == 1
    assert len(fit["blocking_gaps"]) == 1
    assert fit["blocking_gaps"][0]["id"] == cap_id


def test_unmapped_observation_is_ignored(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        _observation(cur, role_id, surface_form="some unresolved skill", canonical_concept_id=None)

        items = load_role_requirements(cur, role_id)
        fit = engine.derive_role_fit(cur, role_id)
    assert items == []
    assert fit["capabilities_required"] == 0


def test_observation_mapped_to_inactive_concept_is_ignored(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Deprecated thing", status="deprecated")
        _observation(cur, role_id, surface_form="deprecated thing", canonical_concept_id=concept_id)

        items = load_role_requirements(cur, role_id)
    assert items == []


def test_rejected_claims_do_not_block_fallback_for_other_roles(client):
    """A role whose only requirement_claim rows are rejected has no *usable*
    claims, so it must fall through to the observation fallback rather than
    being treated as having zero requirements — for concepts the rejected
    claim did not itself touch."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        rejected_concept_id = _concept(cur, "Rejected thing")
        _claim_requirement(cur, role_id, rejected_concept_id, review_status="rejected")

        fallback_concept_id = _concept(cur, "Python")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=fallback_concept_id)

        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["source"] == "role_skill_observation"
    assert items[0]["concept_id"] == fallback_concept_id


def test_rejected_concept_cannot_reenter_through_fallback(client):
    """A rejected claim for Python must prevent a legacy Python observation
    from resurrecting Python — but must NOT prevent an unrelated SQL
    observation from being used."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        python_id = _concept(cur, "Python")
        _claim_requirement(cur, role_id, python_id, review_status="rejected")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=python_id)

        sql_id = _concept(cur, "SQL")
        _observation(cur, role_id, surface_form="SQL", canonical_concept_id=sql_id)

        items = load_role_requirements(cur, role_id)
    concept_ids = {item["concept_id"] for item in items}
    assert python_id not in concept_ids
    assert sql_id in concept_ids
    assert len(items) == 1


def test_superseded_concept_cannot_reenter_through_fallback(client):
    """Only-superseded history (no usable successor claim) for a concept
    must prevent that same concept's legacy observation from resurrecting
    it — an unrelated observation is unaffected."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        old_concept_id = _concept(cur, "Old thing")
        old_claim_id = _claim_requirement(cur, role_id, old_concept_id)
        cur.execute("UPDATE jobber.requirement_claim SET superseded_by = %s WHERE id = %s", (old_claim_id, old_claim_id))
        # A self-superseded row is still superseded (superseded_by IS NOT NULL) —
        # simplest way to exercise the exclusion without a second row.
        _observation(cur, role_id, surface_form="Old thing", canonical_concept_id=old_concept_id)

        fallback_concept_id = _concept(cur, "Python")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=fallback_concept_id)

        items = load_role_requirements(cur, role_id)
    concept_ids = {item["concept_id"] for item in items}
    assert old_concept_id not in concept_ids
    assert fallback_concept_id in concept_ids
    assert len(items) == 1


def test_superseded_concept_with_current_usable_successor_uses_claim_path(client):
    """If the concept is represented by a current usable successor claim,
    the normal authoritative-claim path handles it — the whole role takes
    the claim-authoritative branch, and fallback (with its exclusion logic)
    never runs at all."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Reserving")
        old_claim_id = _claim_requirement(cur, role_id, concept_id, requirement_type="preferred")
        new_claim_id = _claim_requirement(cur, role_id, concept_id, requirement_type="required")
        cur.execute("UPDATE jobber.requirement_claim SET superseded_by = %s WHERE id = %s", (new_claim_id, old_claim_id))
        _observation(cur, role_id, surface_form="Reserving", canonical_concept_id=concept_id)

        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["source"] == "claim"
    assert items[0]["requirement_claim_id"] == new_claim_id
    assert items[0]["requirement_type"] == "required"


def test_rejected_or_superseded_claims_never_reappear_even_with_no_fallback(client):
    """If a role's only claims are rejected/superseded and it has no
    observations either, it must show zero requirements — never resurrect
    the rejected/superseded claim itself."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Rejected thing")
        _claim_requirement(cur, role_id, concept_id, review_status="rejected")

        items = load_role_requirements(cur, role_id)
    assert items == []


def test_no_double_counting_when_both_sources_have_rows(client):
    """Even when a role has both real requirement_claim rows AND
    role_skill_observation rows, only the claim rows are ever counted —
    never both."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        claim_concept_id = _concept(cur, "Claimed thing")
        _claim_requirement(cur, role_id, claim_concept_id)
        obs_concept_id = _concept(cur, "Observed thing")
        _observation(cur, role_id, surface_form="Observed thing", canonical_concept_id=obs_concept_id)

        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["concept_id"] == claim_concept_id


def test_requirement_type_and_importance_preserved_from_observation(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=concept_id, requirement_type="preferred", importance=4)

        items = load_role_requirements(cur, role_id)
    assert items[0]["requirement_type"] == "preferred"
    assert items[0]["importance"] == 4


def test_observation_with_no_stored_requirement_type_is_never_fabricated(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=concept_id, requirement_type=None)

        items = load_role_requirements(cur, role_id)
        fit = engine.derive_role_fit(cur, role_id)
    assert items[0]["requirement_type"] is None
    # Never treated as a blocking "required" gap when the source never said so.
    assert fit["blocking_gaps"] == []
    assert fit["unverified_required"] == []
