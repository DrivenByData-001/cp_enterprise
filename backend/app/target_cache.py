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


def economics_revision(cur):
    """Migration 0023's third counter, advanced by statement triggers on
    compensation_observation, market, planning_assumption and the three
    derived economics tables. Separate from `path` on purpose: accepting a
    compensation observation changes every Pathways answer but changes no
    target path, and vice versa."""
    cur.execute("SELECT economics FROM jobber.target_analysis_revision WHERE singleton")
    row = cur.fetchone()
    return row["economics"] if row else 0


def pathways_revision(cur, target_vec, profile_vec, context_key, personal_fingerprint):
    """The Pathways cache key. Folds in everything that can change the
    answer: the shared evidence/path revisions (so a new accepted
    requirement or a re-assigned archetype invalidates it), the economics
    counter, the selected market/currency context, and a fingerprint of the
    user's own profile360 compensation rows — which no trigger can cover,
    because profile360 is externally owned and this app only reads it."""
    evidence, path = revisions(cur, target_vec, profile_vec)
    # Bump this algorithm version whenever Pathways composition semantics change.
    return digest([1, evidence, path, economics_revision(cur), context_key, personal_fingerprint])


def cached_statuses(cur, revision, concept_ids):
    cur.execute("SELECT concept_id, status FROM jobber.d_target_evidence WHERE revision = %s AND concept_id = ANY(%s::uuid[])",
                (revision, concept_ids))
    return {str(r["concept_id"]): r["status"] for r in cur.fetchall()}


def save_status(cur, revision, concept_id, status):
    cur.execute("""INSERT INTO jobber.d_target_evidence (concept_id, revision, status) VALUES (%s, %s, %s)
                   ON CONFLICT (concept_id) DO UPDATE SET revision = EXCLUDED.revision, status = EXCLUDED.status""",
                (concept_id, revision, status))
