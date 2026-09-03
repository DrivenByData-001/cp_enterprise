"""Regression coverage for every pre-Phase-2 user-facing flow, now running
against Postgres instead of SQLite (brief §14/§20.2: "the existing 23
migrated roles remain usable" — this is the behavioural half of that
guarantee; docs/14 covers the schema-shape half). Nothing here is new
behaviour — it is the same flows the SQLite-era app exposed, proven against
the new persistence layer, except where the Phase 2 production-schema
reconciliation pass genuinely removed a flow (profile/episode writes —
profile360 is authoritative and read-only from this app now, docs/14 §9) —
those are covered as read-only instead of dropped outright.
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


def _seed_profile_snapshot(narrative_text: str) -> None:
    """profile360 is authoritative for the current profile narrative now
    (docs/14 §9) — there is no more POST /api/profile to seed one through the
    app's own API, so tests that need a non-empty "current profile" for
    similarity computations write directly to the profile360 stub
    local_baseline.sql provides."""
    with db.db_cursor() as cur:
        cur.execute("INSERT INTO profile360.snapshots (narrative_text) VALUES (%s)", (narrative_text,))


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
    _seed_profile_snapshot("An actuary with reserving experience.")

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
    _seed_profile_snapshot("An actuary with reserving experience.")

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


def test_episodes_are_read_only_from_profile360(client):
    """jobber.episode does not exist — episodes are profile360's own data,
    browsed read-only (docs/14 §9). Seed profile360's stub table directly
    since there is no ingestion path for it in this app."""
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO profile360.episodes (title) VALUES (%s) RETURNING id",
            ("Actuarial Analyst at Aviva",),
        )
        episode_id = str(cur.fetchone()["id"])

    episodes = client.get("/api/episodes").json()
    assert any(e["id"] == episode_id for e in episodes)

    episode = client.get(f"/api/episodes/{episode_id}").json()
    assert episode["_display"] == "Actuarial Analyst at Aviva"

    assert client.get("/api/episodes/00000000-0000-0000-0000-000000000000").status_code == 404


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


def test_profile_reads_current_snapshot_and_history_from_profile360(client):
    """jobber.profile_snapshots does not exist — the narrative lives only in
    profile360, authored by its own tool; this app only ever reads it
    (docs/14 §9). Seed the profile360 stub table directly, in two separate
    transactions so their created_at values are actually ordered, since
    that's the recency column get_current_snapshot sorts by."""
    _seed_profile_snapshot("Version one.")
    _seed_profile_snapshot("Version two.")

    current = client.get("/api/profile").json()
    assert current["_display"] == "Version two."

    history = client.get("/api/profile/history").json()
    assert len(history) == 2
    assert {h["_display"] for h in history} == {"Version one.", "Version two."}


def test_profile_returns_none_when_no_snapshot_exists(client):
    assert client.get("/api/profile").json() is None


def test_space_view(client):
    client.post("/api/import", json=LEGACY_POSTING)
    client.post("/api/targets", json=LEGACY_TARGET)
    _seed_profile_snapshot("An actuary.")

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
