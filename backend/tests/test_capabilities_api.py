"""Capability catalogue CRUD + component_of edge API (brief §5/§6/§27)."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

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


# Vocabulary concepts exist independently of their assessment specification.


def _vocabulary_capability(name="Capital Modelling", status="active", type_code="capability"):
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept (canonical_name, definition, type_code, status, origin) "
            "VALUES (%s, 'Existing Vocabulary definition', %s, %s, 'curator') RETURNING id",
            (name, type_code, status),
        )
        return str(cur.fetchone()["id"])


def test_discover_only_active_unconfigured_capabilities(client):
    pending = _vocabulary_capability()
    for status in ("proposed", "deprecated", "merged", "rejected"):
        _vocabulary_capability(status, status=status)
    _vocabulary_capability("Tool", type_code="tool")
    client.post("/api/capabilities", json={"canonical_name": "Configured", "demonstration_standard": "x"})
    rows = client.get("/api/capabilities/unconfigured").json()
    assert [r["id"] for r in rows] == [pending]
    assert "min_depth" not in rows[0]
    assert client.get("/api/capabilities/unconfigured?q=capital").json() == rows
    assert client.get("/api/capabilities/unconfigured?q=missing").json() == []
    assert all(r["capability_concept_id"] != pending for r in client.get("/api/capabilities/coverage").json())


def test_configure_preserves_vocabulary_identity_and_components(client):
    cap_id = _vocabulary_capability()
    with db.db_cursor() as cur:
        cur.execute("INSERT INTO jobber.concept_alias (concept_id, alias, origin) VALUES (%s, 'Capital models', 'curator')", (cap_id,))
        tool = _tool_concept(cur, "Capital tool")
        edge = _proposed_edge(cur, tool, cap_id)
        cur.execute("SELECT * FROM jobber.concept ORDER BY id")
        concepts = cur.fetchall()
        cur.execute("SELECT * FROM jobber.concept_alias")
        aliases = cur.fetchall()
        cur.execute("SELECT * FROM jobber.concept_edge")
        edges = cur.fetchall()
    spec = dict(demonstration_standard="Own a capital modelling cycle", min_depth="owned",
                min_autonomy="independent", requires_all_core=False, min_core_required=1,
                economic_salience="high", notes="Curated specification")
    response = client.post(f"/api/capabilities/{cap_id}/configure", json=spec)
    assert response.status_code == 200
    assert response.json()["id"] == cap_id
    assert client.get("/api/capabilities/unconfigured").json() == []
    assert [r["id"] for r in client.get("/api/capabilities").json()] == [cap_id]
    detail = client.get(f"/api/capabilities/{cap_id}").json()
    assert all(detail[k] == v for k, v in spec.items())
    assert detail["components_proposed"]["core"][0]["edge_id"] == edge
    assert detail["components"]["core"] == []
    assert detail["coverage"]["status"] == "not_found"
    assert client.post(f"/api/capabilities/{cap_id}/configure", json={**spec, "notes": "overwrite"}).status_code == 409
    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.concept ORDER BY id")
        assert cur.fetchall() == concepts
        cur.execute("SELECT * FROM jobber.concept_alias")
        assert cur.fetchall() == aliases
        cur.execute("SELECT * FROM jobber.concept_edge")
        assert cur.fetchall() == edges
        cur.execute("SELECT notes FROM jobber.capability_detail WHERE concept_id = %s", (cap_id,))
        assert cur.fetchall() == [{"notes": spec["notes"]}]
    assert client.post(f"/api/capabilities/{cap_id}/components/{edge}/review", json={"action": "accept"}).status_code == 200
    assert len(client.get(f"/api/capabilities/{cap_id}/components").json()["core"]) == 1


@pytest.mark.parametrize("status,type_code", [("active", "tool"), ("deprecated", "capability"), ("proposed", "capability"), ("merged", "capability")])
def test_configure_rejects_ineligible_concept(client, status, type_code):
    cap_id = _vocabulary_capability(status=status, type_code=type_code)
    assert client.post(f"/api/capabilities/{cap_id}/configure", json={"demonstration_standard": "x"}).status_code == 400
    with db.db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.capability_detail")
        assert cur.fetchall() == []


def test_configure_missing_and_invalid_id(client):
    for cap_id, code in [(str(uuid.uuid4()), 404), ("bad-id", 422)]:
        assert client.post(f"/api/capabilities/{cap_id}/configure", json={"demonstration_standard": "x"}).status_code == code


@pytest.mark.parametrize("fields", [{"demonstration_standard": " "}, {"min_depth": "expert"}, {"min_autonomy": "boss"}, {"economic_salience": "extreme"}, {"min_core_required": -1}, {"canonical_name": "Replacement"}])
def test_configure_rejects_invalid_specification(client, fields):
    cap_id = _vocabulary_capability()
    assert client.post(f"/api/capabilities/{cap_id}/configure", json={"demonstration_standard": "x", **fields}).status_code == 422


def test_concurrent_configuration_creates_one_detail(client):
    cap_id = _vocabulary_capability()
    def configure(_):
        return client.post(f"/api/capabilities/{cap_id}/configure", json={"demonstration_standard": "x"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(configure, range(2))) == [200, 409]
    with db.db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM jobber.capability_detail WHERE concept_id = %s", (cap_id,))
        assert cur.fetchone()["n"] == 1


def test_clear_optional_specification_fields(client):
    cap_id = _vocabulary_capability()
    assert client.post(f"/api/capabilities/{cap_id}/configure", json={
        "demonstration_standard": "Own a cycle", "min_autonomy": "independent",
        "economic_salience": "high", "notes": "old", "min_core_required": 2,
    }).status_code == 200
    fields = {"min_autonomy": None, "economic_salience": None, "notes": None, "min_core_required": None}
    assert client.put(f"/api/capabilities/{cap_id}", json=fields).status_code == 200
    detail = client.get(f"/api/capabilities/{cap_id}").json()
    assert all(detail[k] is None for k in fields)
    assert detail["demonstration_standard"] == "Own a cycle"
