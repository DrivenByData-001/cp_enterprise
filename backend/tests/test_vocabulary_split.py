"""Vocabulary "Split cluster" (next-build brief §4) — DB-backed tests, same
precedent as test_vocabulary_curation.py: real Postgres, only embeddings
stubbed. Covers undoing an over-broad lexical cluster (the brief's own
worked example: "stakeholder engagement" wrongly grouped with "stakeholder
management" by the bootstrap's synonym-seed list) while preserving every
concept_proposal row and role_skill_observation, and surviving a later
bootstrap dry-run/real run without being re-collapsed."""

import pytest

from app import db, vocabulary_bootstrap as vb, vocabulary_curation as curation


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


def _seed_stakeholder_cluster(cur) -> str:
    """The brief's own worked example (§3/§4): "stakeholder engagement" and
    "stakeholder management" are related but distinct concepts that the
    bootstrap's lexical clustering (vocabulary_bootstrap._SYNONYM_GROUPS)
    nonetheless groups into one cluster."""
    _role(cur, "Programme Lead", [_skill("stakeholder engagement")], country="UK", seniority_level="mid", career_track="mixed", posting_date="2021-01-01")
    _role(cur, "Change Manager", [_skill("stakeholder management")], country="Ireland", seniority_level="senior", career_track="mixed", posting_date="2023-06-01")
    vb.compute_cluster_keys(cur)
    key = _cluster_key_of(cur, "stakeholder engagement")
    assert key == _cluster_key_of(cur, "stakeholder management"), "fixture assumption: both start in one cluster"
    return key


# --- happy path --------------------------------------------------------------

def test_split_two_form_cluster_into_two_pending_clusters(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)

    preview = client.post(
        "/api/vocabulary/clusters/split/preview",
        json={"cluster_key": key, "groups": [{"surface_forms": ["stakeholder engagement"]}, {"surface_forms": ["stakeholder management"]}]},
    )
    assert preview.status_code == 200
    pbody = preview.json()
    assert len(pbody["resulting_groups"]) == 2
    labels = {g["suggested_canonical_label"] for g in pbody["resulting_groups"]}
    assert labels == {"stakeholder engagement", "stakeholder management"}
    for g in pbody["resulting_groups"]:
        assert g["role_count"] == 1
        assert g["observation_count"] == 1

    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={"cluster_key": key, "groups": [{"surface_forms": ["stakeholder engagement"]}, {"surface_forms": ["stakeholder management"]}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["groups_created"] == 2
    new_keys = {g["new_cluster_key"] for g in body["resulting_clusters"]}
    assert key not in new_keys
    assert len(new_keys) == 2

    with db.db_cursor() as cur:
        key_engagement = _cluster_key_of(cur, "stakeholder engagement")
        key_management = _cluster_key_of(cur, "stakeholder management")
    assert key_engagement != key_management
    assert {key_engagement, key_management} == new_keys

    # Both are now independently visible in the normal pending queue.
    listed = client.get("/api/vocabulary/clusters", params={"status": "pending"}).json()
    listed_keys = {c["cluster_key"] for c in listed["items"]}
    assert key_engagement in listed_keys
    assert key_management in listed_keys
    assert key not in listed_keys  # the original over-broad key no longer exists


def test_split_preserves_all_proposal_rows_and_observations(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept_proposal")
        proposals_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_skill_observation")
        observations_before = cur.fetchone()["n"]

    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={"cluster_key": key, "groups": [{"surface_forms": ["stakeholder engagement"]}, {"surface_forms": ["stakeholder management"]}]},
    )
    assert resp.status_code == 200

    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept_proposal")
        proposals_after = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.role_skill_observation")
        observations_after = cur.fetchone()["n"]
        cur.execute("SELECT status FROM jobber.concept_proposal WHERE surface_form IN ('stakeholder engagement', 'stakeholder management')")
        statuses = {r["status"] for r in cur.fetchall()}

    assert proposals_after == proposals_before
    assert observations_after == observations_before
    assert statuses == {"pending"}  # still pending, no concept accepted by splitting


def test_priority_metrics_recomputed_per_resulting_cluster(client):
    """Priority must be recomputed from each resulting cluster's own
    observations, not inherited from the original combined cluster (brief
    §4.5) — here "stakeholder engagement" gets 3x the role coverage of
    "stakeholder management" after the split."""
    with db.db_cursor() as cur:
        _role(cur, "Programme Lead", [_skill("stakeholder engagement")], posting_date="2021-01-01")
        _role(cur, "PMO Analyst", [_skill("stakeholder engagement")], posting_date="2022-01-01")
        _role(cur, "Delivery Lead", [_skill("stakeholder engagement")], posting_date="2023-01-01")
        _role(cur, "Change Manager", [_skill("stakeholder management")], posting_date="2023-06-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "stakeholder engagement")
        assert key == _cluster_key_of(cur, "stakeholder management")

        curation.split_cluster(cur, cluster_key=key, groups=[["stakeholder engagement"], ["stakeholder management"]])
        key_engagement = _cluster_key_of(cur, "stakeholder engagement")
        key_management = _cluster_key_of(cur, "stakeholder management")

    detail_engagement = client.get(f"/api/vocabulary/clusters/{key_engagement}").json()
    detail_management = client.get(f"/api/vocabulary/clusters/{key_management}").json()

    assert detail_engagement["role_count"] == 3
    assert detail_management["role_count"] == 1
    assert detail_engagement["priority_score"] > detail_management["priority_score"]


def test_accept_merge_reject_work_independently_after_split(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)
        curation.split_cluster(cur, cluster_key=key, groups=[["stakeholder engagement"], ["stakeholder management"]])
        key_engagement = _cluster_key_of(cur, "stakeholder engagement")
        key_management = _cluster_key_of(cur, "stakeholder management")
        existing_concept_id = _active_concept(cur, "Stakeholder Relations", type_code="method")

    accept_resp = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": key_engagement, "type_code": "method", "canonical_name": "Stakeholder Engagement"},
    )
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "accepted_new"

    merge_resp = client.post(
        "/api/vocabulary/clusters/merge",
        json={"cluster_key": key_management, "concept_id": existing_concept_id},
    )
    assert merge_resp.status_code == 200
    assert merge_resp.json()["status"] == "accepted_alias"
    assert merge_resp.json()["resolved_concept_id"] == existing_concept_id

    # Accepting one resulting cluster must never have touched the other.
    with db.db_cursor() as cur:
        cur.execute("SELECT status, resolved_concept_id FROM jobber.concept_proposal WHERE surface_form = 'stakeholder engagement'")
        engagement_row = cur.fetchone()
        cur.execute("SELECT status, resolved_concept_id FROM jobber.concept_proposal WHERE surface_form = 'stakeholder management'")
        management_row = cur.fetchone()
    assert engagement_row["status"] == "accepted_new"
    assert management_row["status"] == "accepted_alias"
    assert str(management_row["resolved_concept_id"]) == existing_concept_id


def test_reject_after_split_only_affects_its_own_cluster(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)
        curation.split_cluster(cur, cluster_key=key, groups=[["stakeholder engagement"], ["stakeholder management"]])
        key_engagement = _cluster_key_of(cur, "stakeholder engagement")
        key_management = _cluster_key_of(cur, "stakeholder management")

    resp = client.post("/api/vocabulary/clusters/reject", json={"cluster_key": key_management})
    assert resp.status_code == 200

    still_pending = client.get("/api/vocabulary/clusters", params={"status": "pending"}).json()
    pending_keys = {c["cluster_key"] for c in still_pending["items"]}
    assert key_engagement in pending_keys
    assert key_management not in pending_keys


def test_split_again_on_a_resulting_cluster_with_multiple_forms(client):
    with db.db_cursor() as cur:
        _role(cur, "Role A", [_skill("stakeholder engagement")], posting_date="2021-01-01")
        _role(cur, "Role B", [_skill("stakeholder engagement plans")], posting_date="2021-06-01")
        _role(cur, "Role C", [_skill("stakeholder management")], posting_date="2022-01-01")
        vb.compute_cluster_keys(cur)
        # These may or may not all land in one bootstrap cluster; force it
        # explicitly so this test doesn't depend on the lexical seed list.
        cur.execute(
            "UPDATE jobber.concept_proposal SET cluster_key = 'combined-test-key' "
            "WHERE surface_form IN ('stakeholder engagement', 'stakeholder engagement plans', 'stakeholder management')"
        )
        curation.split_cluster(
            cur, cluster_key="combined-test-key",
            groups=[["stakeholder engagement", "stakeholder engagement plans"], ["stakeholder management"]],
        )
        merged_key = _cluster_key_of(cur, "stakeholder engagement")
        assert merged_key == _cluster_key_of(cur, "stakeholder engagement plans")

    # The first resulting group still has 2 surface forms — splitting it
    # again must work exactly like splitting any other pending cluster.
    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={
            "cluster_key": merged_key,
            "groups": [{"surface_forms": ["stakeholder engagement"]}, {"surface_forms": ["stakeholder engagement plans"]}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["groups_created"] == 2


# --- validation / error handling ---------------------------------------------

def test_invalid_split_missing_a_surface_form_is_rejected(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)

    # Leaves "stakeholder management" out entirely — not a valid partition.
    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={"cluster_key": key, "groups": [{"surface_forms": ["stakeholder engagement"]}, {"surface_forms": ["something else entirely"]}]},
    )
    assert resp.status_code == 400

    # Nothing should have changed.
    with db.db_cursor() as cur:
        assert _cluster_key_of(cur, "stakeholder engagement") == key
        assert _cluster_key_of(cur, "stakeholder management") == key


def test_invalid_split_duplicate_surface_form_across_groups_is_rejected(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)

    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={
            "cluster_key": key,
            "groups": [
                {"surface_forms": ["stakeholder engagement", "stakeholder management"]},
                {"surface_forms": ["stakeholder management"]},
            ],
        },
    )
    assert resp.status_code == 422  # rejected by the request model's own validator


def test_single_group_split_is_rejected(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)

    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={"cluster_key": key, "groups": [{"surface_forms": ["stakeholder engagement", "stakeholder management"]}]},
    )
    assert resp.status_code == 422


def test_single_form_cluster_cannot_be_split(client):
    with db.db_cursor() as cur:
        _role(cur, "Solo Role", [_skill("Chain ladder reserving")], posting_date="2021-01-01")
        vb.compute_cluster_keys(cur)
        key = _cluster_key_of(cur, "chain ladder reserving")

    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={"cluster_key": key, "groups": [{"surface_forms": ["chain ladder reserving"]}, {"surface_forms": ["some other form"]}]},
    )
    assert resp.status_code == 400


def test_resolved_cluster_cannot_be_split(client):
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)
    accept = client.post(
        "/api/vocabulary/clusters/accept",
        json={"cluster_key": key, "type_code": "method", "canonical_name": "Stakeholder Engagement And Management"},
    )
    assert accept.status_code == 200

    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={"cluster_key": key, "groups": [{"surface_forms": ["stakeholder engagement"]}, {"surface_forms": ["stakeholder management"]}]},
    )
    assert resp.status_code == 409


def test_unknown_cluster_key_split_is_404(client):
    resp = client.post(
        "/api/vocabulary/clusters/split",
        json={"cluster_key": "no-such-cluster", "groups": [{"surface_forms": ["a"]}, {"surface_forms": ["b"]}]},
    )
    assert resp.status_code == 404


def test_split_rolls_back_entirely_on_mid_operation_failure(client, monkeypatch):
    """Transaction-boundary guarantee, same precedent as
    test_batch_accept_is_all_or_nothing_on_failure: if anything fails partway
    through a split's per-group updates, none of them may be left applied —
    not even the group(s) already written before the failure."""
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)

    state = {"updates": 0}

    with pytest.raises(RuntimeError):
        with db.db_cursor() as cur:
            cursor_cls = type(cur)
            real_execute = cursor_cls.execute

            def flaky_execute(self, query, *args, **kwargs):
                if isinstance(query, str) and "UPDATE jobber.concept_proposal" in query and "cluster_key_locked = TRUE" in query:
                    state["updates"] += 1
                    if state["updates"] == 2:
                        raise RuntimeError("simulated failure on the second group's update")
                return real_execute(self, query, *args, **kwargs)

            monkeypatch.setattr(cursor_cls, "execute", flaky_execute)
            curation.split_cluster(
                cur, cluster_key=key, groups=[["stakeholder engagement"], ["stakeholder management"]]
            )

    monkeypatch.undo()  # restore the real Cursor.execute before verifying below

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT surface_form, cluster_key, cluster_key_locked FROM jobber.concept_proposal "
            "WHERE surface_form IN ('stakeholder engagement', 'stakeholder management') ORDER BY surface_form"
        )
        rows = {r["surface_form"]: r for r in cur.fetchall()}
    assert rows["stakeholder engagement"]["cluster_key"] == key
    assert rows["stakeholder management"]["cluster_key"] == key
    assert rows["stakeholder engagement"]["cluster_key_locked"] is False
    assert rows["stakeholder management"]["cluster_key_locked"] is False


# --- survives the bootstrap (brief §4.4) -------------------------------------

def test_manual_split_survives_bootstrap_dry_run_and_real_rerun_stakeholder_case(client):
    """The brief's own regression case: after a curator splits "stakeholder
    engagement" from "stakeholder management", neither a later --dry-run nor
    a later real bootstrap run may recreate the rejected grouping."""
    with db.db_cursor() as cur:
        key = _seed_stakeholder_cluster(cur)
        curation.split_cluster(cur, cluster_key=key, groups=[["stakeholder engagement"], ["stakeholder management"]])
        key_engagement = _cluster_key_of(cur, "stakeholder engagement")
        key_management = _cluster_key_of(cur, "stakeholder management")
        assert key_engagement != key_management

        # --dry-run must report them as separate clusters, not re-collapse them.
        dry_run_result = vb.analyze_cluster_keys_dryrun(cur)
        cluster_by_surface_form = {
            sf: c["cluster_key"] for c in dry_run_result["sample_clusters"] for sf in c["surface_forms"]
        }
        assert cluster_by_surface_form["stakeholder engagement"] == key_engagement
        assert cluster_by_surface_form["stakeholder management"] == key_management

        # A later real bootstrap run must not recompute/overwrite either key.
        vb.compute_cluster_keys(cur)
        assert _cluster_key_of(cur, "stakeholder engagement") == key_engagement
        assert _cluster_key_of(cur, "stakeholder management") == key_management

        # And a *new* posting using either surface form must still resolve
        # into its already-split cluster, not a freshly re-merged one.
        _role(cur, "New Programme Lead", [_skill("stakeholder engagement")], posting_date="2026-01-01")
        vb.compute_cluster_keys(cur)
        assert _cluster_key_of(cur, "stakeholder engagement") == key_engagement
        assert _cluster_key_of(cur, "stakeholder management") == key_management
