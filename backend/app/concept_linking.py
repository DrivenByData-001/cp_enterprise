"""Concept linking (docs/11-capability-model-design.md §7.1, §7.3), Postgres port.

Implements canonicalisation-cascade steps 1-2 (exact match, then embedding kNN
suggestion) as before. Step 3 (model adjudication over the top candidates) is
now implemented too, in `app/extraction.py`, for the two workflows that need
it (role requirement extraction, profile360 mapping) — this module stays the
cheap, always-on cascade used inline on every skill/observation insert; the
AI-adjudicated cascade is a separate, explicit call, not folded in here.

Kept out of db.py deliberately, so db.py doesn't acquire a module-load-time
dependency on embeddings.py (today routes/roles.py and routes/targets.py, plus
app/extraction.py, import it).
"""

import re
from datetime import datetime, timezone

from .embeddings import embed_text, embedding_model_name, nearest_by_vector, set_embedding


def normalize_name(raw: str) -> str:
    """Case-fold + collapse whitespace. The one definition of "same surface
    form" shared by migration, proposal resolution, and the exact-match
    auto-link in db.upsert_role_instance — grouping is consistent everywhere."""
    return re.sub(r"\s+", " ", (raw or "").strip()).casefold()


def exact_match_concept_id(cur, normalized_name: str) -> int | None:
    """§7.3 step 1: case-folded match against active concept.canonical_name /
    concept_alias.alias. Cheap enough to call inline on every skill insert."""
    if not normalized_name:
        return None
    cur.execute(
        "SELECT id FROM jobber.concept WHERE status = 'active' AND LOWER(canonical_name) = %s",
        (normalized_name,),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        """
        SELECT c.id FROM jobber.concept c
        JOIN jobber.concept_alias a ON a.concept_id = c.id
        WHERE c.status = 'active' AND LOWER(a.alias) = %s
        """,
        (normalized_name,),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def ensure_concept_embeddings(cur) -> int:
    """Backfill d_embedding rows for active concepts missing a vector at the
    current embedding model. Idempotent (upsert on the PK). Returns the number
    of vectors computed."""
    model = embedding_model_name()
    cur.execute(
        """
        SELECT c.id, c.canonical_name, c.definition FROM jobber.concept c
        WHERE c.status = 'active' AND NOT EXISTS (
            SELECT 1 FROM jobber.d_embedding d
            WHERE d.owner_kind = 'concept' AND d.owner_id = c.id AND d.model = %s
        )
        """,
        (model,),
    )
    rows = cur.fetchall()
    computed = 0
    for row in rows:
        text = row["canonical_name"]
        if row["definition"]:
            text = f'{text}: {row["definition"]}'
        vec = embed_text(text)
        if not vec:
            continue
        set_embedding(cur, "concept", row["id"], vec, model=model)
        computed += 1
    return computed


def nearest_concept(cur, surface_form: str, limit: int = 1) -> tuple[int, float] | None:
    """§7.3 step 2: embed the surface form, pgvector cosine-distance search
    over active concepts' d_embedding rows. Returns the single best
    (concept_id, similarity) for concept_proposal.nearest_concept_id/
    nearest_similarity. See `nearest_concepts` for the top-N variant Pass B's
    model-adjudication step (§7.3 step 3, app/extraction.py) needs."""
    results = nearest_concepts(cur, surface_form, limit=limit)
    return results[0] if results else None


def nearest_concepts(cur, surface_form: str, limit: int = 10, type_codes: list[str] | None = None) -> list[tuple[int, float]]:
    """Top-`limit` active concepts nearest to `surface_form` by embedding
    cosine similarity, optionally restricted to a set of concept types. This
    is the candidate-retrieval half of §7.3's cascade; the adjudication half
    (present the surface form + these candidates to a model, it may only pick
    from the list or decline) is `app/extraction.py::adjudicate_concept`."""
    vec = embed_text(surface_form)
    if not vec:
        return []

    # A concept can be active with no d_embedding row yet (just curated, or
    # created since the last Pass B run) — without this, such a concept could
    # never be retrieved as a candidate here, and every extraction task built
    # on this cascade (requirement extraction, profile360 mapping) would
    # silently treat it as nonexistent instead of proposing/using it.
    ensure_concept_embeddings(cur)

    clauses = ["status = 'active'"]
    params: list = []
    if type_codes:
        clauses.append("type_code = ANY(%s)")
        params.append(type_codes)
    cur.execute(f"SELECT id FROM jobber.concept WHERE {' AND '.join(clauses)}", params)
    active_ids = [r["id"] for r in cur.fetchall()]
    if not active_ids:
        return []

    return nearest_by_vector(cur, "concept", vec, limit=limit, owner_id_filter=active_ids)


def run_pass_b(cur) -> dict:
    """The concrete Phase 1 implementation of "Pass B over the posting
    corpus" (docs/11 §7.1, scoped per the Phase 1 build notes in §11):
    operates on role_skill_observation.name rather than fresh document-text
    extraction. For every unresolved skill row: exact match auto-resolves it;
    otherwise it joins a concept_proposal grouped by normalized surface form,
    carrying a nearest-concept suggestion. Safe to re-run repeatedly. Takes a
    cursor (not a connection) — callers control the transaction, consistent
    with the rest of this Postgres port."""
    ensure_concept_embeddings(cur)

    cur.execute("SELECT id, name FROM jobber.role_skill_observation WHERE resolved_concept_id IS NULL")
    unresolved = cur.fetchall()

    auto_resolved = 0
    surface_forms: dict[str, list[int]] = {}
    for row in unresolved:
        normalized = normalize_name(row["name"])
        if not normalized:
            continue
        concept_id = exact_match_concept_id(cur, normalized)
        if concept_id is not None:
            cur.execute(
                "UPDATE jobber.role_skill_observation SET resolved_concept_id = %s WHERE id = %s",
                (concept_id, row["id"]),
            )
            auto_resolved += 1
        else:
            surface_forms.setdefault(normalized, []).append(row["id"])

    proposals_created = proposals_updated = 0
    for normalized, ids in surface_forms.items():
        occurrence_count = len(ids)
        cur.execute(
            "SELECT id FROM jobber.concept_proposal WHERE surface_form = %s AND status = 'pending'",
            (normalized,),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE jobber.concept_proposal SET occurrence_count = %s WHERE id = %s",
                (occurrence_count, existing["id"]),
            )
            proposals_updated += 1
        else:
            nearest = nearest_concept(cur, normalized)
            cur.execute(
                """
                INSERT INTO jobber.concept_proposal
                    (surface_form, occurrence_count, nearest_concept_id, nearest_similarity, status)
                VALUES (%s, %s, %s, %s, 'pending')
                """,
                (normalized, occurrence_count, nearest[0] if nearest else None, nearest[1] if nearest else None),
            )
            proposals_created += 1

    return {
        "auto_resolved": auto_resolved,
        "proposals_created": proposals_created,
        "proposals_updated": proposals_updated,
    }


def get_or_create_current_vocabulary_version(cur, note: str | None = None) -> int:
    """A real, non-fabricated vocabulary_version_id for extraction_run to
    reference (brief §9): versioned by *change* in active concept count, not
    by every single extraction run. If the current active-concept count
    matches the most recent version's, reuse it; otherwise record a new one.
    """
    cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE status = 'active'")
    current_count = cur.fetchone()["n"]

    cur.execute("SELECT id, concept_count FROM jobber.vocabulary_version ORDER BY id DESC LIMIT 1")
    latest = cur.fetchone()
    if latest and latest["concept_count"] == current_count:
        return latest["id"]

    cur.execute(
        "INSERT INTO jobber.vocabulary_version (created_at, concept_count, note) VALUES (%s, %s, %s) RETURNING id",
        (datetime.now(timezone.utc), current_count, note),
    )
    return cur.fetchone()["id"]
