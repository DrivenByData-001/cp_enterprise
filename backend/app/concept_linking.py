"""Phase 1 concept linking (docs/11-capability-model-design.md §7.1, §7.3).

Implements canonicalisation-cascade steps 1-2 only: exact match, then
embedding kNN suggestion. Step 3 (model adjudication over the top candidates)
has no host in this codebase — there is no in-process LLM client anywhere in
backend/requirements.txt, extraction here is always a human pasting text into
an external chat UI and pasting a result back through /api/import. Ambiguous
surface forms fall straight through to a human-reviewed `concept_proposal`
instead of an automated pick.

Kept out of db.py deliberately, so db.py doesn't acquire a module-load-time
dependency on embeddings.py (today only routes/roles.py and routes/targets.py
import it).
"""

import re
import sqlite3
from datetime import datetime, timezone

from .embeddings import cosine_similarity, dumps_vec, embed_text, embedding_model_name, loads_vec


def normalize_name(raw: str) -> str:
    """Case-fold + collapse whitespace. The one definition of "same surface
    form" shared by migration, proposal resolution, and the exact-match
    auto-link in db.upsert_job_role — grouping is consistent everywhere."""
    return re.sub(r"\s+", " ", (raw or "").strip()).casefold()


def exact_match_concept_id(cur: sqlite3.Cursor, normalized_name: str) -> int | None:
    """§7.3 step 1: case-folded match against active concept.canonical_name /
    concept_alias.alias. Cheap enough to call inline on every skill insert."""
    if not normalized_name:
        return None
    cur.execute(
        "SELECT id FROM concept WHERE status = 'active' AND LOWER(canonical_name) = ?",
        (normalized_name,),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        """
        SELECT c.id FROM concept c
        JOIN concept_alias a ON a.concept_id = c.id
        WHERE c.status = 'active' AND LOWER(a.alias) = ?
        """,
        (normalized_name,),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def ensure_concept_embeddings(cur: sqlite3.Cursor) -> int:
    """Backfill d_embedding rows for active concepts missing a vector at the
    current embedding model. Idempotent (INSERT OR REPLACE on the PK).
    Returns the number of vectors computed."""
    model = embedding_model_name()
    cur.execute(
        """
        SELECT c.id, c.canonical_name, c.definition FROM concept c
        WHERE c.status = 'active' AND NOT EXISTS (
            SELECT 1 FROM d_embedding d
            WHERE d.owner_kind = 'concept' AND d.owner_id = c.id AND d.model = ?
        )
        """,
        (model,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    now = datetime.now(timezone.utc).isoformat()
    computed = 0
    for row in rows:
        text = row["canonical_name"]
        if row["definition"]:
            text = f'{text}: {row["definition"]}'
        vec = embed_text(text)
        if not vec:
            continue
        cur.execute(
            "INSERT OR REPLACE INTO d_embedding (owner_kind, owner_id, model, dim, vector, computed_at) "
            "VALUES ('concept', ?, ?, ?, ?, ?)",
            (row["id"], model, len(vec), dumps_vec(vec), now),
        )
        computed += 1
    return computed


def nearest_concept(cur: sqlite3.Cursor, surface_form: str) -> tuple[int, float] | None:
    """§7.3 step 2: embed the surface form, brute-force cosine (fine per §3.5,
    well under the ~50k-concept threshold) against active concepts'
    d_embedding rows. Returns the single best (concept_id, similarity) for
    concept_proposal.nearest_concept_id/nearest_similarity."""
    vec = embed_text(surface_form)
    if not vec:
        return None
    model = embedding_model_name()
    cur.execute(
        """
        SELECT d.owner_id AS concept_id, d.vector FROM d_embedding d
        JOIN concept c ON c.id = d.owner_id
        WHERE d.owner_kind = 'concept' AND d.model = ? AND c.status = 'active'
        """,
        (model,),
    )
    best_id, best_sim = None, None
    for row in cur.fetchall():
        sim = cosine_similarity(vec, loads_vec(row["vector"]))
        if sim is None:
            continue
        if best_sim is None or sim > best_sim:
            best_id, best_sim = row["concept_id"], sim
    return (best_id, best_sim) if best_id is not None else None


def run_pass_b(conn: sqlite3.Connection) -> dict:
    """The concrete Phase 1 implementation of "Pass B over the posting
    corpus" (docs/11 §7.1, scoped per the Phase 1 build notes in §11):
    operates on job_role_skills.name rather than fresh document-text
    extraction, since there's no LLM here to produce spans. For every
    unresolved skill row: exact match auto-resolves it; otherwise it joins a
    concept_proposal grouped by normalized surface form, carrying a
    nearest-concept suggestion. Safe to re-run repeatedly, including after
    new postings are imported — unlike Phase 0's one-time backfill, this is
    designed to be a recurring maintenance pass."""
    cur = conn.cursor()
    ensure_concept_embeddings(cur)

    cur.execute("SELECT id, name FROM job_role_skills WHERE resolved_concept_id IS NULL")
    unresolved = [dict(r) for r in cur.fetchall()]

    auto_resolved = 0
    surface_forms: dict[str, list[int]] = {}
    for row in unresolved:
        normalized = normalize_name(row["name"])
        if not normalized:
            continue
        concept_id = exact_match_concept_id(cur, normalized)
        if concept_id is not None:
            cur.execute(
                "UPDATE job_role_skills SET resolved_concept_id = ? WHERE id = ?",
                (concept_id, row["id"]),
            )
            auto_resolved += 1
        else:
            surface_forms.setdefault(normalized, []).append(row["id"])

    proposals_created = proposals_updated = 0
    for normalized, ids in surface_forms.items():
        occurrence_count = len(ids)
        cur.execute(
            "SELECT id FROM concept_proposal WHERE surface_form = ? AND status = 'pending'",
            (normalized,),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE concept_proposal SET occurrence_count = ? WHERE id = ?",
                (occurrence_count, existing["id"]),
            )
            proposals_updated += 1
        else:
            nearest = nearest_concept(cur, normalized)
            cur.execute(
                """
                INSERT INTO concept_proposal
                    (surface_form, occurrence_count, nearest_concept_id, nearest_similarity, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (normalized, occurrence_count, nearest[0] if nearest else None, nearest[1] if nearest else None),
            )
            proposals_created += 1

    conn.commit()
    return {
        "auto_resolved": auto_resolved,
        "proposals_created": proposals_created,
        "proposals_updated": proposals_updated,
    }
