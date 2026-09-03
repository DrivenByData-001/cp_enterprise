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
