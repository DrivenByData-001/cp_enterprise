import pytest
from pydantic import ValidationError
from app.models import RequirementItem, RequirementExtractionResult
from app.requirement_scope import excluded_surface_keys


def test_classification_is_required_and_not_guessed():
    raw = dict(surface_form='Dublin', evidence_span='Our office is in Dublin.',
               basis='stated', requirement_type='contextual')
    with pytest.raises(ValidationError):
        RequirementExtractionResult(requirements=[raw])
    with pytest.raises(ValidationError):
        RequirementItem(**raw, category='location', classification_reason='Office location')
    item = RequirementItem(**raw, category='role_metadata', classification_reason='Office location')
    assert item.category == 'role_metadata'


@pytest.mark.parametrize('category', ['role_metadata', 'eligibility_condition', 'irrelevant'])
def test_only_professional_readings_enter_vocabulary(category):
    assert excluded_surface_keys({'statements': [dict(surface_key='test term', category=category)]}) == {'test term'}
    assert excluded_surface_keys({'statements': [dict(surface_key='test term', category=category),
        dict(surface_key='test term', category='professional_requirement')]}) == set()
