"""Local embedding model + jobber.d_embedding accessors.

Embeddings are a derived, rebuildable signal (doc 11 §4.6) — they live in one
place, `jobber.d_embedding` (a pgvector column), never as a column on a primary
entity. `embed_text`/`embedding_model_name` are unchanged from pre-Phase-2;
everything below them is new: Postgres/pgvector-backed storage and retrieval,
replacing the old per-row `TEXT`-JSON `embedding` column and Python brute-force
cosine scan.
"""

import threading

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding

                _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    text = (text or "").strip()
    if not text:
        return []
    model = _get_model()
    vec = next(model.embed([text]))
    return vec.tolist()


def embedding_model_name() -> str:
    return MODEL_NAME


def cosine_similarity(a: list[float], b: list[float]) -> float | None:
    """Python cosine, for small in-memory sets already fetched from the DB
    (e.g. ranking a handful of stepping-stone candidates). Anything searching
    across a whole table should use pgvector in SQL instead — see
    `nearest_by_vector` below."""
    if not a or not b:
        return None
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return None
    return float(np.dot(va, vb) / denom)


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts its text input format directly as a query parameter —
    no special adapter needed for a fixed-width vector(N) column."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _parse_vector(raw) -> list[float]:
    """The inverse of `_vector_literal`. psycopg has no built-in adapter for
    pgvector's third-party `vector` type (unlike core types), so a fetched
    `vector` column comes back as its raw text form, e.g. "[0.1,0.2,0.3]" —
    parse it back into floats rather than accidentally iterating the string's
    characters."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    text = raw.strip().strip("[]")
    return [float(x) for x in text.split(",")] if text else []


def set_embedding(cur, owner_kind: str, owner_id: str, vector: list[float], model: str | None = None) -> None:
    """Upsert a jobber.d_embedding row. No-op if `vector` is empty (mirrors
    the pre-Phase-2 behaviour of simply not writing an embedding for blank
    text) rather than writing a zero vector that would rank as spuriously
    similar to everything."""
    if not vector:
        return
    model = model or embedding_model_name()
    cur.execute(
        """
        INSERT INTO jobber.d_embedding (owner_kind, owner_id, model, dim, vector, computed_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (owner_kind, owner_id, model) DO UPDATE SET
            dim = EXCLUDED.dim, vector = EXCLUDED.vector, computed_at = now()
        """,
        (owner_kind, owner_id, model, len(vector), _vector_literal(vector)),
    )


def get_embedding(cur, owner_kind: str, owner_id: str, model: str | None = None) -> list[float]:
    model = model or embedding_model_name()
    cur.execute(
        "SELECT vector FROM jobber.d_embedding WHERE owner_kind = %s AND owner_id = %s AND model = %s",
        (owner_kind, owner_id, model),
    )
    row = cur.fetchone()
    if not row:
        return []
    return _parse_vector(row["vector"])


def get_embeddings(cur, owner_kind: str, owner_ids: list[str], model: str | None = None) -> dict[str, list[float]]:
    if not owner_ids:
        return {}
    model = model or embedding_model_name()
    cur.execute(
        "SELECT owner_id, vector FROM jobber.d_embedding "
        "WHERE owner_kind = %s AND model = %s AND owner_id = ANY(%s::uuid[])",
        (owner_kind, model, owner_ids),
    )
    return {str(row["owner_id"]): _parse_vector(row["vector"]) for row in cur.fetchall()}


def nearest_by_vector(
    cur,
    owner_kind: str,
    query_vector: list[float],
    model: str | None = None,
    limit: int = 1,
    exclude_owner_id: str | None = None,
    owner_id_filter: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Postgres-native nearest-neighbour lookup via pgvector's cosine-distance
    operator (`<=>`), replacing the pre-Phase-2 Python brute-force scan
    (concept_linking.py's old `nearest_concept`). Returns
    [(owner_id, cosine_similarity), ...], nearest first. `owner_id_filter`
    scopes the search to a specific id set (e.g. only 'active' concepts,
    resolved by the caller) since d_embedding itself carries no status."""
    if not query_vector:
        return []
    if owner_id_filter is not None and not owner_id_filter:
        return []
    model = model or embedding_model_name()
    qv = _vector_literal(query_vector)

    where_clauses = ["owner_kind = %s", "model = %s"]
    where_params: list = [owner_kind, model]
    if exclude_owner_id is not None:
        where_clauses.append("owner_id != %s::uuid")
        where_params.append(exclude_owner_id)
    if owner_id_filter is not None:
        where_clauses.append("owner_id = ANY(%s::uuid[])")
        where_params.append(owner_id_filter)
    where_sql = " AND ".join(where_clauses)

    cur.execute(
        f"""
        SELECT owner_id, 1 - (vector <=> %s) AS similarity
        FROM jobber.d_embedding
        WHERE {where_sql}
        ORDER BY vector <=> %s
        LIMIT %s
        """,
        [qv, *where_params, qv, limit],
    )
    return [(str(row["owner_id"]), row["similarity"]) for row in cur.fetchall()]


def role_embedding_text(role: dict) -> str:
    """The single canonical text representation of a `role_instance` used to
    compute its embedding — the one function every write path (posting
    import, target import, source-aware raw ingest) and the rebuild/backfill
    path (`rebuild_role_embeddings` below) must agree on, so a migrated role
    rebuilt today and the same role saved/imported today land on identical
    text (this is the fix for the Space regression: migrated roles never got
    a `d_embedding` row backfilled after Phase 2 moved embeddings off the
    old per-row column).

    `role` must be shaped like `db.flatten_role_instance`'s output (or an
    equivalent dict built at import time, before the row exists — see
    `routes/import_routes.py::compose_role_text` /
    `routes/targets.py::_compose_target_text`, both now thin wrappers around
    this): needs `node_type` plus whichever of `title`/`description`/
    `requirements`/`responsibilities`/`key_skills_summary` (posting-shaped)
    or `title`/`summary`/`description`/`typical_tasks`/`skill_decomposition`/
    `technical_subjects` (target-shaped) are populated.

    Dispatches on `node_type` rather than on which fields happen to be set —
    a target can also carry `description`, so guessing from field presence
    would silently drop `summary`/`typical_tasks`/skills for a described
    target. This does not consult `jobber.document` at all; when a role has
    a linked document whose content is the authoritative capture (true for
    every current write path — the document is created from this exact same
    text), preferring that verbatim text is the caller's job
    (`rebuild_role_embeddings` does this) — this function is the pure,
    DB-free fallback/reference composition.
    """
    if role.get("node_type") == "posting":
        parts = [
            role.get("title"),
            role.get("description"),
            role.get("requirements"),
            role.get("responsibilities"),
            role.get("key_skills_summary"),
        ]
        return "\n\n".join(p for p in parts if p)

    skill_decomposition = role.get("skill_decomposition") or []
    technical_subjects = role.get("technical_subjects") or []
    skill_names = [s["skill"] for s in skill_decomposition if isinstance(s, dict) and s.get("skill")]
    subject_names = [s["subject"] for s in technical_subjects if isinstance(s, dict) and s.get("subject")]
    typical_tasks = role.get("typical_tasks") or []
    parts = [
        role.get("title"),
        role.get("summary"),
        role.get("description"),
        "\n".join(typical_tasks) if typical_tasks else None,
        ("Skills: " + ", ".join(skill_names)) if skill_names else None,
        ("Technical subjects: " + ", ".join(subject_names)) if subject_names else None,
    ]
    return "\n\n".join(p for p in parts if p)


def rebuild_role_embeddings(cur, *, missing_only: bool = True) -> dict:
    """Backfill/rebuild `jobber.d_embedding` rows for `role_instance`, for
    the *current* `MODEL_NAME` only (brief: "an embedding belonging to an
    older/different model must not satisfy the current-model check").

    `missing_only=True` (default): only roles with no current-model row are
    (re)computed — safe and cheap to run repeatedly, and idempotent (a
    second run against unchanged data creates/updates nothing further).
    `missing_only=False` (force): every role's current-model embedding is
    recomputed from its canonical text and upserted, even one that already
    exists — for after a source-text correction, not routine use.

    Text per role: the role's linked `jobber.document.content_text` when one
    exists and is non-empty — every current write path already creates that
    document *from* this exact text, so it is the authoritative capture, not
    a guess — else `role_embedding_text` reconstructs it from the role's own
    stored columns (the only option for a legacy row with no linked
    document). A role with neither is skipped, never fed a fabricated or
    empty vector.

    Reads `jobber.role_instance` and `jobber.document`; writes only
    `jobber.d_embedding` rows with `owner_kind='role_instance'` — no source
    table is ever touched, and no other `owner_kind` (`concept`,
    `profile360_snapshot`, `document`) is read or written.
    """
    from .db import flatten_role_instance  # lazy: keeps embeddings.py free of a module-load-time dependency on db.py

    model = embedding_model_name()

    cur.execute(
        "SELECT ri.*, d.content_text AS document_content_text "
        "FROM jobber.role_instance ri LEFT JOIN jobber.document d ON d.id = ri.document_id"
    )
    all_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT owner_id FROM jobber.d_embedding WHERE owner_kind = 'role_instance' AND model = %s",
        (model,),
    )
    has_current_model = {str(r["owner_id"]) for r in cur.fetchall()}

    candidates = all_rows if not missing_only else [r for r in all_rows if str(r["id"]) not in has_current_model]

    created = updated = skipped = 0
    for row in candidates:
        role_id = str(row["id"])
        document_text = (row.pop("document_content_text", None) or "").strip()
        role = flatten_role_instance(row)
        text = document_text or role_embedding_text(role)
        vector = embed_text(text) if text.strip() else []
        if not vector:
            skipped += 1
            continue
        set_embedding(cur, "role_instance", role_id, vector, model=model)
        if role_id in has_current_model:
            updated += 1
        else:
            created += 1

    return {
        "model": model,
        "roles_scanned": len(all_rows),
        "embeddings_created": created,
        "embeddings_updated": updated,
        "skipped": skipped,
    }


def ensure_profile_embedding(cur) -> tuple[str | None, list[float]]:
    """The "current profile vector" every similarity computation (Dashboard,
    Space, Targets) needs, sourced from profile360's current snapshot instead
    of a jobber-local narrative (docs/14 §9 — jobber does not recreate
    person-side truth). The narrative text itself is never copied into
    jobber; only a derived, rebuildable embedding is cached here, keyed by
    the profile360 snapshot's own id — the same "embeddings are a signal,
    never the sole home of a fact" principle d_embedding already applies to
    concepts/role_instances/documents (doc 11 §4.6).

    Returns (profile360_snapshot_id, vector) — (None, []) if profile360 has
    no snapshot yet, or is unreachable from this connection (a local/test
    database with no profile360 schema at all)."""
    from .profile360_reader import Profile360UnavailableError, full_text, get_current_snapshot

    try:
        snapshot = get_current_snapshot(cur)
    except Profile360UnavailableError:
        return None, []
    if not snapshot:
        return None, []

    snapshot_id = str(snapshot["id"])
    vector = get_embedding(cur, "profile360_snapshot", snapshot_id)
    if vector:
        return snapshot_id, vector

    text = full_text(snapshot)
    if not text:
        return snapshot_id, []
    vector = embed_text(text)
    if vector:
        set_embedding(cur, "profile360_snapshot", snapshot_id, vector)
    return snapshot_id, vector
