"""Migrated-role embedding backfill regression (Phase 3 finalization pass).

Roles that existed before Phase 2 moved embeddings into jobber.d_embedding
(owner_kind='role_instance') never got a matching row backfilled — this is
the exact bug the Space page reported ("Need at least 2 embedded points to
project" despite >10 role_instance rows existing). `_bare_role_instance`
below simulates that: real stored columns, no jobber.d_embedding row at
all, exactly like a pre-Phase-2 migrated row.
"""

import pytest

from app import db, embeddings


def _approx_vec(a, b):
    """jobber.d_embedding's `vector` column is pgvector's single-precision
    float32 — a value round-tripped through storage loses float64 precision,
    so vector comparisons after a DB read must be approximate, not exact."""
    assert len(a) == len(b)
    assert a == pytest.approx(b, rel=1e-5, abs=1e-6)


def _bare_role_instance(cur, *, title="Legacy Analyst", description="Reserving and Python.", document_id=None) -> str:
    cur.execute(
        "INSERT INTO jobber.role_instance (instance_type, title, description, document_id) "
        "VALUES ('observed_posting', %s, %s, %s) RETURNING id",
        (title, description, document_id),
    )
    return str(cur.fetchone()["id"])


# --- canonical text construction --------------------------------------------

def test_role_embedding_text_dispatches_on_node_type_not_field_presence():
    """A target can also carry `description` — dispatch must not
    misidentify it as a posting and silently drop summary/tasks/skills."""
    target_role = {
        "node_type": "target",
        "title": "Head of Pricing",
        "summary": "Own pricing strategy.",
        "description": "Some description text.",
        "typical_tasks": ["Set rates"],
        "skill_decomposition": [{"skill": "Pricing models"}],
        "technical_subjects": [{"subject": "GLMs"}],
    }
    text = embeddings.role_embedding_text(target_role)
    assert "Own pricing strategy." in text
    assert "Skills: Pricing models" in text
    assert "Technical subjects: GLMs" in text


def test_role_embedding_text_posting_composition():
    role = {
        "node_type": "posting",
        "title": "T",
        "description": "D",
        "requirements": "R",
        "responsibilities": "Resp",
        "key_skills_summary": "Skills",
    }
    assert embeddings.role_embedding_text(role) == "T\n\nD\n\nR\n\nResp\n\nSkills"


def test_compose_role_text_matches_canonical_format():
    from app.models import Analysis, Job
    from app.routes.import_routes import compose_role_text

    job = Job(title="T", description="D", requirements="R", responsibilities="Resp")
    analysis = Analysis(key_skills_summary="Skills")
    assert compose_role_text(job, analysis) == "T\n\nD\n\nR\n\nResp\n\nSkills"


def test_compose_target_text_matches_canonical_format():
    from app.models import SkillDecompositionItem, TargetRole, TechnicalSubjectItem
    from app.routes.targets import _compose_target_text

    target = TargetRole(
        title="T",
        summary="S",
        description="D",
        typical_tasks=["task1", "task2"],
        skill_decomposition=[SkillDecompositionItem(skill="Python", examples=[])],
        technical_subjects=[TechnicalSubjectItem(subject="Stats")],
    )
    assert _compose_target_text(target) == "T\n\nS\n\nD\n\ntask1\ntask2\n\nSkills: Python\n\nTechnical subjects: Stats"


# --- rebuild/backfill --------------------------------------------------------

def test_rebuild_creates_embeddings_for_migrated_roles_missing_them(client):
    with db.db_cursor() as cur:
        role_ids = [_bare_role_instance(cur, title=f"Legacy Role {i}", description=f"Did thing {i}.") for i in range(3)]
        result = embeddings.rebuild_role_embeddings(cur, missing_only=True)

        for role_id in role_ids:
            cur.execute(
                "SELECT owner_kind, owner_id, model FROM jobber.d_embedding WHERE owner_kind = 'role_instance' AND owner_id = %s",
                (role_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row["model"] == embeddings.embedding_model_name()
            assert str(row["owner_id"]) == role_id

    assert result["roles_scanned"] == 3
    assert result["embeddings_created"] == 3
    assert result["embeddings_updated"] == 0
    assert result["skipped"] == 0


def test_missing_only_rebuild_is_idempotent(client):
    with db.db_cursor() as cur:
        _bare_role_instance(cur)
        first = embeddings.rebuild_role_embeddings(cur, missing_only=True)
        second = embeddings.rebuild_role_embeddings(cur, missing_only=True)

    assert first["embeddings_created"] == 1
    assert second["embeddings_created"] == 0
    assert second["embeddings_updated"] == 0


def test_old_model_embedding_still_counts_as_missing_for_current_model(client):
    with db.db_cursor() as cur:
        role_id = _bare_role_instance(cur)
        vector = embeddings.embed_text("some placeholder text")
        embeddings.set_embedding(cur, "role_instance", role_id, vector, model="old-retired-model")

        result = embeddings.rebuild_role_embeddings(cur, missing_only=True)

        cur.execute(
            "SELECT model FROM jobber.d_embedding WHERE owner_kind = 'role_instance' AND owner_id = %s",
            (role_id,),
        )
        models = {r["model"] for r in cur.fetchall()}

    assert result["embeddings_created"] == 1
    assert models == {"old-retired-model", embeddings.embedding_model_name()}


def test_force_rebuild_recomputes_existing_current_model_embedding(client):
    with db.db_cursor() as cur:
        role_id = _bare_role_instance(cur, title="Legacy Analyst", description="Original description.")
        embeddings.rebuild_role_embeddings(cur, missing_only=True)

        cur.execute("UPDATE jobber.role_instance SET description = 'Updated description entirely.' WHERE id = %s", (role_id,))
        result = embeddings.rebuild_role_embeddings(cur, missing_only=False)

        vector = embeddings.get_embedding(cur, "role_instance", role_id)
        expected = embeddings.embed_text(
            embeddings.role_embedding_text(
                {"node_type": "posting", "title": "Legacy Analyst", "description": "Updated description entirely."}
            )
        )

    assert result["embeddings_created"] == 0
    assert result["embeddings_updated"] == 1
    _approx_vec(vector, expected)


def test_missing_only_rebuild_does_not_recompute_an_existing_current_model_embedding(client):
    """The inverse of the force test: without force, a stale-but-present
    current-model embedding is left alone even if the source text changed."""
    with db.db_cursor() as cur:
        role_id = _bare_role_instance(cur, description="Original description.")
        embeddings.rebuild_role_embeddings(cur, missing_only=True)
        stale_vector = embeddings.get_embedding(cur, "role_instance", role_id)

        cur.execute("UPDATE jobber.role_instance SET description = 'Changed but not rebuilt.' WHERE id = %s", (role_id,))
        result = embeddings.rebuild_role_embeddings(cur, missing_only=True)

        vector = embeddings.get_embedding(cur, "role_instance", role_id)

    assert result["embeddings_created"] == 0
    assert result["embeddings_updated"] == 0
    assert vector == stale_vector


def test_empty_role_text_is_skipped_safely(client):
    with db.db_cursor() as cur:
        cur.execute("INSERT INTO jobber.role_instance (instance_type, title) VALUES ('observed_posting', NULL) RETURNING id")
        role_id = str(cur.fetchone()["id"])
        result = embeddings.rebuild_role_embeddings(cur, missing_only=True)
        cur.execute("SELECT 1 FROM jobber.d_embedding WHERE owner_kind = 'role_instance' AND owner_id = %s", (role_id,))
        assert cur.fetchone() is None

    assert result["skipped"] >= 1


def test_document_content_text_preferred_over_recomposed_fields(client):
    """When a role has a linked document, its content_text (the exact text
    every write path already embeds from) wins over recomposing from the
    role's own stored columns — proven by making them deliberately differ."""
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="The real captured advert text.", provenance_quality="original")
        role_id = _bare_role_instance(cur, title="Should not be used alone", description="Should not be used either.", document_id=document_id)

        embeddings.rebuild_role_embeddings(cur, missing_only=True)

        vector = embeddings.get_embedding(cur, "role_instance", role_id)
        expected_from_document = embeddings.embed_text("The real captured advert text.")
        expected_from_fields = embeddings.embed_text(
            embeddings.role_embedding_text({"node_type": "posting", "title": "Should not be used alone", "description": "Should not be used either."})
        )

    _approx_vec(vector, expected_from_document)
    assert vector != pytest.approx(expected_from_fields, rel=1e-5, abs=1e-6)


def test_concept_and_profile_snapshot_embeddings_untouched(client):
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('tool', 'Python', 'active', 'curator', now()) RETURNING id"
        )
        concept_id = str(cur.fetchone()["id"])
        concept_vector = embeddings.embed_text("Python")
        embeddings.set_embedding(cur, "concept", concept_id, concept_vector)

        cur.execute("INSERT INTO profile360.snapshots (summary) VALUES ('Narrative.') RETURNING id")
        snapshot_id = str(cur.fetchone()["id"])
        snapshot_vector = embeddings.embed_text("Narrative.")
        embeddings.set_embedding(cur, "profile360_snapshot", snapshot_id, snapshot_vector)

        _bare_role_instance(cur)
        embeddings.rebuild_role_embeddings(cur, missing_only=True)

        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_embedding WHERE owner_kind = 'concept'")
        concept_count = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_embedding WHERE owner_kind = 'profile360_snapshot'")
        snapshot_count = cur.fetchone()["n"]
        _approx_vec(embeddings.get_embedding(cur, "concept", concept_id), concept_vector)
        _approx_vec(embeddings.get_embedding(cur, "profile360_snapshot", snapshot_id), snapshot_vector)

    assert concept_count == 1
    assert snapshot_count == 1


# --- Space regression + end-to-end canonical-text consistency --------------

def test_space_recovers_after_rebuilding_migrated_role_embeddings(client):
    with db.db_cursor() as cur:
        _bare_role_instance(cur, title="Role A", description="Did A.")
        _bare_role_instance(cur, title="Role B", description="Did B.")

    before = client.get("/api/space").json()
    assert before["points"] == []
    assert before["note"] == "Need at least 2 embedded points to project."
    assert before["role_count"] == 2
    assert before["embedded_role_count"] == 0
    assert before["embedding_model"] == embeddings.embedding_model_name()

    rebuild = client.post("/api/space/rebuild-role-embeddings")
    assert rebuild.status_code == 200
    assert rebuild.json()["embeddings_created"] == 2

    after = client.get("/api/space").json()
    assert len(after["points"]) == 2
    assert after.get("note") is None
    assert after["embedded_role_count"] == 2


def test_import_and_rebuild_produce_the_same_embedding(client):
    """End-to-end proof of the canonical-text invariant: a role saved via
    the normal import path and the same role force-rebuilt afterwards must
    embed from identical text — asserted via vector equality, which the
    deterministic test fake only produces for identical input text."""
    payload = {
        "metadata": {"source": "user_paste"},
        "job": {"title": "Senior Actuary", "description": "Own the model.", "requirements": "Python required.", "responsibilities": "Lead reserving."},
        "skills": [],
        "analysis": {"key_skills_summary": "Python, reserving"},
    }
    imported = client.post("/api/import", json=payload)
    assert imported.status_code == 200
    role_id = imported.json()["id"]

    with db.db_cursor() as cur:
        original_vector = embeddings.get_embedding(cur, "role_instance", role_id)
        embeddings.rebuild_role_embeddings(cur, missing_only=False)
        rebuilt_vector = embeddings.get_embedding(cur, "role_instance", role_id)

    assert original_vector
    assert original_vector == rebuilt_vector
