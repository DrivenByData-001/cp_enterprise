"""Vocabulary proposal prioritisation and curation — DB-backed tests. Runs
against the real Postgres test database (conftest.py), same precedent as
test_vocabulary_bootstrap.py; only embeddings are stubbed."""

import pytest

from app import db, vocabulary_bootstrap as vb, vocabulary_curation as curation
from app.vocabulary_priority import BAND_HIGH, BAND_MEDIUM, BAND_SPARSE


def _role(cur, title, skills, **cols):
    columns = {"instance_type": "observed_posting", "title": title, **cols}
    return db.upsert_role_instance(cur, None, columns, skills=skills)


def _skill(name):
    return {"name": name, "requirement_type": "required", "importance": 3}


def _active_concept(cur, name, type_code="tool"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _cluster_key_of(cur, surface_form: str) -> str:
    cur.execute(
        "SELECT COALESCE(cluster_key, surface_form) AS ck FROM jobber.concept_proposal WHERE surface_form = %s",
        (surface_form,),
    )
    return cur.fetchone()["ck"]


def _source_aware_role(cur, title="Source-aware role", body="Requires Foo Modelling.", **cols):
    """A role captured via source-aware ingest + extract-requirements: has a
    document but *no* role_skill_observation rows at all — unresolved
    surface forms from this pipeline only ever appear via
    concept_proposal/concept_proposal_occurrence, never the legacy table
    _role/_skill above populate."""
    document_id, _ = db.create_document(cur, kind="job_posting", content_text=body, provenance_quality="original")
    role_id = db.upsert_role_instance(
        cur, None, {"instance_type": "observed_posting", "title": title, "document_id": document_id, **cols}, skills=[]
    )
    return role_id, document_id


def _occurrence(cur, role_id, document_id, *, surface_form, requirement_type="required", basis="stated",
                 evidence_span=None, extraction_run_id=None) -> str:
    """Mirrors extraction.py's unresolved-surface-form path exactly: a
    concept_proposal (deduplicated globally by surface_form, reusing an
    existing pending one for the same term) plus the per-role
    concept_proposal_occurrence link, now also carrying the occurrence's own
    requirement shape (migration 0022) so a later resolution can rebuild a
    faithful claim from it. Returns the proposal id."""
    normalized = surface_form.strip().lower()
    cur.execute("SELECT id FROM jobber.concept_proposal WHERE surface_form = %s AND status = 'pending'", (normalized,))
    existing = cur.fetchone()
    if existing:
        proposal_id = existing["id"]
        cur.execute(
            "UPDATE jobber.concept_proposal SET occurrence_count = occurrence_count + 1 WHERE id = %s", (proposal_id,)
        )
    else:
        cur.execute(
            "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, evidence_span, status) "
            "VALUES (%s, 1, %s, %s, 'pending') RETURNING id",
            (normalized, document_id, evidence_span),
        )
        proposal_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO jobber.concept_proposal_occurrence "
        "(concept_proposal_id, role_instance_id, document_id, extraction_run_id, requirement_type, basis, evidence_span) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (proposal_id, role_id, document_id, extraction_run_id, requirement_type, basis, evidence_span),
    )
    return str(proposal_id)


# --- cluster aggregation (brief §1) -----------------------------------------

def test_cluster_aggregation_groups_lexical_variants_with_full_evidence(client):
    with db.db_cursor() as cur:
        _role(cur, "Actuarial Analyst", [_skill("Solvency II")], country="UK", seniority_level="mid", career_track="actuarial", posting_date="2020-01-01")
        _role(cur, "Actuarial Manager", [_skill("SII")], country="Ireland", seniority_level="senior", career_track="actuarial", posting_date="2023-06-15")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "solvency ii")

    resp = client.get("/api/vocabulary/clusters", params={"status": "pending", "q": "solvency"})
    assert resp.status_code == 200
    body = resp.json()
    matches = [c for c in body["items"] if c["cluster_key"] == key]
    assert len(matches) == 1
    c = matches[0]
    assert set(c["surface_forms"]) == {"solvency ii", "sii"}
    assert c["suggested_canonical_label"] == "solvency ii"
    assert c["role_count"] == 2
    assert c["observation_count"] == 2
    assert set(c["countries"]) == {"UK", "Ireland"}
    assert set(c["seniority_levels"]) == {"mid", "senior"}
    assert c["first_observed"] == "2020-01-01"
    assert c["last_observed"] == "2023-06-15"
    assert c["distinct_years"] == [2020, 2023]
    assert len(c["example_roles"]) == 2
    assert "priority_score" in c and "priority_band" in c


def test_cluster_detail_endpoint_matches_list_evidence(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Chain ladder reserving technique")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "chain ladder reserving technique")

    resp = client.get(f"/api/vocabulary/clusters/{key}")
    assert resp.status_code == 200
    assert resp.json()["cluster_key"] == key
    assert resp.json()["role_count"] == 1

    missing = client.get("/api/vocabulary/clusters/not-a-real-cluster-key")
    assert missing.status_code == 404


# --- deterministic priority ordering + anti-dominance (brief §2) -----------

def test_priority_ordering_is_deterministic_and_broad_beats_narrow_loud(client):
    with db.db_cursor() as cur:
        # "Buzzword" mentioned 20x in ONE role -> must not outrank "Widely
        # Used", seen once each across 5 distinct roles/years/countries.
        loud_skills = [_skill("Buzzword") for _ in range(20)]
        _role(cur, "One noisy role", loud_skills, posting_date="2026-01-01", country="UK")

        for i in range(5):
            _role(
                cur, f"Broad role {i}", [_skill("Widely Used")],
                posting_date=f"{2018 + i}-01-01", country=["UK", "Ireland", "US", "Canada", "Australia"][i],
                seniority_level=["junior", "mid", "senior", "mid", "senior"][i],
            )
        vb.compute_cluster_keys(cur)

    resp1 = client.get("/api/vocabulary/clusters", params={"status": "pending", "sort": "priority", "limit": 200})
    resp2 = client.get("/api/vocabulary/clusters", params={"status": "pending", "sort": "priority", "limit": 200})
    assert resp1.json()["items"] == resp2.json()["items"]  # deterministic — identical on repeat calls

    order = [c["suggested_canonical_label"] for c in resp1.json()["items"]]
    assert order.index("widely used") < order.index("buzzword")

    by_label = {c["suggested_canonical_label"]: c for c in resp1.json()["items"]}
    assert by_label["buzzword"]["role_count"] == 1
    assert by_label["buzzword"]["observation_count"] == 20
    assert by_label["widely used"]["role_count"] == 5


# --- priority bands (brief §7) -----------------------------------------------

def test_priority_bands_reflect_breadth_of_evidence(client):
    with db.db_cursor() as cur:
        _role(cur, "Solo role", [_skill("One Off Term")])  # sparse: 1 role

        for i in range(6):
            _role(
                cur, f"High band role {i}", [_skill("Broad Term")],
                posting_date=f"{2019 + i}-01-01", country=["UK", "Ireland", "UK", "US", "UK", "Canada"][i],
                seniority_level=["junior", "mid", "senior", "mid", "senior", "junior"][i],
            )
        vb.compute_cluster_keys(cur)

    resp = client.get("/api/vocabulary/clusters", params={"status": "pending", "limit": 200})
    by_label = {c["suggested_canonical_label"]: c for c in resp.json()["items"]}
    assert by_label["one off term"]["priority_band"] == BAND_SPARSE
    assert by_label["broad term"]["priority_band"] == BAND_HIGH

    high_band_resp = client.get("/api/vocabulary/clusters", params={"status": "pending", "band": "high"})
    assert all(c["priority_band"] == BAND_HIGH for c in high_band_resp.json()["items"])
    assert any(c["suggested_canonical_label"] == "broad term" for c in high_band_resp.json()["items"])
    assert not any(c["suggested_canonical_label"] == "one off term" for c in high_band_resp.json()["items"])


# --- filtering / search / pagination (brief §3) -----------------------------

def test_filtering_by_role_count_country_and_seniority(client):
    with db.db_cursor() as cur:
        _role(cur, "R1", [_skill("Filtered Term A")], country="UK", seniority_level="senior")
        _role(cur, "R2", [_skill("Filtered Term B")], country="US", seniority_level="junior")
        vb.compute_cluster_keys(cur)

    by_country = client.get("/api/vocabulary/clusters", params={"country": "UK"}).json()["items"]
    labels = {c["suggested_canonical_label"] for c in by_country}
    assert "filtered term a" in labels
    assert "filtered term b" not in labels

    by_seniority = client.get("/api/vocabulary/clusters", params={"seniority": "junior"}).json()["items"]
    labels2 = {c["suggested_canonical_label"] for c in by_seniority}
    assert "filtered term b" in labels2
    assert "filtered term a" not in labels2

    by_min_role_count = client.get("/api/vocabulary/clusters", params={"min_role_count": 2}).json()["items"]
    assert all(c["role_count"] >= 2 for c in by_min_role_count)


def test_search_matches_surface_form_or_canonical_label(client):
    with db.db_cursor() as cur:
        _role(cur, "R1", [_skill("Internal Model Validation")])
        _role(cur, "R2", [_skill("Totally Unrelated Term")])
        vb.compute_cluster_keys(cur)

    resp = client.get("/api/vocabulary/clusters", params={"q": "internal model"})
    labels = {c["suggested_canonical_label"] for c in resp.json()["items"]}
    assert "internal model validation" in labels
    assert "totally unrelated term" not in labels


def test_pagination_covers_full_set_without_duplicates(client):
    with db.db_cursor() as cur:
        for i in range(25):
            _role(cur, f"Role {i}", [_skill(f"Unique Pagination Term {i:02d}")])
        vb.compute_cluster_keys(cur)

    seen = []
    offset = 0
    page_size = 7
    total = None
    while True:
        resp = client.get("/api/vocabulary/clusters", params={"limit": page_size, "offset": offset, "sort": "alphabetical"})
        body = resp.json()
        total = body["total"]
        if not body["items"]:
            break
        seen.extend(c["cluster_key"] for c in body["items"])
        offset += page_size
        if offset > total + page_size:
            break

    assert total >= 25
    assert len(seen) == len(set(seen))  # no duplicates across pages
    assert len(seen) == total


def test_default_view_is_pending_and_highest_priority_first(client):
    with db.db_cursor() as cur:
        _role(cur, "R1", [_skill("Default View Term")])
        vb.compute_cluster_keys(cur)

    resp = client.get("/api/vocabulary/clusters")
    body = resp.json()
    assert body["status"] == "pending"
    assert body["sort"] == "priority"
    scores = [c["priority_score"] for c in body["items"]]
    assert scores == sorted(scores, reverse=True)


# --- cluster acceptance / alias creation / observation mapping (brief §5) --

def test_accept_cluster_creates_one_concept_and_aliases_and_maps_observations(client):
    with db.db_cursor() as cur:
        role_a = _role(cur, "Role A", [_skill("Solvency II")])
        role_b = _role(cur, "Role B", [_skill("SII")])
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "solvency ii")

    resp = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": key, "type_code": "regulation", "canonical_name": "Solvency II"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted_new"
    concept_id = body["resolved_concept_id"]
    assert body["aliases_created"] == 1
    assert body["idempotent_replay"] is False

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE canonical_name = 'Solvency II'")
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT alias FROM jobber.concept_alias WHERE concept_id = %s", (concept_id,))
        assert {r["alias"] for r in cur.fetchall()} == {"sii"}
        cur.execute("SELECT canonical_concept_id FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_a,))
        assert str(cur.fetchone()["canonical_concept_id"]) == concept_id
        cur.execute("SELECT canonical_concept_id FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_b,))
        assert str(cur.fetchone()["canonical_concept_id"]) == concept_id


def test_accept_cluster_is_idempotent_on_retry(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Idempotent Term")])
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "idempotent term")

    payload = {"cluster_key": key, "type_code": "tool", "canonical_name": "Idempotent Term"}
    first = client.post("/api/vocabulary/clusters/accept", json=payload)
    second = client.post("/api/vocabulary/clusters/accept", json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["resolved_concept_id"] == second.json()["resolved_concept_id"]
    assert second.json()["idempotent_replay"] is True

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE canonical_name = 'Idempotent Term'")
        assert cur.fetchone()["n"] == 1  # never duplicated


def test_accept_cluster_conflicts_if_already_resolved_differently(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Conflict Term")])
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "conflict term")

    reject_resp = client.post("/api/vocabulary/clusters/reject", json={"cluster_key": key})
    assert reject_resp.status_code == 200

    accept_resp = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": key, "type_code": "tool", "canonical_name": "Conflict Term"},
    )
    assert accept_resp.status_code == 409


def test_accept_unknown_cluster_key_is_404(client):
    resp = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": "no-such-cluster", "type_code": "tool", "canonical_name": "X"},
    )
    assert resp.status_code == 404


# --- rejection (brief §5) ---------------------------------------------------

def test_reject_cluster_creates_no_concept_and_preserves_history(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Reject Me")])
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "reject me")
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        before = cur.fetchone()["n"]

    resp = client.post("/api/vocabulary/clusters/reject", json={"cluster_key": key})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert resp.json()["resolved_concept_id"] is None

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        assert cur.fetchone()["n"] == before
        cur.execute("SELECT status FROM jobber.concept_proposal WHERE surface_form = 'reject me'")
        assert cur.fetchone()["status"] == "rejected"  # preserved, not deleted

    replay = client.post("/api/vocabulary/clusters/reject", json={"cluster_key": key})
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True


# --- merge into existing concept (brief §5) ---------------------------------

def test_merge_cluster_into_existing_concept_creates_no_second_concept(client):
    with db.db_cursor() as cur:
        target_id = _active_concept(cur, "Python", type_code="tool")
        role_a = _role(cur, "Role A", [_skill("python programming")])
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "python programming")
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        before = cur.fetchone()["n"]

    resp = client.post("/api/vocabulary/clusters/merge", json={"cluster_key": key, "concept_id": target_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted_alias"
    assert body["resolved_concept_id"] == target_id

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        assert cur.fetchone()["n"] == before  # no second concept created
        cur.execute("SELECT alias FROM jobber.concept_alias WHERE concept_id = %s", (target_id,))
        assert "python programming" in {r["alias"] for r in cur.fetchall()}
        cur.execute("SELECT canonical_concept_id FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_a,))
        assert str(cur.fetchone()["canonical_concept_id"]) == target_id

    replay = client.post("/api/vocabulary/clusters/merge", json={"cluster_key": key, "concept_id": target_id})
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True


# --- batch review (brief §6/§14) --------------------------------------------

def test_batch_accept_preview_reports_counts_without_writing(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Batch Term One")])
        _role(cur, "Role B", [_skill("Batch Term Two")])
        vb.compute_cluster_keys(cur)
        key1 = _cluster_key_of(cur, "batch term one")
        key2 = _cluster_key_of(cur, "batch term two")
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        before = cur.fetchone()["n"]

    resp = client.post(
        "/api/vocabulary/clusters/batch/preview",
        json={
            "action": "accept",
            "items": [
                {"cluster_key": key1, "canonical_name": "Batch Term One", "type_code": "tool"},
                {"cluster_key": key2, "canonical_name": "Batch Term Two", "type_code": "tool"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["clusters_selected"] == 2
    assert body["clusters_ready"] == 2
    assert body["resulting_concepts"] == 2
    assert body["observations_affected"] == 2

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        assert cur.fetchone()["n"] == before  # preview writes nothing
        cur.execute("SELECT status FROM jobber.concept_proposal WHERE surface_form IN ('batch term one', 'batch term two')")
        assert all(r["status"] == "pending" for r in cur.fetchall())


def test_batch_accept_executes_all_selected_clusters(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Batch Exec One")])
        _role(cur, "Role B", [_skill("Batch Exec Two")])
        vb.compute_cluster_keys(cur)
        key1 = _cluster_key_of(cur, "batch exec one")
        key2 = _cluster_key_of(cur, "batch exec two")

    resp = client.post(
        "/api/vocabulary/clusters/batch",
        json={
            "action": "accept",
            "items": [
                {"cluster_key": key1, "canonical_name": "Batch Exec One", "type_code": "tool"},
                {"cluster_key": key2, "canonical_name": "Batch Exec Two", "type_code": "tool"},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["clusters_processed"] == 2
    assert all(r["status"] == "accepted_new" for r in resp.json()["results"])

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE canonical_name IN ('Batch Exec One', 'Batch Exec Two')")
        assert cur.fetchone()["n"] == 2


def test_batch_reject_executes_all_selected_clusters(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Batch Reject One")])
        _role(cur, "Role B", [_skill("Batch Reject Two")])
        vb.compute_cluster_keys(cur)
        key1 = _cluster_key_of(cur, "batch reject one")
        key2 = _cluster_key_of(cur, "batch reject two")

    resp = client.post(
        "/api/vocabulary/clusters/batch",
        json={"action": "reject", "items": [{"cluster_key": key1}, {"cluster_key": key2}]},
    )
    assert resp.status_code == 200
    assert all(r["status"] == "rejected" for r in resp.json()["results"])


def test_batch_with_empty_items_is_rejected_not_treated_as_accept_all(client):
    """Brief §6: batch actions must require an explicit selection — there is
    no "accept all" pathway. An empty items list is a validation error, not
    "select everything"."""
    resp = client.post("/api/vocabulary/clusters/batch", json={"action": "accept", "items": []})
    assert resp.status_code == 422


def test_batch_accept_is_all_or_nothing_on_failure(client):
    """Transaction-boundary test (brief §14): if one item in a batch fails
    (here: a canonical-name collision on the second item), NONE of the
    batch's clusters may end up accepted — not even the ones processed
    before the failure."""
    with db.db_cursor() as cur:
        _active_concept(cur, "Existing Collision Name", type_code="tool")
        _role(cur, "Role A", [_skill("Atomic Batch Term")])
        _role(cur, "Role B", [_skill("Colliding Batch Term")])
        vb.compute_cluster_keys(cur)
        key_ok = _cluster_key_of(cur, "atomic batch term")
        key_collides = _cluster_key_of(cur, "colliding batch term")

    resp = client.post(
        "/api/vocabulary/clusters/batch",
        json={
            "action": "accept",
            "items": [
                {"cluster_key": key_ok, "canonical_name": "Atomic Batch Term", "type_code": "tool"},
                # Same (type_code, canonical_name) as the pre-existing active
                # concept above -> UniqueViolation on the second item.
                {"cluster_key": key_collides, "canonical_name": "Existing Collision Name", "type_code": "tool"},
            ],
        },
    )
    assert resp.status_code == 400

    with db.db_cursor() as cur:
        # The first item must NOT have been left accepted despite succeeding
        # before the second item's failure — whole-batch atomicity.
        cur.execute("SELECT status FROM jobber.concept_proposal WHERE surface_form = 'atomic batch term'")
        assert cur.fetchone()["status"] == "pending"
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE canonical_name = 'Atomic Batch Term'")
        assert cur.fetchone()["n"] == 0


def test_no_automatic_acceptance_endpoint_exists(client):
    """There is no bulk 'accept high priority' / 'accept all pending'
    endpoint anywhere in this router — listing/scoring the queue must never
    itself change any proposal's status."""
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Never Auto Accepted")])
        vb.compute_cluster_keys(cur)

    for _ in range(3):
        client.get("/api/vocabulary/clusters", params={"sort": "priority", "band": "high"})
        client.get("/api/vocabulary/progress")

    with db.db_cursor() as cur:
        cur.execute("SELECT status FROM jobber.concept_proposal WHERE surface_form = 'never auto accepted'")
        assert cur.fetchone()["status"] == "pending"

    # And the router genuinely has no route shaped like an all-in-one accept.
    paths = set(client.get("/openapi.json").json()["paths"].keys())
    assert "/api/vocabulary/clusters/accept-all" not in paths
    assert "/api/vocabulary/clusters/accept-high-priority" not in paths
    assert not any("accept-all" in p or "accept_all" in p for p in paths)


# --- progress counts (brief §11) --------------------------------------------

def test_progress_counts_reflect_curation_state(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Progress Accept Me")])
        _role(cur, "Role B", [_skill("Progress Reject Me")])
        _role(cur, "Role C", [_skill("Progress Leave Pending")])
        vb.compute_cluster_keys(cur)
        key_accept = _cluster_key_of(cur, "progress accept me")
        key_reject = _cluster_key_of(cur, "progress reject me")

    before = client.get("/api/vocabulary/progress").json()
    assert before["canonical_vocabulary_curated"] is False
    assert before["accepted_concepts"] == 0
    assert before["pending_clusters"] >= 3

    client.post("/api/vocabulary/clusters/accept", json={"cluster_key": key_accept, "type_code": "tool", "canonical_name": "Progress Accept Me"})
    client.post("/api/vocabulary/clusters/reject", json={"cluster_key": key_reject})

    after = client.get("/api/vocabulary/progress").json()
    assert after["accepted_clusters"] == before["accepted_clusters"] + 1
    assert after["rejected_clusters"] == before["rejected_clusters"] + 1
    assert after["pending_clusters"] == before["pending_clusters"] - 2
    assert after["accepted_concepts"] == 1
    assert after["observations_mapped"] >= 1
    assert after["canonical_vocabulary_curated"] is True


# --- empty canonical-vocabulary messaging (brief §10) -----------------------

def test_empty_vocabulary_flag_before_and_after_first_acceptance(client):
    resp = client.get("/api/vocabulary/progress")
    assert resp.json()["canonical_vocabulary_curated"] is False

    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("First Ever Acceptance")])
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "first ever acceptance")

    client.post("/api/vocabulary/clusters/accept", json={"cluster_key": key, "type_code": "tool", "canonical_name": "First Ever Acceptance"})

    resp2 = client.get("/api/vocabulary/progress")
    assert resp2.json()["canonical_vocabulary_curated"] is True
    assert resp2.json()["accepted_concepts"] == 1


# --- sparse/noise flags surfaced end to end (brief §8) ----------------------

def test_noise_and_sparse_flags_surface_on_queue_rows(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("and reporting on things")])  # single role + fragment-like
        vb.compute_cluster_keys(cur)

    resp = client.get("/api/vocabulary/clusters", params={"q": "reporting on things"})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["priority_band"] == BAND_SPARSE
    assert "single_role" in items[0]["flags"]
    assert "single_observation" in items[0]["flags"]
    assert "possible_fragment" in items[0]["flags"]


# --- methodology endpoint (brief §2: "must be documented") ------------------

def test_methodology_endpoint_exposes_weights_and_thresholds(client):
    resp = client.get("/api/vocabulary/methodology")
    assert resp.status_code == 200
    body = resp.json()
    assert "text" in body and len(body["text"]) > 100
    assert body["bands"] == ["high", "medium", "low", "sparse"]
    assert body["weights"]["role_count"] == 3.0
    assert body["band_thresholds"]["high_min_role_count"] == 6


# --- legacy endpoints remain untouched (regression guard) -------------------

def test_legacy_resolve_endpoints_still_work_after_refactor(client):
    """routes/concepts.py's original endpoints now delegate to
    vocabulary_curation.resolve_surface_form_group — this asserts the move
    didn't change their externally-observable behaviour."""
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Legacy Path Term")])
        from app.concept_linking import run_pass_b

        run_pass_b(cur)

    resp = client.post(
        "/api/concepts/proposals/resolve",
        json={"surface_form": "Legacy Path Term", "action": "accept_new", "type_code": "tool", "canonical_name": "Legacy Path Term"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted_new"


# --- code-review follow-up: resolving a proposal must not lose the -------
# --- requirement it represented for every role that produced it (docs/29 §13)

def test_accepting_a_cluster_creates_an_unreviewed_claim_for_the_source_aware_role(client):
    """The full lifecycle the fix exists for: unresolved term -> vocabulary
    resolution -> role still incomplete (now for a *different* reason,
    unreviewed rather than unresolved) -> requirement eventually
    participates in analysis once a human accepts it."""
    from app.role_requirements import load_requirement_review_summary, load_role_requirements

    with db.db_cursor() as cur:
        role_id, document_id = _source_aware_role(cur, body="Requires Foo Modelling expertise.")
        _occurrence(cur, role_id, document_id, surface_form="Foo Modelling", requirement_type="required",
                    basis="stated", evidence_span="Foo Modelling")

        before = load_requirement_review_summary(cur, role_id)
        assert before["unresolved_proposals"] == 1
        assert before["needs_reextraction"] == 0
        assert before["complete"] is False

    resp = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": "foo modelling", "type_code": "domain", "canonical_name": "Foo Modelling"},
    )
    assert resp.status_code == 200
    concept_id = resp.json()["resolved_concept_id"]

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT id, review_status, basis, evidence_span, document_id, requirement_type "
            "FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s AND superseded_by IS NULL",
            (role_id, concept_id),
        )
        claim = cur.fetchone()
        assert claim is not None
        assert claim["review_status"] == "unreviewed"
        assert claim["requirement_type"] == "required"
        assert claim["basis"] == "stated"
        assert claim["evidence_span"] == "Foo Modelling"
        assert str(claim["document_id"]) == document_id
        claim_id = str(claim["id"])

        # Resolved: no longer counted as an unresolved proposal — but the
        # role must NOT look "complete" as a result. It is incomplete for a
        # different, legitimate reason now: a fresh unreviewed claim.
        mid = load_requirement_review_summary(cur, role_id)
        assert mid["unresolved_proposals"] == 0
        assert mid["unreviewed"] == 1
        assert mid["needs_reextraction"] == 0
        assert mid["complete"] is False
        assert load_role_requirements(cur, role_id) == []  # unreviewed, not yet authoritative

    accept_resp = client.post(f"/api/role-instances/{role_id}/requirements/{claim_id}/accept")
    assert accept_resp.status_code == 200

    with db.db_cursor() as cur:
        after = load_requirement_review_summary(cur, role_id)
        assert after["complete"] is True
        items = load_role_requirements(cur, role_id)
    assert len(items) == 1
    assert items[0]["concept_id"] == concept_id


def test_accepting_a_cluster_marks_role_needing_reextraction_when_occurrence_lacks_requirement_type(client):
    """An occurrence with no requirement_type (a pre-migration-0022
    occurrence, or migration 0021's own backfill — neither could ever carry
    one) has nothing faithful to build a claim from. No claim is fabricated;
    the role stays incomplete (needs_reextraction) rather than silently
    losing the requirement or inventing its shape. Re-running extraction
    (simulated here by a direct claim insert — the point under test is that
    the signal is *live*, not by which path a claim eventually appears)
    clears it immediately."""
    from app.role_requirements import load_requirement_review_summary

    with db.db_cursor() as cur:
        role_id, document_id = _source_aware_role(cur, body="Requires Bar Charting.")
        cur.execute(
            "INSERT INTO jobber.concept_proposal (surface_form, occurrence_count, document_id, status) "
            "VALUES ('bar charting', 1, %s, 'pending') RETURNING id",
            (document_id,),
        )
        proposal_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id) "
            "VALUES (%s, %s, %s)",
            (proposal_id, role_id, document_id),
        )

    resp = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": "bar charting", "type_code": "tool", "canonical_name": "Bar Charting"},
    )
    assert resp.status_code == 200
    concept_id = resp.json()["resolved_concept_id"]

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s",
            (role_id, concept_id),
        )
        assert cur.fetchone()["n"] == 0  # nothing fabricated

        summary = load_requirement_review_summary(cur, role_id)
        assert summary["unresolved_proposals"] == 0
        assert summary["needs_reextraction"] == 1
        assert summary["complete"] is False

        # Self-healing: the moment ANY current claim exists for this
        # (role, concept), the live signal clears — no bookkeeping to update.
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
            "VALUES (%s, %s, 'required', 'user_asserted', 'unreviewed')",
            (role_id, concept_id),
        )
        summary_after = load_requirement_review_summary(cur, role_id)
        assert summary_after["needs_reextraction"] == 0


def test_accepting_a_cluster_marks_role_needing_reextraction_when_span_is_missing_for_stated_basis(client):
    """requirement_claim's own CHECK constraint (migration 0003) requires a
    non-null evidence_span whenever basis is 'stated'/'implied' — an
    occurrence claiming 'stated' with no span (should never happen from a
    real extraction run, but this is still just data) must not crash the
    resolution with a raw CheckViolation. Same outcome as missing
    requirement_type: left for live re-extraction detection."""
    from app.role_requirements import load_requirement_review_summary

    with db.db_cursor() as cur:
        role_id, document_id = _source_aware_role(cur, body="Requires Baz Modelling.")
        _occurrence(cur, role_id, document_id, surface_form="Baz Modelling", requirement_type="required",
                    basis="stated", evidence_span=None)

    resp = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": "baz modelling", "type_code": "domain", "canonical_name": "Baz Modelling"},
    )
    assert resp.status_code == 200
    concept_id = resp.json()["resolved_concept_id"]

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s",
            (role_id, concept_id),
        )
        assert cur.fetchone()["n"] == 0
        summary = load_requirement_review_summary(cur, role_id)
        assert summary["needs_reextraction"] == 1
        assert summary["complete"] is False


def test_accepting_a_cluster_does_not_duplicate_a_claim_that_already_exists(client):
    """If some other route (manual add, an earlier extraction run) already
    gave this role a current claim for the concept a proposal resolves to,
    resolution must not create a second one — migration 0020's uniqueness
    invariant (at most one current claim per role+concept) must hold."""
    from app.role_requirements import load_requirement_review_summary

    with db.db_cursor() as cur:
        role_id, document_id = _source_aware_role(cur, body="Requires Quux Scripting.")
        concept_id = _active_concept(cur, "Quux Scripting", type_code="tool")
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
            "VALUES (%s, %s, 'preferred', 'user_asserted', 'accepted')",
            (role_id, concept_id),
        )
        _occurrence(cur, role_id, document_id, surface_form="Quux Scripting", requirement_type="required", basis="stated")

    resp = client.post(
        "/api/vocabulary/clusters/merge", json={"cluster_key": "quux scripting", "concept_id": concept_id}
    )
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s",
            (role_id, concept_id),
        )
        assert cur.fetchone()["n"] == 1  # never duplicated — the pre-existing accepted claim stands
        summary = load_requirement_review_summary(cur, role_id)
        assert summary["complete"] is True


def test_accepting_a_cluster_gives_one_claim_per_role_across_a_clustered_multi_surface_form_resolution(client):
    """A cluster with more than one surface form (e.g. 'SII' and 'Solvency
    II' merged by vocabulary_bootstrap's clustering) resolves as one unit —
    a role that produced occurrences for *both* surface forms must still end
    up with exactly one claim, not two (which migration 0020's unique index
    would reject outright)."""
    with db.db_cursor() as cur:
        role_id, document_id = _source_aware_role(cur, body="Requires SII and Solvency II knowledge.")
        _occurrence(cur, role_id, document_id, surface_form="SII", requirement_type="required", basis="stated", evidence_span="SII")
        _occurrence(cur, role_id, document_id, surface_form="Solvency II", requirement_type="preferred", basis="implied", evidence_span="Solvency II")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "sii")
        assert key == _cluster_key_of(cur, "solvency ii")  # confirms they really did cluster together

    resp = client.post(
        "/api/vocabulary/clusters/accept", json={"cluster_key": key, "type_code": "regulation", "canonical_name": "Solvency II"}
    )
    assert resp.status_code == 200
    concept_id = resp.json()["resolved_concept_id"]

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s AND superseded_by IS NULL",
            (role_id, concept_id),
        )
        assert cur.fetchone()["n"] == 1


# --- code-review follow-up: cluster evidence must include source-aware ----
# --- role occurrences, not only legacy role_skill_observation (docs/29 §14)

def test_cluster_evidence_includes_a_source_aware_role_that_never_had_an_observation(client):
    """A role captured via source-aware ingest + extract-requirements never
    gets a role_skill_observation row at all (docs/29 §1) — before this fix,
    a cluster made up entirely of such roles' occurrences showed zero
    role_count/observation_count/countries/seniority despite having real
    evidence."""
    with db.db_cursor() as cur:
        role_id, document_id = _source_aware_role(
            cur, title="Data Engineer", body="Requires Widget Orchestration.",
            country="Germany", seniority_level="senior", posting_date="2024-05-01",
        )
        _occurrence(cur, role_id, document_id, surface_form="Widget Orchestration", requirement_type="required", basis="stated")

    resp = client.get("/api/vocabulary/clusters", params={"status": "pending", "q": "widget"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    matches = [c for c in items if "widget orchestration" in c["surface_forms"]]
    assert len(matches) == 1
    c = matches[0]
    assert c["role_count"] == 1
    assert c["observation_count"] == 1
    assert c["countries"] == ["Germany"]
    assert c["seniority_levels"] == ["senior"]
    assert c["distinct_years"] == [2024]
    assert len(c["example_roles"]) == 1
    assert c["example_roles"][0]["id"] == role_id


def test_cluster_evidence_combines_legacy_observations_and_source_aware_occurrences(client):
    """A cluster with contributions from *both* pipelines counts every
    role, not only the legacy-observation ones."""
    with db.db_cursor() as cur:
        legacy_role = _role(cur, "Legacy Role", [_skill("Gadget Tuning")], country="UK")
        source_aware_role, document_id = _source_aware_role(
            cur, title="Source-aware Role", body="Requires Gadget Tuning.", country="France",
        )
        _occurrence(cur, source_aware_role, document_id, surface_form="Gadget Tuning", requirement_type="required", basis="stated")

    resp = client.get("/api/vocabulary/clusters", params={"status": "pending", "q": "gadget"})
    matches = [c for c in resp.json()["items"] if "gadget tuning" in c["surface_forms"]]
    assert len(matches) == 1
    c = matches[0]
    assert c["role_count"] == 2
    assert c["observation_count"] == 2
    assert set(c["countries"]) == {"UK", "France"}
    assert {r["id"] for r in c["example_roles"]} == {legacy_role, source_aware_role}
