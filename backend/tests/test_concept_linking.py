"""Exact concept linking, unresolved concept proposals, and pgvector-backed
nearest-neighbour retrieval (docs/11 §7.3, brief §16)."""

from app import concept_linking, db
from app.embeddings import nearest_by_vector, set_embedding


def _active_concept(cur, name: str, type_code: str = "tool") -> int:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return cur.fetchone()["id"]


def test_exact_match_case_folds_and_matches_alias(client):
    """exact_match_concept_id takes an already-normalized search term (every
    call site pre-normalizes via normalize_name — see below) and matches it
    case-insensitively against canonical_name/alias on the DB side."""
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) VALUES (%s, %s, 'curator', now())",
            (concept_id, "py"),
        )

        assert concept_linking.exact_match_concept_id(cur, "python") == concept_id
        assert concept_linking.exact_match_concept_id(cur, concept_linking.normalize_name("PYTHON")) == concept_id
        assert concept_linking.exact_match_concept_id(cur, "py") == concept_id
        assert concept_linking.exact_match_concept_id(cur, "unrelated term") is None


def test_normalize_name_collapses_whitespace_and_case():
    assert concept_linking.normalize_name("  Solvency   II  ") == "solvency ii"


def test_nearest_by_vector_orders_by_cosine_distance(client):
    """Bypasses the (fake) text embedder entirely and writes controlled
    vectors directly, so this is a precise test of the pgvector ordering
    itself rather than of embedding semantics."""
    with db.db_cursor() as cur:
        near_id = _active_concept(cur, "Near concept")
        far_id = _active_concept(cur, "Far concept")
        set_embedding(cur, "concept", near_id, [1.0, 0.0, 0.0] + [0.0] * 381)
        set_embedding(cur, "concept", far_id, [0.0, 1.0, 0.0] + [0.0] * 381)

        results = nearest_by_vector(cur, "concept", [1.0, 0.0, 0.0] + [0.0] * 381, limit=2)

    assert results[0][0] == near_id
    assert results[0][1] > results[1][1]


def test_run_pass_b_auto_resolves_exact_matches_and_proposes_unresolved(client):
    with db.db_cursor() as cur:
        python_id = _active_concept(cur, "Python")
        role_id = db.upsert_role_instance(cur, None, {"kind": "posting", "title": "R"}, skills=[])
        cur.execute(
            "INSERT INTO jobber.role_skill_observation (role_instance_id, name) VALUES (%s, %s), (%s, %s)",
            (role_id, "Python", role_id, "some brand new skill nobody curated yet"),
        )

        counts = concept_linking.run_pass_b(cur)

        cur.execute("SELECT resolved_concept_id FROM jobber.role_skill_observation WHERE name = 'Python'")
        resolved = cur.fetchone()["resolved_concept_id"]
        cur.execute("SELECT status FROM jobber.concept_proposal WHERE surface_form = %s", ("some brand new skill nobody curated yet",))
        proposal = cur.fetchone()

    assert counts["auto_resolved"] == 1
    assert resolved == python_id
    assert counts["proposals_created"] == 1
    assert proposal["status"] == "pending"


def test_get_or_create_current_vocabulary_version_reuses_when_unchanged(client):
    with db.db_cursor() as cur:
        v1 = concept_linking.get_or_create_current_vocabulary_version(cur)
        v2 = concept_linking.get_or_create_current_vocabulary_version(cur)
        assert v1 == v2

        _active_concept(cur, "A new concept")
        v3 = concept_linking.get_or_create_current_vocabulary_version(cur)
        assert v3 != v2
