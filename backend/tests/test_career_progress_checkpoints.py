"""Phase 9: progress checkpoints — migration shape, checkpoint history/
comparability, current-progress parity, and the progress-diff classifier
(docs/40, build §41/§42/§43).

Real Postgres, same raw-SQL fixture style as test_opportunity_alignment.py.
"""

import uuid
from datetime import date

import psycopg
import pytest

from app import capability_engine, career_directions, career_progress
from app.db import create_document, db_cursor, upsert_role_instance
from app.models import CareerDirectionConstraints, CareerDirectionCreate


# --- Fixtures ----------------------------------------------------------------


def _capability(cur, name):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('capability', %s, 'active', 'curator', now()) RETURNING id",
        (name,),
    )
    concept_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) VALUES (%s, %s, 'owned')",
        (concept_id, f"Demonstrate {name}"),
    )
    return concept_id


def _role(cur, *, title, instance_type="user_defined_target"):
    document_id, _dup = create_document(
        cur, kind="narrative" if instance_type != "observed_posting" else "job_posting",
        content_text=f"{title} — source text.", provenance_quality="original", title=title, source="user_paste",
    )
    columns = {"instance_type": instance_type, "title": title, "document_id": document_id, "description": f"{title} description."}
    return str(upsert_role_instance(cur, None, columns, skills=[]))


def _requirement(cur, role_id, concept_id, *, requirement_type="required", review_status="accepted"):
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, review_status) "
        "VALUES (%s, %s, %s, 'user_asserted', %s)",
        (role_id, concept_id, requirement_type, review_status),
    )


def _mapped_observation(cur, role_id, concept_id, name, *, requirement_type="required"):
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, canonical_concept_id, "
        "requirement_type, observation_basis) VALUES (%s, %s, %s, %s, 'app_capture')",
        (role_id, name, concept_id, requirement_type),
    )


def _evidence_for(cur, concept_id, *, name="capability"):
    cur.execute(
        "INSERT INTO profile360.episodes (start_date, end_date, autonomy, status) VALUES (%s, %s, 'directed_others', 'active') RETURNING id",
        (date(2020, 1, 1), date(2023, 1, 1)),
    )
    episode_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO profile360.claims (claim_text, episode_id, depth) VALUES (%s, %s, 'owned') RETURNING id",
        (f"Led work demonstrating {name}.", episode_id),
    )
    claim_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
        "VALUES (%s, %s, 'ai_suggested', 'accepted')",
        (claim_id, concept_id),
    )


def _selected_direction(cur, *, target_id=None, name="My Direction"):
    payload = CareerDirectionCreate(name=name, target_role_instance_id=target_id, constraints=CareerDirectionConstraints(), dimensions=[])
    saved = career_directions.create_direction(cur, payload)
    career_directions.select_direction(cur, saved["id"])
    return saved["id"]


def _target_with_one_evidenced_one_gap(cur, *, name_suffix=""):
    evidenced = _capability(cur, f"Reserving{name_suffix}")
    gap = _capability(cur, f"Capital management{name_suffix}")
    _evidence_for(cur, evidenced, name="Reserving")
    target_id = _role(cur, title=f"Head of Capital{name_suffix}")
    for concept_id, name in ((evidenced, "Reserving"), (gap, "Capital management")):
        _requirement(cur, target_id, concept_id)
        _mapped_observation(cur, target_id, concept_id, name)
    return target_id, evidenced, gap


# --- Migration shape (build §41) --------------------------------------------


def test_migration_0031_applied(client):
    with db_cursor() as cur:
        cur.execute("SELECT filename FROM jobber.migration_history")
        applied = {row["filename"] for row in cur.fetchall()}
    assert "0031_career_progress_checkpoints.sql" in applied


def test_checkpoint_table_shape(client):
    with db_cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'jobber' AND table_name = 'career_progress_checkpoint'"
        )
        columns = {row["column_name"] for row in cur.fetchall()}
    assert columns == {
        "id", "career_direction_id", "target_role_instance_id", "checkpoint_type", "label",
        "state_schema_version", "state", "source_revision", "created_at",
    }


def test_state_jsonb_is_required(client):
    with db_cursor() as cur:
        direction_id = _selected_direction(cur)
        with pytest.raises(psycopg.errors.NotNullViolation):
            cur.execute(
                "INSERT INTO jobber.career_progress_checkpoint (career_direction_id, checkpoint_type, state) "
                "VALUES (%s, 'baseline', NULL)",
                (direction_id,),
            )


def test_only_valid_checkpoint_types_allowed(client):
    with db_cursor() as cur:
        direction_id = _selected_direction(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO jobber.career_progress_checkpoint (career_direction_id, checkpoint_type, state) "
                "VALUES (%s, 'not_a_real_type', '{}'::jsonb)",
                (direction_id,),
            )


def test_target_deletion_sets_null_and_preserves_checkpoint_history(client):
    with db_cursor() as cur:
        target_id, _evidenced, _gap = _target_with_one_evidenced_one_gap(cur)
        direction_id = _selected_direction(cur, target_id=target_id)

    resp = client.post("/api/cockpit/progress/checkpoints", json={"label": "Before deletion"})
    assert resp.status_code == 200
    checkpoint_id = resp.json()["id"]

    with db_cursor() as cur:
        # Bare delete (no application/artifact history holds this target) —
        # exercises the FK's ON DELETE SET NULL directly, at the DB level.
        cur.execute("DELETE FROM jobber.requirement_claim WHERE role_instance_id = %s", (target_id,))
        cur.execute("DELETE FROM jobber.role_skill_observation WHERE role_instance_id = %s", (target_id,))
        cur.execute("DELETE FROM jobber.role_instance WHERE id = %s", (target_id,))

        cur.execute("SELECT target_role_instance_id, state FROM jobber.career_progress_checkpoint WHERE id = %s", (checkpoint_id,))
        row = cur.fetchone()
    assert row["target_role_instance_id"] is None
    # The state snapshot itself (title etc.) is untouched — history is read
    # from the row's own stored state, never re-joined to the (now gone) Target.
    assert row["state"]["target"]["id"] == target_id


def test_direction_archive_preserves_checkpoint_history(client):
    with db_cursor() as cur:
        target_id, _e, _g = _target_with_one_evidenced_one_gap(cur)
        direction_id = _selected_direction(cur, target_id=target_id)

    resp = client.post("/api/cockpit/progress/checkpoints")
    assert resp.status_code == 200

    archive_resp = client.post(f"/api/career-directions/{direction_id}/archive")
    assert archive_resp.status_code == 200

    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.career_progress_checkpoint WHERE career_direction_id = %s", (direction_id,))
        assert cur.fetchone()["n"] == 1


def test_no_update_or_delete_route_exists(client):
    with db_cursor() as cur:
        target_id, _e, _g = _target_with_one_evidenced_one_gap(cur)
        _selected_direction(cur, target_id=target_id)
    created = client.post("/api/cockpit/progress/checkpoints").json()
    assert client.put(f"/api/cockpit/progress/checkpoints/{created['id']}", json={}).status_code in (404, 405)
    assert client.delete(f"/api/cockpit/progress/checkpoints/{created['id']}").status_code in (404, 405)


# --- Baseline / checkpoint sequencing + comparability (build §5/§6/§41) ----


def test_first_checkpoint_is_baseline_then_checkpoint(client):
    with db_cursor() as cur:
        target_id, _e, _g = _target_with_one_evidenced_one_gap(cur)
        _selected_direction(cur, target_id=target_id)

    first = client.post("/api/cockpit/progress/checkpoints").json()
    assert first["checkpoint_type"] == "baseline"
    second = client.post("/api/cockpit/progress/checkpoints").json()
    assert second["checkpoint_type"] == "checkpoint"


def test_no_comparable_checkpoint_across_different_targets(client):
    with db_cursor() as cur:
        target_a, _e, _g = _target_with_one_evidenced_one_gap(cur, name_suffix=" A")
        target_b, _e2, _g2 = _target_with_one_evidenced_one_gap(cur, name_suffix=" B")
        direction_id = _selected_direction(cur, target_id=target_a)

    client.post("/api/cockpit/progress/checkpoints")

    with db_cursor() as cur:
        career_directions.update_direction(
            cur, direction_id, career_directions.CareerDirectionUpdate(target_role_instance_id=target_b)
        )

    progress = client.get("/api/cockpit/progress").json()
    assert progress["state"] == "no_checkpoint"
    assert progress["checkpoint"] is None


def test_checkpoint_requires_selected_direction_and_target(client):
    resp = client.post("/api/cockpit/progress/checkpoints")
    assert resp.status_code == 409

    with db_cursor() as cur:
        career_directions.create_direction(
            cur, CareerDirectionCreate(name="No target", constraints=CareerDirectionConstraints(), dimensions=[])
        )
        # created but not selected -> still no selected direction
    resp2 = client.post("/api/cockpit/progress/checkpoints")
    assert resp2.status_code == 409


def test_checkpoint_requires_a_linked_target(client):
    with db_cursor() as cur:
        _selected_direction(cur, target_id=None)
    resp = client.post("/api/cockpit/progress/checkpoints")
    assert resp.status_code == 409


def test_get_progress_no_direction_is_an_honest_empty_state(client):
    body = client.get("/api/cockpit/progress").json()
    assert body["state"] == "no_direction"


def test_bounded_checkpoint_history(client):
    with db_cursor() as cur:
        target_id, _e, _g = _target_with_one_evidenced_one_gap(cur)
        _selected_direction(cur, target_id=target_id)
    for i in range(career_progress.CHECKPOINT_HISTORY_LIMIT + 3):
        client.post("/api/cockpit/progress/checkpoints", json={"label": f"cp {i}"})
    progress = client.get("/api/cockpit/progress").json()
    assert len(progress["history"]) == career_progress.CHECKPOINT_HISTORY_LIMIT
    # Newest first.
    assert progress["history"][0]["label"] == f"cp {career_progress.CHECKPOINT_HISTORY_LIMIT + 2}"


# --- Current-progress parity (build §42) ------------------------------------


def test_current_state_matches_canonical_capability_engine(client):
    with db_cursor() as cur:
        target_id, evidenced, gap = _target_with_one_evidenced_one_gap(cur)
        direction = career_directions.get_direction(cur, _selected_direction(cur, target_id=target_id))
        fit = capability_engine.derive_role_fit(cur, target_id)
        current = career_progress.build_current_target_state(cur, direction)

    assert current["counts"]["evidenced"] == fit["n_evidenced"] == 1
    assert current["counts"]["not_found"] == fit["n_not_found"] == 1
    assert current["counts"]["partial"] == fit["n_partial"] == 0
    assert current["counts"]["user_asserted"] == fit["n_asserted"] == 0
    statuses_by_concept = {r["concept_id"]: r["status"] for r in current["requirements"]}
    assert statuses_by_concept[evidenced] == "evidenced"
    assert statuses_by_concept[gap] == "not_found"


def test_mapping_incomplete_is_reported_not_hidden(client):
    with db_cursor() as cur:
        target_id, _e, _g = _target_with_one_evidenced_one_gap(cur)
        _unmapped_observation(cur, target_id, "Some unmapped skill")
        direction = career_directions.get_direction(cur, _selected_direction(cur, target_id=target_id))
        current = career_progress.build_current_target_state(cur, direction)
    assert current["review"]["target_mapping_complete"] is False


def _unmapped_observation(cur, role_id, name):
    cur.execute(
        "INSERT INTO jobber.role_skill_observation (role_instance_id, surface_form, canonical_concept_id, "
        "requirement_type, observation_basis) VALUES (%s, %s, NULL, 'required', 'app_capture')",
        (role_id, name),
    )


def test_development_actions_are_counted_but_never_evidence(client):
    with db_cursor() as cur:
        target_id, evidenced, gap = _target_with_one_evidenced_one_gap(cur)
        cur.execute(
            "INSERT INTO jobber.development_action (role_instance_id, concept_id, title, status) VALUES (%s, %s, 'Close the gap', 'done')",
            (target_id, gap),
        )
        direction = career_directions.get_direction(cur, _selected_direction(cur, target_id=target_id))
        current = career_progress.build_current_target_state(cur, direction)
    assert current["development_actions"] == {"open": 0, "done": 1}
    # A *completed* development action changes nothing about the gap's own
    # evidence status (build §8's "Completed development action = capability
    # gained" is explicitly disallowed wording).
    statuses_by_concept = {r["concept_id"]: r["status"] for r in current["requirements"]}
    assert statuses_by_concept[gap] == "not_found"


def test_recording_a_checkpoint_writes_no_profile360_or_vocabulary_rows(client):
    with db_cursor() as cur:
        target_id, _e, _g = _target_with_one_evidenced_one_gap(cur)
        _selected_direction(cur, target_id=target_id)
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        claims_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        concepts_before = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
        claims_claim_before = cur.fetchone()["n"]

    assert client.post("/api/cockpit/progress/checkpoints").status_code == 200

    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM profile360.claims")
        assert cur.fetchone()["n"] == claims_before
        cur.execute("SELECT COUNT(*) AS n FROM jobber.concept")
        assert cur.fetchone()["n"] == concepts_before
        cur.execute("SELECT COUNT(*) AS n FROM jobber.requirement_claim")
        assert cur.fetchone()["n"] == claims_claim_before


def test_client_supplied_fields_cannot_spoof_checkpoint_state(client):
    with db_cursor() as cur:
        target_id, evidenced, gap = _target_with_one_evidenced_one_gap(cur)
        _selected_direction(cur, target_id=target_id)
    resp = client.post(
        "/api/cockpit/progress/checkpoints",
        json={"label": "spoof attempt", "counts": {"evidenced": 999}, "state": {"fake": True}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["counts"]["evidenced"] == 1  # derived server-side, never the spoofed 999
    assert "fake" not in body["state"]


# --- Progress diff (build §7/§43) -------------------------------------------


def _requirement_state(concept_id, canonical_name, status, requirement_type="required"):
    return {"concept_id": concept_id, "canonical_name": canonical_name, "requirement_type": requirement_type, "status": status}


def _minimal_state(requirements):
    return {"requirements": requirements}


def test_diff_not_found_to_partial_is_strengthened():
    cid = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(cid, "X", "not_found")])
    curr = _minimal_state([_requirement_state(cid, "X", "partial")])
    diff = career_progress.diff_checkpoint(prev, curr)
    assert [e["concept_id"] for e in diff["evidence_strengthened"]] == [cid]
    assert diff["assertion_added"] == []
    assert diff["evidence_weakened"] == []


@pytest.mark.parametrize(
    "previous_status,current_status",
    [("not_found", "evidenced"), ("user_asserted", "evidenced"), ("partial", "evidenced"), ("user_asserted", "partial")],
)
def test_diff_strengthening_transitions(previous_status, current_status):
    cid = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(cid, "X", previous_status)])
    curr = _minimal_state([_requirement_state(cid, "X", current_status)])
    diff = career_progress.diff_checkpoint(prev, curr)
    assert len(diff["evidence_strengthened"]) == 1
    assert diff["evidence_strengthened"][0] == {
        "concept_id": cid, "canonical_name": "X", "requirement_type": "required",
        "previous_status": previous_status, "current_status": current_status,
    }


def test_diff_not_found_to_user_asserted_is_assertion_added_not_strengthened():
    cid = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(cid, "X", "not_found")])
    curr = _minimal_state([_requirement_state(cid, "X", "user_asserted")])
    diff = career_progress.diff_checkpoint(prev, curr)
    assert diff["evidence_strengthened"] == []
    assert len(diff["assertion_added"]) == 1
    assert diff["assertion_added"][0]["previous_status"] == "not_found"
    assert diff["assertion_added"][0]["current_status"] == "user_asserted"


@pytest.mark.parametrize(
    "previous_status,current_status",
    [("evidenced", "partial"), ("evidenced", "not_found"), ("evidenced", "user_asserted"), ("partial", "not_found"), ("user_asserted", "not_found")],
)
def test_diff_weakening_transitions_are_never_hidden(previous_status, current_status):
    cid = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(cid, "X", previous_status)])
    curr = _minimal_state([_requirement_state(cid, "X", current_status)])
    diff = career_progress.diff_checkpoint(prev, curr)
    assert len(diff["evidence_weakened"]) == 1
    assert diff["evidence_weakened"][0]["previous_status"] == previous_status
    assert diff["evidence_weakened"][0]["current_status"] == current_status


def test_diff_requirement_added_and_removed():
    kept = str(uuid.uuid4())
    removed = str(uuid.uuid4())
    added = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(kept, "Kept", "evidenced"), _requirement_state(removed, "Removed", "not_found")])
    curr = _minimal_state([_requirement_state(kept, "Kept", "evidenced"), _requirement_state(added, "Added", "not_found")])
    diff = career_progress.diff_checkpoint(prev, curr)
    assert diff["target_definition_changed"]["requirements_added"] == [{"concept_id": added, "canonical_name": "Added", "requirement_type": "required"}]
    assert diff["target_definition_changed"]["requirements_removed"] == [{"concept_id": removed, "canonical_name": "Removed", "requirement_type": "required"}]
    # Unchanged requirement counted, not itemized.
    assert diff["unchanged_count"] == 1


def test_diff_requirement_type_changed_is_never_evidence_progress():
    cid = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(cid, "X", "not_found", requirement_type="preferred")])
    curr = _minimal_state([_requirement_state(cid, "X", "evidenced", requirement_type="required")])
    diff = career_progress.diff_checkpoint(prev, curr)
    # Reported once, as a definition change — never double-counted as
    # evidence_strengthened even though the status also moved.
    assert diff["evidence_strengthened"] == []
    assert len(diff["target_definition_changed"]["requirement_type_changed"]) == 1
    assert diff["target_definition_changed"]["requirement_type_changed"][0] == {
        "concept_id": cid, "canonical_name": "X", "previous_requirement_type": "preferred", "current_requirement_type": "required",
    }


def test_diff_unchanged_requirements_are_counted_not_itemized():
    cid = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(cid, "X", "partial")])
    curr = _minimal_state([_requirement_state(cid, "X", "partial")])
    diff = career_progress.diff_checkpoint(prev, curr)
    assert diff["unchanged_count"] == 1
    assert diff["evidence_strengthened"] == diff["evidence_weakened"] == diff["assertion_added"] == []


def test_diff_never_computes_a_numeric_score():
    cid = str(uuid.uuid4())
    prev = _minimal_state([_requirement_state(cid, "X", "not_found")])
    curr = _minimal_state([_requirement_state(cid, "X", "evidenced")])
    diff = career_progress.diff_checkpoint(prev, curr)

    def _walk_keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert "score" not in k.lower() and "percent" not in k.lower() and k.lower() != "progress_pct"
                _walk_keys(v)
        elif isinstance(node, list):
            for item in node:
                _walk_keys(item)

    _walk_keys(diff)


def test_full_checkpoint_diff_via_api_reflects_a_new_accepted_claim(client):
    with db_cursor() as cur:
        target_id, evidenced, gap = _target_with_one_evidenced_one_gap(cur)
        _selected_direction(cur, target_id=target_id)

    baseline = client.post("/api/cockpit/progress/checkpoints").json()
    assert baseline["checkpoint_type"] == "baseline"

    with db_cursor() as cur:
        _evidence_for(cur, gap, name="Capital management")

    progress = client.get("/api/cockpit/progress").json()
    assert progress["state"] == "available"
    strengthened_ids = {e["concept_id"] for e in progress["diff"]["evidence_strengthened"]}
    assert gap in strengthened_ids
