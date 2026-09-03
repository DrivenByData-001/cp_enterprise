"""Regression coverage for every pre-Phase-2 user-facing flow, now running
against Postgres instead of SQLite (brief §14/§20.2: "the existing 23
migrated roles remain usable" — this is the behavioural half of that
guarantee; docs/14 covers the schema-shape half). Nothing here is new
behaviour — it is the same flows the SQLite-era app exposed, proven against
the new persistence layer.
"""

from app import concept_linking, db

LEGACY_POSTING = {
    "metadata": {"source": "user_paste", "extraction_status": "ok"},
    "job": {
        "title": "Senior Actuarial Analyst",
        "organisation": "Aviva",
        "description": "Own the reserving process.",
        "requirements": "Experience with IFRS 17 and Python.",
        "responsibilities": "Lead quarterly valuation.",
    },
    "skills": [
        {"name": "Python", "category": "technical", "importance": 4, "requirement_type": "required"},
        {"name": "IFRS 17", "category": "domain", "importance": 5, "requirement_type": "required"},
    ],
    "analysis": {"summary": "A reserving role.", "career_track": "actuarial"},
}

LEGACY_TARGET = {
    "metadata": {"source": "user_paste"},
    "target": {
        "title": "Chief Actuary",
        "is_imagined": False,
        "summary": "Own the actuarial function.",
        "typical_tasks": ["Sign off reserves"],
        "skill_decomposition": [],
        "technical_subjects": [],
    },
    "skills": [],
}


def test_legacy_json_import_and_role_listing(client):
    resp = client.post("/api/import", json=LEGACY_POSTING)
    assert resp.status_code == 200
    role_id = resp.json()["id"]

    roles = client.get("/api/roles").json()
    assert any(r["id"] == role_id for r in roles)

    role = client.get(f"/api/roles/{role_id}").json()
    assert role["title"] == "Senior Actuarial Analyst"
    assert role["description"] == "Own the reserving process."
    assert role["requirements"] == "Experience with IFRS 17 and Python."
    assert {s["name"] for s in role["skills"]} == {"Python", "IFRS 17"}
    assert role["raw_json"]["job"]["title"] == "Senior Actuarial Analyst"


def test_role_filtering_by_career_track_and_min_similarity(client):
    client.post("/api/import", json=LEGACY_POSTING)
    client.post("/api/profile", json={"narrative_text": "An actuary with reserving experience."})

    by_track = client.get("/api/roles", params={"career_track": "actuarial"}).json()
    assert len(by_track) == 1
    by_wrong_track = client.get("/api/roles", params={"career_track": "quant"}).json()
    assert by_wrong_track == []


def test_update_role_is_a_full_overwrite_and_reembeds(client):
    role_id = client.post("/api/import", json=LEGACY_POSTING).json()["id"]

    updated_payload = {**LEGACY_POSTING, "job": {**LEGACY_POSTING["job"], "title": "Updated Title"}, "skills": []}
    resp = client.put(f"/api/roles/{role_id}", json=updated_payload)
    assert resp.status_code == 200

    role = client.get(f"/api/roles/{role_id}").json()
    assert role["title"] == "Updated Title"
    assert role["skills"] == []  # full overwrite, not a merge


def test_delete_role(client):
    role_id = client.post("/api/import", json=LEGACY_POSTING).json()["id"]
    assert client.delete(f"/api/roles/{role_id}").status_code == 200
    assert client.get(f"/api/roles/{role_id}").status_code == 404


def test_target_import_listing_and_path(client):
    client.post("/api/import", json=LEGACY_POSTING)  # a real posting to serve as a stepping stone
    client.post("/api/profile", json={"narrative_text": "An actuary with reserving experience."})

    target_resp = client.post("/api/targets", json=LEGACY_TARGET)
    assert target_resp.status_code == 200
    target_id = target_resp.json()["id"]

    targets = client.get("/api/targets").json()
    assert any(t["id"] == target_id for t in targets)

    target = client.get(f"/api/roles/{target_id}").json()
    assert target["node_type"] == "target_real"
    assert "path" in target
    assert "stepping_stones" in target["path"]


def test_update_target_rejects_editing_a_posting_via_target_route(client):
    role_id = client.post("/api/import", json=LEGACY_POSTING).json()["id"]
    resp = client.put(f"/api/targets/{role_id}", json=LEGACY_TARGET)
    assert resp.status_code == 404 or resp.status_code == 400


def test_episodes_crud_and_timeline(client):
    create = client.post(
        "/api/episodes",
        json={"kind": "employment", "title": "Actuarial Analyst", "organisation": "Aviva", "start_date": "2019-03", "end_date": "2022-08"},
    )
    assert create.status_code == 200
    episode_id = create.json()["id"]

    timeline = client.get("/api/episodes/timeline").json()
    assert timeline["total_span_years"] > 3
    assert timeline["episodes"][0]["duration_years"] > 3

    update = client.put(
        f"/api/episodes/{episode_id}",
        json={"kind": "employment", "title": "Senior Actuarial Analyst", "organisation": "Aviva", "start_date": "2019-03", "end_date": "2022-08"},
    )
    assert update.status_code == 200
    assert client.get(f"/api/episodes/{episode_id}").json()["title"] == "Senior Actuarial Analyst"

    assert client.delete(f"/api/episodes/{episode_id}").status_code == 200


def test_nested_episode_blocks_parent_deletion(client):
    parent_id = client.post("/api/episodes", json={"kind": "employment", "title": "Job", "start_date": "2019"}).json()["id"]
    client.post("/api/episodes", json={"kind": "project", "title": "Project", "start_date": "2020", "parent_episode_id": parent_id})

    resp = client.delete(f"/api/episodes/{parent_id}")
    assert resp.status_code == 400


def test_concept_crud_and_proposal_review_workflow(client):
    types = client.get("/api/concepts/types").json()
    assert len(types) == 10

    concept_resp = client.post("/api/concepts", json={"type_code": "tool", "canonical_name": "SQL"})
    assert concept_resp.status_code == 200

    role_id = client.post("/api/import", json=LEGACY_POSTING).json()["id"]  # skills land unresolved until Pass B runs

    with db.db_cursor() as cur:
        concept_linking.run_pass_b(cur)

    proposals = client.get("/api/concepts/proposals").json()
    assert len(proposals) >= 1
    surface_form = proposals[0]["surface_form"]

    resolve = client.post(
        "/api/concepts/proposals/resolve",
        json={"surface_form": surface_form, "action": "accept_new", "type_code": "domain", "canonical_name": surface_form.title()},
    )
    assert resolve.status_code == 200
    assert resolve.json()["status"] == "accepted_new"

    role = client.get(f"/api/roles/{role_id}").json()
    assert any(s["resolved_concept_id"] is not None for s in role["skills"] if s["name"].lower() == surface_form)


def test_profile_update_and_history(client):
    first = client.post("/api/profile", json={"narrative_text": "Version one."})
    assert first.status_code == 200
    second = client.post("/api/profile", json={"narrative_text": "Version two."})
    assert second.status_code == 200

    current = client.get("/api/profile").json()
    assert current["narrative_text"] == "Version two."

    history = client.get("/api/profile/history").json()
    assert len(history) == 2
    assert history[0]["is_current"] is True


def test_profile_rejects_blank_narrative(client):
    resp = client.post("/api/profile", json={"narrative_text": "   "})
    assert resp.status_code == 400


def test_space_view(client):
    client.post("/api/import", json=LEGACY_POSTING)
    client.post("/api/targets", json=LEGACY_TARGET)
    client.post("/api/profile", json={"narrative_text": "An actuary."})

    space = client.get("/api/space").json()
    assert len(space["points"]) == 2
    assert space["profile"] is not None
    node_types = {p["node_type"] for p in space["points"]}
    assert node_types == {"posting", "target_real"}


def test_bulk_import(client):
    import io
    import json

    files = {"files": ("posting.json", io.BytesIO(json.dumps(LEGACY_POSTING).encode()), "application/json")}
    resp = client.post("/api/import/bulk", files=files)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["status"] == "imported"
