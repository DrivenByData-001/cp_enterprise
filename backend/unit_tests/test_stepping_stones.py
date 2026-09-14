from app.stepping_stones import assess_candidate

def requirement(key, status="accepted", required=True):
    return {"concept_id": key, "canonical_name": key, "type_code": "tool",
            "review_status": status, "requirement_type": "required" if required else "preferred", "source": "claim"}

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

def test_missing_or_pending_data_never_means_reachable():
    target = [requirement("A"), requirement("B")]
    current = {"A": "evidenced", "B": "not_found"}
    assert assess_candidate([], target, current)["assessment"] == "insufficient_evidence"
    assert assess_candidate([requirement("A", "unreviewed")], target, current)["assessment"] == "insufficient_evidence"
    assert assess_candidate([requirement("A")], [requirement("B", "unreviewed")], current)["assessment"] == "insufficient_evidence"
    assert assess_candidate([requirement("A", required=False)], target, current)["assessment"] == "insufficient_evidence"

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
