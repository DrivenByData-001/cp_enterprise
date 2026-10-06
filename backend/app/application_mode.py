"""Application-owned evidence decisions; canonical career facts remain with Profile360."""
import hashlib
import json

from fastapi import HTTPException

from . import profile360_reader as p360
from .comparison_service import build_role_comparison


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def application(cur, application_id, *, lock=False):
    cur.execute('SELECT * FROM jobber.application WHERE id = %s' + (' FOR UPDATE' if lock else ''), (application_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, 'Application not found')
    return dict(row)


def decisions(cur, application_id):
    cur.execute('SELECT * FROM jobber.application_evidence_decision WHERE application_id = %s', (application_id,))
    return {str(r['concept_id']): dict(r) for r in cur.fetchall()}


def canonical_source(cur, ref):
    kind, _, source_id = ref.partition(':')
    getters = {'profile_claim': p360.get_claim, 'profile_capability': p360.get_capability}
    if kind not in getters:
        raise HTTPException(422, 'Select a Profile360 claim or capability')
    from uuid import UUID
    try:
        UUID(source_id)
    except ValueError:
        raise HTTPException(422, 'Invalid evidence reference')
    row = getters[kind](cur, source_id)
    if not row:
        raise HTTPException(409, 'Selected evidence is no longer available; review your selection')
    row = dict(row)
    return {'ref': ref, 'kind': kind, 'label': p360.display_text(row),
            'content': p360.full_text(row) or p360.display_text(row, max_len=10000),
            'category': 'canonical_evidence', 'source_revision': fingerprint(row),
            'episode_id': str(row['episode_id']) if row.get('episode_id') else None}


def candidates(cur, item):
    refs = []
    for m in item['person_side'].get('mappings') or []:
        kind = 'profile_claim' if m.get('mapping_kind') == 'claim' or m.get('profile360_claim_id') else 'profile_capability'
        source_id = m.get('profile360_id') or m.get('profile360_claim_id') or m.get('profile360_capability_id')
        if source_id:
            refs.append(f'{kind}:{source_id}')
    coverage = item['person_side'].get('coverage') or {}
    refs.extend(f'profile_claim:{i}' for i in coverage.get('supporting_profile360_claim_ids', []))
    result = []
    for ref in dict.fromkeys(refs):
        try:
            result.append(canonical_source(cur, ref))
        except HTTPException as error:
            if error.status_code != 409:
                raise
    return result


def review_fingerprint(item, selected_sources):
    # Only selected sources invalidate an accepted judgment, not unrelated matches.
    return fingerprint({'concept': item['concept'], 'requirement': item['role_side'],
                        'sources': sorted(selected_sources, key=lambda s: s['ref'])})


def reviewed_items(cur, application_id, comparison=None):
    app = application(cur, application_id)
    comparison = comparison or build_role_comparison(cur, str(app['role_instance_id']))
    saved = decisions(cur, application_id)
    items = []
    for item in comparison['items']:
        decision = saved.get(str(item['concept']['id']))
        sources = candidates(cur, item)
        selected = []
        missing = False
        for ref in (decision or {}).get('selected_refs', []):
            try:
                source = next((s for s in sources if s['ref'] == ref), None) or canonical_source(cur, ref)
                selected.append(source)
                if not any(s['ref'] == ref for s in sources):
                    sources.append(source)
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                missing = True
        current = review_fingerprint(item, selected)
        stale = bool(decision and (missing or current != decision['input_fingerprint']))
        items.append({**item, 'sources': sources, 'decision': decision, 'stale': stale,
                      'requirement_fingerprint': fingerprint(item['role_side']),
                      'attention_reason': 'The requirement or selected evidence changed. Review and save again.' if stale else None})
    return items, comparison


def apply_curation(cur, application_id, requirements):
    """Generation consumes the same durable selections the user reviewed."""
    saved = decisions(cur, application_id)
    if not saved:
        return [], False
    items, _ = reviewed_items(cur, application_id)
    by_concept = {str(i['concept']['id']): i for i in items}
    selected_sources = []
    for requirement in requirements:
        item = by_concept.get(requirement['concept_id'])
        decision = saved.get(requirement['concept_id'])
        if not item or not decision:
            continue
        selected = [s for s in item['sources'] if s['ref'] in decision['selected_refs']]
        requirement['application_decision'] = {**decision, 'sources': selected, 'stale': item['stale']}
        selected_sources.extend(selected)
    return selected_sources, True
