"""Phase 3 addendum: "true" observed-posting deletion — the posting plus its
own owned, unshared source document and everything that exists only to
describe it — while never touching a shared document, never deleting global
vocabulary (concept_proposal), never touching a target's document, and never
weakening the Phase 3 application-workspace deletion safeguard.

Also covers the role_context_enrichment.extraction_run_id FK-chain fix: a
role that ever had Day-in-the-Life generated for it must still delete
cleanly.
"""

from app import db, role_context
from app.models import RoleContextGeneration
from tests.test_role_context import _fake_run_json_task


def _document(cur, text="Requires Python.", **overrides):
    columns = {"kind": "job_posting", "content_text": text, "provenance_quality": "original", **overrides}
    document_id, _ = db.create_document(cur, **columns)
    return document_id


def _posting(cur, document_id=None, title="Head of Capital") -> str:
    columns = {"instance_type": "observed_posting", "title": title, "document_id": document_id}
    return db.upsert_role_instance(cur, None, columns, skills=[])


def _target(cur, document_id=None, title="Head of Risk (target)") -> str:
    columns = {"instance_type": "user_defined_target", "target_basis": "real_role", "title": title, "document_id": document_id}
    return db.upsert_role_instance(cur, None, columns, skills=[])


def _active_concept(cur, name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


# --- owned, unshared document is deleted with the posting -------------------


def test_deleting_a_posting_deletes_its_owned_unshared_document(client):
    with db.db_cursor() as cur:
        document_id = _document(cur)
        role_id = _posting(cur, document_id)

    assert client.delete(f"/api/roles/{role_id}").status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.document WHERE id = %s", (document_id,))
        assert cur.fetchone() is None


def test_deleting_a_posting_with_no_document_still_succeeds(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur, document_id=None)
    assert client.delete(f"/api/roles/{role_id}").status_code == 200


# --- a shared document is never touched --------------------------------------


def test_shared_document_survives_deleting_one_of_its_postings(client):
    """Two role_instance rows pointing at the same document (a re-association,
    or a duplicate capture that ended up sharing one row) — deleting one must
    never take the document, or the surviving role's evidence, with it."""
    with db.db_cursor() as cur:
        document_id = _document(cur)
        role_a = _posting(cur, document_id, title="Role A")
        role_b = _posting(cur, document_id, title="Role B")

    assert client.delete(f"/api/roles/{role_a}").status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.document WHERE id = %s", (document_id,))
        assert cur.fetchone() is not None
    assert client.get(f"/api/roles/{role_b}").status_code == 200


def test_target_deletion_never_touches_its_document(client):
    """Reclassification is posting-only — a target's linked narrative
    document is never deleted, even when nothing else references it."""
    with db.db_cursor() as cur:
        document_id = _document(cur, kind="narrative")
        target_id = _target(cur, document_id)

    assert client.delete(f"/api/roles/{target_id}").status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.document WHERE id = %s", (document_id,))
        assert cur.fetchone() is not None


# --- global vocabulary is preserved -------------------------------------------


def test_deleting_a_posting_preserves_concept_proposal_but_clears_its_document_reference(client):
    with db.db_cursor() as cur:
        document_id = _document(cur, text="Requires Zap Modelling.")
        role_id = _posting(cur, document_id)
        cur.execute(
            "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, status) "
            "VALUES ('zap modelling', 1, %s, 'pending') RETURNING id",
            (document_id,),
        )
        proposal_id = cur.fetchone()["id"]

    assert client.delete(f"/api/roles/{role_id}").status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT document_id FROM jobber.concept_proposal WHERE id = %s", (proposal_id,))
        row = cur.fetchone()
        assert row is not None  # the proposal itself survives
        assert row["document_id"] is None  # only the dangling reference is cleared
        cur.execute("SELECT 1 FROM jobber.document WHERE id = %s", (document_id,))
        assert cur.fetchone() is None  # the document is still gone


def test_deleting_a_posting_preserves_an_already_accepted_concept(client):
    """A concept a curator already accepted into the vocabulary from this
    posting's own extraction is global knowledge — it must not depend on the
    posting, or its document, surviving at all."""
    with db.db_cursor() as cur:
        document_id = _document(cur)
        role_id = _posting(cur, document_id)
        concept_id = _active_concept(cur, "Zap Modelling")

    assert client.delete(f"/api/roles/{role_id}").status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT status FROM jobber.concept WHERE id = %s", (concept_id,))
        assert cur.fetchone()["status"] == "active"


# --- role_context_enrichment FK-chain fix + Day-in-Life regression ----------


def test_role_with_superseded_day_in_life_versions_still_deletes_cleanly(client, monkeypatch):
    """Every enrichment version — not only the active one — carries its own
    extraction_run_id reference, so a role that was regenerated at least once
    exercises the FK-chain fix twice over (two role_context_enrichment rows,
    two role-subject extraction_run rows)."""
    with db.db_cursor() as cur:
        document_id = _document(cur, text="Head of Capital role. Requires Python.")
        role_id = _posting(cur, document_id)

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    first = client.post(f"/api/roles/{role_id}/context/generate")
    assert first.status_code == 200

    monkeypatch.setattr(
        role_context, "run_json_task",
        _fake_run_json_task(output=RoleContextGeneration(**{
            **first.json()["enrichment"],
            "stakeholders": [{"text": "Finance", "basis": "inferred", "confidence": "medium"}],
            "caveats": "Regenerated version.",
        })),
    )
    second = client.post(f"/api/roles/{role_id}/context/regenerate")
    assert second.status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.role_context_enrichment WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 2  # one superseded, one active

    assert client.delete(f"/api/roles/{role_id}").status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.role_context_enrichment WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 0


def test_posting_generate_day_in_life_delete_leaves_no_residue(client, monkeypatch):
    """The exact regression scenario: posting -> generate Day in the Life ->
    delete posting -> succeeds and leaves no posting/context/source-document
    residue."""
    with db.db_cursor() as cur:
        document_id = _document(cur, text="Head of Capital role. Requires Python and Solvency II.")
        role_id = _posting(cur, document_id)

    monkeypatch.setattr(role_context, "run_json_task", _fake_run_json_task())
    generate_resp = client.post(f"/api/roles/{role_id}/context/generate")
    assert generate_resp.status_code == 200
    assert generate_resp.json()["created"] is True

    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.role_context_enrichment WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1
        cur.execute(
            "SELECT count(*) AS n FROM jobber.extraction_run WHERE task = 'role_context_generate' AND role_instance_id = %s",
            (role_id,),
        )
        assert cur.fetchone()["n"] == 1

    delete_resp = client.delete(f"/api/roles/{role_id}")
    assert delete_resp.status_code == 200

    assert client.get(f"/api/roles/{role_id}").status_code == 404
    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.role_context_enrichment WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT count(*) AS n FROM jobber.extraction_run WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT 1 FROM jobber.document WHERE id = %s", (document_id,))
        assert cur.fetchone() is None


# --- the Phase 3 application-workspace safeguard still wins -----------------


def test_application_safeguard_still_blocks_true_deletion(client):
    """Deleting the posting is a different decision from closing/withdrawing
    an application — an active application still blocks deletion outright,
    with a clear message, and neither the role, its document, nor the
    application history is touched."""
    with db.db_cursor() as cur:
        document_id = _document(cur)
        role_id = _posting(cur, document_id)

    app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]

    resp = client.delete(f"/api/roles/{role_id}")
    assert resp.status_code == 409

    assert client.get(f"/api/roles/{role_id}").status_code == 200
    assert client.get(f"/api/applications/{app_id}").status_code == 200
    with db.db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.document WHERE id = %s", (document_id,))
        assert cur.fetchone() is not None


def test_application_safeguard_still_blocks_deletion_once_closed(client):
    """Closed/withdrawn is still application history, not an all-clear for
    posting deletion — see docs/34 for why this is a deliberate boundary,
    revisited later rather than silently relaxed here."""
    with db.db_cursor() as cur:
        document_id = _document(cur)
        role_id = _posting(cur, document_id)

    app_id = client.post("/api/applications", json={"role_instance_id": role_id}).json()["id"]
    assert client.patch(f"/api/applications/{app_id}", json={"status": "withdrawn"}).status_code == 200

    assert client.delete(f"/api/roles/{role_id}").status_code == 409
    with db.db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.document WHERE id = %s", (document_id,))
        assert cur.fetchone() is not None
