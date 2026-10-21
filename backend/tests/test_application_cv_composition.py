"""Exercise the content-only writer through the real API and Postgres lifecycle."""
import json

import pytest

from alias_support import last_generation_context  # noqa: F401
from app import application_artifacts as artifacts, db
from app.models import CVWritingGeneration
from test_application_artifacts import _claim, _episode, _fake_run_json_task, _grounded_role


@pytest.mark.parametrize('with_bullet', [True, False])
def test_writer_composes_persists_edits_and_adopts_complete_cv(client, monkeypatch, with_bullet):
    with db.db_cursor() as cur:
        setup = _grounded_role(cur)
        old = _episode(cur, title='Analyst', organisation='EarlyCo', start_date='2010-01-01', end_date='2014-12-31')
        project = _episode(cur, title='Reporting project')
        cur.execute("UPDATE profile360.episodes SET episode_type = 'project', parent_episode_id = %s WHERE id = %s",
                    (setup['episode_id'], project))
        claim = _claim(cur, 'Validated the reporting process.', episode_id=project)
    ref = f'profile_claim:{claim}'
    payload = CVWritingGeneration(
        profile_summary=dict(text='Reporting experience.', source_refs=[ref]),
        experience_bullets=[dict(text='Validated the reporting process.', source_refs=[ref])] if with_bullet else [])
    fake = _fake_run_json_task(payload)
    calls = []

    def writer(**kwargs):
        assert kwargs['output_model'] is CVWritingGeneration
        calls.append(kwargs)
        return fake(**kwargs)

    monkeypatch.setattr(artifacts, 'run_json_task', writer)
    url = f"/api/applications/{setup['application_id']}/artifacts"
    response = client.post(f'{url}/cv/generate', json={})
    assert response.status_code == 200, response.text
    generated = response.json()['artifact']
    content = generated['content']
    assert len(calls) == 1  # sparse writing never triggers a coverage repair
    assert [e['episode_id'] for e in content['experience']] == [setup['episode_id'], old]
    assert [e['organisation'] for e in content['experience']] == ['PrevCo', 'EarlyCo']
    assert bool(content['experience'][0]['bullets']) == with_bullet
    assert content['experience'][1]['bullets'] == []
    assert 'experience_bullets' not in content
    assert content['omitted_experience'] == []
    with db.db_cursor() as cur:
        row = artifacts._draft_row(cur, setup['application_id'], 'cv')
        assert [e['episode_id'] for e in row['content']['experience']] == [setup['episode_id'], old]
        assert 'title' not in row['content']['experience'][0]  # authoritative metadata remains a read projection
        assert 'experience_bullets' not in row['raw_output']
        assert ref in json.dumps(row['raw_output'])

    # A manual edit cannot accidentally delete the chronology. Human text keeps
    # the existing explicitly unvalidated edit semantics and has no invented refs.
    edited_content = {**content, 'experience': [dict(episode_id=old, bullets=[dict(text='Human addition.', source_refs=[])])]}
    response = client.post(f"{url}/{generated['id']}/edit", json={'content': edited_content})
    assert response.status_code == 200, response.text
    edited = response.json()['artifact']
    assert edited['grounding_status'] == 'user_edited_not_revalidated'
    assert [e['episode_id'] for e in edited['content']['experience']] == [setup['episode_id'], old]
    assert edited['content']['experience'][1]['bullets'][0]['source_refs'] == []
    assert client.post(f"{url}/{edited['id']}/adopt").status_code == 200
    active = client.get(url).json()['artifacts']['cv']['active']
    assert active['id'] == edited['id']
    assert active['content'] == edited['content']

    # Old/adopted versions remain stable; newly added employment makes the
    # document stale, without silently inserting it on read.
    with db.db_cursor() as cur:
        added = _episode(cur, title='Additional role', organisation='LaterCo', start_date='2025-01-01')
    reread = client.get(url).json()['artifacts']['cv']['active']
    assert reread['stale'] is True
    assert added not in {e['episode_id'] for e in reread['content']['experience']}
