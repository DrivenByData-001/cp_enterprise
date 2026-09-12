"""Source-aware ingest duplicate detection (source-aware ingest cleanup,
problems #1-#3): exact (raw content_sha256) vs possible (whitespace-
normalised) duplicate signals, the pre-persistence preflight endpoint, and
the hash-architecture invariant (no unique constraint / no automatic
collapse — docs/14 §4)."""

from app import db


def test_exact_duplicate_detected_on_second_ingest(client):
    payload = {"text": "Senior Pricing Actuary. Requires IFRS 17.", "title": "Senior Pricing Actuary", "organisation": "L&G"}
    first = client.post("/api/role-instances/ingest", json=payload).json()
    second = client.post("/api/role-instances/ingest", json=payload).json()

    assert first["duplicate"]["exact_duplicate"] is None
    dup = second["duplicate"]["exact_duplicate"]
    assert dup is not None
    assert dup["document_id"] == first["document_id"]
    assert dup["role_instance_id"] == first["id"]
    assert dup["title"] == "Senior Pricing Actuary"
    assert dup["organisation"] == "L&G"
    # A second, distinct document/role is still created — this is a warning
    # signal, never an automatic merge/collapse (docs/14 §4).
    assert second["document_id"] != first["document_id"]
    assert second["id"] != first["id"]


def test_preflight_duplicate_check_never_persists_anything(client):
    payload = {"text": "Head of Reserving at Acme Re, posted 2024."}
    client.post("/api/role-instances/ingest", json=payload)

    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.document")
        before = cur.fetchone()["n"]

    resp = client.post("/api/role-instances/duplicate-check", json={"text": payload["text"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["exact_duplicate"] is not None
    assert body["possible_duplicate"] is None

    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.document")
        after = cur.fetchone()["n"]
    assert after == before  # the check itself created nothing


def test_preflight_duplicate_check_finds_nothing_for_new_text(client):
    resp = client.post("/api/role-instances/duplicate-check", json={"text": "A brand new posting nobody has seen."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["exact_duplicate"] is None
    assert body["possible_duplicate"] is None


def test_normalized_whitespace_duplicate_is_a_possible_not_exact_match(client):
    """The production acceptance case: a PDF re-extraction of the same
    underlying posting differs from an earlier capture only in line endings/
    repeated whitespace, so the raw hash differs but the normalised one
    matches."""
    original = "Life Actuarial Manager\n\nForvis Mazars Ireland\n\nDublin, Ireland."
    reflowed = "Life Actuarial Manager\n \nForvis   Mazars Ireland\n\n\nDublin,  Ireland.   "

    first = client.post("/api/role-instances/ingest", json={"text": original, "title": "Life Actuarial Manager"}).json()
    assert first["duplicate"]["exact_duplicate"] is None
    assert first["duplicate"]["possible_duplicate"] is None

    check = client.post("/api/role-instances/duplicate-check", json={"text": reflowed}).json()
    assert check["exact_duplicate"] is None
    possible = check["possible_duplicate"]
    assert possible is not None
    assert possible["document_id"] == first["document_id"]
    assert possible["role_instance_id"] == first["id"]

    second = client.post("/api/role-instances/ingest", json={"text": reflowed, "title": "Life Actuarial Manager"}).json()
    assert second["duplicate"]["exact_duplicate"] is None
    assert second["duplicate"]["possible_duplicate"]["document_id"] == first["document_id"]
    # Capture anyway still stores a second document/role — never blocked.
    assert second["document_id"] != first["document_id"]
    assert second["id"] != first["id"]


def test_genuinely_different_postings_are_not_flagged(client):
    a = client.post("/api/role-instances/ingest", json={"text": "Pricing Actuary role focused on GI reserving."}).json()
    check = client.post(
        "/api/role-instances/duplicate-check", json={"text": "Data Scientist role focused on NLP pipelines."}
    ).json()
    assert check["exact_duplicate"] is None
    assert check["possible_duplicate"] is None
    assert a["duplicate"]["exact_duplicate"] is None


def test_duplicate_check_is_scoped_by_kind(client):
    """A job-posting capture and a narrative (target) capture of identical
    text must never be flagged against each other."""
    text = "Shared text used for two different capture kinds."
    client.post("/api/role-instances/ingest", json={"text": text, "kind": "posting"})
    check = client.post("/api/role-instances/duplicate-check", json={"text": text, "kind": "target_real"}).json()
    assert check["exact_duplicate"] is None
    assert check["possible_duplicate"] is None


def test_no_unique_constraint_on_content_hashes(client):
    """docs/14 §4 / handoff constraint: content_sha256 and
    content_normalized_sha256 are informational dedup signals only — the
    database must never refuse a second identical insert."""
    payload = {"text": "Repeatable posting text for the hash-architecture check."}
    for _ in range(3):
        resp = client.post("/api/role-instances/ingest", json=payload)
        assert resp.status_code == 200

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'jobber' AND tablename = 'document' "
            "AND indexdef ILIKE '%unique%' AND (indexdef ILIKE '%content_sha256%' OR indexdef ILIKE '%content_normalized_sha256%')"
        )
        assert cur.fetchall() == []


def test_duplicate_check_rejects_blank_text(client):
    resp = client.post("/api/role-instances/duplicate-check", json={"text": "   "})
    assert resp.status_code == 400


def test_pdf_duplicate_check_flow_extracts_then_checks(client, monkeypatch):
    """The two-stage preflight the frontend uses for PDF uploads: extract
    text (no persistence), then check it for duplicates (no persistence),
    before ever calling the real ingest endpoint."""
    import io

    import pypdf

    class _FakePage:
        def extract_text(self):
            return "Reserving Actuary post extracted from a PDF."

    class _FakeReader:
        def __init__(self, _stream):
            self.pages = [_FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    extracted = client.post(
        "/api/role-instances/pdf/extract-text",
        files={"file": ("posting.pdf", io.BytesIO(b"stub"), "application/pdf")},
    )
    assert extracted.status_code == 200
    text = extracted.json()["text"]
    assert "Reserving Actuary" in text

    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.document")
        assert cur.fetchone()["n"] == 0  # extraction alone persisted nothing

    check = client.post("/api/role-instances/duplicate-check", json={"text": text}).json()
    assert check["exact_duplicate"] is None
