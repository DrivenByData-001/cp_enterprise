"""Database integration for the application-owned evidence slice."""
from app import db
from app import application_generation as gen
from fastapi import HTTPException
import pytest
from tests.test_application_artifacts import _concept, _posting, _accepted_claim, _claim, _claim_mapping


def seed(client):
    with db.db_cursor() as cur:
        concept = _concept(cur, 'Model governance')
        role = _posting(cur)
        _accepted_claim(cur, role, concept)
        claim = _claim(cur, 'Challenged internal model assumptions')
        _claim_mapping(cur, claim, concept)
    app = client.post('/api/applications', json={'role_instance_id': role}).json()['id']
    return app, concept, claim


def test_review_resume_source_change_and_conflict(client):
    app, concept, claim = seed(client)
    url = f'/api/applications/{app}/mode'
    item = client.get(url).json()['items'][0]
    source = item['sources'][0]
    payload = {'disposition': 'covered', 'selected_refs': [source['ref']],
               'source_revisions': {source['ref']: source['source_revision']},
               'requirement_fingerprint': item['requirement_fingerprint'], 'revision': 0,
               'rationale': 'Use the challenge example'}
    result = client.put(f'{url}/evidence/{concept}', json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['revision'] == 1
    assert client.put(f'{url}/evidence/{concept}', json=payload).status_code == 409
    assert client.put(f'{url}/resume', json={'concept_id': concept}).status_code == 200
    assert client.get(url).json()['resume_concept_id'] == concept
    assert not client.get(url).json()['items'][0]['stale']
    with db.db_cursor() as cur:
        cur.execute('UPDATE profile360.claims SET claim_text = %s WHERE id = %s', ('Corrected career fact', claim))
    assert client.get(url).json()['items'][0]['stale']
    payload['revision'] = 1
    assert client.put(f'{url}/evidence/{concept}', json=payload).status_code == 409


def test_requirement_scope_and_search(client):
    app, concept, _ = seed(client)
    second, other_concept, _ = seed(client)
    assert client.put(f'/api/applications/{app}/mode/resume', json={'concept_id': other_concept}).status_code == 404
    assert client.get(f'/api/applications/{second}/mode/evidence/{concept}/search?q=model').status_code == 404
    found = client.get(f'/api/applications/{app}/mode/evidence/{concept}/search?q=assumptions')
    assert found.status_code == 200
    assert found.json()['sources']


def test_gap_does_not_mutate_shared_evidence(client):
    app, concept, claim = seed(client)
    url = f'/api/applications/{app}/mode'
    item = client.get(url).json()['items'][0]
    response = client.put(f'{url}/evidence/{concept}', json={'disposition': 'gap', 'selected_refs': [],
        'requirement_fingerprint': item['requirement_fingerprint'], 'rationale': 'This example is not sufficient'})
    assert response.status_code == 200, response.text
    with db.db_cursor() as cur:
        cur.execute('SELECT claim_text FROM profile360.claims WHERE id = %s', (claim,))
        assert cur.fetchone()['claim_text'] == 'Challenged internal model assumptions'


def test_generator_uses_selection_and_rejects_changed_review(client):
    app, concept, claim = seed(client)
    with db.db_cursor() as cur:
        other = _claim(cur, 'Unselected alternative example')
        _claim_mapping(cur, other, concept)
    url = f'/api/applications/{app}/mode'
    item = client.get(url).json()['items'][0]
    ref = 'profile_claim:' + claim
    source = next(s for s in item['sources'] if s['ref'] == ref)
    response = client.put(f'{url}/evidence/{concept}', json={
        'disposition': 'covered', 'selected_refs': [ref],
        'source_revisions': {ref: source['source_revision']},
        'requirement_fingerprint': item['requirement_fingerprint'], 'rationale': 'Choose this example'})
    assert response.status_code == 200, response.text
    with db.db_cursor() as cur:
        ctx = gen.build_application_generation_context(cur, app, artifact_type='positioning')
        assert ref in ctx.known_source_refs()
        assert 'profile_claim:' + other not in ctx.known_source_refs()
        assert 'Choose this example' in ctx.prompt_text
        cur.execute('UPDATE profile360.claims SET claim_text = %s WHERE id = %s', ('Changed fact', claim))
    # Artifact reads still work, but generating against the stale review does not.
    assert client.get(f'/api/applications/{app}/artifacts').status_code == 200
    with db.db_cursor() as cur, pytest.raises(HTTPException) as error:
        gen.build_application_generation_context(cur, app, artifact_type='positioning')
    assert error.value.status_code == 409
