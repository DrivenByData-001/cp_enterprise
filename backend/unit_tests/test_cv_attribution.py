from types import SimpleNamespace
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.application_artifacts import ApplicationArtifactValidationError, _validate_and_shape
from app.application_generation import (
    SourceEntry, CATEGORY_CANONICAL_EVIDENCE, _curated_requirement_sources,
    render_prompt_text, GenerationContext, assign_source_aliases,
)
from app.models import ApplicationCVGeneration, CVExperienceEntry, CVExperienceBullet, SourcedText


def context():
    sources = [
        SourceEntry('claim:scor', 'profile_claim', 'Migration', CATEGORY_CANONICAL_EVIDENCE,
                    'AXIS to Prophet validation', episode_id='scor'),
        SourceEntry('claim:metlife', 'profile_claim', 'Presentation', CATEGORY_CANONICAL_EVIDENCE,
                    'Vibe Coding', episode_id='metlife'),
        SourceEntry('profile_episode:metlife', 'profile_episode', 'MetLife', CATEGORY_CANONICAL_EVIDENCE,
                    'Chronology only', episode_id='metlife', chronology_only=True),
        SourceEntry('profile_episode:scor', 'profile_episode', 'SCOR', CATEGORY_CANONICAL_EVIDENCE,
                    'Chronology only', episode_id='scor', chronology_only=True),
        SourceEntry('claim:qualification', 'profile_claim', 'Qualified', CATEGORY_CANONICAL_EVIDENCE,
                    'Qualified in 2020'),
    ]
    return GenerationContext(bundle=SimpleNamespace(episodes=[{'id':'scor'}, {'id':'metlife'}]),
                             artifact_type='cv', active_positioning=None, sources=sources,
                             guidance=None, input_fingerprint='test', alias_to_ref=assign_source_aliases(sources))


def cv(refs, episode='metlife'):
    aliases = {ref: alias for alias, ref in context().alias_to_ref.items()}
    return ApplicationCVGeneration(
        profile_summary=SourcedText(text='Qualified actuary.', source_refs=[aliases['claim:qualification']]),
        experience=[CVExperienceEntry(episode_id=aliases['profile_episode:' + episode], bullets=[CVExperienceBullet(text='Work.', source_refs=[aliases[r] for r in refs])])],
        skills=[], omissions_or_cautions=[])


@pytest.mark.parametrize('refs', [
    ['claim:scor'],
    ['claim:scor', 'profile_episode:metlife'],
    ['claim:scor', 'claim:metlife'],
    ['claim:qualification', 'profile_episode:metlife'],
])
def test_cv_rejects_cross_employer_or_unassigned_evidence(refs):
    with pytest.raises(ApplicationArtifactValidationError, match='employment attribution|employment episode'):
        _validate_and_shape('cv', cv(refs), context())


def test_curated_chronology_cannot_launder_an_achievement():
    with pytest.raises(ApplicationArtifactValidationError, match='chronology only'):
        _validate_and_shape('cv', cv(['profile_episode:metlife']), context())


def test_correct_employer_and_unassigned_qualification_in_summary_are_valid():
    result = _validate_and_shape('cv', cv(['claim:metlife', 'profile_episode:metlife']), context())
    assert result['experience'][0]['episode_id'] == 'metlife'


def test_curated_claim_episode_survives_into_prompt_and_manifest():
    sources = _curated_requirement_sources(dict(concept_id='communication', canonical_name='Communication',
        application_decision=dict(disposition='partial', rationale='Internal presentation.', sources=[
            dict(ref='claim:metlife',kind='profile_claim',label='Presentation',content='Vibe Coding',episode_id='metlife')
        ])))
    bundle = SimpleNamespace(role={'title':'Actuary','organisation':'Target'}, episodes=[{'id': 'metlife'}])
    sources.append(SourceEntry('profile_episode:metlife', 'profile_episode', 'MetLife', CATEGORY_CANONICAL_EVIDENCE,
                               'Chronology only', episode_id='metlife', chronology_only=True))
    prompt = render_prompt_text(bundle, sources, artifact_type='cv', guidance=None)
    aliases = {ref: alias for alias, ref in assign_source_aliases(sources).items()}
    assert 'SOURCE_EPISODE_ID: ' + aliases['profile_episode:metlife'] in prompt
    assert 'SOURCE_EPISODE_ID: metlife' not in prompt
    assert sources[0].to_public()['episode_id'] == 'metlife'
