"""Reviewable salary suggestions; estimates never become market observations."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai import load_prompt, prompt_version, run_json_task
from .db import to_json_param
from .market import normalize_country
from .posting_compensation import (
    accept_posting_compensation,
    propose_posting_compensation,
)


class SalaryEstimate(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    amount_min: float = Field(gt=0)
    amount_max: float = Field(gt=0)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    pay_period: Literal['annual', 'daily']
    employment_basis: Literal['permanent', 'contract', 'unknown']
    rationale: str = Field(min_length=1)
    assumptions: str = Field(min_length=1)
    confidence: Literal['low', 'medium', 'high']
    evidence_ids: list[UUID]

    @model_validator(mode='after')
    def ordered(self):
        if self.amount_max < self.amount_min:
            raise ValueError('Salary maximum must be at least the minimum')
        if self.pay_period == 'daily' and self.employment_basis != 'contract':
            raise ValueError('A day rate must have contract employment basis')
        if not self.evidence_ids:
            self.confidence = 'low'
        return self


class EstimateOutput(BaseModel):
    estimate: SalaryEstimate | None
    reason: str


class CompensationReview(BaseModel):
    run_id: UUID
    stated_items: list[dict] = Field(default_factory=list, max_length=20)
    estimate: SalaryEstimate | None = None


def source_revision(document_id, text):
    return hashlib.sha256(f'{document_id}\n{text}'.encode()).hexdigest()


def salary_evidence(cur, role, metadata):
    """Rank accepted source evidence, retaining units/dates for model comparison.

    No estimated observation is admitted, including estimates previously reviewed
    by a person. Country and title rank candidates; they do not imply equivalence.
    """
    title = metadata.get('title') or role.get('title') or ''
    country = normalize_country(metadata.get('country') or role.get('country')) or ''
    terms = [t for t in title.lower().split() if len(t) > 3][:8]
    cur.execute("""
        SELECT co.id, co.amount_min, co.amount_max, co.amount_mid, co.currency,
               co.component, co.pay_period, co.employment_basis, co.observed_at,
               co.period_end, co.raw_role_label, co.source_note, co.evidence_span,
               co.basis, ri.title, ri.seniority_level, ri.organisation,
               m.label AS market, m.country, d.title AS document_title
        FROM jobber.compensation_observation co
        LEFT JOIN jobber.role_instance ri ON ri.id = co.role_instance_id
        LEFT JOIN jobber.market m ON m.id = co.market_id
        LEFT JOIN jobber.document d ON d.id = co.document_id
        WHERE co.review_status = 'accepted' AND co.basis IN ('posting_stated', 'survey')
          AND co.component IN ('base', 'day_rate', 'total_package')
          AND (co.amount_min IS NOT NULL OR co.amount_max IS NOT NULL OR co.amount_mid IS NOT NULL)
        ORDER BY (lower(coalesce(m.country, ri.country, '')) = lower(%s)) DESC,
          (SELECT count(*) FROM unnest(%s::text[]) term
           WHERE lower(concat_ws(' ', ri.title, co.raw_role_label)) LIKE '%%' || term || '%%') DESC,
          co.observed_at DESC NULLS LAST, co.id
        LIMIT 60
    """, (country, terms))
    return json.loads(json.dumps([dict(r) for r in cur.fetchall()], default=str))


def propose_compensation(cur, role_id, metadata):
    stated = propose_posting_compensation(cur, role_id)
    if stated['status'] != 'ok':
        # Extraction failure is not evidence that a salary is absent.
        return {'error': stated['error'], 'review': None}
    cur.execute("""SELECT ri.*, d.content_text, d.provenance_quality
                   FROM jobber.role_instance ri JOIN jobber.document d ON d.id = ri.document_id
                   WHERE ri.id = %s""", (role_id,))
    role = dict(cur.fetchone())
    items = stated['proposal']['items']
    if role['provenance_quality'] != 'original':
        items = [{**i, 'acceptable': False,
                  'problems': [*i.get('problems', []), 'Source lacks original provenance']} for i in items]
    accepted_items = [i for i in items if i['acceptable'] and role['provenance_quality'] == 'original']
    usable = any(i.get('component') in ('base', 'day_rate', 'total_package') for i in accepted_items)
    cur.execute("""SELECT id FROM jobber.compensation_observation
                   WHERE role_instance_id = %s AND review_status = 'accepted'
                     AND basis = 'posting_stated' AND component IN ('base', 'day_rate', 'total_package')
                     AND (amount_min IS NOT NULL OR amount_max IS NOT NULL) LIMIT 1""", (role_id,))
    existing_stated = cur.fetchone() is not None
    estimate, evidence, model = None, [], None
    reason = 'Usable stated compensation is available.'
    if not usable and not existing_stated:
        evidence = salary_evidence(cur, role, metadata)
        result = run_json_task(
            task='role_salary_estimate', prompt_name='estimate_role_salary.md',
            user_input=json.dumps({'source': role['content_text'], 'details': metadata,
                                   'existing_details': {k: role.get(k) for k in
                                       ('title', 'country', 'location', 'employment_type', 'seniority_level')},
                                   'salary_evidence': evidence,
                                   'estimate_date': datetime.now(timezone.utc).date().isoformat()}, default=str),
            output_model=EstimateOutput,
        )
        estimate = result.output.estimate.model_dump(mode='json') if result.output.estimate else None
        reason, model = result.output.reason, result.run.model
        allowed = {e['id'] for e in evidence}
        if estimate and not set(estimate['evidence_ids']).issubset(allowed):
            raise ValueError('Salary estimate cited evidence that was not supplied')
    snapshot = {'source_revision': source_revision(role['document_id'], role['content_text']),
                'stated_items': items, 'estimate': estimate, 'evidence': evidence, 'reason': reason}
    cur.execute("""INSERT INTO jobber.extraction_run
        (task, subject_type, role_instance_id, document_id, model, prompt_name, prompt_version,
         started_at, finished_at, status, output_payload)
        VALUES ('role_salary_review', 'role_instance', %s, %s, %s, 'estimate_role_salary.md', %s,
                now(), now(), 'ok', %s) RETURNING id""",
        (role_id, role['document_id'], model or 'stated-extraction',
         prompt_version(load_prompt('estimate_role_salary.md')),
         to_json_param(snapshot)))
    run_id = str(cur.fetchone()['id'])
    return {'error': None, 'review': {'run_id': run_id, 'stated_items': accepted_items,
                                     'estimate': estimate}, 'evidence': evidence,
            'stated_items': items, 'reason': reason, 'model': model}


def save_compensation(cur, role_id, review: CompensationReview):
    cur.execute("""SELECT er.output_payload, er.model, ri.document_id, d.content_text
        FROM jobber.extraction_run er JOIN jobber.role_instance ri ON ri.id = er.role_instance_id
        JOIN jobber.document d ON d.id = ri.document_id
        WHERE er.id = %s AND er.role_instance_id = %s AND er.task = 'role_salary_review'
          AND er.status = 'ok' FOR UPDATE OF ri""", (str(review.run_id), role_id))
    run = cur.fetchone()
    if not run or run['output_payload']['source_revision'] != source_revision(run['document_id'], run['content_text']):
        raise ValueError('Salary proposal is missing or its source changed. Suggest details again.')
    for item in review.stated_items:
        accept_posting_compensation(cur, role_id, item)
    if review.estimate:
        original = run['output_payload']
        if not original.get('estimate'):
            raise ValueError('This proposal did not contain an AI estimate')
        estimate = review.estimate.model_dump(mode='json')
        if not set(estimate['evidence_ids']).issubset({e['id'] for e in original['evidence']}):
            raise ValueError('Unknown salary evidence reference')
        cur.execute("""INSERT INTO jobber.role_salary_estimate
            (role_instance_id, extraction_run_id, estimate, evidence, model, role_updated_at)
            SELECT id, %s, %s, %s, %s, updated_at FROM jobber.role_instance WHERE id = %s
            ON CONFLICT (extraction_run_id) DO UPDATE SET estimate = EXCLUDED.estimate,
              role_updated_at = EXCLUDED.role_updated_at, reviewed_at = now()""",
            (str(review.run_id), to_json_param(estimate), to_json_param(original['evidence']), run['model'], role_id))
