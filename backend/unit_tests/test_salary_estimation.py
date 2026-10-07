from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from app import salary_estimation as salary
from app import compensation_resolver as resolver


def estimate(**changes):
    return salary.SalaryEstimate.model_validate(dict(
        amount_min=80000, amount_max=110000, currency='EUR', pay_period='annual',
        employment_basis='permanent', rationale='Pricing responsibilities in Dublin',
        assumptions='Base salary only; no live market search', confidence='high',
        evidence_ids=[], **changes))


@pytest.mark.parametrize('changes', [
    {'amount_min': -1}, {'amount_max': 1}, {'amount_max': float('inf')},
    {'currency': 'euros'}, {'pay_period': 'daily'},
])
def test_rejects_invalid_ranges(changes):
    fields = estimate().model_dump()
    fields.update(changes)
    with pytest.raises(ValidationError):
        salary.SalaryEstimate.model_validate(fields)


def test_general_knowledge_estimate_cannot_claim_high_confidence():
    assert estimate().confidence == 'low'


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0)


def stated(item=None, failed=False):
    return {'status': 'failed' if failed else 'ok', 'error': 'provider failed' if failed else None,
            'proposal': {'items': [item] if item else []}}


def test_extraction_failure_is_not_treated_as_missing_salary(monkeypatch):
    monkeypatch.setattr(salary, 'propose_posting_compensation', lambda *a: stated(failed=True))
    monkeypatch.setattr(salary, 'run_json_task', lambda **kw: pytest.fail('must not estimate'))
    result = salary.propose_compensation(Cursor([]), 'role', {})
    assert result['review'] is None and result['error']


@pytest.mark.parametrize('existing', [True, False])
def test_usable_stated_salary_skips_estimation(monkeypatch, existing):
    item = {'acceptable': True, 'component': 'base', 'amount_min': 90000}
    monkeypatch.setattr(salary, 'propose_posting_compensation', lambda *a: stated(None if existing else item))
    monkeypatch.setattr(salary, 'run_json_task', lambda **kw: pytest.fail('must not estimate'))
    cur = Cursor([{'document_id': 'doc', 'content_text': 'text', 'provenance_quality': 'original'},
                  {'id': 'existing'} if existing else None, {'id': uuid4()}])
    result = salary.propose_compensation(cur, 'role', {})
    assert result['review']['estimate'] is None
    assert not any('INSERT INTO jobber.compensation_observation' in q for q, _ in cur.queries)


def test_missing_pay_returns_unsaved_estimate_with_evidence(monkeypatch):
    monkeypatch.setattr(salary, 'propose_posting_compensation', lambda *a: stated())
    monkeypatch.setattr(salary, 'salary_evidence', lambda *a: [])
    monkeypatch.setattr(salary, 'run_json_task', lambda **kw: SimpleNamespace(
        output=salary.EstimateOutput(estimate=estimate(), reason='No salary stated'),
        run=SimpleNamespace(model='gpt-5.4-mini')))
    cur = Cursor([{'document_id': 'doc', 'content_text': 'text', 'provenance_quality': 'original'}, None, {'id': uuid4()}])
    result = salary.propose_compensation(cur, 'role', {'country': 'Ireland'})
    assert result['review']['estimate']['amount_min'] == 80000
    assert result['model'] == 'gpt-5.4-mini'
    assert not any('INSERT INTO jobber.role_salary_estimate' in q for q, _ in cur.queries)


def test_rejects_changed_source_before_writing(monkeypatch):
    cur = Cursor([{'output_payload': {'source_revision': 'old'}, 'document_id': 'doc', 'content_text': 'new'}])
    with pytest.raises(ValueError, match='source changed'):
        salary.save_compensation(cur, 'role', salary.CompensationReview(run_id=uuid4(), estimate=estimate()))
    assert len(cur.queries) == 1


def test_reviewed_estimate_has_distinct_basis_and_units():
    result = resolver._from_reviewed_salary({'reviewed_salary': estimate().model_dump(mode='json'),
                                            'salary_model': 'gpt-5.4-mini', 'salary_reviewed_at': '2026-10-07'}, [])
    assert result['basis'] == 'ai_estimate'
    assert result['amount_reference'] == 95000
    assert result['pay_period'] == 'annual'
    assert result['evidence']['n_observations'] == 0


def test_stated_pay_wins_over_reviewed_estimate(monkeypatch):
    monkeypatch.setattr(resolver, '_load_roles', lambda *a: {'r': {'archetype_concept_id': None,
        'reviewed_salary': estimate().model_dump(mode='json')}})
    monkeypatch.setattr(resolver, '_load_role_observations', lambda *a: {'r': []})
    monkeypatch.setattr(resolver, 'load_archetype_benchmarks', lambda *a, **kw: {})
    monkeypatch.setattr(resolver, '_select_primary_stated', lambda *a: ({'id': 'stated'}, []))
    monkeypatch.setattr(resolver, '_from_stated_observation', lambda *a: {'basis': 'advert_stated'})
    result = resolver.resolve_role_compensation_bulk(None, ['r'], freshness={'fresh': True})
    assert result['r']['basis'] == 'advert_stated'
