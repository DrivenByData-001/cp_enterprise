"""Database integration for the application-owned evidence slice."""
from app import db
from app import application_generation as gen
from fastapi import HTTPException
import pytest
from uuid import uuid4
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


def acceptance_payload(**overrides):
    return {'operation_id': str(uuid4()), 'claim_text': 'Led the reviewed model challenge',
            'reason': 'Checked against my project notes', 'confirmed': True, **overrides}


def test_accepted_addition_is_reusable_audited_and_idempotent(client):
    app, concept, _ = seed(client)
    base = f'/api/applications/{app}/mode'
    payload = acceptance_payload()
    url = f'{base}/evidence/{concept}/accept-claim'
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted['status'] == 'accepted'
    assert client.post(url, json=payload).json() == accepted
    assert client.post(url, json={**payload, 'claim_text': 'Different text'}).status_code == 409
    item = client.get(base).json()['items'][0]
    source = accepted['source']
    assert source['ref'] in [s['ref'] for s in item['sources']]
    # Acceptance does not invent an application coverage decision.
    assert item['decision'] is None
    saved = client.put(f'{base}/evidence/{concept}', json={
        'disposition': 'covered', 'selected_refs': [source['ref']],
        'source_revisions': {source['ref']: source['source_revision']},
        'requirement_fingerprint': item['requirement_fingerprint']})
    assert saved.status_code == 200, saved.text
    with db.db_cursor() as cur:
        ctx = gen.build_application_generation_context(cur, app, artifact_type='positioning')
        assert source['ref'] in ctx.known_source_refs()
        assert payload['claim_text'] in ctx.prompt_text
        cur.execute('SELECT * FROM jobber.profile360_acceptance')
        receipts = cur.fetchall()
        assert len(receipts) == 1
        assert receipts[0]['before_state'] is None
        assert receipts[0]['accepted_by'] == 'authenticated_operator'
        assert receipts[0]['after_state']['evidence_class'] == 'user_asserted'
        cur.execute('SELECT count(*) AS n FROM profile360.evidence WHERE locator = %s',
                    ('cp_enterprise_acceptance:' + payload['operation_id'],))
        assert cur.fetchone()['n'] == 1


def test_correction_preserves_history_and_invalidates_other_reviews(client):
    app, concept, claim = seed(client)
    base = f'/api/applications/{app}/mode'
    item = client.get(base).json()['items'][0]
    source = item['sources'][0]
    assert client.put(f'{base}/evidence/{concept}', json={
        'disposition': 'covered', 'selected_refs': [source['ref']],
        'source_revisions': {source['ref']: source['source_revision']},
        'requirement_fingerprint': item['requirement_fingerprint']}).status_code == 200
    context = client.get(f'{base}/evidence/{concept}/claim-context?claim_id={claim}').json()
    payload = acceptance_payload(claim_id=claim, source_revision=context['claim']['source_revision'])
    url = f'{base}/evidence/{concept}/accept-claim'
    accepted = client.post(url, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert client.get(base).json()['items'][0]['stale']
    assert client.post(url, json={**payload, 'operation_id': str(uuid4())}).status_code == 409
    with db.db_cursor() as cur:
        cur.execute('SELECT before_state, after_state FROM jobber.profile360_acceptance')
        row = cur.fetchone()
        assert row['before_state']['claim_text'] == 'Challenged internal model assumptions'
        assert row['after_state']['claim_text'] == payload['claim_text']
        assert row['after_state']['evidence_class'] == 'mixed'


def test_acceptance_validation_scope_and_rollback(client):
    app, concept, _ = seed(client)
    base = f'/api/applications/{app}/mode/evidence'
    payload = acceptance_payload()
    assert client.post(f'{base}/{uuid4()}/accept-claim', json=payload).status_code == 404
    url = f'{base}/{concept}/accept-claim'
    assert client.post(url, json={**payload, 'confirmed': False}).status_code == 422
    assert client.post(url, json={**payload, 'claim_text': '   '}).status_code == 422
    assert client.post(url, json={**payload, 'episode_id': str(uuid4())}).status_code == 409
    # A failure writing provenance must roll back the canonical change too.
    with db.db_cursor() as cur:
        cur.execute("ALTER TABLE profile360.evidence ADD CONSTRAINT test_failure CHECK (evidence_type <> 'user_asserted')")
    try:
        with pytest.raises(Exception):
            client.post(url, json=payload)
        with db.db_cursor() as cur:
            cur.execute('SELECT count(*) AS n FROM jobber.profile360_acceptance')
            assert cur.fetchone()['n'] == 0
            cur.execute('SELECT count(*) AS n FROM profile360.claims WHERE claim_text = %s', (payload['claim_text'],))
            assert cur.fetchone()['n'] == 0
    finally:
        with db.db_cursor() as cur:
            cur.execute('ALTER TABLE profile360.evidence DROP CONSTRAINT test_failure')
