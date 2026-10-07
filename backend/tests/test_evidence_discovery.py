from types import SimpleNamespace

from app import db, evidence_discovery as discovery, capability_engine
from app.ai import AITaskRun
from tests.test_application_artifacts import _concept, _posting, _accepted_claim
from tests.test_capability_engine import _claim, _episode


def seed(client, monkeypatch):
    with db.db_cursor() as cur:
        concept = _concept(cur,'problem solving','capability')
        role = _posting(cur)
        _accepted_claim(cur,role,concept,evidence_span='Self-driven problem solver')
        episode = _episode(cur,start_date='2024-09-01',autonomy='Technical discretion under senior review')
        claim = _claim(cur,'Diagnosed and corrected model behaviour',episode_id=episode,depth='owned')
    app = client.post('/api/applications',json={'role_instance_id':role}).json()['id']
    proposal = discovery.Proposal(claim_id=claim,concept_id=concept,rationale='Diagnosis and correction',limitations='No final sign-off',question='',depth='owned',autonomy='independent')
    monkeypatch.setattr(discovery.ai,'run_json_task',lambda **kw: SimpleNamespace(output=discovery.DiscoveryOutput(findings=[proposal]),
        run=AITaskRun('discovery','gpt-5.4-mini','discover_evidence.md','test','start','end','ok',100,100)))
    base = f'/api/applications/{app}/evidence-discovery'
    assert client.post(base).status_code == 200
    data = client.get(base).json()
    assert data['run']['status'] == 'complete',data
    return base,data['findings'][0],claim,concept,episode


def test_review_applies_scoped_assessment_and_preserves_episode(client,monkeypatch):
    base,f,claim,concept,episode=seed(client,monkeypatch)
    payload={'action':'approve','revision':1,'rationale':'Checked the record','clarification':'I owned the investigation', 'depth':'owned','autonomy':'independent'}
    result=client.post(f"{base}/{f['id']}/review",json=payload)
    assert result.status_code==200,result.text
    assert not client.get(base).json()['findings'][0]['stale']
    assert client.post(f"{base}/{f['id']}/review",json=payload).status_code==409
    with db.db_cursor() as cur:
        assert capability_engine._direct_evidence(cur,concept)[0]['autonomy']=='independent'
        cur.execute('SELECT autonomy FROM profile360.episodes WHERE id=%s',(episode,))
        assert cur.fetchone()['autonomy']=='Technical discretion under senior review'
        cur.execute('SELECT count(*) n FROM profile360.evidence WHERE claim_id=%s',(claim,))
        assert cur.fetchone()['n']==1
        cur.execute("UPDATE profile360.claims SET claim_text='Revised account' WHERE id=%s",(claim,))
        assert capability_engine._direct_evidence(cur,concept)[0]['autonomy'] is None


def test_edit_reject_and_repeat_discovery_do_not_change_career_facts(client,monkeypatch):
    base,f,claim,concept,_=seed(client,monkeypatch)
    payload={'action':'edit','revision':1,'rationale':'Needs clarification'}
    assert client.post(f"{base}/{f['id']}/review",json=payload).status_code==200
    payload.update(action='reject',revision=2)
    assert client.post(f"{base}/{f['id']}/review",json=payload).status_code==200
    assert client.post(base).status_code==200
    assert len(client.get(base).json()['findings'])==1
    with db.db_cursor() as cur:
        cur.execute('SELECT count(*) n FROM jobber.profile360_claim_mapping WHERE profile360_claim_id=%s',(claim,))
        assert cur.fetchone()['n']==0


def test_stale_source_and_wrong_application_cannot_be_approved(client,monkeypatch):
    base,f,claim,_,_=seed(client,monkeypatch)
    other,_,_,_,_=seed(client,monkeypatch)
    payload={'action':'approve','revision':1,'rationale':'Checked'}
    assert client.post(f"{other}/{f['id']}/review",json=payload).status_code==404
    with db.db_cursor() as cur:
        cur.execute("UPDATE profile360.claims SET claim_text='Changed' WHERE id=%s",(claim,))
    assert client.post(f"{base}/{f['id']}/review",json=payload).status_code==409
    assert client.get(base).json()['findings'][0]['stale']
