from types import SimpleNamespace

import pytest

from app.application_artifacts import _validate_and_shape, ApplicationArtifactValidationError
from app.application_generation import GenerationContext, SourceEntry, assign_source_aliases
from app.models import (ApplicationPositioningGeneration, ApplicationSupportingStatementGeneration,
                        PositioningGapOrCaution, SupportingStatementSection)


@pytest.mark.parametrize('value', [None, 'null', ' NULL '])
def test_nullable_concepts_normalize_only_null(value):
    assert PositioningGapOrCaution(concept_id=value, message='Caution').concept_id is None
    assert SupportingStatementSection(concept_id=value, heading='Overview').concept_id is None


def context(kind):
    sources = [SourceEntry('profile_claim:fact', 'profile_claim', 'Fact', 'canonical_evidence', 'Fact'),
               SourceEntry('role_requirement:req', 'role_requirement', 'Requirement', 'role_side_context', 'Requirement')]
    return GenerationContext(SimpleNamespace(accepted_requirements=[{'concept_id': 'req'}], legacy_requirements=[]),
                             kind, None, sources, None, 'test', alias_to_ref=assign_source_aliases(sources))


def test_positioning_text_null_reaches_validated_output_as_json_null():
    output = ApplicationPositioningGeneration.model_validate(dict(
        positioning_statement=dict(text='Fact', source_refs=['C001']),
        gaps_and_cautions=[dict(concept_id='null', message='General caution', source_refs=['C001'])]))
    assert _validate_and_shape('positioning', output, context('positioning'))['gaps_and_cautions'][0]['concept_id'] is None


def test_supporting_statement_text_null_reaches_validated_output_as_json_null():
    output = ApplicationSupportingStatementGeneration.model_validate(dict(
        opening=dict(text='Fact', source_refs=['C001']),
        sections=[dict(heading='Overview', concept_id='null', paragraphs=[dict(text='Fact', source_refs=['C001'])])]))
    assert _validate_and_shape('supporting_statement', output, context('supporting_statement'))['sections'][0]['concept_id'] is None


@pytest.mark.parametrize('value', ['R999', '', 'None'])
def test_other_unknown_optional_handles_remain_rejected(value):
    output = ApplicationPositioningGeneration.model_validate(dict(
        positioning_statement=dict(text='Fact', source_refs=['C001']),
        gaps_and_cautions=[dict(concept_id=value, message='Caution', source_refs=['C001'])]))
    with pytest.raises(ApplicationArtifactValidationError, match='unknown source alias'):
        _validate_and_shape('positioning', output, context('positioning'))


def test_required_concept_and_source_refs_do_not_accept_text_null():
    for changes in [dict(requirements_to_lead_with=[dict(concept_id='null', reason='Lead', source_refs=['C001'])]),
                    dict(gaps_and_cautions=[dict(message='Caution', source_refs=['null'])])]:
        output = ApplicationPositioningGeneration.model_validate(dict(
            positioning_statement=dict(text='Fact', source_refs=['C001']), **changes))
        with pytest.raises(ApplicationArtifactValidationError, match='unknown source alias'):
            _validate_and_shape('positioning', output, context('positioning'))
