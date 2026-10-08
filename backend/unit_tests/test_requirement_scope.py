import pytest

from app.requirement_scope import is_posting_metadata


@pytest.mark.parametrize('term,span', [
    ('dublin, ireland', 'Location: Dublin, Ireland'),
    ('Dublin', 'Location: Dublin, Ireland'),
    ('Ireland', 'Location: Dublin, Ireland'),
    ('London', '• Job location: London / UK'),
    ('Location: Dublin, Ireland', 'Location: Dublin, Ireland'),
    ('€80,000–€100,000', 'Salary: €80,000–€100,000'),
    ('full time', 'Employment type: Full time'),
    ('hybrid', 'Working pattern: hybrid'),
])
def test_labelled_metadata_is_not_a_capability(term, span):
    assert is_posting_metadata(term, span)


@pytest.mark.parametrize('term,span', [
    ('Irish insurance regulation', 'Knowledge of Irish insurance regulation required.'),
    ('remote team management', 'Experience in remote team management.'),
    ('Python', 'Location: Dublin, Ireland\nPython experience required.'),
    ('Python', 'Location: Dublin, Ireland. Python experience required.'),
    ('Dublin market knowledge', 'Location: Dublin, Ireland'),
    ('Ireland', 'Experience advising clients in Ireland.'),
    ('unfamiliar technical method', None),
])
def test_professional_requirements_and_uncertain_items_remain(term, span):
    assert not is_posting_metadata(term, span)
