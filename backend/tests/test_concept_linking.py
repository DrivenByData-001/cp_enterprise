"""Exact concept linking, unresolved concept proposals, and pgvector-backed
nearest-neighbour retrieval (docs/11 §7.3, brief §16)."""

from app import concept_linking, db
from app.embeddings import nearest_by_vector, set_embedding


def _active_concept(cur, name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


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


class _CountingCursor:
    """Wraps a real cursor, counting `.execute()` calls without changing
    behaviour — everything else proxies straight through."""

    def __init__(self, cur):
        self._cur = cur
        self.query_count = 0

    def execute(self, *args, **kwargs):
        self.query_count += 1
        return self._cur.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def test_bulk_exact_match_resolves_canonical_alias_and_unmatched_together(client):
    """One call covering all three outcomes at once: a canonical-name hit,
    an alias hit, and a name matching neither — each name gets exactly the
    same answer bulk_exact_match_concept_ids would give one at a time via
    exact_match_concept_id."""
    with db.db_cursor() as cur:
        python_id = _active_concept(cur, "Python")
        sql_id = _active_concept(cur, "SQL")
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) VALUES (%s, %s, 'curator', now())",
            (sql_id, "structured query language"),
        )

        matches = concept_linking.bulk_exact_match_concept_ids(
            cur, ["python", "structured query language", "totally unmatched term"]
        )

    assert matches == {"python": python_id, "structured query language": sql_id}


def test_bulk_exact_match_prefers_canonical_name_over_a_same_text_alias(client):
    """A name that matches one concept's canonical_name AND a different
    concept's alias must resolve to the canonical match — the alias query
    only ever runs for names that didn't already match a canonical_name."""
    with db.db_cursor() as cur:
        canonical_id = _active_concept(cur, "Widget")
        other_id = _active_concept(cur, "Something else")
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) VALUES (%s, %s, 'curator', now())",
            (other_id, "widget"),
        )

        matches = concept_linking.bulk_exact_match_concept_ids(cur, ["widget"])

    assert matches == {"widget": canonical_id}


def test_bulk_exact_match_dedupes_repeated_names_and_ignores_blanks(client):
    with db.db_cursor() as cur:
        python_id = _active_concept(cur, "Python")
        counting = _CountingCursor(cur)
        matches = concept_linking.bulk_exact_match_concept_ids(counting, ["python", "python", "", None])

    assert matches == {"python": python_id}
    assert counting.query_count == 1  # every name matched a canonical_name; alias query never runs


def test_bulk_exact_match_returns_empty_without_querying_for_empty_input(client):
    with db.db_cursor() as cur:
        counting = _CountingCursor(cur)
        assert concept_linking.bulk_exact_match_concept_ids(counting, []) == {}
    assert counting.query_count == 0


def test_bulk_exact_match_query_count_does_not_grow_with_item_count(client):
    """The whole point: N names split across canonical/alias/unmatched must
    cost a small, fixed number of queries (at most one for canonical_name,
    one for alias), never one (or two) per name."""
    with db.db_cursor() as cur:
        canonical_names = [f"Canonical {i}" for i in range(8)]
        for name in canonical_names:
            _active_concept(cur, name)

        aliased_id = _active_concept(cur, "Aliased Concept")
        alias_names = [f"alias-{i}" for i in range(8)]
        for alias in alias_names:
            cur.execute(
                "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) VALUES (%s, %s, 'curator', now())",
                (aliased_id, alias),
            )

        unmatched_names = [f"nobody curated {i}" for i in range(8)]

        counting = _CountingCursor(cur)
        wanted = [n.lower() for n in canonical_names] + alias_names + unmatched_names
        matches = concept_linking.bulk_exact_match_concept_ids(counting, wanted)

    assert len(matches) == 16  # 8 canonical + 8 alias; the 8 unmatched names are simply absent
    assert all(matches[n.lower()] for n in canonical_names)
    assert all(matches[a] == aliased_id for a in alias_names)
    assert counting.query_count == 2  # one canonical bulk match, one alias bulk match — not 24


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
        role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "R"}, skills=[])
        cur.execute(
            "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form) VALUES (%s, %s), (%s, %s)",
            (role_id, "Python", role_id, "some brand new skill nobody curated yet"),
        )

        counts = concept_linking.run_pass_b(cur)

        cur.execute("SELECT canonical_concept_id FROM jobber.role_skill_observation WHERE surface_form = 'Python'")
        resolved = str(cur.fetchone()["canonical_concept_id"])
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
