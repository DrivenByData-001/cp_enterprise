"""Role archetype curation/assignment API (Phase 4 prompt §3), HTTP-level
against the real Postgres test database."""

from app import db


def _role(cur, title="Senior Actuarial Analyst", country=None):
    return db.upsert_role_instance(
        cur, None, {"instance_type": "observed_posting", "title": title, "country": country}, skills=[]
    )


def _concept(cur, name, type_code="tool", status="active"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, name, status),
    )
    return str(cur.fetchone()["id"])


def test_create_archetype_atomically_creates_concept_and_detail(client):
    resp = client.post("/api/archetypes", json={"canonical_name": "Chief Actuary", "seniority_band": "chief", "notes": "test"})
    assert resp.status_code == 200
    archetype_id = resp.json()["id"]

    detail = client.get(f"/api/archetypes/{archetype_id}").json()
    assert detail["canonical_name"] == "Chief Actuary"
    assert detail["seniority_band"] == "chief"
    assert detail["status"] == "active"
    assert detail["role_count"] == 0


def test_create_archetype_with_role_assignment(client):
    with db.db_cursor() as cur:
        role_id = str(_role(cur, title="Senior Pricing Actuary"))

    resp = client.post(
        "/api/archetypes",
        json={"canonical_name": "Pricing Manager", "role_instance_ids": [role_id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned_count"] == 1

    detail = client.get(f"/api/archetypes/{body['id']}").json()
    assert detail["role_count"] == 1
    assert detail["roles"][0]["id"] == role_id


def test_assign_title_group(client):
    """The full curation flow: title-groups surfaces unassigned observed
    roles grouped by normalised title; creating an archetype from that
    group assigns them all."""
    with db.db_cursor() as cur:
        role_id_1 = str(_role(cur, title="Senior Actuarial Analyst"))
        role_id_2 = str(_role(cur, title="senior actuarial analyst"))  # same normalised title, different case
        role_id_3 = str(_role(cur, title="Head of Pricing"))

    groups = client.get("/api/archetypes/title-groups").json()
    matching = [g for g in groups if g["normalized_title"] == "senior actuarial analyst"]
    assert len(matching) == 1
    assert matching[0]["role_count"] == 2
    group_role_ids = {r["id"] for r in matching[0]["roles"]}
    assert group_role_ids == {role_id_1, role_id_2}
    assert role_id_3 not in group_role_ids

    resp = client.post("/api/archetypes", json={"canonical_name": "Senior Actuarial Analyst", "role_instance_ids": list(group_role_ids)})
    assert resp.status_code == 200
    archetype_id = resp.json()["id"]

    # Assigned roles drop out of the unassigned title-groups list.
    groups_after = client.get("/api/archetypes/title-groups").json()
    assert all(g["normalized_title"] != "senior actuarial analyst" for g in groups_after)

    detail = client.get(f"/api/archetypes/{archetype_id}").json()
    assert detail["role_count"] == 2


def test_reassign_role_to_different_archetype(client):
    with db.db_cursor() as cur:
        role_id = str(_role(cur, title="Ambiguous Title"))

    first = client.post("/api/archetypes", json={"canonical_name": "Archetype A", "role_instance_ids": [role_id]}).json()
    second = client.post("/api/archetypes", json={"canonical_name": "Archetype B"}).json()

    resp = client.post(f"/api/archetypes/{second['id']}/assign", json={"role_instance_ids": [role_id]})
    assert resp.status_code == 200

    detail_a = client.get(f"/api/archetypes/{first['id']}").json()
    detail_b = client.get(f"/api/archetypes/{second['id']}").json()
    assert detail_a["role_count"] == 0
    assert detail_b["role_count"] == 1
    assert detail_b["roles"][0]["id"] == role_id


def test_assign_to_non_role_archetype_concept_rejected(client):
    with db.db_cursor() as cur:
        role_id = str(_role(cur))
        not_an_archetype_id = _concept(cur, "Python", type_code="tool")

    resp = client.post(f"/api/archetypes/{not_an_archetype_id}/assign", json={"role_instance_ids": [role_id]})
    assert resp.status_code == 404


def test_create_archetype_rejects_unknown_role_ids(client):
    resp = client.post("/api/archetypes", json={"canonical_name": "Some Archetype", "role_instance_ids": ["00000000-0000-0000-0000-000000000000"]})
    assert resp.status_code == 400


def test_deprecated_archetype_excluded_from_default_listing_and_rejects_new_assignment(client):
    with db.db_cursor() as cur:
        role_id = str(_role(cur))

    created = client.post("/api/archetypes", json={"canonical_name": "Soon Deprecated"}).json()
    archetype_id = created["id"]

    update = client.put(f"/api/archetypes/{archetype_id}", json={"status": "deprecated"})
    assert update.status_code == 200

    active_listing = client.get("/api/archetypes").json()
    assert all(a["id"] != archetype_id for a in active_listing)

    all_listing = client.get("/api/archetypes?status=all").json()
    assert any(a["id"] == archetype_id and a["status"] == "deprecated" for a in all_listing)

    assign_resp = client.post(f"/api/archetypes/{archetype_id}/assign", json={"role_instance_ids": [role_id]})
    assert assign_resp.status_code == 400


def test_update_archetype_metadata(client):
    created = client.post("/api/archetypes", json={"canonical_name": "Draft Name"}).json()
    resp = client.put(
        f"/api/archetypes/{created['id']}",
        json={"canonical_name": "Final Name", "typical_market": "UK life insurance", "notes": "updated"},
    )
    assert resp.status_code == 200

    detail = client.get(f"/api/archetypes/{created['id']}").json()
    assert detail["canonical_name"] == "Final Name"
    assert detail["typical_market"] == "UK life insurance"
    assert detail["notes"] == "updated"


def test_duplicate_canonical_name_rejected(client):
    client.post("/api/archetypes", json={"canonical_name": "Unique Archetype"})
    resp = client.post("/api/archetypes", json={"canonical_name": "Unique Archetype"})
    assert resp.status_code == 400
