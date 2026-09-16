"""Canonical role requirement evidence loader, and the requirement-review
curation gate (unreviewed AI claims are visible proposals only — never
analytically authoritative). Runs `app.role_requirements` directly against
the real Postgres test database, plus integration checks through
`capability_engine.derive_role_fit` and `db.role_skills_display` (Role
Detail's own consumer of the same evidence, split into reviewed-requirements
and legacy-skills lists rather than a single merged/fallback one)."""

import uuid

from app import capability_engine as engine
from app import db
from app.concept_linking import get_or_create_current_vocabulary_version
from app.role_requirements import (
    load_requirement_review_summary,
    load_requirement_review_summary_bulk,
    load_role_requirements,
    vetoed_concept_ids,
)


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


def _claim_requirement(cur, role_id, concept_id, *, requirement_type="required", basis="user_asserted", review_status="accepted", superseded_by=None, claim_id=None):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (id, role_instance_id, concept_id, requirement_type, basis, review_status, superseded_by) "
        "VALUES (COALESCE(%s, gen_random_uuid()), %s, %s, %s, %s, %s, %s) RETURNING id",
        (claim_id, role_id, concept_id, requirement_type, basis, review_status, superseded_by),
    )
    return str(cur.fetchone()["id"])


def _supersede(cur, old_claim_id, *, new_review_status="accepted", old_review_status=None, **new_claim_kwargs) -> str:
    """Migration 0020's partial unique index allows at most one *current*
    claim per (role, concept), so a same-concept replacement must free the
    old row's slot (superseded_by set) *before* the new row reclaims it —
    the new row's id is generated here so that ordering is possible despite
    superseded_by's own foreign key pointing at it (deferred to commit; see
    routes/role_instances.py::_supersede_with_new_claim, the same pattern
    used in application code). `old_review_status` optionally also flips the
    old row's status (e.g. to 'corrected'); left alone (None) when a test
    wants to simulate proposal churn where the old row stays 'unreviewed'."""
    new_claim_id = str(uuid.uuid4())
    if old_review_status is not None:
        cur.execute(
            "UPDATE jobber.requirement_claim SET review_status = %s, superseded_by = %s WHERE id = %s",
            (old_review_status, new_claim_id, old_claim_id),
        )
    else:
        cur.execute(
            "UPDATE jobber.requirement_claim SET superseded_by = %s WHERE id = %s",
            (new_claim_id, old_claim_id),
        )
    _claim_requirement(cur, review_status=new_review_status, claim_id=new_claim_id, **new_claim_kwargs)
    return new_claim_id


def _proposal(cur, role_id, document_id, *, surface_form, status="pending", evidence_span=None):
    """Mirrors extraction.py's unresolved-surface-form path: a concept_proposal
    row plus the concept_proposal_occurrence row that attributes it to this
    particular role (migration 0021 — see role_requirements.py's
    unresolved_proposals query, which joins through the occurrence table
    rather than concept_proposal's own once-only document_id column)."""
    cur.execute(
        "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, evidence_span, status) "
        "VALUES (%s, 1, %s, %s, %s) RETURNING id",
        (surface_form, document_id, evidence_span, status),
    )
    proposal_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id) "
        "VALUES (%s, %s, %s)",
        (proposal_id, role_id, document_id),
    )
    return proposal_id


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


def test_corrected_concept_cannot_reenter_through_fallback(client):
    """A concept whose only claim history is a human *correction* (review_
    status='corrected', superseded_by set — routes/role_instances.py's edit
    endpoint always sets both together) must prevent that same concept's
    legacy observation from resurrecting it — an unrelated observation is
    unaffected. This is real curator authority: a human looked at this
    concept and actively changed their mind about it."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        old_concept_id = _concept(cur, "Old thing")
        old_claim_id = _claim_requirement(cur, role_id, old_concept_id, review_status="corrected")
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


def test_superseded_but_never_reviewed_claim_does_not_veto_fallback(client):
    """A claim superseded purely by extraction re-running (extraction.py's
    conservative rerun/supersession handling — see test_requirement_claims.py)
    is proposal churn, not a curator decision: no human ever looked at it.
    Unlike a rejected or human-corrected claim, it must NOT veto the legacy
    observation fallback for that concept — 'unreviewed claims must not
    suppress the legacy observation fallback' must hold even across a chain
    of still-unreviewed proposals."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Old thing")
        old_claim_id = _claim_requirement(cur, role_id, concept_id, review_status="unreviewed")
        _supersede(cur, old_claim_id, role_id=role_id, concept_id=concept_id, new_review_status="unreviewed")
        _observation(cur, role_id, surface_form="Old thing", canonical_concept_id=concept_id)

        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["source"] == "role_skill_observation"
    assert items[0]["concept_id"] == concept_id


def test_superseded_concept_with_current_usable_successor_uses_claim_path(client):
    """If the concept is represented by a current usable successor claim,
    the normal authoritative-claim path handles it — the whole role takes
    the claim-authoritative branch, and fallback (with its exclusion logic)
    never runs at all."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Reserving")
        old_claim_id = _claim_requirement(cur, role_id, concept_id, requirement_type="preferred")
        new_claim_id = _supersede(cur, old_claim_id, role_id=role_id, concept_id=concept_id, requirement_type="required")
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


# --- the curation gate itself: unreviewed is a proposal, never authoritative -

def test_unreviewed_claim_is_excluded_from_authoritative_requirements(client):
    """The core rule: an unreviewed claim is a visible proposal only. With no
    fallback observation and no other claim, a role whose only claim is
    unreviewed must show zero *authoritative* requirements — not because the
    proposal doesn't exist, but because it was never reviewed."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        _claim_requirement(cur, role_id, concept_id, review_status="unreviewed")

        items = load_role_requirements(cur, role_id)
    assert items == []


def test_unreviewed_claim_does_not_suppress_legacy_observation_fallback(client):
    """A lone unreviewed claim for a concept (no supersession involved) must
    not prevent that same concept's legacy observation from participating in
    fallback — only a rejected or corrected claim earns that veto."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        _claim_requirement(cur, role_id, concept_id, review_status="unreviewed")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=concept_id)

        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["source"] == "role_skill_observation"
    assert items[0]["concept_id"] == concept_id


def test_accepted_claims_used_alone_when_unreviewed_claims_also_exist(client):
    """A role with both accepted and unreviewed current claims is only
    partially reviewed. The accepted claims are usable (brief §1: 'if
    accepted claims coexist with unreviewed claims, accepted claims may be
    used'), but the unreviewed ones must not sneak in alongside them, and
    the review summary must report the role as incomplete so a downstream/UI
    consumer never mistakes this for a finished review."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        accepted_concept_id = _concept(cur, "Python")
        _claim_requirement(cur, role_id, accepted_concept_id, review_status="accepted")
        pending_concept_id = _concept(cur, "SQL")
        _claim_requirement(cur, role_id, pending_concept_id, review_status="unreviewed")

        items = load_role_requirements(cur, role_id)
        summary = load_requirement_review_summary(cur, role_id)

    assert len(items) == 1
    assert items[0]["concept_id"] == accepted_concept_id
    assert summary == {
        "accepted": 1, "unreviewed": 1, "rejected": 0,
        "unresolved_proposals": 0, "extraction_attempted": False, "needs_reextraction": 0, "complete": False,
    }


# --- review-summary helper --------------------------------------------------

def test_review_summary_counts_current_claims_by_status(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        _claim_requirement(cur, role_id, _concept(cur, "A"), review_status="accepted")
        _claim_requirement(cur, role_id, _concept(cur, "B"), review_status="accepted")
        _claim_requirement(cur, role_id, _concept(cur, "C"), review_status="unreviewed")
        _claim_requirement(cur, role_id, _concept(cur, "D"), review_status="rejected")

        summary = load_requirement_review_summary(cur, role_id)
    assert summary == {
        "accepted": 2, "unreviewed": 1, "rejected": 1,
        "unresolved_proposals": 0, "extraction_attempted": False, "needs_reextraction": 0, "complete": False,
    }


def test_review_summary_complete_when_no_current_claims_are_unreviewed(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        _claim_requirement(cur, role_id, _concept(cur, "A"), review_status="accepted")
        _claim_requirement(cur, role_id, _concept(cur, "B"), review_status="rejected")

        summary = load_requirement_review_summary(cur, role_id)
    assert summary["complete"] is True


def test_review_summary_vacuously_complete_for_a_role_with_no_claims(client):
    """A role with no requirement_claim rows at all (relying entirely on the
    legacy observation fallback, or simply never extracted) has nothing
    outstanding to review — 'complete' is true, not a false negative."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        summary = load_requirement_review_summary(cur, role_id)
    assert summary == {
        "accepted": 0, "unreviewed": 0, "rejected": 0,
        "unresolved_proposals": 0, "extraction_attempted": False, "needs_reextraction": 0, "complete": True,
    }


def test_review_summary_is_not_complete_while_unresolved_vocabulary_proposals_remain(client):
    """A surface form extraction could not resolve to any concept becomes a
    concept_proposal, never a requirement_claim — so a role can have every
    one of its claims accepted while still carrying pending, unresolved
    proposals for the same document. Those requirements are just as
    excluded from analysis as an unreviewed claim would be (they never
    became a claim at all), so 'complete' must stay False."""
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Requires Python and Solvency II.",
                                             provenance_quality="original")
        role_id = db.upsert_role_instance(
            cur, None, {"instance_type": "observed_posting", "title": "T", "document_id": document_id}, skills=[]
        )
        _claim_requirement(cur, role_id, _concept(cur, "Python"), review_status="accepted")
        _proposal(cur, role_id, document_id, surface_form="solvency ii", evidence_span="Solvency II")
        summary = load_requirement_review_summary(cur, role_id)
    assert summary["accepted"] == 1
    assert summary["unreviewed"] == 0
    assert summary["unresolved_proposals"] == 1
    assert summary["complete"] is False


def test_resolved_or_unrelated_proposals_do_not_count_as_unresolved(client):
    """A proposal is excluded from this role's count if it is no longer
    pending (already resolved into the vocabulary), or if its occurrence
    belongs to some other role entirely."""
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Requires Python.", provenance_quality="original")
        other_document_id, _ = db.create_document(cur, kind="job_posting", content_text="A different posting.", provenance_quality="original")
        role_id = db.upsert_role_instance(
            cur, None, {"instance_type": "observed_posting", "title": "T", "document_id": document_id}, skills=[]
        )
        other_role_id = db.upsert_role_instance(
            cur, None, {"instance_type": "observed_posting", "title": "Other", "document_id": other_document_id}, skills=[]
        )
        _proposal(cur, role_id, document_id, surface_form="resolved thing", status="resolved", evidence_span="x")
        _proposal(cur, other_role_id, other_document_id, surface_form="unrelated thing", status="pending", evidence_span="y")
        summary = load_requirement_review_summary(cur, role_id)
    assert summary["unresolved_proposals"] == 0
    assert summary["complete"] is True


def test_unresolved_proposal_counts_for_every_role_that_hit_it_not_just_the_first(client):
    """Code-review follow-up: concept_proposal is globally deduplicated by
    surface_form, and its own document_id column only ever remembers the
    *first* role/document that produced a given unresolved term (extraction.py
    sets it via `document_id = COALESCE(document_id, %s)`). Role B hitting the
    exact same still-pending term as role A must still see it as unresolved —
    it must not inherit completeness just because role A got there first."""
    with db.db_cursor() as cur:
        document_a, _ = db.create_document(cur, kind="job_posting", content_text="Requires Foo Modelling.", provenance_quality="original")
        document_b, _ = db.create_document(cur, kind="job_posting", content_text="Also requires Foo Modelling.", provenance_quality="original")
        role_a = db.upsert_role_instance(
            cur, None, {"instance_type": "observed_posting", "title": "A", "document_id": document_a}, skills=[]
        )
        role_b = db.upsert_role_instance(
            cur, None, {"instance_type": "observed_posting", "title": "B", "document_id": document_b}, skills=[]
        )
        # Single global concept_proposal row (surface_form dedup), document_id
        # forever pinned to role A's document — exactly as COALESCE leaves it.
        cur.execute(
            "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, evidence_span, status) "
            "VALUES ('foo modelling', 2, %s, 'Foo Modelling', 'pending') RETURNING id",
            (document_a,),
        )
        proposal_id = str(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id) VALUES (%s, %s, %s)",
            (proposal_id, role_a, document_a),
        )
        cur.execute(
            "INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id) VALUES (%s, %s, %s)",
            (proposal_id, role_b, document_b),
        )
        summary = load_requirement_review_summary_bulk(cur, [role_a, role_b])
    assert summary[role_a]["unresolved_proposals"] == 1
    assert summary[role_a]["complete"] is False
    assert summary[role_b]["unresolved_proposals"] == 1
    assert summary[role_b]["complete"] is False


def test_extraction_attempted_reflects_whether_a_requirement_extract_run_exists(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        summary = load_requirement_review_summary(cur, role_id)
        assert summary["extraction_attempted"] is False

        vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
        cur.execute(
            "INSERT INTO jobber.extraction_run (task, subject_type, role_instance_id, model, prompt_name, "
            "prompt_version, vocabulary_version_id, started_at, finished_at, status) "
            "VALUES ('requirement_extract', 'role_instance', %s, 'm', 'p', 'v', %s, now(), now(), 'ok')",
            (role_id, vocabulary_version_id),
        )
        summary = load_requirement_review_summary(cur, role_id)
    assert summary["extraction_attempted"] is True
    assert summary["complete"] is True  # zero claims and zero proposals — attempted, but nothing pending either


def test_review_summary_excludes_superseded_and_corrected_history(client):
    """A corrected claim's superseded predecessor is history, not a current
    decision — it must not inflate any count, including 'accepted' merely
    because it happened to be accepted before being corrected."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        old_claim_id = _claim_requirement(cur, role_id, concept_id, review_status="accepted")
        _supersede(cur, old_claim_id, role_id=role_id, concept_id=concept_id, old_review_status="corrected")
        summary = load_requirement_review_summary(cur, role_id)
    assert summary == {
        "accepted": 1, "unreviewed": 0, "rejected": 0,
        "unresolved_proposals": 0, "extraction_attempted": False, "needs_reextraction": 0, "complete": True,
    }


def test_review_summary_bulk_handles_multiple_roles_and_empty_input(client):
    with db.db_cursor() as cur:
        role_a = _role(cur, "Role A")
        role_b = _role(cur, "Role B")
        _claim_requirement(cur, role_a, _concept(cur, "A"), review_status="unreviewed")
        _claim_requirement(cur, role_b, _concept(cur, "B"), review_status="accepted")

        assert load_requirement_review_summary_bulk(cur, []) == {}
        summary = load_requirement_review_summary_bulk(cur, [role_a, role_b])
    assert summary[role_a]["unreviewed"] == 1
    assert summary[role_a]["complete"] is False
    assert summary[role_b]["accepted"] == 1
    assert summary[role_b]["complete"] is True


# --- db.role_skills_display: Role Detail's own consumer ---------------------
#
# Two clearly separate lists rather than one fallback-merged list: "skills"
# is exactly the canonical loader's claim-sourced items (never its own
# role_skill_observation-fallback items), so a reviewed decision always shows
# fresh, current data. "legacy_skills" is every role_skill_observation not
# already covered by one of those and not curator-vetoed — a pre-curation-
# gate role with no claim history shows *everything* there, honestly
# labelled as legacy/unreviewed rather than mixed into a reviewed list.

def test_role_skills_display_reviewed_shows_only_accepted_current_claims(client):
    with db.db_cursor() as cur:
        role_id = _role(cur)
        accepted_id = _concept(cur, "Python")
        _claim_requirement(cur, role_id, accepted_id, review_status="accepted")
        _claim_requirement(cur, role_id, _concept(cur, "SQL"), review_status="unreviewed")
        _claim_requirement(cur, role_id, _concept(cur, "Rust"), review_status="rejected")

        display = db.role_skills_display(cur, role_id)
    assert [s["resolved_concept_id"] for s in display["skills"]] == [accepted_id]
    assert display["legacy_skills"] == []


def test_role_skills_display_reviewed_excludes_superseded_history(client):
    """A corrected claim's superseded predecessor must not render as a
    duplicate skill alongside its replacement."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        old_claim_id = _claim_requirement(cur, role_id, concept_id, review_status="accepted")
        _supersede(cur, old_claim_id, role_id=role_id, concept_id=concept_id,
                   requirement_type="preferred", old_review_status="corrected")
        skills = db.role_skills_display(cur, role_id)["skills"]
    assert len(skills) == 1
    assert skills[0]["requirement_type"] == "preferred"


def test_role_skills_display_reviewed_claim_wins_over_legacy_grading(client):
    """The task-2 scenario, verbatim: legacy Python = required (an old
    role_skill_observation), a curator grades the reviewed claim down to
    preferred (same concept — the edit endpoint marks the old claim row
    'corrected' but leaves a live accepted successor for the same concept).
    Role Detail must show 'Python · preferred' from the reviewed claim — not
    the stale 'required' the legacy observation still carries — and must not
    show Python twice."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        concept_id = _concept(cur, "Python")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=concept_id, requirement_type="required")
        old_claim_id = _claim_requirement(cur, role_id, concept_id, requirement_type="required", review_status="accepted")
        _supersede(cur, old_claim_id, role_id=role_id, concept_id=concept_id,
                   requirement_type="preferred", old_review_status="corrected")

        display = db.role_skills_display(cur, role_id)
    assert [(s["resolved_concept_id"], s["requirement_type"]) for s in display["skills"]] == [(concept_id, "preferred")]
    assert display["legacy_skills"] == []


def test_role_skills_display_reviewed_remap_shows_new_concept_not_nothing(client):
    """The task-2 scenario, verbatim: Python is remapped to SQL (a curator
    edit that corrects the old Python claim away and creates a new current
    accepted SQL claim). Role Detail must show the accepted SQL requirement
    — not silently drop Python and show nothing. A legacy Python observation
    that also exists must not reappear either (Python is genuinely vetoed:
    corrected away with nothing current left behind for *that* concept)."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        python_id = _concept(cur, "Python")
        sql_id = _concept(cur, "SQL")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=python_id, requirement_type="required")
        old_claim_id = _claim_requirement(cur, role_id, python_id, requirement_type="required", review_status="accepted")
        _supersede(cur, old_claim_id, role_id=role_id, concept_id=sql_id,
                   requirement_type="required", old_review_status="corrected")

        display = db.role_skills_display(cur, role_id)
    assert [s["resolved_concept_id"] for s in display["skills"]] == [sql_id]
    assert display["legacy_skills"] == []


def test_role_skills_display_legacy_covers_a_role_with_no_claim_history_at_all(client):
    """A pre-curation-gate role relying entirely on role_skill_observation
    (no requirement_claim rows at all) shows *everything* as legacy/
    unreviewed — never silently presented as if it had been reviewed."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        obs_concept_id = _concept(cur, "Pricing")
        _observation(cur, role_id, surface_form="Pricing", canonical_concept_id=obs_concept_id, requirement_type="required")

        display = db.role_skills_display(cur, role_id)
    assert display["skills"] == []
    assert [s["name"] for s in display["legacy_skills"]] == ["Pricing"]


def test_role_skills_display_reviewed_and_legacy_coexist_for_a_mixed_role(client):
    """A role with *some* reviewed claim history and *other* concepts only
    ever captured as legacy observations shows both, separated — the
    reviewed claim never merges with, and the legacy item never masquerades
    as, the other."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        reviewed_id = _concept(cur, "Python")
        _claim_requirement(cur, role_id, reviewed_id, review_status="accepted")
        legacy_id = _concept(cur, "Pricing")
        _observation(cur, role_id, surface_form="Pricing", canonical_concept_id=legacy_id, requirement_type="required")

        display = db.role_skills_display(cur, role_id)
    assert [s["resolved_concept_id"] for s in display["skills"]] == [reviewed_id]
    assert [s["resolved_concept_id"] for s in display["legacy_skills"]] == [legacy_id]


def test_role_skills_display_legacy_still_applies_curator_veto(client):
    """The legacy list is not exempt from curator authority: a concept a
    human has explicitly rejected (or corrected away) as a requirement for
    this role must not keep showing as a legacy skill chip just because a
    legacy role_skill_observation also mentions it — display must not
    contradict what analysis already excludes."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        rejected_concept_id = _concept(cur, "Python")
        _observation(cur, role_id, surface_form="Python", canonical_concept_id=rejected_concept_id, requirement_type="required")
        kept_concept_id = _concept(cur, "SQL")
        _observation(cur, role_id, surface_form="SQL", canonical_concept_id=kept_concept_id, requirement_type="preferred")
        _claim_requirement(cur, role_id, rejected_concept_id, review_status="rejected")

        display = db.role_skills_display(cur, role_id)
    resolved_ids = {s["resolved_concept_id"] for s in display["legacy_skills"]}
    assert rejected_concept_id not in resolved_ids
    assert kept_concept_id in resolved_ids
    assert len(display["legacy_skills"]) == 1
    assert display["skills"] == []


def test_vetoed_concept_ids_excludes_a_concept_with_a_current_accepted_successor(client):
    """Code-review follow-up: grading a claim (required -> preferred) for the
    SAME concept marks the old row 'corrected' while a new row for that
    concept stays current and accepted. That must NOT earn a veto — a
    corrected/rejected claim only vetoes when nothing current and accepted is
    left behind for the same concept_id."""
    with db.db_cursor() as cur:
        role_id = _role(cur)
        graded_concept_id = _concept(cur, "Python")
        old_claim_id = _claim_requirement(cur, role_id, graded_concept_id, requirement_type="required", review_status="accepted")
        _supersede(cur, old_claim_id, role_id=role_id, concept_id=graded_concept_id,
                   requirement_type="preferred", old_review_status="corrected")

        rejected_concept_id = _concept(cur, "Rust")
        _claim_requirement(cur, role_id, rejected_concept_id, review_status="rejected")

        vetoed = vetoed_concept_ids(cur, role_id)
    assert graded_concept_id not in vetoed
    assert rejected_concept_id in vetoed
