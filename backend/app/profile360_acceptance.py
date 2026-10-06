"""Explicit human acceptance into the confirmed open-brain Profile360 schema.

The caller owns the transaction. Claim, provenance and acceptance receipt commit
together. Queued imports are deliberately not consumed or marked accepted here.
"""
from uuid import uuid4

from fastapi import HTTPException

from . import application_mode as mode
from .db import to_json_param


def accept_claim(cur, application_id, concept_id, payload):
    request = {'application_id': application_id, 'concept_id': concept_id, **payload.model_dump(mode='json')}
    request_hash = mode.fingerprint(request)
    operation_id = str(payload.operation_id)
    # Serialize retries even if a caller tries to reuse an operation across apps.
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (operation_id,))
    cur.execute('SELECT * FROM jobber.profile360_acceptance WHERE id = %s', (operation_id,))
    receipt = cur.fetchone()
    if receipt:
        if receipt['request_fingerprint'] != request_hash:
            raise HTTPException(409, 'This acceptance identifier was already used for different changes')
        return result(cur, receipt)

    before = None
    claim_id = str(payload.claim_id) if payload.claim_id else str(uuid4())
    if payload.claim_id:
        cur.execute('SELECT * FROM profile360.claims WHERE id = %s FOR UPDATE', (claim_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(409, 'The career claim is no longer available')
        before = dict(row)
        if mode.fingerprint(before) != payload.source_revision:
            raise HTTPException(409, 'The career claim changed. Reload it and review the correction again')
    if payload.episode_id:
        cur.execute('SELECT id FROM profile360.episodes WHERE id = %s FOR KEY SHARE', (str(payload.episode_id),))
        if not cur.fetchone():
            raise HTTPException(409, 'The career episode is no longer available')
    episode = str(payload.episode_id) if payload.episode_id else None
    if before:
        cur.execute('UPDATE profile360.claims SET claim_text = %s, episode_id = %s, '
                    "evidence_class = CASE WHEN evidence_class = 'user_asserted' THEN 'user_asserted' ELSE 'mixed' END, "
                    'updated_at = now() WHERE id = %s RETURNING *', (payload.claim_text, episode, claim_id))
    else:
        cur.execute('INSERT INTO profile360.claims (id, claim_key, episode_id, claim_text, evidence_class) '
                    "VALUES (%s, %s, %s, %s, 'user_asserted') RETURNING *",
                    (claim_id, 'cp_enterprise_accepted:' + operation_id, episode, payload.claim_text))
    after = dict(cur.fetchone())
    cur.execute('INSERT INTO profile360.evidence (claim_id, evidence_type, passage, locator, notes) '
                "VALUES (%s, 'user_asserted', %s, %s, %s)",
                (claim_id, payload.claim_text, 'cp_enterprise_acceptance:' + operation_id, payload.reason))
    # JSON snapshots also preserve UUID/timestamp fields without lossy SQL casts.
    import json
    snapshot = lambda row: json.loads(json.dumps(row, default=str)) if row else None
    cur.execute('INSERT INTO jobber.profile360_acceptance '
                '(id, application_id, concept_id, claim_id, request_fingerprint, operation, '
                'reviewed_payload, before_state, after_state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (operation_id, application_id, concept_id, claim_id, request_hash,
                 'correct' if before else 'add', to_json_param(request), to_json_param(snapshot(before)),
                 to_json_param(snapshot(after))))
    return result(cur, cur.fetchone())


def result(cur, receipt):
    # Return the current version on retry, so a later correction is never hidden.
    return {'status': 'accepted', 'acceptance_id': str(receipt['id']),
            'accepted_at': receipt['accepted_at'],
            'accepted_revision': mode.fingerprint(receipt['after_state']),
            'source': mode.canonical_source(cur, 'profile_claim:' + str(receipt['claim_id']))}
