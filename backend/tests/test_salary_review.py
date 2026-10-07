"""Database/API integration: combined review, atomicity and estimate isolation."""
from types import SimpleNamespace

from app import db, salary_estimation as salary, posting_compensation as posting, metadata_enrichment
from app.models import RoleMetadataProposal
from app.ai import AITaskRun


def prepare(monkeypatch):
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind='job_posting',
            content_text='Pricing Actuary in Dublin, Ireland. Permanent actuarial work.',
            provenance_quality='original', source='user_paste')
        role_id = db.upsert_role_instance(cur, None, {'instance_type': 'observed_posting',
            'document_id': document_id, 'title': 'Original title'}, skills=[])
    run = AITaskRun('test', 'gpt-5.4-mini', 'test', 'test', 'start', 'end', 'ok', 1, 1)
    monkeypatch.setattr(metadata_enrichment, 'run_json_task', lambda **kw: SimpleNamespace(
        output=RoleMetadataProposal(title='Pricing Actuary', country='Ireland'), run=run))
    monkeypatch.setattr(posting, 'run_json_task', lambda **kw: SimpleNamespace(
        output=posting.PostingCompensationProposal(items=[], no_compensation_stated=True), run=run))
    monkeypatch.setattr(salary, 'run_json_task', lambda **kw: SimpleNamespace(
        output=salary.EstimateOutput(estimate=salary.SalaryEstimate(
            amount_min=80000, amount_max=110000, currency='EUR', pay_period='annual',
            employment_basis='permanent', rationale='Role scope', assumptions='Base only',
            confidence='low', evidence_ids=[]), reason='No stated pay'), run=run))
    return role_id


def test_review_save_and_resolve(client, monkeypatch):
    role = prepare(monkeypatch)
    proposal = client.post(f'/api/role-instances/{role}/metadata/propose').json()
    review = proposal['compensation']['review']
    with db.db_cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM jobber.role_salary_estimate WHERE role_instance_id=%s', (role,))
        assert cur.fetchone()['n'] == 0
    payload = {**proposal['proposal'], 'compensation_review': review}
    assert client.patch(f'/api/role-instances/{role}/metadata', json=payload).status_code == 200
    assert client.patch(f'/api/role-instances/{role}/metadata', json=payload).status_code == 200
    with db.db_cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM jobber.role_salary_estimate WHERE role_instance_id=%s', (role,))
        assert cur.fetchone()['n'] == 1
        cur.execute('SELECT count(*) AS n FROM jobber.compensation_observation WHERE role_instance_id=%s', (role,))
        assert cur.fetchone()['n'] == 0
    resolved = client.get(f'/api/role-instances/{role}/compensation').json()['compensation']
    assert resolved['basis'] == 'ai_estimate'
    assert resolved['amount_min'] == 80000


def test_invalid_salary_rolls_back_metadata(client, monkeypatch):
    role = prepare(monkeypatch)
    proposal = client.post(f'/api/role-instances/{role}/metadata/propose').json()
    review = proposal['compensation']['review']
    review['estimate']['amount_max'] = 1
    response = client.patch(f'/api/role-instances/{role}/metadata',
        json={'title': 'Must roll back', 'compensation_review': review})
    assert response.status_code == 422
    assert client.get(f'/api/roles/{role}').json()['title'] == 'Original title'
