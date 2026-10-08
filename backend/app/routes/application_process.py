from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import application_mode as mode, application_process as process
from ..db import db_cursor, to_json_param, update_role_metadata
from ..models import RoleMetadataUpdate
from ..vocabulary_curation import resolve_surface_form_group
from ..requirement_scope import pending_requirement_proposals

router = APIRouter(prefix='/api/applications', tags=['application process'])


@router.get('/{application_id}/process')
def get_process(application_id: UUID):
    with db_cursor() as cur:
        return process.process_view(cur, str(application_id))


class PackageItem(BaseModel):
    id: UUID
    kind: Literal['cv', 'cover_letter', 'supporting_statement', 'question', 'portfolio', 'other']
    label: str = Field(min_length=1, max_length=200)
    required: bool = True
    word_limit: int | None = Field(default=None, ge=1, le=100000)
    instructions: str = Field(default='', max_length=10000)

    @field_validator('label')
    @classmethod
    def label_not_blank(cls, value):
        if not value.strip():
            raise ValueError('Give each package item a name')
        return value.strip()


class OpportunityInput(BaseModel):
    revision: int = Field(ge=0)
    target_revision: str
    metadata: RoleMetadataUpdate
    deadline: date | None = None
    package_items: list[PackageItem] = Field(default_factory=list, max_length=50)
    confirm: bool = False


@router.put('/{application_id}/process/opportunity')
def save_opportunity(application_id: UUID, payload: OpportunityInput):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id), lock=True)
        role_id = str(app['role_instance_id'])
        cur.execute('SELECT id FROM jobber.role_instance WHERE id = %s FOR UPDATE', (role_id,))
        saved = process.preparation(cur, str(application_id))
        if saved['revision'] != payload.revision or payload.target_revision != mode.fingerprint(process.target_state(cur, role_id)):
            raise HTTPException(409, 'Opportunity changed. Reload and review before saving')
        ids = [item.id for item in payload.package_items]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, 'Package items must have unique identifiers')
        metadata = payload.metadata.model_dump(exclude_unset=True)
        if metadata.get('posting_date'):
            try:
                date.fromisoformat(metadata['posting_date'])
            except ValueError:
                raise HTTPException(422, 'Posting date must be a valid date')
        update_role_metadata(cur, role_id, metadata)
        package = [item.model_dump(mode='json') for item in payload.package_items]
        target = process.target_state(cur, role_id)
        fp = mode.fingerprint({'target': target, 'deadline': payload.deadline, 'package': package}) if payload.confirm else saved['opportunity_fingerprint']
        cur.execute('INSERT INTO jobber.application_preparation (application_id, deadline, package_items, opportunity_fingerprint) '
                    'VALUES (%s,%s,%s,%s) ON CONFLICT (application_id) DO UPDATE SET deadline=EXCLUDED.deadline, '
                    'package_items=EXCLUDED.package_items, opportunity_fingerprint=EXCLUDED.opportunity_fingerprint, '
                    'revision=application_preparation.revision+1, updated_at=now()',
                    (str(application_id), payload.deadline, to_json_param(package), fp))
        cur.execute('UPDATE jobber.application SET updated_at=now() WHERE id=%s', (str(application_id),))
        return process.process_view(cur, str(application_id))


class CheckpointInput(BaseModel):
    fingerprint: str


@router.put('/{application_id}/process/requirements')
def confirm_requirements(application_id: UUID, payload: CheckpointInput):
    with db_cursor() as cur:
        mode.application(cur, str(application_id), lock=True)
        app, saved, comparison, accepted, _, req_fp, _, _ = process.checkpoints(cur, str(application_id))
        if payload.fingerprint != req_fp:
            raise HTTPException(409, 'Requirements changed. Reload before confirming')
        if not accepted or not comparison['review_summary']['complete']:
            raise HTTPException(409, 'Review the requirements and resolve pending vocabulary first')
        cur.execute('INSERT INTO jobber.application_preparation (application_id, requirements_fingerprint) VALUES (%s,%s) '
                    'ON CONFLICT (application_id) DO UPDATE SET requirements_fingerprint=EXCLUDED.requirements_fingerprint, '
                    'revision=application_preparation.revision+1, updated_at=now()', (str(application_id), req_fp))
        return process.process_view(cur, str(application_id))


class ProcessResumeInput(BaseModel):
    stage: Literal['overview', 'opportunity', 'requirements', 'evidence']
    concept_id: UUID | None = None


@router.put('/{application_id}/process/resume')
def save_process_resume(application_id: UUID, payload: ProcessResumeInput):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id))
        concept = str(payload.concept_id) if payload.concept_id and payload.stage == 'evidence' else None
        if concept:
            from ..comparison_service import build_role_comparison
            comparison = build_role_comparison(cur, str(app['role_instance_id']))
            if not any(str(i['concept']['id']) == concept and i['role_side']['requirement_source'] == 'claim' for i in comparison['items']):
                raise HTTPException(404, 'Confirmed requirement is no longer on this application')
        cur.execute('INSERT INTO jobber.application_resume (application_id,stage,concept_id) VALUES (%s,%s,%s) '
                    'ON CONFLICT (application_id) DO UPDATE SET stage=EXCLUDED.stage,concept_id=EXCLUDED.concept_id,updated_at=now()',
                    (str(application_id), payload.stage, concept))
    return {'saved': True}


def proposals(cur, role_id):
    rows = pending_requirement_proposals(cur, [role_id])
    for row in rows:
        row.pop('role_instance_id')
    return [{**row, 'revision': mode.fingerprint(row)} for row in rows]


@router.get('/{application_id}/process/vocabulary')
def list_scoped_vocabulary(application_id: UUID):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id))
        return {'items': proposals(cur, str(app['role_instance_id']))}


class VocabularyResolution(BaseModel):
    revision: str
    action: Literal['accept_new', 'accept_alias', 'reject']
    concept_id: UUID | None = None
    type_code: str | None = None
    canonical_name: str | None = Field(default=None, max_length=200)
    definition: str | None = Field(default=None, max_length=10000)


@router.post('/{application_id}/process/vocabulary/{proposal_id}')
def resolve_scoped_vocabulary(application_id: UUID, proposal_id: UUID, payload: VocabularyResolution):
    with db_cursor() as cur:
        app = mode.application(cur, str(application_id), lock=True)
        cur.execute('SELECT id FROM jobber.concept_proposal WHERE id=%s FOR UPDATE', (str(proposal_id),))
        proposal = next((p for p in proposals(cur, str(app['role_instance_id'])) if str(p['id']) == str(proposal_id)), None)
        if not proposal:
            raise HTTPException(404, 'Pending vocabulary proposal is not linked to this application')
        if proposal['revision'] != payload.revision:
            raise HTTPException(409, 'Vocabulary proposal changed. Reload before resolving')
        if payload.action == 'accept_new':
            if not payload.canonical_name or not payload.canonical_name.strip():
                raise HTTPException(422, 'Enter a canonical concept name')
            cur.execute('SELECT 1 FROM jobber.concept_type WHERE code=%s', (payload.type_code,))
            if not cur.fetchone():
                raise HTTPException(422, 'Choose a valid concept type')
        status, concept = resolve_surface_form_group(cur, [proposal['surface_form']], action=payload.action,
            now=datetime.now(timezone.utc), type_code=payload.type_code,
            canonical_name=payload.canonical_name.strip() if payload.canonical_name else None,
            definition=payload.definition, concept_id=str(payload.concept_id) if payload.concept_id else None)
        return {'status': status, 'concept_id': concept}
