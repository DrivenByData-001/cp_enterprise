from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import ai
from app.evidence_discovery import DiscoveryOutput, Proposal, Review, batch_sources, validate_proposals


def proposal(claim, concept, **overrides):
    return Proposal(claim_id=claim, concept_id=concept, rationale='Traced an incorrect reserve to model logic',
                    limitations='No final sign-off', question='', depth='owned', autonomy='independent', **overrides)


def test_rejects_unknown_sources_and_concepts_before_saving_batch():
    claim, concept = str(uuid4()), str(uuid4())
    sources = [{'data': {'claim': {'id': claim}}}]
    output = DiscoveryOutput(findings=[proposal(claim, concept), proposal(str(uuid4()), concept)])
    with pytest.raises(ValueError):
        list(validate_proposals(output, sources, {concept: {}}))
    with pytest.raises(ValueError):
        list(validate_proposals(DiscoveryOutput(findings=[proposal(claim, concept)]), sources, {}))


def test_multiple_capabilities_allowed_duplicate_pairs_removed():
    claim, c1, c2 = [str(uuid4()) for _ in range(3)]
    output = DiscoveryOutput(findings=[proposal(claim,c1),proposal(claim,c1),proposal(claim,c2)])
    assert len(list(validate_proposals(output, [{'data': {'claim': {'id': claim}}}], {c1: {},c2: {}}))) == 2


def test_batches_preserve_recent_first_order_and_do_not_truncate():
    sources = [{'id': i, 'text': 'x'*30} for i in range(5)]
    batches = list(batch_sources(sources, 100))
    assert [s for b in batches for s in b] == sources
    with pytest.raises(ValueError):
        list(batch_sources([{'text': 'x'*101}], 100))


def test_reviews_require_meaningful_rationale_and_known_levels():
    with pytest.raises(ValidationError):
        Review(action='approve', revision=1, rationale='  ')
    with pytest.raises(ValidationError):
        Review(action='approve', revision=1, rationale='Checked', autonomy='senior')
    assert Review(action='approve', revision=1, rationale='Checked').depth is None


def test_model_default_and_override(monkeypatch):
    monkeypatch.delenv('CP_AI_MODEL', raising=False)
    assert ai.ai_model_name() == 'gpt-5.4-mini'
    monkeypatch.setenv('CP_AI_MODEL',' custom-model ')
    assert ai.ai_model_name() == 'custom-model'


@pytest.mark.parametrize('finish,refusal', [('length',None),('content_filter',None),('stop','Cannot comply')])
def test_incomplete_or_refused_response_never_accepted(monkeypatch, finish, refusal):
    monkeypatch.setattr(ai, 'load_prompt', lambda _: 'JSON')
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        message=SimpleNamespace(content='{"findings": []}', refusal=refusal))])
    monkeypatch.setattr(ai, '_client', lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))))
    with pytest.raises(ai.AIResponseFormatError):
        ai.run_json_task(task='test', prompt_name='test', user_input='', output_model=DiscoveryOutput)


def test_discovery_uses_strict_schema(monkeypatch):
    captured = {}
    def call(**kw):
        captured.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{"findings": []}',refusal=None))])
    monkeypatch.setattr(ai,'load_prompt',lambda _: 'JSON')
    monkeypatch.setattr(ai,'_client',lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=call))))
    ai.run_json_task(task='test',prompt_name='test',user_input='',output_model=DiscoveryOutput,structured=True)
    assert captured['response_format']['json_schema']['strict']
    assert captured['response_format']['json_schema']['schema']['additionalProperties'] is False
