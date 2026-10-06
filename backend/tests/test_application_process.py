from uuid import uuid4
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app import db, application_generation as gen, application_artifacts as artifacts
from tests.test_application_mode import seed
from tests.test_application_artifacts import _concept, _legacy_observation


def opportunity_payload(view, **changes):
    return {'revision': view['preparation']['revision'], 'target_revision': view['target_revision'],
            'metadata': {}, 'deadline': None, 'package_items': [], 'confirm': True, **changes}


def test_opportunity_requirement_checkpoints_resume_and_invalidation(client):
    app, concept, _ = seed(client)
    base = f'/api/applications/{app}/process'
    initial = client.get(base).json()
    assert initial['stages']['opportunity'] == 'not_started'
    draft = client.put(base + '/opportunity', json=opportunity_payload(initial, confirm=False))
    assert draft.status_code == 200, draft.text
    initial = draft.json()
    assert initial['stages']['opportunity'] == 'in_progress'
    package = [{'id': str(uuid4()), 'kind': 'question', 'label': 'Why this role?', 'required': True,
                'word_limit': 300, 'instructions': 'Explain motivation'}]
    body = opportunity_payload(initial, metadata={'organisation': 'Corrected Employer'}, package_items=package)
    response = client.put(base + '/opportunity', json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['stages']['opportunity'] == 'complete'
    assert saved['role']['organisation'] == 'Corrected Employer'
    assert saved['preparation']['package_items'] == package
    assert client.put(base + '/opportunity', json=body).status_code == 409
    confirmed = client.put(base + '/requirements', json={'fingerprint': saved['requirements_fingerprint']})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['stages']['requirements'] == 'complete'
    assert client.put(base + '/resume', json={'stage': 'evidence', 'concept_id': concept}).status_code == 200
    assert client.get(base).json()['resume']['concept_id'] == concept
    with db.db_cursor() as cur:
        cur.execute('UPDATE jobber.role_instance SET organisation=%s WHERE id=%s', ('Changed elsewhere', initial['role']['id']))
    changed = client.get(base).json()
    assert changed['stages']['opportunity'] == 'needs_attention'
    assert changed['stages']['requirements'] == 'needs_attention'
    assert changed['stages']['evidence'] == 'needs_attention'
    assert client.put(base + '/requirements', json={'fingerprint': saved['requirements_fingerprint']}).status_code == 409


def test_process_excludes_legacy_and_keeps_old_decisions(client):
    app, concept, _ = seed(client)
    base = f'/api/applications/{app}/process'
    view = client.get(base).json()
    with db.db_cursor() as cur:
        legacy = _concept(cur, 'Legacy signal')
        _legacy_observation(cur, view['role']['id'], legacy, 'Legacy signal')
    view = client.get(base).json()
    assert view['legacy_requirement_count'] == 1
    assert [i['concept']['id'] for i in view['items']] == [concept]
    assert client.put(base + '/resume', json={'stage': 'evidence', 'concept_id': legacy}).status_code == 404
    client.put(base + '/resume', json={'stage': 'evidence', 'concept_id': concept})
    with db.db_cursor() as cur:
        cur.execute("UPDATE jobber.requirement_claim SET review_status='rejected' WHERE role_instance_id=%s AND concept_id=%s", (view['role']['id'], concept))
    assert client.get(base).json()['resume']['concept_id'] is None


def test_process_generation_requires_checkpoints_and_evidence(client):
    app, concept, _ = seed(client)
    base = f'/api/applications/{app}/process'
    view = client.get(base).json()
    client.put(base + '/opportunity', json=opportunity_payload(view))
    with db.db_cursor() as cur, pytest.raises(HTTPException):
        gen.build_application_generation_context(cur, app, artifact_type='positioning')
    view = client.get(base).json()
    client.put(base + '/requirements', json={'fingerprint': view['requirements_fingerprint']})
    with db.db_cursor() as cur, pytest.raises(HTTPException):
        gen.build_application_generation_context(cur, app, artifact_type='positioning')
    item = view['items'][0]
    source = item['sources'][0]
    assert client.put(f'/api/applications/{app}/mode/evidence/{concept}', json={
        'disposition': 'covered', 'selected_refs': [source['ref']],
        'source_revisions': {source['ref']: source['source_revision']},
        'requirement_fingerprint': item['requirement_fingerprint']}).status_code == 200
    with db.db_cursor() as cur:
        context = gen.build_application_generation_context(cur, app, artifact_type='positioning')
        before = context.input_fingerprint
        assert context.category_for_ref(f'application_package:{app}') == gen.CATEGORY_ROLE_SIDE
        artifacts._insert(cur, application_id=app, artifact_type='positioning', status='active', origin='ai',
            model='test', prompt_name='test', prompt_version='1', guidance=None, input_fingerprint=before,
            source_manifest=context.source_manifest(), content={}, raw_output={}, now=datetime.now(timezone.utc))
    view = client.get(base).json()
    assert view['downstream']['positioning'] == 'complete'
    client.put(base + '/opportunity', json=opportunity_payload(view, deadline='2026-12-01'))
    with db.db_cursor() as cur:
        context = gen.build_application_generation_context(cur, app, artifact_type='positioning')
        assert context.input_fingerprint != before
        assert '2026-12-01' in context.prompt_text
    changed = client.get(base).json()
    assert changed['stages']['evidence'] == 'complete'
    assert changed['downstream']['positioning'] == 'needs_attention'
    with db.db_cursor() as cur:
        cur.execute("UPDATE jobber.requirement_claim SET requirement_type='preferred' WHERE role_instance_id=%s AND concept_id=%s", (view['role']['id'], concept))
    changed = client.get(base).json()
    assert changed['stages']['requirements'] == 'needs_attention'
    assert changed['stages']['evidence'] == 'needs_attention'
    assert changed['downstream']['positioning'] == 'needs_attention'


def test_scoped_vocabulary_reuses_resolution_and_rejects_unrelated_proposals(client):
    app, _, _ = seed(client)
    other, _, _ = seed(client)
    base = f'/api/applications/{app}/process'
    role = client.get(base).json()['role']['id']
    with db.db_cursor() as cur:
        document, _ = db.create_document(cur, kind='job_posting', content_text='Advanced modelling required.', provenance_quality='original')
        cur.execute('UPDATE jobber.role_instance SET document_id=%s WHERE id=%s', (document, role))
        cur.execute("INSERT INTO jobber.concept_proposal(surface_form,suggested_type) VALUES ('advanced modelling','tool') RETURNING id")
        proposal = str(cur.fetchone()['id'])
        cur.execute('INSERT INTO jobber.concept_proposal_occurrence '
                    '(concept_proposal_id,role_instance_id,document_id,basis,evidence_span,requirement_type) '
                    "VALUES (%s,%s,%s,'stated','Advanced modelling required.','required')", (proposal, role, document))
    item = client.get(base + '/vocabulary').json()['items'][0]
    body = {'revision': item['revision'], 'action': 'accept_new', 'type_code': 'tool', 'canonical_name': 'Advanced modelling'}
    assert client.post(f'/api/applications/{other}/process/vocabulary/{proposal}', json=body).status_code == 404
    assert client.post(base + f'/vocabulary/{proposal}', json={**body, 'revision': 'old'}).status_code == 409
    accepted = client.post(base + f'/vocabulary/{proposal}', json=body)
    assert accepted.status_code == 200, accepted.text
    claims = client.get(f'/api/role-instances/{role}/requirements').json()['items']
    created = next(c for c in claims if c['concept_id'] == accepted.json()['concept_id'])
    assert created['review_status'] == 'unreviewed'
    assert client.get(base + '/vocabulary').json()['items'] == []


def test_package_validation_and_process_auth(client, anon_client):
    app, _, _ = seed(client)
    base = f'/api/applications/{app}/process'
    view = client.get(base).json()
    assert anon_client.get(base).status_code == 401
    assert client.put(base + '/opportunity', json=opportunity_payload(view, deadline='invalid')).status_code == 422
    assert client.put(base + '/requirements', json={'fingerprint': 'outdated'}).status_code == 409
