"""Role ingestion, role_instance creation, and source provenance handling
(brief §4/§13/§16)."""

import io

from app import db


def test_ingest_text_creates_document_and_role_instance(client):
    resp = client.post(
        "/api/role-instances/ingest",
        json={"text": "Senior Pricing Actuary. Requires IFRS 17.", "title": "Senior Pricing Actuary", "organisation": "L&G"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ingested"

    with db.db_cursor() as cur:
        cur.execute("SELECT title, organisation, document_id FROM jobber.role_instance WHERE id = %s", (body["id"],))
        role = cur.fetchone()
        cur.execute("SELECT provenance_quality, kind, content_text FROM jobber.document WHERE id = %s", (role["document_id"],))
        document = cur.fetchone()

    assert role["title"] == "Senior Pricing Actuary"
    assert role["organisation"] == "L&G"
    assert document["provenance_quality"] == "original"
    assert document["kind"] == "job_posting"
    assert "IFRS 17" in document["content_text"]


def test_ingest_text_derives_title_when_missing(client):
    resp = client.post("/api/role-instances/ingest", json={"text": "Head of Actuarial Function at Acme Re.\nMore details here."})
    assert resp.status_code == 200
    with db.db_cursor() as cur:
        cur.execute("SELECT title FROM jobber.role_instance WHERE id = %s", (resp.json()["id"],))
        title = cur.fetchone()["title"]
    assert title == "Head of Actuarial Function at Acme Re."


def test_ingest_text_rejects_blank(client):
    resp = client.post("/api/role-instances/ingest", json={"text": "   "})
    assert resp.status_code == 400


def test_ingest_text_rejects_invalid_kind(client):
    resp = client.post("/api/role-instances/ingest", json={"text": "hello", "kind": "not_a_real_kind"})
    assert resp.status_code == 400


def test_ingest_pdf_rejects_image_only_pdf(client):
    """A pypdf-generated blank page has no text layer at all — exactly the
    "scanned/image-only" case the brief says must not be silently OCR'd."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    resp = client.post(
        "/api/role-instances/ingest/pdf",
        files={"file": ("scan.pdf", buf, "application/pdf")},
    )
    assert resp.status_code == 422
    assert "OCR" in resp.json()["detail"]


def test_ingest_pdf_extracts_selectable_text(client, monkeypatch):
    """Decoupled from pypdf's own extraction correctness (a well-tested
    third-party concern) — this exercises this route's own logic: join page
    text, and if non-empty, feed it through the same ingest path as pasted
    text."""
    import pypdf

    class _FakePage:
        def extract_text(self):
            return "Senior Reserving Actuary. Requires Python and IFRS 17."

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    resp = client.post(
        "/api/role-instances/ingest/pdf",
        files={"file": ("posting.pdf", io.BytesIO(b"not a real pdf, but PdfReader is mocked"), "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()

    with db.db_cursor() as cur:
        cur.execute("SELECT source FROM jobber.document WHERE id = %s", (body["document_id"],))
        source = cur.fetchone()["source"]
    assert source == "pdf"


def test_duplicate_content_creates_a_new_document_but_flags_the_duplicate(client):
    """docs/14 §4: content_sha256 is informational dedup only, never identity
    — production allows (and has) distinct document rows sharing identical
    content, so repeated ingestion of the same text must still insert a new
    document row every time, merely flagging the earlier one it matches."""
    payload = {"text": "Duplicate posting text for idempotency check."}
    first = client.post("/api/role-instances/ingest", json=payload).json()
    second = client.post("/api/role-instances/ingest", json=payload).json()

    assert first["duplicate_of_document_id"] is None
    assert second["duplicate_of_document_id"] == first["document_id"]
    assert first["document_id"] != second["document_id"]
    assert first["id"] != second["id"]
