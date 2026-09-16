from app.stepping_stones import assess_candidate

def requirement(key, required=True, source="claim"):
    return {"concept_id": key, "canonical_name": key, "type_code": "tool",
            "requirement_type": "required" if required else "preferred", "source": source}

def test_balances_reachability_and_target_progress():
    target = [requirement("A"), requirement("B"), requirement("C")]
    current = {"A": "evidenced", "B": "not_found", "C": "not_found"}
    step = assess_candidate([requirement("A"), requirement("B")], target, current)
    assert step["assessment"] == "potential_step"
    assert step["evidence_coverage"] == .5
    assert step["target_gap_coverage"] == .5
    assert step["missing_required"] == ["B"]
    assert step["target_gaps_addressed"] == ["B"]
    assert assess_candidate(target, target, current)["assessment"] == "not_an_intermediate_step"

def _review(**counts):
    """A review summary in the shape `role_requirements.load_requirement_
    review_summary_bulk` returns: per-kind counts plus the canonical
    `complete` flag, which is true only when every kind is zero."""
    summary = {"unreviewed": 0, "unresolved_proposals": 0, "needs_reextraction": 0, **counts}
    summary["complete"] = not any(summary[k] for k in ("unreviewed", "unresolved_proposals", "needs_reextraction"))
    return summary

def test_missing_or_pending_data_never_means_reachable():
    # `requirements`/`target_requirements` only ever carry *usable* (accepted
    # claim or legacy-observation fallback) rows now — an unreviewed claim
    # never appears in them. Incomplete review is signalled via the separate
    # per-role review summaries (role_requirements.py's review-summary
    # helper), not by row content, so this test exercises that gate directly.
    target = [requirement("A"), requirement("B")]
    current = {"A": "evidenced", "B": "not_found"}
    assert assess_candidate([], target, current)["assessment"] == "insufficient_evidence"
    assert assess_candidate([requirement("A")], target, current, review=_review(unreviewed=1))["assessment"] == "insufficient_evidence"
    assert assess_candidate([requirement("A")], [requirement("B")], current, target_review=_review(unreviewed=1))["assessment"] == "insufficient_evidence"
    assert assess_candidate([requirement("A", required=False)], target, current)["assessment"] == "insufficient_evidence"

def test_every_part_of_the_review_gate_blocks_not_just_unreviewed_claims():
    """The canonical gate is `complete`, which folds in unresolved vocabulary
    proposals and concepts needing re-extraction as well as unreviewed
    claims. Checking the unreviewed count alone let a role with a dangling
    vocabulary term be assessed as though its requirements were final."""
    target = [requirement("A"), requirement("B")]
    current = {"A": "evidenced", "B": "not_found"}
    candidate = [requirement("A"), requirement("B", required=False)]

    # With nothing outstanding this candidate is a genuine step.
    assert assess_candidate(candidate, target, current, review=_review(), target_review=_review())["assessment"] == "potential_step"

    for kind in ("unreviewed", "unresolved_proposals", "needs_reextraction"):
        blocked = assess_candidate(candidate, target, current, review=_review(**{kind: 2}), target_review=_review())
        assert blocked["assessment"] == "insufficient_evidence", kind
        assert blocked["review_complete"] is False
        assert [b["kind"] for b in blocked["review_blockers"]] == [kind]
        assert "review is not complete" in blocked["explanation"]

        target_blocked = assess_candidate(candidate, target, current, review=_review(), target_review=_review(**{kind: 1}))
        assert target_blocked["assessment"] == "insufficient_evidence", kind
        assert target_blocked["target_review_complete"] is False

def test_assertions_and_partial_evidence_are_not_full_coverage():
    rows = [requirement("A"), requirement("B")]
    result = assess_candidate(rows, rows, {"A": "user_asserted", "B": "partial"})
    assert result["evidence_coverage"] == 0
    assert result["unverified_required"] == ["A", "B"]
    assert result["assessment"] == "needs_evidence"

def test_repeated_claims_do_not_inflate_coverage_or_progress():
    target = [requirement("A"), requirement("B"), requirement("C")]
    current = {"A": "evidenced", "B": "not_found", "C": "not_found"}
    unique = [requirement("A"), requirement("B")]
    repeated = [*unique, requirement("A"), requirement("B", required=False)]
    assert assess_candidate(unique, target, current) == assess_candidate(repeated, target, current)

def test_evidenced_target_and_unrelated_roles_not_recommended():
    assert assess_candidate([requirement("A")], [requirement("A")], {"A": "evidenced"})["assessment"] == "target_evidenced"
    assert assess_candidate([requirement("A")], [requirement("B")], {"A": "evidenced", "B": "not_found"})["assessment"] == "no_target_progress"
