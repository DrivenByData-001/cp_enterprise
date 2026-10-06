"""Phase 9: Career Cockpit composition (docs/40, build §45/§46/§47).

Covers the end-to-end evidence-loop boundary (the single most important
Phase 9 semantic guarantee), the composed `/api/cockpit` response across
Direction/Target/checkpoint/application/learning/market states, bulk-vs-
single opportunity relationship parity, and Cockpit query-count boundedness
as the corpus grows.
"""

import uuid
from datetime import date

from app import capability_engine, career_cockpit, career_directions, career_progress, db, opportunity_alignment as alignment
from app.db import create_document, db_cursor, upsert_role_instance
from app.models import CareerDirectionConstraints, CareerDirectionCreate
from tests.query_counter import count_queries


# --- Fixtures (mirrors test_opportunity_alignment.py / test_career_progress_checkpoints.py) --


def _active_concept(cur, name: str, type_code: str = "capability") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    concept_id = str(cur.fetchone()["id"])
    if type_code == "capability":
        cur.execute(
            "INSERT INTO jobber.capability_detail (concept_id, demonstration_standard, min_depth) VALUES (%s, %s, 'owned')",
            (concept_id, f"Demonstrate {name}"),
        )
    return concept_id


def _archetype(cur, name):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES ('role_archetype', %s, 'active', 'curator', now()) RETURNING id",
        (name,),
    )
    concept_id = str(cur.fetchone()["id"])
    cur.execute("INSERT INTO jobber.role_archetype_detail (concept_id) VALUES (%s)", (concept_id,))
    return concept_id


def _role(cur, *, title, instance_type="observed_posting", archetype_concept_id=None, organisation="Acme"):
    document_id, _dup = create_document(
        cur, kind="job_posting" if instance_type == "observed_posting" else "narrative",
        content_text=f"{title} — source text.", provenance_quality="original", title=title, source="user_paste",
    )
    columns = {"instance_type": instance_type, "title": title, "organisation": organisation, "document_id": document_id, "description": f"{title} description."}
    role_id = upsert_role_instance(cur, None, columns, skills=[])
    if archetype_concept_id:
        cur.execute("UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s", (archetype_concept_id, role_id))
    return str(role_id)


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
    cur.execute(
        "INSERT INTO jobber.role_skill_observation_concept (role_skill_observation_id, concept_id, mapping_basis) "
        "SELECT id, %s, 'legacy_single' FROM jobber.role_skill_observation "
        "WHERE role_instance_id = %s AND canonical_concept_id = %s ORDER BY created_at DESC LIMIT 1 "
        "ON CONFLICT DO NOTHING",
        (concept_id, role_id, concept_id),
    )

def _evidence_for(cur, concept_id, *, name="capability"):
    cur.execute(
        "INSERT INTO profile360.episodes (start_date, end_date, autonomy, status) VALUES (%s, %s, 'directed_others', 'active') RETURNING id",
        (date(2020, 1, 1), date(2023, 1, 1)),
    )
    episode_id = str(cur.fetchone()["id"])
    cur.execute("INSERT INTO profile360.claims (claim_text, episode_id, depth) VALUES (%s, %s, 'owned') RETURNING id", (f"Led work demonstrating {name}.", episode_id))
    claim_id = str(cur.fetchone()["id"])
    cur.execute(
        "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) VALUES (%s, %s, 'ai_suggested', 'accepted')",
        (claim_id, concept_id),
    )


def _selected_direction(cur, *, target_id=None, name="My Direction"):
    payload = CareerDirectionCreate(name=name, target_role_instance_id=target_id, constraints=CareerDirectionConstraints(), dimensions=[])
    saved = career_directions.create_direction(cur, payload)
    career_directions.select_direction(cur, saved["id"])
    return saved["id"]


def _application(cur, role_id, status="preparing"):
    cur.execute("INSERT INTO jobber.application (role_instance_id, status) VALUES (%s, %s) RETURNING id", (role_id, status))
    return str(cur.fetchone()["id"])


def _note(cur, application_id, *, text="An example.", note_type="evidence_example", concept_id=None):
    cur.execute(
        "INSERT INTO jobber.application_note (application_id, concept_id, note_type, note_text) VALUES (%s, %s, %s, %s) RETURNING id",
        (application_id, concept_id, note_type, text),
    )
    return str(cur.fetchone()["id"])


def _event(cur, application_id, *, event_type="interview_completed", notes="Reflection.", event_at=None):
    cur.execute(
        "INSERT INTO jobber.application_event (application_id, event_type, event_at, notes) VALUES (%s, %s, COALESCE(%s, now()), %s) RETURNING id",
        (application_id, event_type, event_at, notes),
    )
    return str(cur.fetchone()["id"])


def _target_with_gap(cur, *, name_suffix=""):
    gap = _active_concept(cur, f"Capital management{name_suffix}")
    target_id = _role(cur, title=f"Head of Capital{name_suffix}", instance_type="user_defined_target")
    _requirement(cur, target_id, gap)
    _mapped_observation(cur, target_id, gap, "Capital management")
    return target_id, gap


# --- Zero state / composition sections (build §46) --------------------------


def test_cockpit_zero_state_is_honest_not_blank(client):
    body = client.get("/api/cockpit").json()
    assert body["direction"]["state"] == "no_direction"
    assert body["target_progress"]["state"] == "no_direction"
    assert body["applications"]["state"] == "available"
    assert body["applications"]["active_count"] == 0
    assert body["opportunities"]["state"] == "available"
    assert body["opportunities"]["items"] == []
    assert body["learning"]["state"] == "available"
    assert body["market_context"]["state"] == "no_direction"
    assert len(body["next_actions"]) >= 1
    assert body["next_actions"][0]["code"] == "select_direction"


def test_cockpit_direction_without_target(client):
    with db_cursor() as cur:
        _selected_direction(cur, target_id=None)
    body = client.get("/api/cockpit").json()
    assert body["direction"]["state"] == "no_target"
    assert body["target_progress"]["state"] == "no_target"
    assert body["next_actions"][0]["code"] == "link_target"


def test_cockpit_direction_with_target_no_checkpoint(client):
    with db_cursor() as cur:
        target_id, _gap = _target_with_gap(cur)
        _selected_direction(cur, target_id=target_id)
    body = client.get("/api/cockpit").json()
    assert body["direction"]["state"] == "with_target"
    assert body["target_progress"]["state"] == "no_checkpoint"
    assert body["target_progress"]["current"]["counts"]["not_found"] == 1
    assert any(a["code"] == "record_baseline" for a in body["next_actions"])


def test_cockpit_get_never_writes_a_checkpoint(client):
    with db_cursor() as cur:
        target_id, _gap = _target_with_gap(cur)
        _selected_direction(cur, target_id=target_id)
    for _ in range(3):
        client.get("/api/cockpit")
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.career_progress_checkpoint")
        assert cur.fetchone()["n"] == 0


def test_cockpit_no_score_anywhere_in_response(client):
    with db_cursor() as cur:
        target_id, gap = _target_with_gap(cur)
        _selected_direction(cur, target_id=target_id)
    client.post("/api/cockpit/progress/checkpoints")
    with db_cursor() as cur:
        _evidence_for(cur, gap, name="Capital management")
    body = client.get("/api/cockpit").json()

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert "score" not in k.lower()
                assert k.lower() not in ("readiness", "career_health", "overall_score")
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


def test_cockpit_active_applications_and_next_interview(client):
    with db_cursor() as cur:
        role_id = _role(cur, title="Reserving Actuary")
        application_id = _application(cur, role_id, status="interviewing")
        _event(cur, application_id, event_type="interview_scheduled", notes=None, event_at="2099-01-01T10:00:00Z")

    body = client.get("/api/cockpit").json()
    assert body["applications"]["active_count"] == 1
    assert body["applications"]["by_status"]["interviewing"] == 1
    assert len(body["applications"]["active_items"]) == 1
    assert body["applications"]["next_interview"]["application_id"] == application_id


def test_cockpit_recent_outcomes(client):
    with db_cursor() as cur:
        role_id = _role(cur, title="Reserving Actuary")
        application_id = _application(cur, role_id, status="closed")
        _event(cur, application_id, event_type="rejected", notes="They chose someone with more Solvency II experience.")

    body = client.get("/api/cockpit").json()
    outcomes = body["applications"]["recent_outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["event_type"] == "rejected"
    assert outcomes[0]["has_notes"] is True


def test_cockpit_learning_items_and_queue_status(client):
    with db_cursor() as cur:
        role_id = _role(cur, title="Reserving Actuary")
        application_id = _application(cur, role_id)
        note_id = _note(cur, application_id, text="I ran the reserving process end to end.")

    body = client.get("/api/cockpit").json()
    items = body["learning"]["items"]
    assert len(items) == 1
    assert items[0]["source_id"] == note_id
    assert items[0]["queue_status"] == "not_queued"

    client.post(f"/api/applications/{application_id}/notes/{note_id}/promote")
    body2 = client.get("/api/cockpit").json()
    assert body2["learning"]["items"][0]["queue_status"] == "queued_pending"


def test_cockpit_recent_opportunities_no_fabricated_relationship_without_target(client):
    with db_cursor() as cur:
        _selected_direction(cur, target_id=None)
        _role(cur, title="An opportunity")
    body = client.get("/api/cockpit").json()
    assert len(body["opportunities"]["items"]) == 1
    assert body["opportunities"]["items"][0]["relationship"] is None


def test_cockpit_opportunity_marks_existing_application(client):
    with db_cursor() as cur:
        role_id = _role(cur, title="An opportunity")
        _application(cur, role_id)
    body = client.get("/api/cockpit").json()
    assert body["opportunities"]["items"][0]["has_application"] is True


def test_cockpit_market_context_reuses_phase8_semantics(client):
    with db_cursor() as cur:
        archetype_id = _archetype(cur, "Reserving Actuary Archetype")
        target_id, _gap = _target_with_gap(cur)
        cur.execute("UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s", (archetype_id, target_id))
        _selected_direction(cur, target_id=target_id)
    body = client.get("/api/cockpit").json()
    assert body["market_context"]["state"] == "available"
    assert body["market_context"]["archetype_concept_id"] == archetype_id
    assert body["market_context"]["representativeness"]["known"] is False


def test_cockpit_section_failure_isolation(client, monkeypatch):
    with db_cursor() as cur:
        target_id, _gap = _target_with_gap(cur)
        _selected_direction(cur, target_id=target_id)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated market coverage failure")

    monkeypatch.setattr(career_cockpit, "_build_market_context_section", _boom)
    body = client.get("/api/cockpit").json()
    assert body["market_context"]["state"] == "unavailable"
    # Everything else is still present — one section's failure never blanks the page.
    assert body["direction"]["state"] == "with_target"
    assert body["target_progress"]["state"] == "no_checkpoint"
    assert body["applications"]["state"] == "available"


def test_cockpit_bounded_next_actions():
    now_action_lists = career_cockpit.build_next_actions(
        direction=None, target_progress={"state": "no_direction"}, applications={"state": "available", "next_interview": None},
        learning={"state": "available", "items": []}, opportunities={"state": "available", "items": []},
        development_actions_due=[], now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    assert 1 <= len(now_action_lists) <= career_cockpit.NEXT_ACTIONS_LIMIT


# --- Bulk vs single-role relationship parity (build §20) --------------------


def test_bulk_relationship_matches_single_role_alignment(client):
    with db_cursor() as cur:
        shared_archetype = _archetype(cur, "Capital Leadership")
        gap = _active_concept(cur, "Capital modelling")
        target_id = _role(cur, title="Head of Capital", instance_type="user_defined_target", archetype_concept_id=shared_archetype)
        _requirement(cur, target_id, gap)
        _mapped_observation(cur, target_id, gap, "Capital modelling")

        same_family = _role(cur, title="Capital Director", archetype_concept_id=shared_archetype)
        _requirement(cur, same_family, gap, requirement_type="required")

        stepping_stone = _role(cur, title="Capital Analyst")
        _requirement(cur, stepping_stone, gap, requirement_type="required")

        _selected_direction(cur, target_id=target_id)

        single_same_family = alignment.build_opportunity_alignment(cur, same_family)
        single_stepping_stone = alignment.build_opportunity_alignment(cur, stepping_stone)

        bulk = alignment.bulk_target_relationship(
            cur, target_id, [same_family, stepping_stone],
            target_archetype_id=shared_archetype, direction_archetype_id=None,
        )

    assert single_same_family["relationship"]["state"] == bulk[same_family]["relationship"]["state"] == "same_destination_family"
    assert single_stepping_stone["relationship"]["state"] == bulk[stepping_stone]["relationship"]["state"]


def test_bulk_relationship_empty_for_no_candidates(client):
    with db_cursor() as cur:
        assert alignment.bulk_target_relationship(cur, str(uuid.uuid4()), [], target_archetype_id=None, direction_archetype_id=None) == {}


# --- End-to-end evidence-loop boundary (build §45) — the most important ----
# Phase 9 semantic guarantee: queueing is never evidence: only a genuine
# accepted Profile360 claim + mapping can move the needle, and once it does,
# it shows up both in current Target status and in the checkpoint diff.


def test_end_to_end_evidence_loop_boundary(client):
    with db_cursor() as cur:
        target_id, gap = _target_with_gap(cur)
        direction_id = _selected_direction(cur, target_id=target_id)
        role_id = _role(cur, title="Capital Analyst role")
        application_id = _application(cur, role_id)

    # 1. Target requirement is not_found.
    progress1 = client.get("/api/cockpit/progress").json()
    assert progress1["current"]["counts"]["not_found"] == 1
    assert progress1["current"]["counts"]["evidenced"] == 0

    baseline = client.post("/api/cockpit/progress/checkpoints").json()
    assert baseline["checkpoint_type"] == "baseline"

    # 2. Application evidence-example note is created.
    with db_cursor() as cur:
        note_id = _note(cur, application_id, text="I personally built the capital model.", concept_id=gap)

    # 3. Note is queued to Profile360.
    promote_resp = client.post(f"/api/applications/{application_id}/notes/{note_id}/promote")
    assert promote_resp.status_code == 200
    assert promote_resp.json()["status"] == "queued_pending"

    # 4. Target remains not_found.
    progress2 = client.get("/api/cockpit/progress").json()
    assert progress2["current"]["counts"]["not_found"] == 1
    assert progress2["current"]["counts"]["evidenced"] == 0

    # 5. Queue row is marked processed (simulating Profile360's own tool).
    with db_cursor() as cur:
        cur.execute(
            "UPDATE profile360.manual_import_queue SET processed = true, processed_at = now() WHERE source_key = %s",
            (promote_resp.json()["queue_source_key"],),
        )

    # 6. Target remains not_found — "processed" is not "evidenced".
    progress3 = client.get("/api/cockpit/progress").json()
    assert progress3["current"]["counts"]["not_found"] == 1
    assert progress3["current"]["counts"]["evidenced"] == 0
    learning_item = next(i for i in client.get("/api/cockpit").json()["learning"]["items"] if i["source_id"] == note_id)
    assert learning_item["queue_status"] == "processed_by_profile360"
    assert learning_item["current_target_evidence_status"] == "not_found"

    # 7. Genuine Profile360 claim + accepted mapping is later created —
    #    decoupled from the promotion queue entirely (a real curator/tool
    #    action, not triggered by this test's queue update above).
    with db_cursor() as cur:
        _evidence_for(cur, gap, name="Capital modelling")

    # 8. Canonical Target status changes according to the capability engine.
    with db_cursor() as cur:
        fit = capability_engine.derive_role_fit(cur, target_id)
    assert fit["n_evidenced"] == 1
    progress4 = client.get("/api/cockpit/progress").json()
    assert progress4["current"]["counts"]["evidenced"] == 1
    assert progress4["current"]["counts"]["not_found"] == 0

    # 9. Cockpit checkpoint diff reflects the evidence change since baseline.
    assert progress4["state"] == "available"
    strengthened_ids = {e["concept_id"] for e in progress4["diff"]["evidence_strengthened"]}
    assert gap in strengthened_ids
    assert progress4["diff"]["assertion_added"] == []
    assert progress4["diff"]["evidence_weakened"] == []

    cockpit = client.get("/api/cockpit").json()
    assert cockpit["target_progress"]["diff"]["evidence_strengthened"]
    assert direction_id  # sanity: the direction used throughout stayed selected


# --- Performance / query-count boundedness (build §38/§47) -----------------


def test_cockpit_query_count_does_not_grow_with_corpus_size(client):
    with db_cursor() as cur:
        target_id, gap = _target_with_gap(cur)
        _selected_direction(cur, target_id=target_id)
        role_id = _role(cur, title="Small corpus role")
        application_id = _application(cur, role_id)
        _note(cur, application_id, text="One note.")
        _event(cur, application_id, event_type="interview_completed", notes="One reflection.")

    # Two caching effects would otherwise make measurements incomparable —
    # neither is evidence about whether Cockpit scales with corpus size:
    # (1) jobber.d_target_evidence (the stepping-stone/opportunity-alignment
    # evidence-status cache keyed by revision), cleared before each
    # measurement below; (2) profile360_reader's module-level, once-per-
    # process schema-introspection cache (an information_schema query on
    # first-ever use of profile360.capabilities), warmed here by one
    # untimed call so neither the small nor the large measurement pays for
    # it.
    career_cockpit.build_cockpit()
    with db_cursor() as cur:
        cur.execute("TRUNCATE TABLE jobber.d_target_evidence")
    with count_queries() as small_corpus:
        career_cockpit.build_cockpit()

    with db_cursor() as cur:
        # Grow every corpus dimension well past each section's own bound
        # (OPPORTUNITIES_LIMIT/ACTIVE_APPLICATIONS_LIMIT/LEARNING_ITEMS_LIMIT
        # are all 5-8) — a query-per-item regression would show up here.
        for i in range(20):
            _role(cur, title=f"Extra opportunity {i}")
        for i in range(20):
            extra_role = _role(cur, title=f"Extra application role {i}")
            extra_application = _application(cur, extra_role, status="preparing")
            _note(cur, extra_application, text=f"Extra note {i}.")
            _event(cur, extra_application, event_type="interview_completed", notes=f"Extra reflection {i}.")
        cur.execute("TRUNCATE TABLE jobber.d_target_evidence")

    with count_queries() as large_corpus:
        career_cockpit.build_cockpit()

    assert large_corpus.count == small_corpus.count, (
        f"Cockpit query count grew with corpus size: {small_corpus.count} -> {large_corpus.count}"
    )


def test_no_ai_call_anywhere_in_cockpit(client, monkeypatch):
    """Belt-and-braces: Cockpit must never call the AI task runner (build
    §16/§36) — patch it to explode if anything tries."""
    from app import ai

    def _boom(*args, **kwargs):
        raise AssertionError("Cockpit must never call the AI provider")

    monkeypatch.setattr(ai, "run_json_task", _boom)
    with db_cursor() as cur:
        target_id, _gap = _target_with_gap(cur)
        _selected_direction(cur, target_id=target_id)
    resp = client.get("/api/cockpit")
    assert resp.status_code == 200
