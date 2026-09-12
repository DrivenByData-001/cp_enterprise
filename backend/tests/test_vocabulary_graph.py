"""Vocabulary Map graph projection (docs/25-vocabulary-map.md) — DB-backed
tests, same precedent as test_vocabulary_curation.py: real Postgres, only
embeddings stubbed (conftest.py's autouse `_stub_embeddings`). Covers the
brief's own required coverage list: cluster membership, split-lock survival,
accepted concepts/aliases, status/priority/type/search/evidence filters,
result limits + truncation metadata, no writes, rejected vocabulary staying
hidden, advisory/bounded similarity, real-accepted-only ontology edges, and
surface-form detail evidence."""

import psycopg
import pytest

from app import db, vocabulary_bootstrap as vb, vocabulary_curation as curation


def _role(cur, title, skills, **cols):
    columns = {"instance_type": "observed_posting", "title": title, **cols}
    return db.upsert_role_instance(cur, None, columns, skills=skills)


def _skill(name):
    return {"name": name, "requirement_type": "required", "importance": 3}


def _active_concept(cur, name, type_code="tool", definition=None):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, definition, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name, definition),
    )
    return str(cur.fetchone()["id"])


def _cluster_key_of(cur, surface_form: str) -> str:
    cur.execute(
        "SELECT COALESCE(cluster_key, surface_form) AS ck FROM jobber.concept_proposal WHERE surface_form = %s",
        (surface_form,),
    )
    return cur.fetchone()["ck"]


def _node_by_id(body, node_id):
    return next((n for n in body["nodes"] if n["id"] == node_id), None)


def _edges_with(body, **kwargs):
    return [e for e in body["edges"] if all(e.get(k) == v for k, v in kwargs.items())]


def _snapshot_counts(cur):
    cur.execute("SELECT status, COUNT(*) AS n FROM jobber.concept_proposal GROUP BY status ORDER BY status")
    proposal_statuses = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
    concepts = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM jobber.concept_alias")
    aliases = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM jobber.concept_edge")
    edges = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM jobber.role_skill_observation WHERE canonical_concept_id IS NOT NULL")
    mapped_observations = cur.fetchone()["n"]
    cur.execute("SELECT cluster_key, cluster_key_locked FROM jobber.concept_proposal ORDER BY surface_form")
    cluster_keys = [dict(r) for r in cur.fetchall()]
    return {
        "proposal_statuses": proposal_statuses,
        "concepts": concepts,
        "aliases": aliases,
        "edges": edges,
        "mapped_observations": mapped_observations,
        "cluster_keys": cluster_keys,
    }


# --- pending cluster membership (brief §30.1) -------------------------------

def test_pending_graph_represents_cluster_membership_and_root_wiring(client):
    with db.db_cursor() as cur:
        _role(cur, "Actuarial Analyst", [_skill("Solvency II")], country="UK", posting_date="2020-01-01")
        _role(cur, "Actuarial Manager", [_skill("SII")], country="Ireland", posting_date="2023-06-15")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "solvency ii")

    resp = client.get("/api/vocabulary/graph", params={"status": "pending", "q": "solvency"})
    assert resp.status_code == 200
    body = resp.json()

    cluster_id = f"cluster:{key}"
    cluster_node = _node_by_id(body, cluster_id)
    assert cluster_node is not None
    assert cluster_node["kind"] == "pending_cluster"
    assert cluster_node["role_count"] == 2

    sii_node = _node_by_id(body, f"surface:{key}:sii")
    solvency_node = _node_by_id(body, f"surface:{key}:solvency ii")
    assert sii_node is not None and sii_node["kind"] == "surface_form" and sii_node["status"] == "pending"
    assert solvency_node is not None

    assert _edges_with(body, source=f"surface:{key}:sii", target=cluster_id, relation="member_of")
    assert _edges_with(body, source=f"surface:{key}:solvency ii", target=cluster_id, relation="member_of")

    # Root -> priority-band group -> cluster (default grouping, brief §6.1).
    band = cluster_node["priority_band"]
    group_id = f"group:priority:{band}"
    assert _node_by_id(body, group_id) is not None
    assert _edges_with(body, source=group_id, target=cluster_id, relation="contains")
    assert _node_by_id(body, "root") is not None
    assert _edges_with(body, source="root", target=group_id, relation="contains")


def test_group_by_none_wires_clusters_directly_to_root(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Chain ladder reserving technique")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "chain ladder reserving technique")

    resp = client.get("/api/vocabulary/graph", params={"status": "pending", "group_by": "none"})
    body = resp.json()
    cluster_id = f"cluster:{key}"
    assert _edges_with(body, source="root", target=cluster_id, relation="contains")
    assert not [n for n in body["nodes"] if n["kind"] == "group" and n["id"] != "root"]


# --- split-locked clusters (brief §30.2) ------------------------------------

def test_split_locked_clusters_use_their_persisted_curated_cluster_keys(client):
    with db.db_cursor() as cur:
        _role(cur, "Programme Lead", [_skill("stakeholder engagement")], country="UK", posting_date="2021-01-01")
        _role(cur, "Change Manager", [_skill("stakeholder management")], country="Ireland", posting_date="2023-06-01")
        vb.compute_cluster_keys(cur)
        merged_key = _cluster_key_of(cur, "stakeholder engagement")
        assert merged_key == _cluster_key_of(cur, "stakeholder management")

        curation.split_cluster(
            cur, cluster_key=merged_key,
            groups=[["stakeholder engagement"], ["stakeholder management"]],
        )
        # A real bootstrap re-run must not re-collapse the split (docs/21 §2.2).
        vb.compute_cluster_keys(cur)
        engagement_key = _cluster_key_of(cur, "stakeholder engagement")
        management_key = _cluster_key_of(cur, "stakeholder management")
        assert engagement_key != management_key

    resp = client.get("/api/vocabulary/graph", params={"status": "pending", "limit": 500})
    body = resp.json()
    assert _node_by_id(body, f"cluster:{engagement_key}") is not None
    assert _node_by_id(body, f"cluster:{management_key}") is not None
    assert _node_by_id(body, f"cluster:{merged_key}") is None
    assert _edges_with(body, source=f"surface:{engagement_key}:stakeholder engagement", target=f"cluster:{engagement_key}", relation="member_of")
    assert _edges_with(body, source=f"surface:{management_key}:stakeholder management", target=f"cluster:{management_key}", relation="member_of")


# --- accepted concepts + aliases (brief §30.3/§30.4) ------------------------

def test_accepted_concept_shows_extraction_proposal_aliases_as_maps_to(client):
    with db.db_cursor() as cur:
        _role(cur, "Analyst A", [_skill("Solvency II")], posting_date="2021-01-01")
        _role(cur, "Analyst B", [_skill("SII")], posting_date="2022-01-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "solvency ii")
        result = curation.accept_cluster(cur, cluster_key=key, type_code="regulation", canonical_name="Solvency II")
        concept_id = result["resolved_concept_id"]

    resp = client.get("/api/vocabulary/graph", params={"status": "accepted"})
    assert resp.status_code == 200
    body = resp.json()

    concept_node = _node_by_id(body, f"concept:{concept_id}")
    assert concept_node is not None
    assert concept_node["kind"] == "concept"
    assert concept_node["type_code"] == "regulation"

    alias_node = _node_by_id(body, f"surface:{concept_id}:sii")
    assert alias_node is not None
    assert alias_node["status"] == "accepted"
    assert _edges_with(body, source=f"surface:{concept_id}:sii", target=f"concept:{concept_id}", relation="maps_to")

    # Grouped by concept type by default (brief §6.2).
    assert _edges_with(body, source="group:type:regulation", target=f"concept:{concept_id}", relation="contains")


def test_curator_added_alias_is_alias_of_never_maps_to(client):
    with db.db_cursor() as cur:
        from app import concept_curation

        concept_id = _active_concept(cur, "Prophet", type_code="tool")
        concept_curation.add_alias(cur, concept_id, "Prophet Actuarial Software")

    resp = client.get("/api/vocabulary/graph", params={"status": "accepted", "q": "prophet"})
    body = resp.json()
    alias_id = f"surface:{concept_id}:Prophet Actuarial Software"
    assert _node_by_id(body, alias_id) is not None
    assert _edges_with(body, source=alias_id, target=f"concept:{concept_id}", relation="alias_of")
    assert not _edges_with(body, source=alias_id, relation="maps_to")


# --- status / rejected vocabulary (brief §30.5, §30.14) ---------------------

def test_status_filter_separates_pending_accepted_and_hides_rejected(client):
    with db.db_cursor() as cur:
        _role(cur, "Role P", [_skill("Pending Term")], posting_date="2021-01-01")
        _role(cur, "Role A", [_skill("Accepted Term")], posting_date="2021-01-01")
        _role(cur, "Role R", [_skill("Rejected Term")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)
        pending_key = _cluster_key_of(cur, "pending term")
        accepted_key = _cluster_key_of(cur, "accepted term")
        rejected_key = _cluster_key_of(cur, "rejected term")
        accept_result = curation.accept_cluster(cur, cluster_key=accepted_key, type_code="tool", canonical_name="Accepted Term")
        curation.reject_cluster(cur, cluster_key=rejected_key)
        concept_id = accept_result["resolved_concept_id"]

    pending_body = client.get("/api/vocabulary/graph", params={"status": "pending", "limit": 500}).json()
    assert _node_by_id(pending_body, f"cluster:{pending_key}") is not None
    assert _node_by_id(pending_body, f"cluster:{accepted_key}") is None
    assert _node_by_id(pending_body, f"cluster:{rejected_key}") is None

    accepted_body = client.get("/api/vocabulary/graph", params={"status": "accepted", "limit": 500}).json()
    assert _node_by_id(accepted_body, f"concept:{concept_id}") is not None
    assert not any(n["kind"] == "pending_cluster" for n in accepted_body["nodes"])
    assert not any("rejected" in (n.get("label") or "").lower() for n in accepted_body["nodes"] if n["kind"] == "concept")

    combined_body = client.get("/api/vocabulary/graph", params={"status": "combined", "limit": 500}).json()
    assert _node_by_id(combined_body, f"cluster:{pending_key}") is not None
    assert _node_by_id(combined_body, f"concept:{concept_id}") is not None
    assert _node_by_id(combined_body, f"cluster:{rejected_key}") is None


# --- priority band filter (brief §30.6) -------------------------------------

def test_priority_band_filter(client):
    with db.db_cursor() as cur:
        _role(cur, "Solo role", [_skill("One Off Term")])
        for i in range(6):
            _role(
                cur, f"High band role {i}", [_skill("Broad Term")],
                posting_date=f"{2019 + i}-01-01",
                country=["UK", "Ireland", "UK", "US", "UK", "Canada"][i],
                seniority_level=["junior", "mid", "senior", "mid", "senior", "junior"][i],
            )
        vb.compute_cluster_keys(cur)
        high_key = _cluster_key_of(cur, "broad term")
        sparse_key = _cluster_key_of(cur, "one off term")

    resp = client.get("/api/vocabulary/graph", params={"status": "pending", "band": "high", "limit": 500})
    body = resp.json()
    assert _node_by_id(body, f"cluster:{high_key}") is not None
    assert _node_by_id(body, f"cluster:{sparse_key}") is None
    assert all(n.get("priority_band") in (None, "high") for n in body["nodes"] if n["kind"] == "pending_cluster")


# --- concept type filter (brief §30.7) --------------------------------------

def test_concept_type_filter_on_accepted_graph(client):
    with db.db_cursor() as cur:
        knowledge_id = _active_concept(cur, "Reserving Theory", type_code="knowledge")
        tool_id = _active_concept(cur, "Excel", type_code="tool")

    resp = client.get("/api/vocabulary/graph", params={"status": "accepted", "type_code": "knowledge"})
    body = resp.json()
    assert _node_by_id(body, f"concept:{knowledge_id}") is not None
    assert _node_by_id(body, f"concept:{tool_id}") is None


# --- search filter (brief §30.8) --------------------------------------------

def test_search_filter_matches_pending_and_accepted(client):
    with db.db_cursor() as cur:
        _role(cur, "Role X", [_skill("Zzyzx Unique Term")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "zzyzx unique term")
        concept_id = _active_concept(cur, "Zzyzx Accepted Concept")

    pending_body = client.get("/api/vocabulary/graph", params={"status": "pending", "q": "zzyzx"}).json()
    assert _node_by_id(pending_body, f"cluster:{key}") is not None

    accepted_body = client.get("/api/vocabulary/graph", params={"status": "accepted", "q": "zzyzx"}).json()
    assert _node_by_id(accepted_body, f"concept:{concept_id}") is not None

    empty_body = client.get("/api/vocabulary/graph", params={"status": "pending", "q": "no-such-term-anywhere"}).json()
    assert not [n for n in empty_body["nodes"] if n["kind"] == "pending_cluster"]


# --- evidence minimums (brief §30.9/§30.10) ---------------------------------

def test_min_role_count_and_min_observation_count_filters(client):
    with db.db_cursor() as cur:
        _role(cur, "Single role", [_skill("Narrow Term")], posting_date="2021-01-01")
        for i in range(3):
            _role(cur, f"Broad role {i}", [_skill("Wide Term")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)
        narrow_key = _cluster_key_of(cur, "narrow term")
        wide_key = _cluster_key_of(cur, "wide term")

    resp = client.get("/api/vocabulary/graph", params={"status": "pending", "min_role_count": 2, "limit": 500})
    body = resp.json()
    assert _node_by_id(body, f"cluster:{wide_key}") is not None
    assert _node_by_id(body, f"cluster:{narrow_key}") is None

    resp2 = client.get("/api/vocabulary/graph", params={"status": "pending", "min_observation_count": 2, "limit": 500})
    body2 = resp2.json()
    assert _node_by_id(body2, f"cluster:{wide_key}") is not None
    assert _node_by_id(body2, f"cluster:{narrow_key}") is None


# --- result limit + truncation metadata (brief §30.11/§30.12) --------------

def test_result_limit_and_truncated_metadata(client):
    with db.db_cursor() as cur:
        for i in range(10):
            _role(cur, f"Role {i}", [_skill(f"Unique Term {i}")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)

    small = client.get("/api/vocabulary/graph", params={"status": "pending", "limit": 3}).json()
    assert small["meta"]["truncated"] is True
    # `limit` bounds real vocabulary nodes (clusters/concepts/surface forms);
    # the handful of presentation-only group/root scaffold nodes (brief
    # §10.4: "not persisted vocabulary concepts") sit outside that budget.
    real_nodes = [n for n in small["nodes"] if n["kind"] in ("pending_cluster", "concept", "surface_form")]
    assert len(real_nodes) <= 3
    assert small["meta"]["total_nodes"] >= 10

    large = client.get("/api/vocabulary/graph", params={"status": "pending", "limit": 500}).json()
    assert large["meta"]["truncated"] is False
    assert sum(1 for n in large["nodes"] if n["kind"] == "pending_cluster") == 10


# --- no writes (brief §30.13) -----------------------------------------------

def test_graph_and_surface_form_endpoints_perform_no_writes(client):
    with db.db_cursor() as cur:
        _role(cur, "Analyst A", [_skill("Solvency II")], posting_date="2021-01-01")
        _role(cur, "Analyst B", [_skill("SII")], posting_date="2022-01-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "solvency ii")
        result = curation.accept_cluster(cur, cluster_key=key, type_code="regulation", canonical_name="Solvency II")
        concept_id = result["resolved_concept_id"]
        before = _snapshot_counts(cur)

    client.get("/api/vocabulary/graph", params={"status": "pending"})
    client.get("/api/vocabulary/graph", params={"status": "accepted"})
    client.get("/api/vocabulary/graph", params={"status": "combined"})
    client.get("/api/vocabulary/graph", params={"focus": f"concept:{concept_id}"})
    client.get("/api/vocabulary/surface-form", params={"value": "sii"})

    with db.db_cursor() as cur:
        after = _snapshot_counts(cur)
    assert before == after


# --- similarity: advisory + bounded (brief §30.16/§30.17) -------------------

def test_similarity_edges_are_advisory_and_similarity_focus_is_bounded(client):
    from app.concept_linking import ensure_concept_embeddings

    with db.db_cursor() as cur:
        _role(cur, "Analyst A", [_skill("Solvency II")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "solvency ii")
        nearest_id = _active_concept(cur, "Solvency II Nearest Concept", type_code="regulation")
        cur.execute(
            "UPDATE jobber.concept_proposal SET nearest_concept_id = %s, nearest_similarity = 0.81 WHERE surface_form = %s",
            (nearest_id, "solvency ii"),
        )
        for i in range(10):
            _active_concept(cur, f"Neighbour Concept {i}", type_code="tool")
        # Simulate an already-completed backfill (the existing rebuild_
        # embeddings-style write path) — the graph/focus routes themselves
        # must never compute this on a GET (see the dedicated no-backfill
        # test above), so the fixture does it explicitly instead.
        ensure_concept_embeddings(cur)

    body = client.get("/api/vocabulary/graph", params={"status": "pending", "q": "solvency"}).json()
    sim_edges = _edges_with(body, source=f"cluster:{key}", target=f"concept:{nearest_id}", relation="similar_to")
    assert len(sim_edges) == 1
    assert sim_edges[0]["similarity"] == pytest.approx(0.81)

    focus = client.get(
        "/api/vocabulary/graph", params={"focus": f"concept:{nearest_id}", "similarity_limit": 4}
    ).json()
    assert focus["meta"]["mode"] == "similarity_focus"
    neighbour_nodes = [n for n in focus["nodes"] if n["id"] != f"concept:{nearest_id}"]
    assert 0 < len(neighbour_nodes) <= 4
    assert all(n["id"] != f"concept:{nearest_id}" for n in neighbour_nodes)  # self excluded
    for e in focus["edges"]:
        assert e["relation"] == "similar_to"
        assert isinstance(e["similarity"], float)


# --- similarity focus never computes/persists a new embedding -------------
# (brief §25: "no AI calls during graph GET requests... never regenerate
# embeddings during ordinary graph rendering"). A read-only focus request
# must not have the side effect of backfilling jobber.d_embedding for a
# concept that doesn't have one yet — that stays the job of the existing,
# explicit rebuild_embeddings script/write path.

def test_similarity_focus_never_backfills_a_missing_concept_embedding(client):
    with db.db_cursor() as cur:
        # An active concept created after the fact, deliberately never
        # touched by anything that would compute its embedding.
        concept_id = _active_concept(cur, "Freshly Curated Concept With No Embedding Yet", type_code="knowledge")
        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_embedding WHERE owner_kind = 'concept' AND owner_id = %s", (concept_id,))
        assert cur.fetchone()["n"] == 0  # fixture assumption

    resp = client.get("/api/vocabulary/graph", params={"focus": f"concept:{concept_id}"})
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.d_embedding WHERE owner_kind = 'concept' AND owner_id = %s", (concept_id,))
        assert cur.fetchone()["n"] == 0  # still no embedding — never backfilled by a GET


def test_similarity_focus_degrades_gracefully_if_the_embedding_model_is_unavailable(client, monkeypatch):
    """Similarity is advisory-only decoration on a read endpoint (brief
    §26) — an embedding-subsystem failure (e.g. a model that needs a
    network fetch on first use, in an environment with no such access) must
    never surface as a 500; the focus response should still come back with
    just the center node and no neighbours."""
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Concept With Unavailable Embeddings", type_code="knowledge")

    def _broken_embed_text(_text):
        raise RuntimeError("simulated: embedding model unavailable in this environment")

    monkeypatch.setattr("app.concept_linking.embed_text", _broken_embed_text)

    resp = client.get("/api/vocabulary/graph", params={"focus": f"concept:{concept_id}"})
    assert resp.status_code == 200
    body = resp.json()
    assert [n["id"] for n in body["nodes"]] == [f"concept:{concept_id}"]
    assert body["edges"] == []


# --- ontology edges: real accepted relationships only (brief §30.18) ------

def test_ontology_edges_are_real_accepted_concept_edges_only(client):
    with db.db_cursor() as cur:
        knowledge_id = _active_concept(cur, "Reserving Theory", type_code="knowledge")
        capability_id = _active_concept(cur, "Reserve Setting", type_code="capability")
        other_capability_id = _active_concept(cur, "Pricing", type_code="capability")
        cur.execute(
            "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, origin, status) "
            "VALUES (%s, %s, 'component_of', 'curator', 'accepted')",
            (knowledge_id, capability_id),
        )
        cur.execute(
            "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, origin, status) "
            "VALUES (%s, %s, 'component_of', 'bootstrap', 'proposed')",
            (knowledge_id, other_capability_id),
        )

    body = client.get("/api/vocabulary/graph", params={"status": "accepted", "include_ontology": True, "limit": 500}).json()
    assert _edges_with(body, source=f"concept:{knowledge_id}", target=f"concept:{capability_id}", relation="ontology")
    assert not _edges_with(body, source=f"concept:{knowledge_id}", target=f"concept:{other_capability_id}", relation="ontology")

    off_body = client.get("/api/vocabulary/graph", params={"status": "accepted", "include_ontology": False, "limit": 500}).json()
    assert not [e for e in off_body["edges"] if e["relation"] == "ontology"]


# --- surface-form detail (brief §30.19) -------------------------------------

def test_surface_form_detail_returns_per_form_evidence_not_whole_cluster(client):
    with db.db_cursor() as cur:
        _role(cur, "Analyst A", [_skill("Solvency II")], country="UK", posting_date="2021-01-01")
        _role(cur, "Analyst B", [_skill("Solvency II")], country="Ireland", posting_date="2022-01-01")
        _role(cur, "Analyst C", [_skill("SII")], country="France", posting_date="2023-01-01")
        vb.compute_cluster_keys(cur)

    detail = client.get("/api/vocabulary/surface-form", params={"value": "sii"}).json()
    assert detail["surface_form"] == "sii"
    assert detail["status"] == "pending"
    assert detail["observation_count"] == 1
    assert detail["role_count"] == 1
    assert detail["countries"] == ["France"]

    other_detail = client.get("/api/vocabulary/surface-form", params={"value": "Solvency II"}).json()
    assert other_detail["observation_count"] == 2
    assert other_detail["role_count"] == 2
    assert set(other_detail["countries"]) == {"UK", "Ireland"}

    missing = client.get("/api/vocabulary/surface-form", params={"value": "not-a-real-term-anywhere"})
    assert missing.status_code == 404


# --- no N+1 query dependency (brief §30.20) ---------------------------------

def test_graph_query_count_does_not_scale_linearly_with_cluster_count(client):
    def _seed(n):
        with db.db_cursor() as cur:
            for i in range(n):
                _role(cur, f"Role {i}", [_skill(f"Term {i}")], posting_date="2021-01-01")
            vb.compute_cluster_keys(cur)

    def _count_queries(fn):
        calls = {"n": 0}
        original = psycopg.Cursor.execute

        def counting_execute(self, *args, **kwargs):
            calls["n"] += 1
            return original(self, *args, **kwargs)

        psycopg.Cursor.execute = counting_execute
        try:
            fn()
        finally:
            psycopg.Cursor.execute = original
        return calls["n"]

    _seed(5)
    small_count = _count_queries(lambda: client.get("/api/vocabulary/graph", params={"status": "pending", "limit": 500}))

    _seed(60)
    large_count = _count_queries(lambda: client.get("/api/vocabulary/graph", params={"status": "pending", "limit": 500}))

    # A per-node query pattern would scale ~linearly with cluster count
    # (5 -> 65 clusters, 13x); a handful of grouped/bulk queries should not.
    assert large_count < small_count * 3
