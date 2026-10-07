"""Stable evidence context shared by discovery and reviewed modifier assessment."""
import json

from .application_mode import fingerprint


def source_context(cur, claim_id, *, lock=False):
    cur.execute('SELECT * FROM profile360.claims WHERE id = %s' + (' FOR UPDATE' if lock else ''), (claim_id,))
    claim = cur.fetchone()
    if not claim:
        return None
    episode = None
    if claim['episode_id']:
        cur.execute('SELECT * FROM profile360.episodes WHERE id = %s' + (' FOR SHARE' if lock else ''), (claim['episode_id'],))
        episode = cur.fetchone()
    cur.execute('SELECT e.*, d.title AS document_title FROM profile360.evidence e '
                'LEFT JOIN profile360.documents d ON d.id = e.document_id WHERE e.claim_id = %s ORDER BY e.id', (claim_id,))
    source = json.loads(json.dumps({'claim': dict(claim), 'episode': dict(episode) if episode else None,
                                   'evidence': [dict(e) for e in cur.fetchall()]}, default=str))
    return {'data': source, 'revision': fingerprint(source)}
