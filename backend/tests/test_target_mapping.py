"""app.target_mapping.resolve_requirements — resolving a target's typed-in
requirement list against the vocabulary (explicit concept_id, exact-match by
name, or left unmapped), and the once-per-batch active-concept verification
this build collapsed from one query per skill (routes/targets.py's
resolve-requirements endpoint runs this live as a target's requirement list
is edited and re-previewed, so N skills previously meant N round trips)."""

import pytest
from fastapi import HTTPException

from app import db
from app.target_mapping import resolve_requirements


def _active_concept(cur, name, type_code="tool"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _deprecated_concept(cur, name, type_code="tool"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'deprecated', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


class _CountingCursor:
    """Wraps a real cursor, counting `.execute()` calls without changing
    behaviour — everything else proxies straight through to the real
    cursor, including `.fetchone()`/`.fetchall()` reading the last
    `.execute()`'s result set."""

    def __init__(self, cur):
        self._cur = cur
        self.query_count = 0

    def execute(self, *args, **kwargs):
        self.query_count += 1
        return self._cur.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def test_resolve_requirements_verifies_every_candidate_in_one_query(client):
    """Five skills, each explicitly mapped to a distinct active concept,
    must cost one verification query total, not five single-row ones."""
    with db.db_cursor() as raw_cur:
        concept_ids = [_active_concept(raw_cur, f"Concept {i}") for i in range(5)]
        counting = _CountingCursor(raw_cur)
        skills = [{"name": f"Concept {i}", "concept_id": concept_ids[i], "mapping_reviewed": True} for i in range(5)]
        result = resolve_requirements(counting, skills)

    assert [r["concept_id"] for r in result] == concept_ids
    assert all(r["mapping_status"] == "mapped" for r in result)
    assert counting.query_count == 1


def test_resolve_requirements_dedupes_repeated_concept_across_skills(client):
    """Two skills explicitly mapped to the *same* concept still cost one
    verification query, not two."""
    with db.db_cursor() as raw_cur:
        concept_id = _active_concept(raw_cur, "Shared Concept")
        counting = _CountingCursor(raw_cur)
        skills = [
            {"name": "Shared Concept", "concept_id": concept_id, "mapping_reviewed": True},
            {"name": "Shared Concept (again)", "concept_id": concept_id, "mapping_reviewed": True},
        ]
        result = resolve_requirements(counting, skills)

    assert [r["concept_id"] for r in result] == [concept_id, concept_id]
    assert counting.query_count == 1


def test_resolve_requirements_query_count_does_not_grow_with_requirement_count(client):
    """The full mix in one call — several requirements that auto-match a
    canonical name, several that only auto-match via an alias, and several
    that match nothing at all — must still cost a small, fixed number of
    queries: one bulk canonical-name match, one bulk alias match, one
    active-concept verification for whatever resolved. Before batching the
    auto-match step, this alone cost up to 2 queries per auto-matched
    requirement (exact_match_concept_id) plus 1 per resolved requirement
    (the old per-item verification) — here that would have been up to
    8*2 + 8*2 + 8 = 40 queries for 24 requirements; batched, it's 3."""
    with db.db_cursor() as raw_cur:
        canonical_names = [f"Auto Match {i}" for i in range(8)]
        for name in canonical_names:
            _active_concept(raw_cur, name)

        aliased_id = _active_concept(raw_cur, "Aliased Target Concept")
        alias_names = [f"target-alias-{i}" for i in range(8)]
        for alias in alias_names:
            raw_cur.execute(
                "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) VALUES (%s, %s, 'curator', now())",
                (aliased_id, alias),
            )

        unmatched_names = [f"nobody curated this {i}" for i in range(8)]

        counting = _CountingCursor(raw_cur)
        skills = (
            [{"name": name} for name in canonical_names]
            + [{"name": alias} for alias in alias_names]
            + [{"name": name} for name in unmatched_names]
        )
        result = resolve_requirements(counting, skills)

    mapped = [r for r in result if r["mapping_status"] == "mapped"]
    unmapped = [r for r in result if r["mapping_status"] == "unmapped"]
    assert len(mapped) == 16  # 8 canonical + 8 alias
    assert len(unmapped) == 8
    assert all(r["concept_id"] == aliased_id for r in result[8:16])
    assert counting.query_count == 3  # bulk canonical match, bulk alias match, active-id verification


def test_resolve_requirements_skips_the_verification_query_when_nothing_resolves(client):
    """`mapping_reviewed=True` with no explicit concept_id skips the
    exact-match lookup too (per resolve_requirements' own short-circuit —
    a caller marking something reviewed with nothing chosen means "treat
    as unmapped", not "go try to auto-match it") — so nothing here resolves
    to a candidate id at all, and the batch verification query is never
    issued."""
    with db.db_cursor() as raw_cur:
        counting = _CountingCursor(raw_cur)
        result = resolve_requirements(counting, [{"name": "Whatever", "mapping_reviewed": True}])

    assert result[0]["mapping_status"] == "unmapped"
    assert result[0]["concept_id"] is None
    assert counting.query_count == 0


def test_resolve_requirements_resolves_by_exact_match_when_no_explicit_id(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        result = resolve_requirements(cur, [{"name": "python"}])  # case-insensitive exact match
    assert result[0]["concept_id"] == concept_id
    assert result[0]["canonical_name"] == "Python"
    assert result[0]["mapping_status"] == "mapped"


def test_resolve_requirements_leaves_genuinely_unresolvable_skill_unmapped(client):
    with db.db_cursor() as cur:
        result = resolve_requirements(cur, [{"name": "Some skill nobody curated yet"}])
    assert result[0]["concept_id"] is None
    assert result[0]["canonical_name"] is None
    assert result[0]["mapping_status"] == "unmapped"


def test_resolve_requirements_maps_an_explicit_reviewed_active_concept(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        result = resolve_requirements(cur, [{"name": "Python", "concept_id": concept_id, "mapping_reviewed": True}])
    assert result[0]["concept_id"] == concept_id
    assert result[0]["mapping_status"] == "mapped"


def test_resolve_requirements_rejects_an_explicit_mapping_that_is_not_confirmed_reviewed(client):
    """An explicit concept_id must be paired with mapping_reviewed=True — a
    curator picking a concept without confirming it is a validation error,
    not silently downgraded to 'unmapped'."""
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        with pytest.raises(HTTPException) as exc_info:
            resolve_requirements(cur, [{"name": "Python", "concept_id": concept_id, "mapping_reviewed": False}])
    assert exc_info.value.status_code == 422


def test_resolve_requirements_rejects_an_explicit_reviewed_but_inactive_concept(client):
    with db.db_cursor() as cur:
        concept_id = _deprecated_concept(cur, "Old Thing")
        with pytest.raises(HTTPException) as exc_info:
            resolve_requirements(cur, [{"name": "Old Thing", "concept_id": concept_id, "mapping_reviewed": True}])
    assert exc_info.value.status_code == 422


def test_resolve_target_requirements_endpoint_batches_end_to_end(client):
    """The live HTTP endpoint TargetDraftEditor calls on every re-preview,
    exercised for real (payload validated through TargetRequirement)."""
    with db.db_cursor() as cur:
        python_id = _active_concept(cur, "Python")
        sql_id = _active_concept(cur, "SQL")

    resp = client.post(
        "/api/targets/resolve-requirements",
        json=[
            {"name": "Python", "concept_id": python_id, "mapping_reviewed": True},
            {"name": "sql"},
            {"name": "Unmapped thing entirely"},
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["concept_id"] == python_id
    assert body[0]["mapping_status"] == "mapped"
    assert body[1]["concept_id"] == sql_id
    assert body[1]["mapping_status"] == "mapped"
    assert body[2]["mapping_status"] == "unmapped"
