"""Read-only AI discovery followed by explicit, transactional human review."""
import json
from dataclasses import asdict
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, create_model

from . import ai, application_mode as mode
from .db import db_cursor, to_json_param
from .evidence_sources import source_context
from .profile360_mapping import curator_map

Depth = Literal['exposed', 'applied', 'owned', 'set_standard']
Autonomy = Literal['assisted', 'independent', 'directed_others', 'accountable']


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    claim_id: UUID
    concept_id: UUID
    rationale: str = Field(min_length=1, max_length=5000)
    limitations: str = Field(max_length=5000)
    question: str = Field(max_length=2000)
    depth: Depth | None
    autonomy: Autonomy | None


class DiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    findings: list[Proposal] = Field(max_length=30)


class Review(BaseModel):
    action: Literal['approve', 'reject', 'edit']
    revision: int = Field(ge=1)
    rationale: str = Field(min_length=1, max_length=5000)
    limitations: str = Field(default='', max_length=5000)
    clarification: str = Field(default='', max_length=5000)
    depth: Depth | None = None
    autonomy: Autonomy | None = None

    @field_validator('rationale')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Explain the reviewed connection')
        return value.strip()


def requirements(cur, application_id):
    app = mode.application(cur, application_id)
    cur.execute('SELECT rc.*, c.canonical_name, c.definition FROM jobber.requirement_claim rc '
                'JOIN jobber.concept c ON c.id=rc.concept_id '
                "WHERE rc.role_instance_id=%s AND rc.superseded_by IS NULL AND rc.review_status='accepted' AND c.status='active' "
                'ORDER BY c.canonical_name', (app['role_instance_id'],))
    return {str(r['concept_id']): json.loads(json.dumps(dict(r), default=str)) for r in cur.fetchall()}


def batch_sources(sources, max_chars=45000):
    batch, size = [], 0
    for source in sources:
        length = len(json.dumps(source))
        if length > max_chars:
            raise ValueError('A career record exceeds the discovery context limit; narrow its supporting material before retrying')
        if batch and size + length > max_chars:
            yield batch
            batch, size = [], 0
        batch.append(source)
        size += length
    if batch:
        yield batch


def validate_proposals(output, sources, reqs):
    known = {s['data']['claim']['id'] for s in sources}
    seen = set()
    for p in output.findings:
        pair = (str(p.claim_id), str(p.concept_id))
        if pair[0] not in known or pair[1] not in reqs:
            raise ValueError('AI returned a source or requirement outside the supplied records; no findings from this batch were saved')
        if pair not in seen:
            seen.add(pair)
            yield p


def batch_output_model(sources, reqs):
    """Constrain generation itself to the IDs in this batch, not arbitrary UUIDs."""
    claim_ids = tuple(str(s['data']['claim']['id']) for s in sources)
    concept_ids = tuple(reqs)
    if not claim_ids or not concept_ids:
        raise ValueError('Discovery needs career records and accepted requirements')
    batch_proposal = create_model('BatchEvidenceProposal', __base__=Proposal,
        claim_id=(Literal[claim_ids], ...), concept_id=(Literal[concept_ids], ...))
    return create_model('BatchEvidenceOutput', __base__=DiscoveryOutput,
        findings=(list[batch_proposal], Field(max_length=30)))


def discover_batch(batch, reqs, document, excluded):
    schema = batch_output_model(batch, reqs)
    # Avoid confusing a requirement-claim ID with its capability concept ID.
    request = {'requirements': [{'concept_id': cid, 'canonical_name': r.get('canonical_name'),
                'definition': r.get('definition'), 'requirement_type': r.get('requirement_type'),
                'evidence_span': r.get('evidence_span')} for cid, r in reqs.items()],
               'job_specification': document, 'sources': batch, 'excluded_pairs': excluded}
    for attempt in range(2):
        try:
            result = ai.run_json_task(task='evidence_discovery', prompt_name='discover_evidence.md',
                user_input=json.dumps(request), output_model=schema, max_tokens=16000, structured=True)
            proposals = list(validate_proposals(result.output, batch, reqs))
            return result, proposals
        except (ai.AISchemaValidationError, ValueError):
            if attempt:
                raise
            request['validation_reminder'] = ('The previous response failed validation. Copy claim_id only '
                'from sources[].data.claim.id and concept_id only from requirements[].concept_id. '
                'Return an empty findings array if there is no supported connection.')


def discover(run_id, application_id):
    # No transaction or row lock is held while calling the provider.
    try:
        with db_cursor() as cur:
            reqs = requirements(cur, application_id)
            cur.execute('SELECT cl.id FROM profile360.claims cl LEFT JOIN profile360.episodes ep ON ep.id=cl.episode_id '
                        'ORDER BY ep.start_date DESC NULLS LAST, cl.updated_at DESC, cl.id')
            sources = [source_context(cur, str(r['id'])) for r in cur.fetchall()]
            revisions = {s['data']['claim']['id']: s['revision'] for s in sources}
            cur.execute("SELECT claim_id, concept_id,COALESCE(accepted_source_revision,source_revision) AS source_revision,requirement_revision FROM jobber.evidence_finding WHERE application_id=%s AND status IN ('accepted','rejected')", (application_id,))
            excluded = [(str(r['claim_id']), str(r['concept_id'])) for r in cur.fetchall()
                        if revisions.get(str(r['claim_id'])) == r['source_revision']
                        and mode.fingerprint(reqs.get(str(r['concept_id']))) == r['requirement_revision']]
            cur.execute("SELECT profile360_claim_id, jobber_concept_id FROM jobber.profile360_claim_mapping WHERE review_status='rejected'")
            excluded += [(str(r['profile360_claim_id']), str(r['jobber_concept_id'])) for r in cur.fetchall()]
            cur.execute('SELECT d.content_text FROM jobber.application a JOIN jobber.role_instance r ON r.id=a.role_instance_id '
                        'LEFT JOIN jobber.document d ON d.id=r.document_id WHERE a.id=%s', (application_id,))
            document = (cur.fetchone() or {}).get('content_text') or ''
        batches = list(batch_sources(sources))
        with db_cursor() as cur:
            cur.execute('UPDATE jobber.evidence_discovery_run SET total_sources=%s,total_batches=%s WHERE id=%s', (len(sources), len(batches), run_id))
        for batch in batches:
            result, proposals = discover_batch(batch, reqs, document, excluded)
            by_id = {s['data']['claim']['id']: s for s in batch}
            with db_cursor() as cur:
                cur.execute('SELECT status FROM jobber.evidence_discovery_run WHERE id=%s FOR UPDATE', (run_id,))
                active = cur.fetchone()
                if not active or active['status'] != 'running':
                    return
                for p in proposals:
                    claim_id, concept_id = str(p.claim_id), str(p.concept_id)
                    if (claim_id, concept_id) in excluded:
                        continue
                    source = by_id[claim_id]
                    cur.execute('INSERT INTO jobber.evidence_finding '
                        '(run_id,application_id,claim_id,concept_id,source_snapshot,source_revision,requirement_snapshot,requirement_revision,proposal) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                        (run_id, application_id, claim_id, concept_id, to_json_param(source['data']), source['revision'],
                         to_json_param(reqs[concept_id]), mode.fingerprint(reqs[concept_id]), to_json_param(p.model_dump(mode='json'))))
                cur.execute('UPDATE jobber.evidence_discovery_run SET completed_batches=completed_batches+1, model=%s, metadata=%s, updated_at=now() WHERE id=%s',
                            (result.run.model, to_json_param(asdict(result.run)), run_id))
        with db_cursor() as cur:
            cur.execute("UPDATE jobber.evidence_discovery_run SET status='complete',updated_at=now() WHERE id=%s AND status='running'", (run_id,))
    except Exception as error:
        # Persist failures so refresh/retry never presents an empty result as success.
        with db_cursor() as cur:
            cur.execute("UPDATE jobber.evidence_discovery_run SET status='failed',error=%s,updated_at=now() WHERE id=%s AND status='running'",
                        (str(error)[:2000], run_id))


def review(cur, application_id, finding_id, payload):
    mode.application(cur, application_id, lock=True)
    cur.execute('SELECT * FROM jobber.evidence_finding WHERE id=%s AND application_id=%s FOR UPDATE', (finding_id, application_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, 'Finding not found')
    if row['revision'] != payload.revision or row['status'] != 'pending':
        raise HTTPException(409, 'Finding was already reviewed or edited. Reload the findings')
    reqs = requirements(cur, application_id)
    requirement = reqs.get(str(row['concept_id']))
    source = source_context(cur, str(row['claim_id']), lock=True)
    if payload.action != 'reject' and (not source or source['revision'] != row['source_revision'] or not requirement
                                      or mode.fingerprint(requirement) != row['requirement_revision']):
        raise HTTPException(409, 'Evidence or requirement changed. Reject this stale finding and run discovery again')
    mapping = None
    if payload.action == 'approve':
        if payload.clarification.strip():
            cur.execute("INSERT INTO profile360.evidence(claim_id,evidence_type,passage,locator,notes) VALUES (%s,'user_asserted',%s,%s,%s)",
                        (row['claim_id'], payload.clarification.strip(), 'evidence_finding:' + finding_id, payload.rationale))
            source = source_context(cur, str(row['claim_id']))
        mapping = curator_map(cur, 'claim', str(row['claim_id']), str(row['concept_id']))
        cur.execute('INSERT INTO jobber.evidence_assessment (mapping_id,source_revision,depth,autonomy,finding_id) VALUES (%s,%s,%s,%s,%s) '
                    'ON CONFLICT(mapping_id) DO UPDATE SET source_revision=EXCLUDED.source_revision,depth=EXCLUDED.depth,autonomy=EXCLUDED.autonomy,finding_id=EXCLUDED.finding_id,reviewed_at=now()',
                    (mapping['mapping_id'], source['revision'], payload.depth, payload.autonomy, finding_id))
    status = {'approve': 'accepted', 'reject': 'rejected', 'edit': 'pending'}[payload.action]
    reviewed = payload.model_dump(mode='json')
    cur.execute('INSERT INTO jobber.evidence_finding_review(finding_id,action,before_state,reviewed_payload,mapping_result) VALUES (%s,%s,%s,%s,%s)',
                (finding_id, payload.action, to_json_param(json.loads(json.dumps(dict(row), default=str))),
                 to_json_param(reviewed), to_json_param(json.loads(json.dumps(mapping, default=str)))))
    cur.execute('UPDATE jobber.evidence_finding SET status=%s,reviewed_payload=%s,accepted_source_revision=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *',
                (status, to_json_param(reviewed), source['revision'] if payload.action == 'approve' else None, finding_id))
    return dict(cur.fetchone())
