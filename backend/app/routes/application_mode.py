from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import application_mode as mode
from .. import profile360_reader as p360
from ..comparison_service import build_role_comparison
from ..db import db_cursor, to_json_param
from ..profile360_acceptance import accept_claim

router = APIRouter(prefix='/api/applications', tags=['application mode'])


def scoped_requirement(cur, application_id, concept_id, *, lock=False):
    app = mode.application(cur, str(application_id), lock=lock)
    comparison = build_role_comparison(cur, str(app['role_instance_id']))
    item = next((i for i in comparison['items'] if str(i['concept']['id']) == str(concept_id)), None)
    if not item:
        raise HTTPException(404, 'Requirement is no longer on this application')
    return item


class ClaimAcceptanceInput(BaseModel):
    operation_id: UUID
    claim_id: UUID | None = None
    source_revision: str | None = None
    claim_text: str = Field(min_length=1, max_length=10000)
    episode_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=10000)
    confirmed: Literal[True]

    @field_validator('claim_text', 'reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Enter the reviewed career fact and its source or correction reason')
        return value.strip()

    @model_validator(mode='after')
    def correction_revision(self):
        if self.claim_id and not self.source_revision:
            raise ValueError('A correction requires the original source revision')
        return self


@router.get('/{application_id}/mode/evidence/{concept_id}/claim-context')
def claim_context(application_id: UUID, concept_id: UUID, claim_id: UUID | None = None):
    with db_cursor() as cur:
        scoped_requirement(cur, application_id, concept_id)
        claim = p360.get_claim(cur, str(claim_id)) if claim_id else None
        if claim_id and not claim:
            raise HTTPException(404, 'Career claim not found')
        cur.execute('SELECT id, title, organisation, start_date, end_date FROM profile360.episodes ORDER BY start_date DESC NULLS LAST, id')
        return {'claim': {'claim_text': claim['claim_text'], 'episode_id': claim.get('episode_id'),
                          'source_revision': mode.fingerprint(dict(claim))} if claim else None,
                'episodes': list(cur.fetchall())}


@router.post('/{application_id}/mode/evidence/{concept_id}/accept-claim')
def accept_career_claim(application_id: UUID, concept_id: UUID, payload: ClaimAcceptanceInput):
    with db_cursor() as cur:
        scoped_requirement(cur, application_id, concept_id, lock=True)
        return accept_claim(cur, str(application_id), str(concept_id), payload)


@router.get('/{application_id}/mode')
def get_mode(application_id: UUID):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id))
        items, comparison = mode.reviewed_items(cur, str(application_id))
        cur.execute('SELECT organisation FROM jobber.role_instance WHERE id = %s', (str(app['role_instance_id']),))
        organisation = cur.fetchone()['organisation']
        cur.execute('SELECT concept_id FROM jobber.application_resume WHERE application_id = %s', (str(application_id),))
        resume = cur.fetchone()
        complete = bool(items) and comparison['review_summary']['complete'] and all(
            i['decision'] and not i['stale'] and i['decision']['disposition'] != 'investigate' for i in items)
        return {'application': app, 'role': {**comparison['role'], 'organisation': organisation}, 'items': items,
                'review_summary': comparison['review_summary'], 'evidence_complete': complete,
                'resume_concept_id': str(resume['concept_id']) if resume and resume['concept_id'] else None}


class ResumeInput(BaseModel):
    concept_id: UUID | None = None


@router.put('/{application_id}/mode/resume')
def save_resume(application_id: UUID, payload: ResumeInput):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id))
        if payload.concept_id:
            comparison = build_role_comparison(cur, str(app['role_instance_id']))
            if not any(str(i['concept']['id']) == str(payload.concept_id) for i in comparison['items']):
                raise HTTPException(404, 'Requirement is no longer on this application')
        cur.execute('INSERT INTO jobber.application_resume (application_id, concept_id) VALUES (%s, %s) '
                    "ON CONFLICT (application_id) DO UPDATE SET stage = 'evidence', concept_id = EXCLUDED.concept_id, updated_at = now()",
                    (str(application_id), str(payload.concept_id) if payload.concept_id else None))
    return {'saved': True}


class DecisionInput(BaseModel):
    disposition: Literal['covered', 'partial', 'investigate', 'gap']
    selected_refs: list[str] = Field(default_factory=list, max_length=50)
    source_revisions: dict[str, str] = Field(default_factory=dict)
    rationale: str = Field(default='', max_length=10000)
    revision: int = Field(default=0, ge=0)
    requirement_fingerprint: str


@router.put('/{application_id}/mode/evidence/{concept_id}')
def save_decision(application_id: UUID, concept_id: UUID, payload: DecisionInput):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id), lock=True)
        comparison = build_role_comparison(cur, str(app['role_instance_id']))
        item = next((i for i in comparison['items'] if str(i['concept']['id']) == str(concept_id)), None)
        if not item:
            raise HTTPException(404, 'Requirement is no longer on this application')
        if payload.requirement_fingerprint != mode.fingerprint(item['role_side']):
            raise HTTPException(409, 'Requirement changed. Reload and review before saving')
        old = mode.decisions(cur, str(application_id)).get(str(concept_id))
        if payload.revision != (old['revision'] if old else 0):
            raise HTTPException(409, 'This review changed in another tab. Reload before saving')
        refs = list(dict.fromkeys(payload.selected_refs))
        if payload.disposition == 'covered' and not refs:
            raise HTTPException(422, 'Select supporting evidence before marking covered')
        if payload.disposition == 'gap' and refs:
            raise HTTPException(422, 'A genuine gap cannot also have selected supporting evidence')
        selected = [mode.canonical_source(cur, ref) for ref in refs]
        if any(payload.source_revisions.get(s['ref']) != s['source_revision'] for s in selected):
            raise HTTPException(409, 'Evidence changed. Reload and review before saving')
        cur.execute('INSERT INTO jobber.application_evidence_decision '
                    '(application_id, concept_id, disposition, selected_refs, rationale, input_fingerprint) '
                    'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (application_id, concept_id) DO UPDATE SET '
                    'disposition = EXCLUDED.disposition, selected_refs = EXCLUDED.selected_refs, '
                    'rationale = EXCLUDED.rationale, input_fingerprint = EXCLUDED.input_fingerprint, '
                    'revision = application_evidence_decision.revision + 1, updated_at = now() RETURNING *',
                    (str(application_id), str(concept_id), payload.disposition, to_json_param(refs),
                     payload.rationale.strip(), mode.review_fingerprint(item, selected)))
        result = dict(cur.fetchone())
        cur.execute('INSERT INTO jobber.application_resume (application_id, concept_id) VALUES (%s, %s) '
                    "ON CONFLICT (application_id) DO UPDATE SET stage = 'evidence', concept_id = EXCLUDED.concept_id, updated_at = now()",
                    (str(application_id), str(concept_id)))
        cur.execute('UPDATE jobber.application SET updated_at = now() WHERE id = %s', (str(application_id),))
    return result


@router.get('/{application_id}/mode/evidence/{concept_id}/search')
def search_evidence(application_id: UUID, concept_id: UUID, q: str = Query(min_length=2, max_length=200)):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id))
        comparison = build_role_comparison(cur, str(app['role_instance_id']))
        if not any(str(i['concept']['id']) == str(concept_id) for i in comparison['items']):
            raise HTTPException(404, 'Requirement is no longer on this application')
        p360.list_columns(cur, 'claims')
        cur.execute('SELECT id FROM profile360.claims WHERE claim_text ILIKE %s ORDER BY id LIMIT 30', ('%' + q + '%',))
        ids = [str(row['id']) for row in cur.fetchall()]
        return {'sources': [mode.canonical_source(cur, 'profile_claim:' + source_id) for source_id in ids]}
