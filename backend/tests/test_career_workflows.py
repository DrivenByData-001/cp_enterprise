from app import db
from app.ai import AITaskResult, AITaskRun, AIConfigError
from app.models import TargetImport
from app.role_requirements import load_role_requirements, load_role_requirements_bulk

def concept(cur, name):
    cur.execute("INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) VALUES ('tool', %s, 'active', 'curator', now()) RETURNING id", (name,))
    return str(cur.fetchone()["id"])

def role(cur, name="Role", kind="observed_posting"):
    return db.upsert_role_instance(cur, None, {"title": name, "instance_type": kind}, skills=[])

def claim(cur, rid, cid, status="accepted"):
    cur.execute("INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) VALUES (%s, %s, 'required', 'user_asserted', %s) RETURNING id", (rid, cid, status))
    return str(cur.fetchone()["id"])

def observation(cur, rid, cid):
    cur.execute("INSERT INTO jobber.role_skill_observation (role_instance_id, canonical_concept_id, surface_form, observation_basis, requirement_type) VALUES (%s, %s, 'Python', 'legacy_extraction', 'required')", (rid, cid))

def test_facets_filters_and_comparison_share_evidence(client):
    with db.db_cursor() as cur:
        cid = concept(cur, "Python")
        source, legacy, rejected, target = role(cur, "Source"), role(cur, "Legacy"), role(cur, "Rejected"), role(cur, "Target", "user_defined_target")
        claim(cur, source, cid)
        observation(cur, legacy, cid)
        claim(cur, rejected, cid, "rejected")
        observation(cur, rejected, cid)
        claim(cur, target, cid)
        grouped = load_role_requirements_bulk(cur, [source, legacy, rejected])
        assert grouped[source] == load_role_requirements(cur, source)
    facets = client.get("/api/concepts/facets?type_code=tool").json()
    assert facets == [{"id": cid, "canonical_name": "Python", "role_count": 2}]
    listed = client.get("/api/roles", params={"concept_id": cid, "period": "all"}).json()
    assert {r["id"] for r in listed["items"]} == {source, legacy}
    assert len(client.get(f"/api/comparison/role/{source}").json()["items"]) == 1
    assert len(client.get(f"/api/comparison/role/{rejected}").json()["items"]) == 0

def test_superseded_claim_does_not_reappear_in_filter(client):
    with db.db_cursor() as cur:
        old, new = concept(cur, "Old"), concept(cur, "New")
        rid = role(cur)
        old_claim = claim(cur, rid, old)
        new_claim = claim(cur, rid, new)
        cur.execute("UPDATE jobber.requirement_claim SET superseded_by = %s WHERE id = %s", (new_claim, old_claim))
        observation(cur, rid, old)
    assert client.get("/api/roles", params={"concept_id": old, "period": "all"}).json()["total"] == 0
    assert client.get("/api/roles", params={"concept_id": new, "period": "all"}).json()["total"] == 1

def test_development_actions_are_scoped_and_do_not_create_evidence(client):
    with db.db_cursor() as cur:
        rid, other, cid = role(cur), role(cur, "Other"), concept(cur, "Python")
        claim(cur, rid, cid)
    before = client.get(f"/api/comparison/role/{rid}").json()["counts"]
    response = client.post(f"/api/comparison/role/{rid}/actions", json={"concept_id": cid, "title": "Build a project", "note": "Document the result", "due_date": "2026-12-01"})
    assert response.status_code == 200, response.text
    action = response.json()
    assert client.patch(f"/api/comparison/role/{other}/actions/{action['id']}", json={"status": "done"}).status_code == 404
    assert client.patch(f"/api/comparison/role/{rid}/actions/{action['id']}", json={"status": "done"}).json()["status"] == "done"
    assert client.get(f"/api/comparison/role/{rid}").json()["counts"] == before
    assert client.patch(f"/api/comparison/role/{rid}/actions/{action['id']}", json={"status": "open"}).status_code == 200
    assert client.delete(f"/api/comparison/role/{rid}/actions/{action['id']}").status_code == 200
    assert client.get(f"/api/comparison/role/{rid}/actions").json() == []

def test_target_draft_is_reviewable_and_audited_before_save(client, monkeypatch):
    from app import target_preview
    def generate(**kwargs):
        output = TargetImport.model_validate({"metadata": {}, "target": {"title": "Model title", "is_imagined": False, "typical_tasks": ["Analyse risk"]}, "skills": []})
        run = AITaskRun("target_decompose", "test-model", "decompose_target_role.md", "v1", "2026-09-13", "2026-09-13", "ok", 20, 50)
        return AITaskResult(output, run)
    monkeypatch.setattr(target_preview, "run_json_task", generate)
    result = client.post("/api/targets/preview", json={"title": "My target", "is_imagined": True, "description": "Risk role"}).json()
    assert result["status"] == "ok"
    assert result["proposal"]["target"]["title"] == "My target"
    assert result["proposal"]["target"]["is_imagined"] is True
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_instance")
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT status, task FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        assert dict(cur.fetchone()) == {"status": "ok", "task": "target_decompose"}
    saved = client.post("/api/targets", json=result["proposal"])
    assert saved.status_code == 200, saved.text

def test_target_draft_failure_records_attempt_and_allows_manual_creation(client, monkeypatch):
    from app import target_preview
    def fail(**kwargs):
        raise AIConfigError("AI unavailable")
    monkeypatch.setattr(target_preview, "run_json_task", fail)
    result = client.post("/api/targets/preview", json={"title": "Manual target"}).json()
    assert result["status"] == "failed" and result["proposal"] is None
    with db.db_cursor() as cur:
        cur.execute("SELECT status FROM jobber.extraction_run WHERE id = %s", (result["extraction_run_id"],))
        assert cur.fetchone()["status"] == "failed"
    assert client.post("/api/targets", json={"metadata": {"source": "user_defined"}, "target": {"title": "Manual target"}, "skills": []}).status_code == 200

def test_invalid_draft_input_is_rejected_before_ai(client):
    assert client.post("/api/targets/preview", json={"title": "  "}).status_code == 422
    assert client.post("/api/targets/preview", json={"title": "X", "supporting_material": "x" * 40001}).status_code == 422

def test_path_works_without_embeddings_and_prefers_reachable_bridge(client, monkeypatch):
    from app import stepping_stones
    with db.db_cursor() as cur:
        a, b, c = concept(cur, "A"), concept(cur, "B"), concept(cur, "C")
        target = role(cur, "Target", "user_defined_target")
        bridge, near_target, empty = role(cur, "Bridge"), role(cur, "Near target"), role(cur, "Empty")
        for cid in (a, b, c):
            claim(cur, target, cid)
            claim(cur, near_target, cid)
        for cid in (a, b):
            claim(cur, bridge, cid)
    monkeypatch.setattr(stepping_stones, "atomic_concept_evidence", lambda cur, cid: {"status": "evidenced" if cid == a else "not_found"})
    result = client.get(f"/api/roles/{target}")
    assert result.status_code == 200, result.text
    path = result.json()["path"]
    assert path["stepping_stones"][0]["id"] == bridge
    assert path["stepping_stones"][0]["assessment"] == "potential_step"
    assert next(r for r in path["stepping_stones"] if r["id"] == empty)["assessment"] == "insufficient_evidence"

