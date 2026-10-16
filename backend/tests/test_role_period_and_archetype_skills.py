from uuid import uuid4

from app import db, archetype_skills


def test_live_historic_filters_and_application_guard(client):
    live, historic = str(uuid4()), str(uuid4())
    with db.db_cursor() as cur:
        for role_id, closing in ((live, '2099-01-01'), (historic, '2000-01-01')):
            cur.execute("INSERT INTO jobber.role_instance(id,instance_type,title,application_closing_date) VALUES(%s,'observed_posting','Test role',%s)", (role_id, closing))
    assert {r['id'] for r in client.get('/api/roles?period=live').json()['items']} == {live}
    assert {r['id'] for r in client.get('/api/roles?period=historic').json()['items']} == {historic}
    assert client.post('/api/applications', json={'role_instance_id': historic}).status_code == 409
    response = client.post('/api/applications', json={'role_instance_id': live})
    assert response.status_code == 200
    application_id = response.json()['id']
    client.patch(f'/api/applications/{application_id}', json={'status': 'closed'})
    with db.db_cursor() as cur:
        cur.execute("UPDATE jobber.role_instance SET application_closing_date='2000-01-01' WHERE id=%s", (live,))
    assert client.patch(f'/api/applications/{application_id}', json={'status': 'preparing'}).status_code == 409


def test_archetype_skillset_roundtrip_and_unavailable_skill(client, monkeypatch):
    monkeypatch.setattr(archetype_skills, 'atomic_concept_evidence', lambda *_: {'status': 'partial'})
    archetype_id = client.post('/api/archetypes', json={'canonical_name': 'Test archetype'}).json()['id']
    ids = [str(uuid4()) for _ in range(3)]
    with db.db_cursor() as cur:
        for i, skill in enumerate(ids):
            cur.execute("INSERT INTO jobber.concept(id,type_code,canonical_name,status,origin) VALUES(%s,'method',%s,'active','curator')", (skill, f'Skill {i}'))
    path = f'/api/archetypes/{archetype_id}/skill-profile'
    assert client.get(path).json()['axes'] == []
    response = client.put(path, json={'concept_ids': ids})
    assert response.status_code == 200
    assert response.json()['source'] == 'saved'
    assert [axis['coverage'] for axis in response.json()['axes']] == [50, 50, 50]
    assert [axis['id'] for axis in client.get(path).json()['axes']] == ids
    assert client.put(path, json={'concept_ids': ids * 3}).status_code == 422
    with db.db_cursor() as cur:
        cur.execute("UPDATE jobber.concept SET status='deprecated' WHERE id=%s", (ids[0],))
    assert client.get(path).json()['unavailable_skill_ids'] == ids[:1]
    assert client.put(path, json={'concept_ids': ids}).status_code == 400
