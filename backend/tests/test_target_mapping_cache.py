import json
import uuid

import pytest

from app import db, stepping_stones
from test_career_workflows import concept, role, claim


def save_target(client, skills):
    response = client.post('/api/targets', json={
        'metadata': {}, 'target': {'title': 'Target'}, 'skills': skills})
    assert response.status_code == 200, response.text
    return response.json()['id']


@pytest.mark.parametrize('wording', ['Capital Modelling', 'capital modelling', 'CM'])
def test_target_canonical_name_and_alias_reach_analysis(client, wording):
    with db.db_cursor() as cur:
        cid = concept(cur, 'Capital Modelling')
        cur.execute("INSERT INTO jobber.concept_alias (concept_id, alias, origin) VALUES (%s, 'CM', 'curator')", (cid,))
    target = save_target(client, [{'name': wording, 'requirement_type': 'required'}])
    path = client.get(f'/api/roles/{target}').json()['path']
    assert path['target_mapping']['complete']
    assert path['target_mapping']['items'][0]['concept_id'] == cid
    assert path['target_mapping']['items'][0]['name'] == wording
    assert path['distinct_concepts'] == 1


def test_different_wording_needs_explicit_review_and_survives_save(client):
    with db.db_cursor() as cur:
        cid = concept(cur, 'Capital Modelling')
    skill = {'name': 'Calculate regulatory capital', 'requirement_type': 'required'}
    assert client.post('/api/targets/resolve-requirements', json=[skill]).json()[0]['mapping_status'] == 'unmapped'
    assert client.post('/api/targets/resolve-requirements', json=[{**skill, 'concept_id': cid}]).status_code == 422
    target = save_target(client, [{**skill, 'concept_id': cid, 'mapping_reviewed': True}])
    item = client.get(f'/api/roles/{target}').json()['path']['target_mapping']['items'][0]
    assert item['mapping_status'] == 'mapped' and item['concept_id'] == cid
    assert item['name'] == skill['name']
    edited = client.put(f'/api/targets/{target}', json={'metadata': {}, 'target': {'title': 'Edited'},
                                                        'skills': [{'name': 'A different requirement'}]})
    assert edited.status_code == 200
    assert client.get(f'/api/roles/{target}').json()['path']['target_mapping']['unresolved'] == 1


def test_unmapped_requirement_prevents_complete_readiness(client):
    with db.db_cursor() as cur:
        cid = concept(cur, 'Python')
        candidate = role(cur)
        claim(cur, candidate, cid)
        cur.execute("INSERT INTO jobber.person_capability_assertion (jobber_concept_id, note) VALUES (%s, 'Experience')", (cid,))
    target = save_target(client, [{'name': 'Python', 'requirement_type': 'required'}, {'name': 'An unknown capability'}])
    path = client.get(f'/api/roles/{target}').json()['path']
    assert path['target_mapping']['mapped'] == 1
    assert path['target_mapping']['unresolved'] == 1
    assert not path['target_mapping']['complete']
    assert path['stepping_stones'][0]['assessment'] == 'incomplete_target_mapping'
    comparison = client.get(f'/api/comparison/role/{target}').json()
    assert comparison['target_mapping']['unresolved'] == 1
    assert comparison['fit_score'] is None
    assert path['target_mapping']['items'][1]['concept_id'] is None or path['target_mapping']['items'][0]['concept_id'] is None
    forced = client.post('/api/targets/resolve-requirements', json=[{'name': 'Python', 'mapping_reviewed': True, 'concept_id': None}]).json()[0]
    assert forced['mapping_status'] == 'unmapped' and forced['concept_id'] is None


def test_invalid_and_deprecated_concepts_cannot_be_selected(client):
    with db.db_cursor() as cur:
        cid = concept(cur, 'Old concept')
        cur.execute("UPDATE jobber.concept SET status = 'deprecated' WHERE id = %s", (cid,))
    for invalid in [cid, str(uuid.uuid4()), 'invalid-uuid']:
        response = client.post('/api/targets/resolve-requirements', json=[{'name': 'X', 'concept_id': invalid, 'mapping_reviewed': True}])
        assert response.status_code == 422
    assert client.post('/api/targets/resolve-requirements', json=[{'name': 'Old concept'}]).json()[0]['mapping_status'] == 'unmapped'


def test_cache_reuses_evidence_and_invalidates_role_assertion_and_external_edits(client):
    with db.db_cursor() as cur:
        cid = concept(cur, 'Python')
        candidate = role(cur)
        claim(cur, candidate, cid)
    target = save_target(client, [{'name': 'Python', 'requirement_type': 'required'}])
    def path():
        return client.get(f'/api/roles/{target}').json()['path']
    assert path()['metrics']['concepts_evaluated'] == 1
    assert path()['metrics']['cache_hit']
    with db.db_cursor() as cur:
        cur.execute("UPDATE jobber.role_instance SET title = 'Changed title' WHERE id = %s", (candidate,))
    changed = path()
    assert not changed['metrics']['cache_hit'] and changed['metrics']['concepts_evaluated'] == 0
    assert changed['stepping_stones'][0]['title'] == 'Changed title'
    assert client.post('/api/comparison/assert', json={'concept_id': cid, 'note': 'Example'}).status_code == 200
    assert path()['metrics']['concepts_evaluated'] == 1
    with db.db_cursor() as cur:
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('External evidence') RETURNING id")
        source = str(cur.fetchone()['id'])
    assert not path()['metrics']['cache_hit']
    assert path()['metrics']['cache_hit']
    with db.db_cursor() as cur:
        # No updated_at change: external source freshness must still be detected.
        cur.execute("UPDATE profile360.claims SET claim_text = 'Changed externally' WHERE id = %s", (source,))
    assert not path()['metrics']['cache_hit']
    with db.db_cursor() as cur:
        cur.execute("DELETE FROM profile360.claims WHERE id = %s", (source,))
    assert not path()['metrics']['cache_hit']


def test_concept_deprecation_invalidates_mapping_and_cached_result(client):
    with db.db_cursor() as cur:
        cid = concept(cur, 'Python')
    target = save_target(client, [{'name': 'Python'}])
    assert client.get(f'/api/roles/{target}').json()['path']['target_mapping']['complete']
    with db.db_cursor() as cur:
        cur.execute("UPDATE jobber.concept SET status = 'deprecated' WHERE id = %s", (cid,))
    path = client.get(f'/api/roles/{target}').json()['path']
    assert not path['target_mapping']['complete'] and not path['metrics']['cache_hit']


def test_evidence_review_capability_rules_and_requirements_invalidate_cache(client):
    from test_capability_engine import _capability, _claim, _claim_mapping, _component_edge
    with db.db_cursor() as cur:
        cap = _capability(cur, 'Risk analysis')
        atom = concept(cur, 'Python')
        source = _claim(cur, 'Analysed risk', depth='applied')
        _claim_mapping(cur, source, cap, review_status='unreviewed')
    target = save_target(client, [{'name': 'Risk analysis', 'requirement_type': 'required'}])
    def status():
        path = client.get(f'/api/roles/{target}').json()['path']
        with db.db_cursor() as cur:
            cur.execute('SELECT status FROM jobber.d_target_evidence WHERE concept_id = %s', (cap,))
            return path, cur.fetchone()['status']
    assert status()[1] == 'partial'
    assert status()[0]['metrics']['cache_hit']
    with db.db_cursor() as cur:
        cur.execute("UPDATE jobber.profile360_claim_mapping SET review_status = 'accepted' WHERE jobber_concept_id = %s", (cap,))
        cur.execute("UPDATE jobber.capability_detail SET min_depth = 'applied' WHERE concept_id = %s", (cap,))
    path, evidence = status()
    assert not path['metrics']['cache_hit'] and evidence == 'evidenced'
    with db.db_cursor() as cur:
        _component_edge(cur, atom, cap)
    assert status()[0]['metrics']['concepts_evaluated'] == 1
    with db.db_cursor() as cur:
        # Accepted claims become authoritative; the old observation is visible
        # as excluded, rather than silently disappearing behind claim precedence.
        claim(cur, target, atom)
    path, _ = status()
    assert path['target_mapping']['unresolved'] == 1
    assert any(i['mapping_status'] == 'excluded' for i in path['target_mapping']['items'])


class CountingCursor:
    def __init__(self, cur):
        self.cur, self.queries = cur, 0

    def execute(self, *args, **kwargs):
        self.queries += 1
        return self.cur.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.cur, name)


def test_target_path_corpus_benchmark(client, monkeypatch, record_property):
    # Reproducible corpus: 1,000 roles, 20,000 observations, 200 distinct
    # concepts (20 capabilities), 1,000 external claims. Real evidence engines.
    with db.db_cursor() as cur:
        cur.execute("""INSERT INTO jobber.concept (canonical_name, type_code, status, origin)
                       SELECT 'Concept ' || n, CASE WHEN n <= 20 THEN 'capability' ELSE 'tool' END,
                              'active', 'curator' FROM generate_series(1, 200) n""")
        cur.execute("""INSERT INTO jobber.capability_detail (concept_id, demonstration_standard)
                       SELECT id, 'Demonstrated work' FROM jobber.concept WHERE type_code = 'capability'""")
        cur.execute("""INSERT INTO jobber.role_instance (title, instance_type)
                       SELECT 'Role ' || n, 'observed_posting' FROM generate_series(1, 1000) n""")
        cur.execute("""INSERT INTO jobber.role_skill_observation
                       (role_instance_id, canonical_concept_id, surface_form, observation_basis, requirement_type)
                       SELECT r.id, c.id, c.canonical_name, 'app_capture', 'required'
                       FROM jobber.role_instance r CROSS JOIN jobber.concept c
                       WHERE (substring(r.title from '[0-9]+')::int + substring(c.canonical_name from '[0-9]+')::int) % 10 = 0""")
        cur.execute("INSERT INTO profile360.claims (claim_text) SELECT 'Evidence ' || n FROM generate_series(1, 1000) n")
    target = save_target(client, [{'name': f'Concept {n}', 'requirement_type': 'required'} for n in range(1, 21)])
    calls = []
    for name in ['atomic_concept_evidence', 'derive_capability_coverage']:
        original = getattr(stepping_stones, name)
        def counted(cur, cid, original=original):
            calls.append(cid)
            return original(cur, cid)
        monkeypatch.setattr(stepping_stones, name, counted)
    with db.db_cursor() as cur:
        counter = CountingCursor(cur)
        cold = stepping_stones.path_to_target(counter, target, [], [])
        cold_queries = counter.queries
        counter.queries = 0
        warm = stepping_stones.path_to_target(counter, target, [], [])
        assert counter.queries == 2  # revision/fingerprint read + prepared result read
    assert len(calls) == len(set(calls)) == 200
    assert cold['metrics']['candidates'] == 1000
    assert warm['metrics']['concepts_evaluated'] == 0 and warm['metrics']['cache_hit']
    assert cold_queries < 3000  # protects against per-role evidence/requirement queries
    benchmark = {'cold': cold['metrics'], 'warm': warm['metrics'], 'cold_queries': cold_queries, 'warm_queries': 2}
    record_property('target_path_benchmark', json.dumps(benchmark))
    print('TARGET_PATH_BENCHMARK ' + json.dumps(benchmark))
