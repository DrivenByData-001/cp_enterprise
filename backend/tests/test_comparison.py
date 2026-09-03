"""Comparison traceability and epistemic status semantics (brief §11/§16):
evidenced / partial / user_asserted / not_found, each traceable to both the
role side (requirement_claim -> role_instance -> document/evidence) and the
person side (mapping -> profile360 row)."""

from app import db


def _active_concept(cur, name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _role_with_requirement(cur, concept_id: str, basis="stated", document=True) -> tuple[str, str]:
    document_id = None
    if document:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Requires the thing.", provenance_quality="original")
    role_id = db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "R", "document_id": document_id}, skills=[])
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span) "
        "VALUES (%s, %s, 'required', %s, %s, %s) RETURNING id",
        (role_id, concept_id, basis, document_id, "Requires the thing." if document else None),
    )
    return role_id, str(cur.fetchone()["id"])


def test_not_found_when_nothing_backs_the_requirement(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id, _ = _role_with_requirement(cur, concept_id)

    resp = client.get(f"/api/comparison/role/{role_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"]["not_found"] == 1
    assert body["items"][0]["status"] == "not_found"
    assert body["items"][0]["role_side"]["document"]["provenance"] == "original"
    assert body["items"][0]["role_side"]["evidence_span"] == "Requires the thing."


def test_evidenced_when_accepted_mapping_exists(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id, _ = _role_with_requirement(cur, concept_id)
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Used Python daily.') RETURNING id")
        claim_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
            "VALUES (%s, %s, 'curator_asserted', 'accepted')",
            (claim_id, concept_id),
        )

    resp = client.get(f"/api/comparison/role/{role_id}")
    body = resp.json()
    assert body["items"][0]["status"] == "evidenced"
    assert body["counts"]["evidenced"] == 1
    mapping = body["items"][0]["person_side"]["mappings"][0]
    assert mapping["display"] == "Used Python daily."


def test_partial_when_mapping_is_unreviewed(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id, _ = _role_with_requirement(cur, concept_id)
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Used Python once.') RETURNING id")
        claim_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
            "VALUES (%s, %s, 'ai_suggested', 'unreviewed')",
            (claim_id, concept_id),
        )

    resp = client.get(f"/api/comparison/role/{role_id}")
    assert resp.json()["items"][0]["status"] == "partial"


def test_rejected_mapping_does_not_count_as_evidence(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id, _ = _role_with_requirement(cur, concept_id)
        cur.execute("INSERT INTO profile360.claims (claim_text) VALUES ('Not actually relevant.') RETURNING id")
        claim_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
            "VALUES (%s, %s, 'ai_suggested', 'rejected')",
            (claim_id, concept_id),
        )

    resp = client.get(f"/api/comparison/role/{role_id}")
    assert resp.json()["items"][0]["status"] == "not_found"


def test_assert_and_retract_capability_via_api(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        role_id, _ = _role_with_requirement(cur, concept_id)

    before = client.get(f"/api/comparison/role/{role_id}").json()
    assert before["items"][0]["status"] == "not_found"

    assert_resp = client.post("/api/comparison/assert", json={"concept_id": concept_id, "note": "5 years"})
    assert assert_resp.status_code == 200

    after = client.get(f"/api/comparison/role/{role_id}").json()
    assert after["items"][0]["status"] == "user_asserted"
    assert after["items"][0]["person_side"]["assertion"]["note"] == "5 years"

    retract = client.delete(f"/api/comparison/assert/{concept_id}")
    assert retract.status_code == 200

    final = client.get(f"/api/comparison/role/{role_id}").json()
    assert final["items"][0]["status"] == "not_found"
