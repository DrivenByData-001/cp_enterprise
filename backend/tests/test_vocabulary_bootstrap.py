"""Vocabulary/capability bootstrap (docs/18 §3, app/vocabulary_bootstrap.py).
Runs against the real Postgres test database — nothing here is mocked
except embeddings (conftest.py's deterministic pseudo-embedding stub)."""

from app import db, vocabulary_bootstrap as vb


def _concept(cur, name, type_code="tool", status="active"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, %s, 'curator', now()) RETURNING id",
        (type_code, name, status),
    )
    return str(cur.fetchone()["id"])


def _role(cur, title, skills):
    return db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": title}, skills=skills)


def _skill(name, category=None, requirement_type="required"):
    return {"name": name, "category": category, "requirement_type": requirement_type, "importance": 3}


# --- clustering key -----------------------------------------------------

def test_cluster_key_covers_the_brief_examples():
    assert vb.cluster_key_for("Solvency II") == vb.cluster_key_for("SII")
    assert vb.cluster_key_for("stochastic modelling") == vb.cluster_key_for("stochastic models")
    assert vb.cluster_key_for("stakeholder management") == vb.cluster_key_for("stakeholder engagement")
    assert vb.cluster_key_for("R") == vb.cluster_key_for("R programming")
    assert vb.cluster_key_for("internal model") == vb.cluster_key_for("internal modelling")


def test_cluster_key_does_not_over_collapse_distinct_concepts():
    """Sibling-looking pairs that are NOT the same concept must stay distinct
    — clustering must never generalise 'management'/'engagement' as an
    interchangeable suffix beyond the one curated stakeholder pair."""
    assert vb.cluster_key_for("risk management") != vb.cluster_key_for("risk engagement")
    assert vb.cluster_key_for("Python") != vb.cluster_key_for("R")
    assert vb.cluster_key_for("IFRS 17") != vb.cluster_key_for("IFRS 9")


def test_cluster_key_is_stable_and_never_raises_on_edge_input():
    assert vb.cluster_key_for("") == ""
    assert vb.cluster_key_for("   ") == ""
    assert vb.cluster_key_for("Actuaries") == vb.cluster_key_for("Actuary")


# --- compute_cluster_keys / grouped proposal review ----------------------

def test_compute_cluster_keys_groups_lexical_duplicates(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Solvency II")])
        _role(cur, "Role B", [_skill("SII")])
        result = vb.compute_cluster_keys(cur)
        assert result["proposals_keyed"] == 2

        cur.execute("SELECT surface_form, cluster_key FROM jobber.concept_proposal WHERE status = 'pending'")
        rows = {r["surface_form"]: r["cluster_key"] for r in cur.fetchall()}
    assert rows["solvency ii"] == rows["sii"]


def test_proposal_queue_api_groups_cluster_into_one_card(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Solvency II")])
        _role(cur, "Role B", [_skill("SII")])
        vb.compute_cluster_keys(cur)

    resp = client.get("/api/concepts/proposals?status=pending")
    assert resp.status_code == 200
    groups = resp.json()
    matching = [g for g in groups if set(g["surface_forms"]) == {"solvency ii", "sii"}]
    assert len(matching) == 1
    assert matching[0]["occurrence_count"] == 2


def test_resolve_cluster_links_every_member_surface_form(client):
    with db.db_cursor() as cur:
        role_a = _role(cur, "Role A", [_skill("Solvency II")])
        role_b = _role(cur, "Role B", [_skill("SII")])
        vb.compute_cluster_keys(cur)

    resp = client.post(
        "/api/concepts/proposals/resolve-cluster",
        json={
            "cluster_key": "solvency ii",
            "action": "accept_new",
            "type_code": "regulation",
            "canonical_name": "Solvency II",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["surface_forms"]) == {"solvency ii", "sii"}
    concept_id = body["resolved_concept_id"]

    with db.db_cursor() as cur:
        cur.execute("SELECT canonical_concept_id FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_a,))
        assert str(cur.fetchone()["canonical_concept_id"]) == concept_id
        cur.execute("SELECT canonical_concept_id FROM jobber.role_skill_observation WHERE role_instance_id = %s", (role_b,))
        assert str(cur.fetchone()["canonical_concept_id"]) == concept_id
        # "SII" becomes an alias of the newly-created "Solvency II" concept.
        cur.execute("SELECT alias FROM jobber.concept_alias WHERE concept_id = %s", (concept_id,))
        aliases = {r["alias"] for r in cur.fetchall()}
    assert "sii" in aliases


def test_resolve_single_surface_form_endpoint_is_unchanged(client):
    """The original, single-item resolve_proposal contract must keep working
    exactly as before clustering was added — one surface form in, one out."""
    from app.concept_linking import run_pass_b

    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("Some Unique Skill Name")])
        run_pass_b(cur)

    resp = client.post(
        "/api/concepts/proposals/resolve",
        json={
            "surface_form": "Some Unique Skill Name",
            "action": "accept_new",
            "type_code": "tool",
            "canonical_name": "Some Unique Skill Name",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted_new"


# --- candidate capabilities ------------------------------------------------

def test_candidate_capability_necessity_reflects_cooccurrence_frequency(client):
    with db.db_cursor() as cur:
        chain_ladder = _concept(cur, "Chain ladder", type_code="method")
        reserving = _concept(cur, "Reserving", type_code="function")
        ifrs17 = _concept(cur, "IFRS 17", type_code="regulation")  # supporting: co-occurs in most core roles
        excel = _concept(cur, "Excel", type_code="tool")  # contextual: co-occurs occasionally
        unrelated = _concept(cur, "Unrelated tool", type_code="tool")

        # 5 roles form the core (chain ladder + reserving always together).
        for i in range(5):
            skills = [_skill("Chain ladder"), _skill("Reserving")]
            if i < 4:
                skills.append(_skill("IFRS 17"))  # 4/5 = 80% -> supporting
            if i < 1:
                skills.append(_skill("Excel"))  # 1/5 = 20% -> contextual
            _role(cur, f"Reserving role {i}", skills)
        _role(cur, "Unrelated role", [_skill("Unrelated tool")])

        candidates = vb.compute_candidate_capabilities(cur, min_concept_support=3, min_pair_support=5)

    matches = [c for c in candidates if {m["id"] for m in c.core} == {chain_ladder, reserving}]
    assert len(matches) == 1
    candidate = matches[0]
    assert candidate.support_role_count == 5
    assert {m["id"] for m in candidate.supporting} == {ifrs17}
    assert {m["id"] for m in candidate.contextual} == {excel}
    assert unrelated not in {m["id"] for m in candidate.all_members}


def test_run_bootstrap_dry_run_writes_nothing(client):
    with db.db_cursor() as cur:
        _concept(cur, "Chain ladder X", type_code="method")
        _concept(cur, "Reserving X", type_code="function")
        for i in range(5):
            _role(cur, f"Role {i}", [_skill("Chain ladder X"), _skill("Reserving X")])

        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE type_code = 'capability'")
        before = cur.fetchone()["n"]

        result = vb.run_bootstrap(cur, min_concept_support=3, min_pair_support=5, dry_run=True)

        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE type_code = 'capability'")
        after = cur.fetchone()["n"]

    assert result["dry_run"] is True
    assert result["candidate_capabilities_found"] >= 1
    assert after == before


def test_run_bootstrap_persists_proposed_capability_with_correct_edge_direction(client):
    with db.db_cursor() as cur:
        _concept(cur, "Chain ladder Y", type_code="method")
        _concept(cur, "Reserving Y", type_code="function")
        for i in range(5):
            _role(cur, f"Role {i}", [_skill("Chain ladder Y"), _skill("Reserving Y")])

        result = vb.run_bootstrap(cur, min_concept_support=3, min_pair_support=5, dry_run=False)
        assert result["persisted"]["capabilities_created"] >= 1

        cur.execute(
            "SELECT id, status, origin FROM jobber.concept WHERE type_code = 'capability' AND origin = 'bootstrap'"
        )
        proposed = cur.fetchall()
        assert len(proposed) >= 1
        for row in proposed:
            assert row["status"] == "proposed"  # never active — must never affect matching/coverage until reviewed

        capability_id = str(proposed[0]["id"])
        cur.execute("SELECT demonstration_standard, min_depth FROM jobber.capability_detail WHERE concept_id = %s", (capability_id,))
        detail = cur.fetchone()
        assert detail is not None
        assert "curator-authored" in detail["demonstration_standard"]
        assert detail["min_depth"] == "exposed"

        # Edge direction: atomic concept -> component_of -> capability (docs/16 §2), never reversed.
        cur.execute(
            "SELECT from_concept_id, to_concept_id, status FROM jobber.concept_edge "
            "WHERE to_concept_id = %s AND relation = 'component_of'",
            (capability_id,),
        )
        edges = cur.fetchall()
        assert len(edges) >= 1
        for edge in edges:
            assert str(edge["to_concept_id"]) == capability_id
            assert edge["status"] == "proposed"
            cur.execute("SELECT type_code FROM jobber.concept WHERE id = %s", (edge["from_concept_id"],))
            assert cur.fetchone()["type_code"] != "capability"

        # This proposal must be invisible to the engine's accepted-only component read.
        from app import capability_engine as engine

        accepted = engine.load_components(cur, capability_id)
        assert accepted["core"] == [] and accepted["supporting"] == [] and accepted["contextual"] == []


def test_run_bootstrap_is_safe_to_rerun_without_duplicating(client):
    with db.db_cursor() as cur:
        _concept(cur, "Chain ladder Z", type_code="method")
        _concept(cur, "Reserving Z", type_code="function")
        for i in range(5):
            _role(cur, f"Role {i}", [_skill("Chain ladder Z"), _skill("Reserving Z")])

        vb.run_bootstrap(cur, min_concept_support=3, min_pair_support=5, dry_run=False)
        second = vb.run_bootstrap(cur, min_concept_support=3, min_pair_support=5, dry_run=False)

        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept WHERE type_code = 'capability' AND origin = 'bootstrap'")
        total = cur.fetchone()["n"]

    assert second["persisted"]["capabilities_skipped_existing_name"] >= 1
    assert total == second["candidate_capabilities_found"]  # no duplicates created on rerun
