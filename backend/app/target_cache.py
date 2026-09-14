"""Rebuildable, database-shared caches; no copies of person-side source text.

Jobber writes advance transactional revisions through migration 0019 triggers.
Profile360 is externally owned/read-only: hash its evidence rows to detect even
external edits/deletes without requiring triggers or reliable updated_at fields.
Only digests leave that query. A cache miss is rebuilt lazily, never served stale.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def revisions(cur, target_vec, profile_vec):
    cur.execute("""SELECT evidence, path,
        (SELECT md5(COALESCE(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) FROM profile360.claims t) AS claims,
        (SELECT md5(COALESCE(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) FROM profile360.capabilities t) AS capabilities,
        (SELECT md5(COALESCE(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) FROM profile360.episodes t) AS episodes
        FROM jobber.target_analysis_revision WHERE singleton""")
    row = dict(cur.fetchone())
    # Increment this algorithm version whenever evidence/ranking semantics change.
    evidence = digest([1, row["evidence"], row["claims"], row["capabilities"], row["episodes"]])
    return evidence, digest([1, evidence, row["path"], target_vec, profile_vec])


def cached_statuses(cur, revision, concept_ids):
    cur.execute("SELECT concept_id, status FROM jobber.d_target_evidence WHERE revision = %s AND concept_id = ANY(%s::uuid[])",
                (revision, concept_ids))
    return {str(r["concept_id"]): r["status"] for r in cur.fetchall()}


def save_status(cur, revision, concept_id, status):
    cur.execute("""INSERT INTO jobber.d_target_evidence (concept_id, revision, status) VALUES (%s, %s, %s)
                   ON CONFLICT (concept_id) DO UPDATE SET revision = EXCLUDED.revision, status = EXCLUDED.status""",
                (concept_id, revision, status))
