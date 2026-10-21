from unittest.mock import patch

import pytest

from app import capability_engine as engine


@pytest.mark.parametrize("mapped", [False, True])
def test_unconfigured_capability_preserves_evidence_without_awarding_full_coverage(mapped):
    row = {"id": "cap", "canonical_name": "Contract negotiations", "assessment_concept_id": None}
    evidence = [{"profile360_claim_id": "claim", "review_status": "accepted", "episode": None}] if mapped else []
    with patch.object(engine, "_capability_row", return_value=row), \
         patch.object(engine, "_direct_evidence", return_value=evidence), \
         patch.object(engine, "_assertion", return_value=None):
        result = engine.derive_capability_coverage(None, "cap")
    assert result["status"] == ("partial" if mapped else "not_found")
    assert result["coverage_score"] < 1
    assert result["supporting_profile360_claim_ids"] == (["claim"] if mapped else [])
    assert result["trace"]["status_reason"]["code"] == "assessment_unconfigured"
    assert result["trace"]["requirement"]["min_depth"] is None


def test_unknown_capability_still_raises():
    with patch.object(engine, "_capability_row", return_value=None), pytest.raises(engine.CapabilityNotFoundError):
        engine.derive_capability_coverage(None, "missing")

