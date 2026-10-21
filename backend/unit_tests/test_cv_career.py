from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from app.application_artifacts import _resolve_cv_content, _validate_and_shape, ApplicationArtifactValidationError
from app.application_generation import (EvidenceBundle, GenerationContext, build_source_registry,
    compute_fingerprint_for, render_prompt_text, assign_source_aliases)
from app.cv_career import employment_id, review_warnings
from app.models import ApplicationCVGeneration
from app import application_artifacts as artifacts


def bundle():
    episodes = [
        dict(id='irish', episode_type='employment', organisation='Irish Life', start_date='2016-11-01', end_date='2018-08-31'),
        dict(id='scor', episode_type='employment', organisation='SCOR', start_date='2018-09-01', end_date='2019-06-30'),
        dict(id='ulp', episode_type='project', parent_episode_id='irish'),
        dict(id='nested', episode_type='interview_episode', parent_episode_id='ulp'),
    ]
    claims = [dict(id='migration', episode_id='scor', claim_text='Validated migration', evidence_class='stated', confidence='high'),
              dict(id='restatement', episode_id='nested', claim_text='Restated model', evidence_class='mixed', confidence='moderate', uncertainty='No sign-off authority'),
              dict(id='fellow', episode_id=None, claim_text='Fellow of IFoA', evidence_class='user_asserted')]
    return EvidenceBundle('app', 'role', {}, {}, {'counts': {}}, [], [], [], None, [episodes[0]],
                          curated=True, career_episodes=episodes, career_claims=claims)


def context(b=None):
    b = b or bundle()
    sources = build_source_registry(b, artifact_type='cv', active_positioning=None)
    return GenerationContext(b, 'cv', None, sources, None, 'test', alias_to_ref=assign_source_aliases(sources))


def output(ctx, *, include_scor=True):
    aliases = {ref: alias for alias, ref in ctx.alias_to_ref.items()}
    data = dict(profile_summary=dict(text='Life actuary', source_refs=[aliases['profile_claim:fellow']]),
                experience=[dict(episode_id=aliases['profile_episode:irish'], bullets=[dict(text='Restated model', source_refs=[aliases['profile_claim:restatement']])])],
                qualifications=[dict(text='Fellow of IFoA', source_refs=[aliases['profile_claim:fellow']])])
    if include_scor:
        data['experience'].append(dict(episode_id=aliases['profile_episode:scor'], bullets=[dict(text='Validated migration', source_refs=[aliases['profile_claim:migration']])]))
    return data


def test_curated_cv_includes_unselected_employment_claims_with_limits():
    ctx = context()
    refs = {s.ref: s for s in ctx.sources}
    assert 'profile_episode:scor' in refs
    assert 'profile_claim:migration' in refs
    project = refs['profile_claim:restatement']
    assert project.episode_id == 'nested'
    assert project.employment_episode_id == 'irish'
    assert project.category == 'partial_evidence'
    assert 'No sign-off authority' in project.content
    assert refs['profile_claim:fellow'].category == 'user_supplied_context'
    prompt = render_prompt_text(ctx.bundle, ctx.sources, artifact_type='cv', guidance=None)
    assert 'Employment to account for:' in prompt
    assert 'SCOR' in prompt
    aliases = {ref: alias for alias, ref in ctx.alias_to_ref.items()}
    assert f"Achievement sources allowed for {aliases['profile_episode:scor']}: {aliases['profile_claim:migration']}" in prompt
    assert len(ctx.source_manifest()) == len({s.ref for s in ctx.sources})


def test_other_artifacts_remain_curated():
    sources = build_source_registry(bundle(), artifact_type='positioning', active_positioning=None)
    assert 'profile_claim:migration' not in {s.ref for s in sources}
    assert 'profile_episode:scor' not in {s.ref for s in sources}


def test_nested_project_attribution_and_qualification_are_valid():
    ctx = context()
    result = _validate_and_shape('cv', ApplicationCVGeneration(**output(ctx)), ctx)
    assert result['experience'][0]['episode_id'] == 'irish'
    assert result['experience'][0]['bullets'][0]['source_refs'] == ['profile_claim:restatement']
    assert result['qualifications'][0]['text'] == 'Fellow of IFoA'


def test_silent_employer_omission_rejected_but_reason_allowed():
    ctx = context()
    data = output(ctx, include_scor=False)
    with pytest.raises(ApplicationArtifactValidationError, match='account for every employment'):
        _validate_and_shape('cv', ApplicationCVGeneration(**data), ctx)
    aliases = {ref: alias for alias, ref in ctx.alias_to_ref.items()}
    data['omitted_experience'] = [dict(episode_id=aliases['profile_episode:scor'], reason='Deliberately shortened version')]
    result = _validate_and_shape('cv', ApplicationCVGeneration(**data), ctx)
    assert any('SCOR: employment omitted' in w for w in result['review_warnings'])


@pytest.mark.parametrize('repair', ['achievements', 'chronology', 'omission'])
def test_missing_employment_gets_one_complete_revalidated_correction(repair):
    ctx = context()
    bad = ApplicationCVGeneration(**output(ctx, include_scor=False))
    corrected = output(ctx, include_scor=repair != 'omission')
    aliases = {ref: alias for alias, ref in ctx.alias_to_ref.items()}
    if repair == 'chronology':
        corrected['experience'][1]['bullets'] = []
    elif repair == 'omission':
        corrected['omitted_experience'] = [dict(episode_id=aliases['profile_episode:scor'],
                                               reason='Shortened CV; earlier migration role omitted.')]
    with patch.object(artifacts, 'run_json_task', side_effect=[
        SimpleNamespace(output=bad), SimpleNamespace(output=ApplicationCVGeneration(**corrected)),
    ]) as ai:
        _, result = artifacts._generate_validated('cv', ctx)
    assert ai.call_count == 2
    feedback = ai.call_args.kwargs['user_input'].split('APPLICATION VALIDATION FEEDBACK:')[1].split('PREVIOUS REJECTED OUTPUT')[0]
    assert aliases['profile_episode:scor'] in feedback
    assert aliases['profile_episode:irish'] not in feedback  # only the missing employment
    assert 'empty bullets' in feedback
    assert 'omitted_experience' in feedback
    assert 'Never invent' in feedback
    assert {e['episode_id'] for e in result['experience'] + result['omitted_experience']} == {'irish', 'scor'}


def test_repeated_missing_employment_preserves_previous_draft_without_writes():
    ctx = context()
    bad = ApplicationCVGeneration(**output(ctx, include_scor=False))
    with patch.object(artifacts, 'db_cursor') as db, \
         patch.object(artifacts, 'build_application_generation_context', return_value=ctx), \
         patch.object(artifacts, 'run_json_task', return_value=SimpleNamespace(output=bad)) as ai, \
         patch.object(artifacts, '_insert') as insert:
        with pytest.raises(ApplicationArtifactValidationError, match='SCOR') as failure:
            artifacts.generate_artifact('app', 'cv')
    assert 'previous draft' in str(failure.value)
    assert ai.call_count == 2
    assert db.call_count == 1
    insert.assert_not_called()


def test_coverage_repair_still_rejects_misattributed_evidence_without_a_third_attempt():
    ctx = context()
    missing = ApplicationCVGeneration(**output(ctx, include_scor=False))
    misattributed = ApplicationCVGeneration(**output(ctx))
    misattributed.experience[1].bullets = misattributed.experience[0].bullets
    with patch.object(artifacts, 'db_cursor') as db, \
         patch.object(artifacts, 'build_application_generation_context', return_value=ctx), \
         patch.object(artifacts, 'run_json_task', side_effect=[
             SimpleNamespace(output=missing), SimpleNamespace(output=misattributed),
         ]) as ai, patch.object(artifacts, '_insert') as insert:
        with pytest.raises(ApplicationArtifactValidationError, match='SCOR.*employment episode'):
            artifacts.generate_artifact('app', 'cv')
    assert ai.call_count == 2
    assert db.call_count == 1
    insert.assert_not_called()


def test_project_cannot_move_to_unrelated_employer():
    ctx = context()
    data = output(ctx)
    data['experience'][1]['bullets'] = data['experience'][0]['bullets']
    with pytest.raises(ApplicationArtifactValidationError, match='employment episode'):
        _validate_and_shape('cv', ApplicationCVGeneration(**data), ctx)


def test_boundary_claim_is_available_as_a_limit_but_cannot_substantiate_achievement():
    b = bundle()
    b.career_claims[0]['cv_disposition'] = 'boundary'
    ctx = context(b)
    source = next(s for s in ctx.sources if s.ref == 'profile_claim:migration')
    assert source.limitations_only
    assert 'LIMITATION ONLY' in source.content
    with pytest.raises(ApplicationArtifactValidationError, match='limitations only'):
        _validate_and_shape('cv', ApplicationCVGeneration(**output(ctx)), ctx)


def test_duplicate_employment_rejected():
    ctx = context()
    data = output(ctx)
    data['experience'].append(data['experience'][0])
    with pytest.raises(ApplicationArtifactValidationError, match='must be unique'):
        _validate_and_shape('cv', ApplicationCVGeneration(**data), ctx)


def test_attribution_failure_gets_one_revalidated_correction_attempt():
    ctx = context()
    good = ApplicationCVGeneration(**output(ctx))
    bad = good.model_copy(deep=True)
    bad.experience[1].bullets = bad.experience[0].bullets
    with patch.object(artifacts, 'run_json_task', side_effect=[SimpleNamespace(output=bad), SimpleNamespace(output=good)]) as ai:
        _, result = artifacts._generate_validated('cv', ctx)
    assert ai.call_count == 2
    assert 'APPLICATION VALIDATION FEEDBACK' in ai.call_args.kwargs['user_input']
    assert 'Do not merely remove a citation' in ai.call_args.kwargs['user_input']
    assert result['experience'][1]['bullets'][0]['source_refs'] == ['profile_claim:migration']


def test_repeated_attribution_failure_preserves_previous_draft_without_writes():
    ctx = context()
    bad = ApplicationCVGeneration(**output(ctx))
    bad.experience[1].bullets = bad.experience[0].bullets
    with patch.object(artifacts, 'db_cursor') as db, \
         patch.object(artifacts, 'build_application_generation_context', return_value=ctx), \
         patch.object(artifacts, 'run_json_task', return_value=SimpleNamespace(output=bad)) as ai, \
         patch.object(artifacts, '_insert') as insert:
        with pytest.raises(ApplicationArtifactValidationError, match='SCOR.*employment episode'):
            artifacts.generate_artifact('app', 'cv')
    assert ai.call_count == 2
    assert db.call_count == 1  # context read only, never the persistence transaction
    insert.assert_not_called()


def test_unknown_sources_are_not_repaired_or_silently_removed():
    ctx = context()
    bad = ApplicationCVGeneration(**output(ctx))
    bad.experience[0].bullets[0].source_refs = ['C999']
    with patch.object(artifacts, 'run_json_task', return_value=SimpleNamespace(output=bad)) as ai:
        with pytest.raises(ApplicationArtifactValidationError, match='unknown source alias'):
            artifacts._generate_validated('cv', ctx)
    assert ai.call_count == 1


def limitation_context():
    b = bundle()
    b.career_claims.append(dict(id='limit', claim_text='No final valuation sign-off authority',
                               evidence_class='stated', cv_disposition='boundary'))
    return context(b)


def test_limitations_render_as_constraints_without_citable_source_handle():
    ctx = limitation_context()
    prompt = render_prompt_text(ctx.bundle, ctx.sources, artifact_type='cv', guidance=None)
    alias = next(a for a, ref in ctx.alias_to_ref.items() if ref == 'profile_claim:limit')
    assert f'[SOURCE_REF: {alias}]' not in prompt
    assert 'No final valuation sign-off authority' in prompt
    assert 'LIMITATIONS — CONSTRAINTS ONLY' in prompt
    eligible = next(line for line in prompt.splitlines() if line.startswith('Eligible evidence handles'))
    assert alias not in eligible


@pytest.mark.parametrize('refs_kind', ['limitation', 'empty'])
def test_summary_grounding_failure_is_repaired_and_revalidated(refs_kind):
    ctx = limitation_context()
    good = ApplicationCVGeneration(**output(ctx))
    bad = good.model_copy(deep=True)
    alias = next(a for a, ref in ctx.alias_to_ref.items() if ref == 'profile_claim:limit')
    bad.profile_summary.source_refs = [alias] if refs_kind == 'limitation' else []
    with patch.object(artifacts, 'run_json_task', side_effect=[SimpleNamespace(output=bad), SimpleNamespace(output=good)]) as ai:
        _, result = artifacts._generate_validated('cv', ctx)
    assert ai.call_count == 2
    assert 'profile_summary' in ai.call_args.kwargs['user_input']
    assert result['profile_summary']['source_refs'] == ['profile_claim:fellow']


def test_repeated_limitation_only_summary_never_writes():
    ctx = limitation_context()
    bad = ApplicationCVGeneration(**output(ctx))
    bad.profile_summary.source_refs = [next(a for a, ref in ctx.alias_to_ref.items() if ref == 'profile_claim:limit')]
    with patch.object(artifacts, 'db_cursor') as db, \
         patch.object(artifacts, 'build_application_generation_context', return_value=ctx), \
         patch.object(artifacts, 'run_json_task', return_value=SimpleNamespace(output=bad)) as ai, \
         patch.object(artifacts, '_insert') as insert:
        with pytest.raises(ApplicationArtifactValidationError, match='limitations only'):
            artifacts.generate_artifact('app', 'cv')
    assert ai.call_count == 2
    assert db.call_count == 1
    insert.assert_not_called()


@pytest.mark.parametrize('episodes', [
    [dict(id='p', episode_type='project')],
    [dict(id='p', parent_episode_id='missing')],
    [dict(id='p', parent_episode_id='q'), dict(id='q', parent_episode_id='p')],
])
def test_unlinked_missing_or_cyclic_ancestry_fails_closed(episodes):
    assert employment_id('p', episodes) is None


def test_old_draft_projects_group_under_parent_and_sort_without_invented_dates():
    records = {e['id']: e for e in bundle().career_episodes}
    content = dict(experience=[dict(episode_id='irish', bullets=[dict(text='Employment', source_refs=[])]),
                              dict(episode_id='nested', bullets=[dict(text='Project', source_refs=['original'])]),
                              dict(episode_id='scor', bullets=[])])
    with patch('app.application_artifacts.p360.get_episode', side_effect=lambda cur, eid: records.get(eid)):
        result = _resolve_cv_content(None, content)
    assert [e['episode_id'] for e in result['experience']] == ['scor', 'irish']
    assert result['experience'][1]['start_date'] == '2016-11-01'
    assert result['experience'][1]['bullets'][1]['source_refs'] == ['original']
    assert records['nested'].get('start_date') is None
    assert len(content['experience']) == 3  # read projection never mutates stored drafts


def test_new_career_evidence_and_parent_edits_only_stale_cv():
    original = bundle()
    edited = replace(original, career_claims=[*original.career_claims, dict(id='new', claim_text='New fact')])
    moved = replace(original, career_episodes=[dict(e, parent_episode_id='scor') if e['id'] == 'ulp' else e for e in original.career_episodes])
    def fingerprint(b, kind):
        return compute_fingerprint_for(b, artifact_type=kind, guidance=None, active_positioning=None)
    for changed in (edited, moved):
        assert fingerprint(original, 'cv') != fingerprint(changed, 'cv')
        assert fingerprint(original, 'positioning') == fingerprint(changed, 'positioning')


def test_thin_duplicate_and_qualification_warnings():
    b = bundle()
    claims = [dict(id=str(i), episode_id='irish') for i in range(8)]
    content = dict(experience=[dict(episode_id='irish', bullets=[dict(text='Duplicate')])],
                   skills=[dict(text='Duplicate'), dict(text='Fellow of IFoA')])
    warnings = review_warnings(content, b.career_episodes, claims)
    assert any('thin coverage' in w for w in warnings)
    assert any('Repeated wording' in w for w in warnings)
    assert any('qualification' in w for w in warnings)
