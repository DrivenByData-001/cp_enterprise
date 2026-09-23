"""One current requirement_claim per (role_instance_id, concept_id), many
supporting jobber.requirement_evidence occurrences (migration 0026,
consolidated requirement evidence + graph fix build).

Mirrors test_requirement_claims.py's conventions: app.extraction directly,
with app.extraction.run_json_task mocked (never a live OpenAI call)."""

import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app import ai, db, extraction, role_requirements
from app.config import test_database_url as get_test_database_url
from app.models import RequirementExtractionResult, RequirementItem


def _fake_run(output, task, prompt_name):
    run = ai.AITaskRun(
        task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        status="ok", input_chars=10, output_chars=10,
    )
    return ai.AITaskResult(output=output, run=run)


def _make_role_with_document(cur, body: str) -> tuple[str, str]:
    document_id, _ = db.create_document(cur, kind="job_posting", content_text=body, provenance_quality="original")
    role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "Test posting", "document_id": document_id}, skills=[])
    return role_id, document_id


def _make_active_concept(cur, canonical_name: str, type_code: str = "function", aliases: tuple[str, ...] = ()) -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, canonical_name),
    )
    concept_id = str(cur.fetchone()["id"])
    # Aliases resolve extra surface forms via the cheap exact-match path
    # (concept_linking.exact_match_concept_id) so these tests can exercise
    # several distinct occurrences of one concept without needing to mock
    # the separate concept-adjudication AI call too.
    for alias in aliases:
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin) VALUES (%s, %s, 'curator')",
            (concept_id, alias),
        )
    return concept_id


def _current_claim(cur, role_id: str) -> dict:
    cur.execute(
        "SELECT id, requirement_type, review_status FROM jobber.requirement_claim "
        "WHERE role_instance_id = %s AND superseded_by IS NULL",
        (role_id,),
    )
    rows = cur.fetchall()
    assert len(rows) == 1, f"expected exactly one current claim, found {len(rows)}"
    return rows[0]


def _evidence_spans(cur, claim_id: str) -> set[str]:
    cur.execute("SELECT evidence_span FROM jobber.requirement_evidence WHERE requirement_claim_id = %s", (claim_id,))
    return {row["evidence_span"] for row in cur.fetchall()}


# 1 & 2: two different passages mapping to one concept create one current
# claim, and both passages survive as evidence.
def test_two_occurrences_of_one_concept_create_one_claim_with_both_as_evidence(client, monkeypatch):
    body = "Looking for an effective communicator who can communicate with colleagues daily."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="effective communicator", requirement_type="required", basis="stated",
                                 evidence_span="effective communicator"),
                RequirementItem(surface_form="communicate with colleagues", requirement_type="required", basis="stated",
                                 evidence_span="communicate with colleagues"),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Communication", aliases=("effective communicator", "communicate with colleagues"))
        role_id, _ = _make_role_with_document(cur, body)
        result = extraction.extract_role_requirements(cur, role_id)

        assert result["claims_created"] == 1
        assert result["evidence_created"] == 2
        claim = _current_claim(cur, role_id)
        spans = _evidence_spans(cur, claim["id"])

    assert spans == {"effective communicator", "communicate with colleagues"}


# 3: identical rerun does not duplicate evidence.
def test_identical_rerun_does_not_duplicate_evidence(client, monkeypatch):
    body = "Requires Python experience."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python experience."),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python", type_code="tool")
        role_id, _ = _make_role_with_document(cur, body)
        result1 = extraction.extract_role_requirements(cur, role_id)
        assert result1["evidence_created"] == 1

        result2 = extraction.extract_role_requirements(cur, role_id)
        assert result2["evidence_created"] == 0
        assert result2["evidence_deduplicated"] == 1

        claim = _current_claim(cur, role_id)
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_evidence WHERE requirement_claim_id = %s", (claim["id"],))
        assert cur.fetchone()["n"] == 1


# 4: rerun with a new passage adds evidence but not another claim.
def test_rerun_with_a_new_passage_adds_evidence_not_another_claim(client, monkeypatch):
    body = "Requires Python experience. Must communicate with confidence about Python design."
    calls = {"n": 0}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        calls["n"] += 1
        items = [RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python experience.")]
        if calls["n"] == 2:
            items.append(RequirementItem(surface_form="Python design", requirement_type="required", basis="stated", evidence_span="about Python design"))
        return _fake_run(RequirementExtractionResult(requirements=items), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python", type_code="tool", aliases=("Python design",))
        role_id, _ = _make_role_with_document(cur, body)
        result1 = extraction.extract_role_requirements(cur, role_id)
        assert result1["claims_created"] == 1
        first_claim = _current_claim(cur, role_id)

        result2 = extraction.extract_role_requirements(cur, role_id)
        assert result2["claims_created"] == 0
        assert result2["evidence_created"] == 1

        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1  # never a second claim

        second_claim = _current_claim(cur, role_id)
        assert second_claim["id"] == first_claim["id"]
        spans = _evidence_spans(cur, second_claim["id"])

    assert spans == {"Requires Python experience.", "about Python design"}


# 5: accepted review status survives rerun (with evidence still attached).
def test_accepted_review_status_survives_rerun_and_still_gains_evidence(client, monkeypatch):
    body = "Requires Python experience. Strong Python skills are a must."
    calls = {"n": 0}

    def _dispatch(*, task, prompt_name, user_input, output_model):
        calls["n"] += 1
        items = [RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python experience.")]
        if calls["n"] == 2:
            items.append(RequirementItem(surface_form="Python skills", requirement_type="required", basis="stated", evidence_span="Strong Python skills are a must."))
        return _fake_run(RequirementExtractionResult(requirements=items), task, prompt_name)

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Python", type_code="tool", aliases=("Python skills",))
        role_id, _ = _make_role_with_document(cur, body)
        extraction.extract_role_requirements(cur, role_id)
        claim = _current_claim(cur, role_id)

    accept_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim['id']}/accept")
    assert accept_resp.status_code == 200

    with db.db_cursor() as cur:
        result2 = extraction.extract_role_requirements(cur, role_id)
        assert result2["claims_created"] == 0
        assert result2["claims_superseded"] == 0
        assert result2["evidence_created"] == 1  # the new passage still attaches...

        cur.execute("SELECT review_status FROM jobber.requirement_claim WHERE id = %s", (claim["id"],))
        assert cur.fetchone()["review_status"] == "accepted"  # ...but review decision is untouched

        spans = _evidence_spans(cur, claim["id"])
    assert spans == {"Requires Python experience.", "Strong Python skills are a must."}


# 6: differing occurrence requirement types resolve deterministically
# (required > preferred > contextual), all occurrences still kept as evidence.
def test_differing_occurrence_requirement_types_resolve_deterministically(client, monkeypatch):
    body = "Ideally you have Python context, though Python is required and Python is preferred too."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="Python context", requirement_type="contextual", basis="stated", evidence_span="Python context"),
                RequirementItem(surface_form="Python is required", requirement_type="required", basis="stated", evidence_span="Python is required"),
                RequirementItem(surface_form="Python is preferred", requirement_type="preferred", basis="stated", evidence_span="Python is preferred"),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(
            cur, "Python", type_code="tool",
            aliases=("Python context", "Python is required", "Python is preferred"),
        )
        role_id, _ = _make_role_with_document(cur, body)
        result = extraction.extract_role_requirements(cur, role_id)

        assert result["claims_created"] == 1
        assert result["evidence_created"] == 3
        claim = _current_claim(cur, role_id)
        spans = _evidence_spans(cur, claim["id"])

    assert claim["requirement_type"] == "required"  # strongest reading wins the claim
    assert spans == {"Python context", "Python is required", "Python is preferred"}  # nothing lost


# 7: API returns all evidence occurrences.
def test_api_returns_all_evidence_occurrences(client, monkeypatch):
    body = "Effective communicator needed; must communicate with colleagues daily."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="effective communicator", requirement_type="required", basis="stated", evidence_span="Effective communicator needed"),
                RequirementItem(surface_form="communicate with colleagues", requirement_type="required", basis="stated", evidence_span="communicate with colleagues"),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Communication", aliases=("effective communicator", "communicate with colleagues"))
        role_id, _ = _make_role_with_document(cur, body)
        extraction.extract_role_requirements(cur, role_id)

    resp = client.get(f"/api/role-instances/{role_id}/requirements")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1  # one requirement...
    assert len(items[0]["evidence"]) == 2  # ...with both passages surfaced


# 9: analytical loader returns one role/concept row regardless of evidence count.
def test_analytical_loader_returns_one_row_per_role_concept(client, monkeypatch):
    body = "Effective communicator needed; must communicate with colleagues daily."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="effective communicator", requirement_type="required", basis="stated", evidence_span="Effective communicator needed"),
                RequirementItem(surface_form="communicate with colleagues", requirement_type="required", basis="stated", evidence_span="communicate with colleagues"),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        _make_active_concept(cur, "Communication", aliases=("effective communicator", "communicate with colleagues"))
        role_id, _ = _make_role_with_document(cur, body)
        extraction.extract_role_requirements(cur, role_id)
        claim = _current_claim(cur, role_id)
        cur.execute("UPDATE jobber.requirement_claim SET review_status = 'accepted' WHERE id = %s", (claim["id"],))

        rows = role_requirements.load_role_requirements(cur, role_id)

    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "Communication"


# 11 & 12: no duplicate-current role/concept groups remain after a
# multi-occurrence rerun, and migration 0020's uniqueness invariant still
# holds (a direct duplicate insert is still rejected).
def test_no_duplicate_current_rows_remain_and_0020_invariant_holds(client, monkeypatch):
    body = "Requires Python. Python is essential. Python is a must-have."

    def _dispatch(*, task, prompt_name, user_input, output_model):
        return _fake_run(
            RequirementExtractionResult(requirements=[
                RequirementItem(surface_form="Python", requirement_type="required", basis="stated", evidence_span="Requires Python."),
                RequirementItem(surface_form="Python essential", requirement_type="required", basis="stated", evidence_span="Python is essential."),
                RequirementItem(surface_form="Python must-have", requirement_type="required", basis="stated", evidence_span="Python is a must-have."),
            ]),
            task, prompt_name,
        )

    monkeypatch.setattr(extraction, "run_json_task", _dispatch)

    with db.db_cursor() as cur:
        concept_id = _make_active_concept(cur, "Python", type_code="tool", aliases=("Python essential", "Python must-have"))
        role_id, _ = _make_role_with_document(cur, body)
        extraction.extract_role_requirements(cur, role_id)

        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.requirement_claim "
            "WHERE role_instance_id = %s AND concept_id = %s AND superseded_by IS NULL",
            (role_id, concept_id),
        )
        assert cur.fetchone()["n"] == 1

        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
                "VALUES (%s, %s, 'required', 'user_asserted', 'unreviewed')",
                (role_id, concept_id),
            )


# 10: backfill moves evidence from superseded duplicate claims to the
# current survivor. Mirrors test_migration_compatibility.py's technique for
# testing an individual migration file's SQL in isolation: replay every
# migration up to (but not including) 0026 against a fresh database, seed a
# pre-existing supersession chain the way production data actually looks
# (claim history predating requirement_evidence entirely), then apply 0026's
# SQL text directly and confirm both the superseded and the survivor claim's
# own evidence_span/document_id end up attached to the survivor.
def test_migration_0026_backfill_follows_supersession_chain_to_survivor(client, monkeypatch):
    from app import db as db_module

    admin_url = get_test_database_url()
    db_name = f"cp_test_reqevidence_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')
    parts = urlsplit(admin_url)
    scoped_url = urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))
    target_name = "0026_requirement_evidence.sql"
    target_path = db_module.MIGRATIONS_DIR / target_name
    baseline_sql = (db_module.MIGRATIONS_DIR.parent / "scripts" / "local_baseline.sql").read_text(encoding="utf-8")
    try:
        with psycopg.connect(scoped_url, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(baseline_sql)
            conn.execute(
                "CREATE SCHEMA IF NOT EXISTS jobber; "
                "CREATE TABLE IF NOT EXISTS jobber.migration_history "
                "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            conn.execute(
                "INSERT INTO jobber.migration_history (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                (target_name,),
            )

        db_module.reset_pool()
        monkeypatch.setenv("DATABASE_URL", scoped_url)
        applied = db_module.run_migrations()
        assert target_name not in applied

        with db_module.db_cursor() as cur:
            document_id, _ = db_module.create_document(cur, kind="job_posting", content_text="Requires Python. Advanced Python required.", provenance_quality="original")
            role_id = db_module.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "T", "document_id": document_id}, skills=[])
            cur.execute(
                "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
                "VALUES ('tool', 'Python', 'active', 'curator', now()) RETURNING id"
            )
            concept_id = cur.fetchone()["id"]

            # A pre-0026 supersession chain: an old (now-historical) claim
            # with its own evidence_span, superseded by a current claim with
            # a different evidence_span — production-shaped data that
            # predates jobber.requirement_evidence entirely.
            new_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO jobber.requirement_claim "
                "(role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span, review_status, superseded_by) "
                "VALUES (%s, %s, 'preferred', 'stated', %s, 'Requires Python.', 'unreviewed', %s)",
                (role_id, concept_id, document_id, new_id),
            )
            cur.execute(
                "INSERT INTO jobber.requirement_claim "
                "(id, role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span, review_status, superseded_by) "
                "VALUES (%s, %s, %s, 'required', 'stated', %s, 'Advanced Python required.', 'unreviewed', NULL)",
                (new_id, role_id, concept_id, document_id),
            )

        with db_module.db_cursor() as cur:
            cur.execute(target_path.read_text(encoding="utf-8"))

        with db_module.db_cursor() as cur:
            cur.execute(
                "SELECT evidence_span FROM jobber.requirement_evidence WHERE requirement_claim_id = %s",
                (new_id,),
            )
            spans = {row["evidence_span"] for row in cur.fetchall()}
        assert spans == {"Requires Python.", "Advanced Python required."}
    finally:
        db_module.reset_pool()
