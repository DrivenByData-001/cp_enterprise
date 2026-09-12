"""Source-aware role metadata: manual fallback fields on PDF ingest (problem
#6), the metadata-only PATCH used by both manual Edit and 'Accept metadata'
(problem #7), and the reviewable AI metadata-enrichment proposal (problem
#5) — including the hard requirement that a missing posting date is never
silently replaced by the capture date."""

import io

from app import ai, db, metadata_enrichment
from app.models import RoleMetadataProposal


def test_ingest_pdf_accepts_manual_metadata_fallback_fields(client, monkeypatch):
    import pypdf

    class _FakePage:
        def extract_text(self):
            return "Forvis Mazars in Ireland is hiring a Life Actuarial Manager."

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    resp = client.post(
        "/api/role-instances/ingest/pdf",
        params={
            "organisation": "Forvis Mazars Ireland",
            "location": "Dublin",
            "country": "Ireland",
            "posting_date": "2025-06-13",
            "title": "Life Actuarial Manager",
        },
        files={"file": ("posting.pdf", io.BytesIO(b"stub"), "application/pdf")},
    )
    assert resp.status_code == 200
    role_id = resp.json()["id"]

    role = client.get(f"/api/roles/{role_id}").json()
    assert role["organisation"] == "Forvis Mazars Ireland"
    assert role["location"] == "Dublin"
    assert role["country"] == "Ireland"
    assert role["posting_date"] == "2025-06-13"


def test_ingest_pdf_leaves_posting_date_null_when_not_supplied(client, monkeypatch):
    """The exact acceptance-case failure mode: no posting date supplied at
    capture time must stay unknown — never the upload date."""
    import pypdf

    class _FakePage:
        def extract_text(self):
            return "A role with no stated date."

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    resp = client.post(
        "/api/role-instances/ingest/pdf",
        files={"file": ("posting.pdf", io.BytesIO(b"stub"), "application/pdf")},
    )
    role_id = resp.json()["id"]
    role = client.get(f"/api/roles/{role_id}").json()
    assert role["posting_date"] is None
    assert role["organisation"] is None


def _make_source_aware_role(text="Some posting text to back a source-aware role.") -> str:
    """A bare role_instance with no `raw_json`/legacy_analysis — the shape
    every role created via /api/role-instances/ingest has, and exactly the
    shape RoleEdit.tsx's crash bug (problem #7) needed."""
    with db.db_cursor() as cur:
        document_id, _ = db.create_document(
            cur, kind="job_posting", content_text=text, provenance_quality="original", source="user_paste",
        )
        return db.upsert_role_instance(
            cur, None,
            {"instance_type": "observed_posting", "document_id": document_id, "title": "Untitled posting"},
            skills=[],
        )


def test_metadata_patch_updates_fields_without_touching_document(client):
    original_text = "The document content must remain untouched by metadata edits."
    role_id = _make_source_aware_role(original_text)
    role_before = client.get(f"/api/roles/{role_id}").json()

    resp = client.patch(
        f"/api/role-instances/{role_id}/metadata",
        json={
            "organisation": "Forvis Mazars Ireland", "location": "Dublin", "country": "Ireland",
            "posting_date": "2025-06-13", "employment_type": "full_time", "seniority_level": "senior",
            "remote_type": "hybrid",
        },
    )
    assert resp.status_code == 200
    role = client.get(f"/api/roles/{role_id}").json()
    assert role["organisation"] == "Forvis Mazars Ireland"
    assert role["location"] == "Dublin"
    assert role["country"] == "Ireland"
    assert role["posting_date"] == "2025-06-13"
    assert role["employment_type"] == "full_time"
    assert role["seniority_level"] == "senior"
    assert role["remote_type"] == "hybrid"
    # title untouched (not supplied) and the linked document's own content is unchanged
    assert role["title"] == role_before["title"]

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT d.content_text FROM jobber.document d JOIN jobber.role_instance ri ON ri.document_id = d.id "
            "WHERE ri.id = %s",
            (role_id,),
        )
        assert cur.fetchone()["content_text"] == original_text


def test_metadata_patch_only_changes_supplied_fields(client):
    role_id = _make_source_aware_role()
    client.patch(f"/api/role-instances/{role_id}/metadata", json={"organisation": "Acme"})
    client.patch(f"/api/role-instances/{role_id}/metadata", json={"location": "London"})
    role = client.get(f"/api/roles/{role_id}").json()
    assert role["organisation"] == "Acme"  # not clobbered by the second, unrelated PATCH
    assert role["location"] == "London"


def test_metadata_patch_unknown_role_is_404(client):
    import uuid

    resp = client.patch(f"/api/role-instances/{uuid.uuid4()}/metadata", json={"organisation": "Acme"})
    assert resp.status_code == 404


def test_metadata_patch_rejects_target_roles(client):
    with db.db_cursor() as cur:
        role_id = db.upsert_role_instance(
            cur, None, {"instance_type": "user_defined_target", "target_basis": "real_role", "title": "A target"}, skills=[],
        )
    resp = client.patch(f"/api/role-instances/{role_id}/metadata", json={"organisation": "Acme"})
    assert resp.status_code == 400


# --- metadata enrichment proposal (AI-assisted, review-before-accept) -------


def _fake_run_json_task(output: RoleMetadataProposal | None = None, raise_error: Exception | None = None):
    def _dispatch(*, task, prompt_name, user_input, output_model):
        if raise_error is not None:
            raise raise_error
        run = ai.AITaskRun(
            task=task, model="test-model", prompt_name=prompt_name, prompt_version="testversion",
            started_at="2026-05-27T00:00:00+00:00", finished_at="2026-05-27T00:00:01+00:00",
            status="ok", input_chars=len(user_input), output_chars=10,
        )
        return ai.AITaskResult(output=output or RoleMetadataProposal(organisation="Forvis Mazars Ireland", location="Dublin"), run=run)

    return _dispatch


def test_propose_metadata_never_persists_until_accepted(client, monkeypatch):
    role_id = _make_source_aware_role("Forvis Mazars in Ireland is hiring a Life Actuarial Manager, Dublin.")

    monkeypatch.setattr(
        metadata_enrichment, "run_json_task",
        _fake_run_json_task(RoleMetadataProposal(organisation="Forvis Mazars Ireland", location="Dublin", posting_date=None)),
    )
    resp = client.post(f"/api/role-instances/{role_id}/metadata/propose")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["proposal"]["organisation"] == "Forvis Mazars Ireland"
    assert body["proposal"]["posting_date"] is None

    # Nothing written to the role until the user explicitly accepts it.
    role = client.get(f"/api/roles/{role_id}").json()
    assert role["organisation"] is None

    accept = client.patch(f"/api/role-instances/{role_id}/metadata", json=body["proposal"])
    assert accept.status_code == 200
    role = client.get(f"/api/roles/{role_id}").json()
    assert role["organisation"] == "Forvis Mazars Ireland"
    assert role["posting_date"] is None  # never substituted with a capture/today date


def test_propose_metadata_records_extraction_run(client, monkeypatch):
    role_id = _make_source_aware_role()
    monkeypatch.setattr(metadata_enrichment, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/role-instances/{role_id}/metadata/propose")
    run_id = resp.json()["extraction_run_id"]

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT task, subject_type, role_instance_id, status, vocabulary_version_id FROM jobber.extraction_run WHERE id = %s",
            (run_id,),
        )
        run = cur.fetchone()
    assert run["task"] == "role_metadata_enrich"
    assert run["subject_type"] == "role_instance"
    assert str(run["role_instance_id"]) == role_id
    assert run["status"] == "ok"
    assert run["vocabulary_version_id"] is None  # no controlled-vocabulary dependency


def test_propose_metadata_on_unknown_role_is_404(client, monkeypatch):
    import uuid

    monkeypatch.setattr(metadata_enrichment, "run_json_task", _fake_run_json_task())
    resp = client.post(f"/api/role-instances/{uuid.uuid4()}/metadata/propose")
    assert resp.status_code == 404


def test_propose_metadata_ai_failure_is_reported_not_persisted(client, monkeypatch):
    role_id = _make_source_aware_role()
    monkeypatch.setattr(
        metadata_enrichment, "run_json_task",
        _fake_run_json_task(raise_error=ai.AIProviderError("provider unreachable")),
    )
    resp = client.post(f"/api/role-instances/{role_id}/metadata/propose")
    assert resp.status_code == 502

    with db.db_cursor() as cur:
        cur.execute("SELECT status, error_type FROM jobber.extraction_run WHERE role_instance_id = %s", (role_id,))
        run = cur.fetchone()
    assert run["status"] == "failed"
    assert run["error_type"] == "AIProviderError"
