from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException

from .. import application_mode as mode, evidence_discovery as discovery
from ..db import db_cursor
from ..evidence_sources import source_context

router = APIRouter(prefix='/api/applications', tags=['evidence discovery'])


def expire_runs(cur, application_id):
    cur.execute("UPDATE jobber.evidence_discovery_run SET status='failed',error='Search interrupted or timed out. Run discovery again.',updated_at=now() "
                "WHERE application_id=%s AND status='running' AND updated_at < now()-interval '15 minutes'", (application_id,))


@router.post('/{application_id}/evidence-discovery')
def start(application_id: UUID, tasks: BackgroundTasks):
    aid = str(application_id)
    with db_cursor() as cur:
        mode.application(cur, aid, lock=True)
        expire_runs(cur, aid)
        if not discovery.requirements(cur, aid):
            raise HTTPException(422, 'Accept the role requirements before finding evidence')
        cur.execute("SELECT id FROM jobber.evidence_discovery_run WHERE application_id=%s AND status='running'", (aid,))
        existing = cur.fetchone()
        if existing:
            return {'id': str(existing['id']), 'status': 'running'}
        cur.execute('INSERT INTO jobber.evidence_discovery_run(application_id) VALUES (%s) RETURNING id', (aid,))
        run_id = str(cur.fetchone()['id'])
    tasks.add_task(discovery.discover, run_id, aid)
    return {'id': run_id, 'status': 'running'}


@router.get('/{application_id}/evidence-discovery')
def list_findings(application_id: UUID):
    aid = str(application_id)
    with db_cursor() as cur:
        mode.application(cur, aid)
        expire_runs(cur, aid)
        reqs = discovery.requirements(cur, aid)
        cur.execute('SELECT * FROM jobber.evidence_discovery_run WHERE application_id=%s ORDER BY created_at DESC LIMIT 1', (aid,))
        run = cur.fetchone()
        cur.execute('SELECT f.* FROM jobber.evidence_finding f WHERE application_id=%s '
                    "ORDER BY (source_snapshot->'episode'->>'start_date') DESC NULLS LAST, created_at DESC", (aid,))
        findings = [dict(r) for r in cur.fetchall()]
        sources = {}
        for f in findings:
            cid = str(f['claim_id'])
            if cid not in sources:
                sources[cid] = source_context(cur, cid)
            source = sources[cid]
            requirement = reqs.get(str(f['concept_id']))
            f['stale'] = not source or source['revision'] != (f['accepted_source_revision'] or f['source_revision']) or not requirement or mode.fingerprint(requirement) != f['requirement_revision']
        return {'run': run, 'findings': findings}


@router.post('/{application_id}/evidence-discovery/{finding_id}/review')
def review(application_id: UUID, finding_id: UUID, payload: discovery.Review):
    with db_cursor() as cur:
        return discovery.review(cur, str(application_id), str(finding_id), payload)
