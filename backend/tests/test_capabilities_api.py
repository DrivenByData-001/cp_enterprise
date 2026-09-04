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


# --- docs/18 §10: proposed-capability / proposed-component-edge review -----

def _proposed_edge(cur, atomic_id, capability_id, necessity="core", origin="bootstrap"):
    cur.execute(
        "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, necessity, origin, status) "
        "VALUES (%s, %s, 'component_of', %s, %s, 'proposed') RETURNING id",
        (atomic_id, capability_id, necessity, origin),
    )
    return str(cur.fetchone()["id"])


def test_proposed_capability_is_listed_only_under_its_own_status_filter(client):
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('capability', 'Proposed cap', 'proposed', 'bootstrap', now()) RETURNING id"
        )
        cap_id = str(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) "
            "VALUES (%s, 'placeholder', 'exposed')",
            (cap_id,),
        )

    active_list = client.get("/api/capabilities?status=active").json()
    assert not any(c["id"] == cap_id for c in active_list)

    proposed_list = client.get("/api/capabilities?status=proposed").json()
    assert any(c["id"] == cap_id for c in proposed_list)

    detail = client.get(f"/api/capabilities/{cap_id}").json()
    assert detail["status"] == "proposed"
    assert detail["components_proposed"] == {"core": [], "supporting": [], "contextual": []}


def test_review_component_accept_moves_edge_from_proposed_to_accepted(client):
    cap = client.post("/api/capabilities", json={"canonical_name": "Cap review accept", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "ReviewedTool")
        edge_id = _proposed_edge(cur, tool_id, cap["id"])

    detail = client.get(f"/api/capabilities/{cap['id']}").json()
    assert len(detail["components_proposed"]["core"]) == 1
    assert detail["components"] == {"core": [], "supporting": [], "contextual": []}

    resp = client.post(f"/api/capabilities/{cap['id']}/components/{edge_id}/review", json={"action": "accept"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    detail = client.get(f"/api/capabilities/{cap['id']}").json()
    assert detail["components_proposed"] == {"core": [], "supporting": [], "contextual": []}
    assert len(detail["components"]["core"]) == 1


def test_review_component_reject_never_becomes_accepted(client):
    cap = client.post("/api/capabilities", json={"canonical_name": "Cap review reject", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "RejectedTool")
        edge_id = _proposed_edge(cur, tool_id, cap["id"])

    resp = client.post(f"/api/capabilities/{cap['id']}/components/{edge_id}/review", json={"action": "reject"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    detail = client.get(f"/api/capabilities/{cap['id']}").json()
    assert detail["components_proposed"] == {"core": [], "supporting": [], "contextual": []}
    assert detail["components"] == {"core": [], "supporting": [], "contextual": []}

    with db.db_cursor() as cur:
        cur.execute("SELECT status FROM jobber.concept_edge WHERE id = %s", (edge_id,))
        assert cur.fetchone()["status"] == "rejected"


def test_review_component_rejects_edge_not_belonging_to_capability(client):
    cap_a = client.post("/api/capabilities", json={"canonical_name": "Cap A review", "demonstration_standard": "x"}).json()
    cap_b = client.post("/api/capabilities", json={"canonical_name": "Cap B review", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "CrossCapTool")
        edge_id = _proposed_edge(cur, tool_id, cap_a["id"])

    resp = client.post(f"/api/capabilities/{cap_b['id']}/components/{edge_id}/review", json={"action": "accept"})
    assert resp.status_code == 404


def test_merge_capability_rewires_component_edges_and_marks_source_merged(client):
    source = client.post("/api/capabilities", json={"canonical_name": "Duplicate cap A", "demonstration_standard": "x"}).json()
    target = client.post("/api/capabilities", json={"canonical_name": "Duplicate cap B", "demonstration_standard": "x"}).json()
    with db.db_cursor() as cur:
        tool_id = _tool_concept(cur, "MergedTool")
        _proposed_edge(cur, tool_id, source["id"])

    resp = client.post(f"/api/capabilities/{source['id']}/merge", json={"merge_into_id": target["id"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": source["id"], "status": "merged", "merged_into": target["id"]}

    with db.db_cursor() as cur:
        cur.execute("SELECT status, merged_into FROM jobber.concept WHERE id = %s", (source["id"],))
        row = cur.fetchone()
        assert row["status"] == "merged"
        assert str(row["merged_into"]) == target["id"]

        cur.execute(
            "SELECT necessity, status FROM jobber.concept_edge WHERE from_concept_id = %s AND to_concept_id = %s",
            (tool_id, target["id"]),
        )
        rewired = cur.fetchone()
        assert rewired is not None and rewired["status"] == "proposed"

        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.concept_edge WHERE to_concept_id = %s", (source["id"],)
        )
        assert cur.fetchone()["n"] == 0


def test_merge_capability_rejects_self_merge_and_missing_target(client):
    cap = client.post("/api/capabilities", json={"canonical_name": "Self merge cap", "demonstration_standard": "x"}).json()
    self_merge = client.post(f"/api/capabilities/{cap['id']}/merge", json={"merge_into_id": cap["id"]})
    assert self_merge.status_code == 400

    bad_target = client.post(f"/api/capabilities/{cap['id']}/merge", json={"merge_into_id": "00000000-0000-0000-0000-000000000000"})
    assert bad_target.status_code == 400
