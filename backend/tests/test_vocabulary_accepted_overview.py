"""Accepted Vocabulary overview panel (Phase 4 prompt §14), HTTP-level
against the real Postgres test database. No AI call is involved anywhere
in this feature."""

from app import db


def _concept(cur, name, *, type_code="tool", status="active", definition="a definition"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at, reviewed_at) "
        "VALUES (%s, %s, %s, %s, 'curator', now(), now()) RETURNING id",
        (type_code, name, definition, status),
    )
    return str(cur.fetchone()["id"])


def _alias(cur, concept_id, alias):
    cur.execute("INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) VALUES (%s, %s, 'curator', now())", (concept_id, alias))


def _dossier(cur, concept_id, *, status="active", related_concepts="[]"):
    cur.execute(
        "INSERT INTO jobber.concept_dossier (concept_id, status, origin, generator_version, related_concepts) "
        "VALUES (%s, %s, 'curator', 'test', %s::jsonb)",
        (concept_id, status, related_concepts),
    )


def test_counts_by_type(client):
    with db.db_cursor() as cur:
        _concept(cur, "Python", type_code="tool")
        _concept(cur, "SQL", type_code="tool")
        _concept(cur, "Reserving", type_code="function")

    overview = client.get("/api/vocabulary/accepted-overview").json()
    by_type = {row["type_code"]: row["count"] for row in overview["by_type"]}
    assert by_type["tool"] == 2
    assert by_type["function"] == 1
    assert overview["total_active_concepts"] == 3


def test_only_active_concepts_counted(client):
    with db.db_cursor() as cur:
        _concept(cur, "Active Concept", status="active")
        _concept(cur, "Deprecated Concept", status="deprecated")
        _concept(cur, "Proposed Concept", status="proposed")

    overview = client.get("/api/vocabulary/accepted-overview").json()
    assert overview["total_active_concepts"] == 1


def test_missing_definition(client):
    with db.db_cursor() as cur:
        with_def = _concept(cur, "Has Definition", definition="something")
        without_def = _concept(cur, "No Definition", definition=None)
        blank_def = _concept(cur, "Blank Definition", definition="   ")

    overview = client.get("/api/vocabulary/accepted-overview").json()
    missing_ids = set(overview["missing_definition"]["concept_ids"])
    assert without_def in missing_ids
    assert blank_def in missing_ids
    assert with_def not in missing_ids
    assert overview["missing_definition"]["count"] == 2


def test_no_active_dossier(client):
    with db.db_cursor() as cur:
        with_dossier = _concept(cur, "Has Dossier")
        _dossier(cur, with_dossier, status="active")
        without_dossier = _concept(cur, "No Dossier")
        draft_only = _concept(cur, "Draft Only")
        _dossier(cur, draft_only, status="draft")

    overview = client.get("/api/vocabulary/accepted-overview").json()
    no_dossier_ids = set(overview["no_active_dossier"]["concept_ids"])
    assert without_dossier in no_dossier_ids
    assert draft_only in no_dossier_ids  # a draft is not an active dossier
    assert with_dossier not in no_dossier_ids


def test_dossier_with_no_related_concepts(client):
    with db.db_cursor() as cur:
        empty_related = _concept(cur, "Empty Related")
        _dossier(cur, empty_related, status="active", related_concepts="[]")
        other = _concept(cur, "Other Concept")
        with_related = _concept(cur, "Has Related")
        _dossier(
            cur, with_related, status="active",
            related_concepts=f'[{{"concept_id": "{other}", "canonical_name": "Other Concept", "type_code": "tool", "relationship": "related to", "explanation": "x"}}]',
        )

    overview = client.get("/api/vocabulary/accepted-overview").json()
    no_related_ids = set(overview["dossier_no_related_concepts"]["concept_ids"])
    assert empty_related in no_related_ids
    assert with_related not in no_related_ids


def test_alias_count(client):
    with db.db_cursor() as cur:
        concept_id = _concept(cur, "Solvency II")
        _alias(cur, concept_id, "SII")
        _alias(cur, concept_id, "Solvency 2")

    overview = client.get("/api/vocabulary/accepted-overview").json()
    assert overview["alias_count"] == 2


def test_recent_concepts_limited_to_ten_most_recent(client):
    with db.db_cursor() as cur:
        for i in range(12):
            cur.execute(
                "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at, reviewed_at) "
                "VALUES ('tool', %s, 'active', 'curator', now() - interval '1 hour' * %s, now() - interval '1 hour' * %s)",
                (f"Concept {i}", i, i),
            )

    overview = client.get("/api/vocabulary/accepted-overview").json()
    assert len(overview["recent_concepts"]) == 10
    # Most recently reviewed first.
    assert overview["recent_concepts"][0]["canonical_name"] == "Concept 0"
