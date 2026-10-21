"""AI authors content; the app retains and places the whole employment timeline."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app import application_artifacts as artifacts
from app import application_generation as generation
from app.cv_career import complete_cv_structure, employment_timeline
from app.models import CVWrittenBlock, CVWritingGeneration
from test_cv_career import bundle, context


def writing(ctx, refs=('profile_claim:restatement',)):
    aliases = {ref: alias for alias, ref in ctx.alias_to_ref.items()}
    return CVWritingGeneration(profile_summary=dict(text='Qualified actuary.', source_refs=[aliases['profile_claim:fellow']]),
        experience_bullets=[dict(text='Restated model.', source_refs=[aliases[r] for r in refs])] if refs else [])


@pytest.mark.parametrize('with_content', [True, False])
def test_sparse_writer_cannot_remove_any_employment_and_needs_no_retry(with_content):
    b = bundle()
    b.career_episodes += [dict(id=f'older-{i}', episode_type='employment', organisation=f'Company {i}',
                              start_date=f'{1990+i}-01-01', end_date=f'{1990+i}-12-31') for i in range(20)]
    ctx = context(b)
    payload = writing(ctx, refs=('profile_claim:restatement',) if with_content else ())
    with patch.object(artifacts, 'run_json_task', return_value=SimpleNamespace(output=payload)) as ai:
        _, content = artifacts._generate_validated('cv', ctx)
    assert ai.call_count == 1
    assert ai.call_args.kwargs['output_model'] is CVWritingGeneration
    assert len(content['experience']) == 22
    assert [e['episode_id'] for e in content['experience']][:2] == ['scor', 'irish']
    assert 'nested' not in {e['episode_id'] for e in content['experience']}
    assert content['omitted_experience'] == []
    assert content['experience'][0]['bullets'] == []
    assert bool(content['experience'][1]['bullets']) == with_content
    assert any('SCOR: chronology only' in w for w in content['review_warnings'])


def test_source_ancestry_assigns_bullets_and_deduplicates_without_mutating_writer():
    ctx = context()
    payload = writing(ctx)
    aliases = {r: a for a, r in ctx.alias_to_ref.items()}
    payload.experience_bullets += [payload.experience_bullets[0].model_copy(deep=True)]
    payload.experience_bullets.append(CVWrittenBlock(text='Validated migration.', source_refs=[aliases['profile_claim:migration']]))
    original = payload.model_dump()
    result = artifacts._validate_and_shape('cv', payload, ctx)
    assert payload.model_dump() == original
    assert [e['episode_id'] for e in result['experience']] == ['scor', 'irish']
    assert result['experience'][0]['bullets'][0]['source_refs'] == ['profile_claim:migration']
    assert result['experience'][1]['bullets'] == [dict(text='Restated model.', source_refs=['profile_claim:restatement'])]


@pytest.mark.parametrize('field,value', [('experience', []), ('omitted_experience', []), ('episode_id', 'E001')])
def test_writer_has_no_authority_over_document_structure(field, value):
    payload = writing(context()).model_dump()
    payload[field] = value
    with pytest.raises(ValidationError, match='Extra inputs'):
        CVWritingGeneration.model_validate(payload)


def test_writer_cannot_smuggle_placement_into_a_bullet():
    payload = writing(context()).model_dump()
    payload['experience_bullets'][0]['episode_id'] = 'E001'
    with pytest.raises(ValidationError, match='Extra inputs'):
        CVWritingGeneration.model_validate(payload)


@pytest.mark.parametrize('refs', [
    ('profile_claim:restatement', 'profile_claim:migration'),
    ('profile_claim:restatement', 'profile_claim:fellow'),
    ('profile_claim:fellow',),
])
def test_ambiguous_and_unlinked_bullets_are_not_guessed_or_dropped(refs):
    ctx = context()
    with pytest.raises(artifacts.CVAttributionValidationError, match='single employment'):
        artifacts._validate_and_shape('cv', writing(ctx, refs), ctx)


def test_invalid_writer_preserves_existing_draft_after_bounded_retry():
    ctx = context()
    bad = writing(ctx, ('profile_claim:restatement', 'profile_claim:migration'))
    with patch.object(artifacts, 'db_cursor') as db, \
         patch.object(artifacts, 'build_application_generation_context', return_value=ctx), \
         patch.object(artifacts, 'run_json_task', return_value=SimpleNamespace(output=bad)) as ai, \
         patch.object(artifacts, '_insert') as insert:
        with pytest.raises(artifacts.CVAttributionValidationError):
            artifacts.generate_artifact('app', 'cv')
    assert ai.call_count == 2
    assert db.call_count == 1  # read context only, no persistence transaction
    insert.assert_not_called()


def test_grounding_repair_cannot_change_the_timeline():
    ctx = context()
    bad = writing(ctx, ('profile_claim:restatement', 'profile_claim:migration'))
    good = writing(ctx, ())
    with patch.object(artifacts, 'run_json_task', side_effect=[SimpleNamespace(output=bad), SimpleNamespace(output=good)]) as ai:
        _, result = artifacts._generate_validated('cv', ctx)
    assert ai.call_count == 2
    assert [e['episode_id'] for e in result['experience']] == ['scor', 'irish']
    assert all(e['bullets'] == [] for e in result['experience'])


def test_no_employment_records_produces_empty_structure_and_unlinked_warning():
    b = bundle()
    b.career_episodes = [dict(id='orphan', episode_type='project')]
    result = artifacts._validate_and_shape('cv', writing(context(b), ()), context(b))
    assert result['experience'] == []
    assert any('no valid employment link' in w for w in result['review_warnings'])


def test_manual_edit_preserves_explicit_omission_and_restores_unexplained_removal():
    episodes = bundle().career_episodes
    original = dict(experience=[], omitted_experience=[])
    assert len(complete_cv_structure(original, episodes)['experience']) == 2
    explicit = dict(experience=[], omitted_experience=[dict(episode_id='scor', reason='Shortened CV')])
    result = complete_cv_structure(explicit, episodes)
    assert [e['episode_id'] for e in result['experience']] == ['irish']
    assert result['omitted_experience'] == explicit['omitted_experience']
    assert original['experience'] == []


@pytest.mark.parametrize('reason', ['', '  '])
def test_manual_omission_requires_a_real_reason(reason):
    with pytest.raises(ValueError, match='specific reason'):
        complete_cv_structure(dict(omitted_experience=[dict(episode_id='scor', reason=reason)]), bundle().career_episodes)


def test_order_is_stable_for_duplicate_employers_and_missing_dates():
    episodes = [dict(id='b', organisation='Same', episode_type='employment'),
                dict(id='a', organisation='Same', episode_type='employment'),
                dict(id='current', episode_type='employment', start_date='2025-01-01')]
    original = deepcopy(episodes)
    assert [e['id'] for e in employment_timeline(episodes)] == ['current', 'a', 'b']
    assert employment_timeline(episodes[::-1]) == employment_timeline(episodes)
    assert episodes == original


def test_pipeline_upgrade_stales_cv_only():
    def fingerprints():
        return {kind: generation.compute_fingerprint_for(bundle(), artifact_type=kind, active_positioning=None, guidance=None)
                for kind in ('cv', 'positioning', 'cover_letter')}
    current = fingerprints()
    with patch.object(generation, 'CV_PIPELINE_VERSION', 2):
        previous = fingerprints()
    assert current['cv'] != previous['cv']
    assert current['positioning'] == previous['positioning']
    assert current['cover_letter'] == previous['cover_letter']
