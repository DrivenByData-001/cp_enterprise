"""Capability catalogue CRUD + component_of edge API (brief §5/§6/§27)."""

from app import db


def test_create_list_get_update_capability(client):
    create = client.post(
        "/api/capabilities",
        json={
            "canonical_name": "Lead a reserving process",
            "demonstration_standard": "Named owner of a reserving cycle end to end.",
            "min_depth": "owned",
            "min_autonomy": "directed_others",
            "requires_all_core": True,
        },
    )
    assert create.status_code == 200
    cap_id = create.json()["id"]

    listed = client.get("/api/capabilities")
    assert any(c["id"] == cap_id for c in listed.json())

    detail = client.get(f"/api/capabilities/{cap_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["canonical_name"] == "Lead a reserving process"
    assert body["min_autonomy"] == "directed_others"
    assert body["components"] == {"core": [], "supporting": [], "contextual": []}
    assert body["coverage"]["status"] == "not_found"

    update = client.put(f"/api/capabilities/{cap_id}", json={"notes": "curated for the actuarial track"})
    assert update.status_code == 200
    assert client.get(f"/api/capabilities/{cap_id}").json()["notes"] == "curated for the actuarial track"


def test_create_capability_rejects_invalid_min_depth(client):
    resp = client.post(
        "/api/capabilities",
        json={"canonical_name": "Bad capability", "demonstration_standard": "x", "min_depth": "expert"},
    )
    assert resp.status_code == 422


def test_duplicate_canonical_name_rejected(client):
    payload = {"canonical_name": "Dup capability", "demonstration_standard": "x"}
    first = client.post("/api/capabilities", json=payload)
    assert first.status_code == 200
    second = client.post("/api/capabilities", json=payload)
    assert second.status_code == 400


def _tool_concept(cur, name, status="active"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('tool', %s, %s, 'curator', now()) RETURNING id",
        (name, status),
    )
    return str(cur.fetchone()["id"])


def test_component_edge_add_update_remove(client):
    cap = client.post("/api/capabilities", json={"canonical_name": "Cap with components", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "SomeTool")

    add = client.post(f"/api/capabilities/{cap['id']}/components", json={"concept_id": tool_id, "necessity": "core"})
    assert add.status_code == 200
    edge_id = add.json()["id"]

    components = client.get(f"/api/capabilities/{cap['id']}/components").json()
    assert len(components["core"]) == 1

    update = client.put(f"/api/capabilities/{cap['id']}/components/{edge_id}", json={"necessity": "supporting"})
    assert update.status_code == 200
    components = client.get(f"/api/capabilities/{cap['id']}/components").json()
    assert len(components["core"]) == 0
    assert len(components["supporting"]) == 1

    remove = client.delete(f"/api/capabilities/{cap['id']}/components/{edge_id}")
    assert remove.status_code == 200
    components = client.get(f"/api/capabilities/{cap['id']}/components").json()
    assert components == {"core": [], "supporting": [], "contextual": []}


def test_component_edge_rejects_invalid_necessity(client):
    cap = client.post("/api/capabilities", json={"canonical_name": "Cap X", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "ToolX")
    resp = client.post(f"/api/capabilities/{cap['id']}/components", json={"concept_id": tool_id, "necessity": "essential"})
    assert resp.status_code == 422


def test_component_edge_rejects_inactive_concept(client):
    cap = client.post("/api/capabilities", json={"canonical_name": "Cap Y", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "InactiveTool", status="proposed")
    resp = client.post(f"/api/capabilities/{cap['id']}/components", json={"concept_id": tool_id, "necessity": "core"})
    assert resp.status_code == 400


def test_component_edge_rejects_duplicate(client):
    cap = client.post("/api/capabilities", json={"canonical_name": "Cap Z", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "ToolZ")
    first = client.post(f"/api/capabilities/{cap['id']}/components", json={"concept_id": tool_id, "necessity": "core"})
    assert first.status_code == 200
    second = client.post(f"/api/capabilities/{cap['id']}/components", json={"concept_id": tool_id, "necessity": "supporting"})
    assert second.status_code == 400


def test_bulk_coverage_endpoint(client):
    create = client.post("/api/capabilities", json={"canonical_name": "Bulk coverage capability", "demonstration_standard": "x"})
    cap_id = create.json()["id"]
    resp = client.get("/api/capabilities/coverage")
    assert resp.status_code == 200
    rows = resp.json()
    match = next(r for r in rows if r["capability_concept_id"] == cap_id)
    assert match["status"] == "not_found"
    assert match["canonical_name"] == "Bulk coverage capability"


def test_rebuild_endpoint(client):
    client.post("/api/capabilities", json={"canonical_name": "Rebuild target", "demonstration_standard": "x"})
    resp = client.post("/api/capabilities/rebuild")
    assert resp.status_code == 200
    body = resp.json()
    assert body["engine_version"] == "capability-engine-v1"
    assert body["capability_coverage"]["computed"] >= 1
    assert "role_fit" in body
