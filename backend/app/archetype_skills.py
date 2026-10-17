"""Small, curated archetype target sets over the existing evidence engine."""
from .capability_engine import (
    CapabilityNotFoundError, atomic_concept_evidence, derive_capability_coverage,
)
from .role_requirements import REQUIREMENT_EVIDENCE_SQL

SKILL_TYPES = ('capability', 'knowledge', 'method', 'tool', 'function', 'domain', 'product', 'regulation', 'credential')
SCORES = {'evidenced': 100, 'partial': 50, 'user_asserted': 25, 'not_found': 0}


def skill_profile(cur, archetype_id: str) -> dict:
    cur.execute('SELECT target_skill_ids FROM jobber.role_archetype_detail WHERE concept_id = %s', (archetype_id,))
    saved = cur.fetchone()['target_skill_ids']
    cur.execute(
        'SELECT e.concept_id, COUNT(DISTINCT e.role_instance_id) AS role_count '
        'FROM (' + REQUIREMENT_EVIDENCE_SQL + ') e '
        'JOIN jobber.role_instance ri ON ri.id = e.role_instance_id '
        "WHERE ri.archetype_concept_id = %s AND ri.instance_type = 'observed_posting' "
        "AND e.concept_status = 'active' AND e.requirement_type = 'required' "
        'GROUP BY e.concept_id', (archetype_id,),
    )
    counts = {str(r['concept_id']): r['role_count'] for r in cur.fetchall()}
    cur.execute("SELECT id, canonical_name, type_code FROM jobber.concept WHERE status = 'active' AND type_code = ANY(%s) ORDER BY canonical_name", (list(SKILL_TYPES),))
    candidates = [{**dict(r), 'id': str(r['id']), 'role_count': counts.get(str(r['id']), 0)} for r in cur.fetchall()]
    by_id = {r['id']: r for r in candidates}
    suggested = sorted((r for r in candidates if r['role_count']), key=lambda r: (-r['role_count'], r['canonical_name'], r['id']))[:8]
    ids = saved if saved is not None else [r['id'] for r in suggested]
    axes = []
    unavailable = []
    for concept_id in ids:
        concept = by_id.get(concept_id)
        if concept is None:
            unavailable.append(concept_id)
            continue
        if concept['type_code'] == 'capability':
            try:
                evidence = derive_capability_coverage(cur, concept_id)
            except CapabilityNotFoundError:
                # Unconfigured capabilities have no valid assessment standard.
                axes.append({**concept, 'status': 'unconfigured', 'coverage': None, 'target': 100})
                continue
        else:
            evidence = atomic_concept_evidence(cur, concept_id)
        axes.append({**concept, 'status': evidence['status'], 'coverage': SCORES[evidence['status']], 'target': 100})
    cur.execute("SELECT COUNT(*) AS n FROM jobber.role_instance WHERE archetype_concept_id = %s AND instance_type = 'observed_posting'", (archetype_id,))
    return {'archetype_id': archetype_id, 'source': 'saved' if saved is not None else 'suggested',
            'axes': axes, 'candidates': candidates, 'unavailable_skill_ids': unavailable,
            'sample_role_count': cur.fetchone()['n']}
