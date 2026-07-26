import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.span_validation import validate_span  # noqa: E402

BODY = "Rebuilt the annuity reserving model in Python, replacing a legacy Excel process."


def test_literal_substring_match():
    assert validate_span(BODY, "annuity reserving model")


def test_exact_offset_match():
    start = BODY.index("Python")
    assert validate_span(BODY, "Python", start, start + len("Python"))


def test_offset_drift_fails():
    start = BODY.index("Python")
    assert not validate_span(BODY, "Python", start + 1, start + 1 + len("Python"))


def test_fabricated_span_fails():
    assert not validate_span(BODY, "led the quarterly valuation")


def test_whitespace_and_unicode_edge_case():
    body = "Owned the Solvency II sign-off — end to end, no exceptions."
    assert validate_span(body, "Solvency II sign-off — end to end")
    assert not validate_span(body, "Solvency  II sign-off")  # double space, not present verbatim


def test_empty_inputs_fail():
    assert not validate_span("", "anything")
    assert not validate_span(BODY, "")
