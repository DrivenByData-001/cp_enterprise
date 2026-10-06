"""Application checkpoints orchestrate existing authoritative domains."""
from fastapi import HTTPException
from . import application_mode as mode
from .db import build_role_view
from .comparison_service import build_role_comparison


def preparation(cur, application_id):
    cur.execute('SELECT * FROM jobber.application_preparation WHERE application_id = %s', (application_id,))
    row = cur.fetchone()
    return dict(row) if row else {'revision': 0, 'deadline': None, 'package_items': [],
                                 'opportunity_fingerprint': None, 'requirements_fingerprint': None}


def target_state(cur, role_id):
    cur.execute('SELECT ri.*, d.content_text, d.url FROM jobber.role_instance ri '
                'LEFT JOIN jobber.document d ON d.id = ri.document_id WHERE ri.id = %s', (role_id,))
    row = dict(cur.fetchone())
    # Ignore processing/cache timestamps; fingerprint the actual opportunity.
    return {key: row.get(key) for key in ('id', 'title', 'organisation', 'location', 'country',
        'remote_type', 'employment_type', 'seniority_level', 'posting_date', 'description',
        'requirements', 'responsibilities', 'content_text', 'url', 'salary_min', 'salary_max',
        'currency', 'archetype_concept_id')}


def checkpoints(cur, application_id):
    app = mode.application(cur, application_id)
    role_id = str(app['role_instance_id'])
    saved = preparation(cur, application_id)
    target = target_state(cur, role_id)
    comparison = build_role_comparison(cur, role_id)
    accepted = [i for i in comparison['items'] if i['role_side']['requirement_source'] == 'claim']
    accepted.sort(key=lambda item: str(item['concept']['id']))
    opportunity = mode.fingerprint({'target': target, 'deadline': saved['deadline'], 'package': saved['package_items']})
    requirements = mode.fingerprint({'target': target, 'summary': comparison['review_summary'],
                                     'requirements': [{ 'concept': i['concept'], 'role_side': i['role_side']} for i in accepted]})
    opportunity_current = saved['opportunity_fingerprint'] == opportunity
    requirements_current = (saved['requirements_fingerprint'] == requirements and bool(accepted)
                            and comparison['review_summary']['complete'])
    return app, saved, comparison, accepted, opportunity, requirements, opportunity_current, requirements_current


def require_current_checkpoints(cur, application_id):
    # Existing workspace-only applications retain their previous behaviour.
    if not preparation(cur, application_id)['revision']:
        return
    _, _, comparison, accepted, _, _, opportunity_current, requirements_current = checkpoints(cur, application_id)
    if not opportunity_current or not requirements_current:
        raise HTTPException(409, 'Review and confirm Opportunity and Requirements in Application Mode before generating')
    items, _ = mode.reviewed_items(cur, application_id, {**comparison, 'items': accepted})
    if not items or any(not i['decision'] or i['stale'] or i['decision']['disposition'] == 'investigate' for i in items):
        raise HTTPException(409, 'Complete the current evidence reviews before generating')


def downstream_states(cur, application_id, upstream_current):
    """Expose existing artifact freshness without migrating their editors."""
    from .application_artifacts import compute_staleness
    from .application_generation import gather_application_evidence, get_active_positioning
    cur.execute("SELECT * FROM jobber.application_artifact WHERE application_id=%s "
                "AND status IN ('active','draft') AND artifact_type IN "
                "('positioning','cv','cover_letter','supporting_statement')", (application_id,))
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        return {}
    bundle = gather_application_evidence(cur, application_id)
    positioning = get_active_positioning(cur, application_id)
    states = {}
    for row in rows:
        state = ('needs_attention' if not upstream_current or compute_staleness(
            cur, application_id, row, bundle=bundle, active_positioning=positioning)
            else 'complete' if row['status'] == 'active' else 'in_progress')
        previous = states.get(row['artifact_type'])
        # An outstanding or stale draft remains visible even beside an active version.
        priority = {'complete': 0, 'in_progress': 1, 'needs_attention': 2}
        if previous is None or priority[state] > priority[previous]:
            states[row['artifact_type']] = state
    return states


def process_view(cur, application_id):
    app, saved, comparison, accepted, opp_fp, req_fp, opp_ok, req_ok = checkpoints(cur, application_id)
    evidence, _ = mode.reviewed_items(cur, application_id, {**comparison, 'items': accepted})
    evidence_ok = req_ok and bool(evidence) and all(i['decision'] and not i['stale'] and
                    i['decision']['disposition'] != 'investigate' for i in evidence)
    def checkpoint_state(value, current):
        return 'complete' if current else 'needs_attention' if value else 'not_started'
    stages = {
        'opportunity': checkpoint_state(saved['opportunity_fingerprint'], opp_ok),
        'requirements': checkpoint_state(saved['requirements_fingerprint'], req_ok),
        'evidence': 'complete' if evidence_ok else 'needs_attention' if any(i['stale'] for i in evidence)
            or (saved['requirements_fingerprint'] and not req_ok) else 'in_progress' if any(i['decision'] for i in evidence) else 'not_started',
    }
    if stages['opportunity'] == 'not_started' and saved['revision']:
        stages['opportunity'] = 'in_progress'
    if stages['requirements'] == 'not_started' and (accepted or comparison['review_summary']['extraction_attempted']):
        stages['requirements'] = 'in_progress'
    cur.execute('SELECT stage, concept_id, updated_at FROM jobber.application_resume WHERE application_id = %s', (application_id,))
    resume = cur.fetchone()
    resume = dict(resume) if resume else {'stage': 'overview', 'concept_id': None}
    if resume.get('concept_id') and not any(str(i['concept']['id']) == str(resume['concept_id']) for i in accepted):
        resume['concept_id'] = None
    return {'application': app, 'role': build_role_view(cur, str(app['role_instance_id'])), 'preparation': saved,
            'opportunity_fingerprint': opp_fp, 'requirements_fingerprint': req_fp,
            'target_revision': mode.fingerprint(target_state(cur, str(app['role_instance_id']))),
            'stages': stages, 'resume': resume, 'items': evidence, 'review_summary': comparison['review_summary'],
            'downstream': downstream_states(cur, application_id, opp_ok and req_ok and evidence_ok),
            'legacy_requirement_count': len(comparison['items']) - len(accepted),
            'next_stage': next((s for s, state in stages.items() if state != 'complete'), 'evidence')}
