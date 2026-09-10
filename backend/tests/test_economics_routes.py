"""End-to-end HTTP-level integration for the Economics API surface: backfill
-> archetype assignment -> full rebuild -> archetype-comp / gap-value /
readiness endpoints. Complements the pure-function unit tests in
test_economics_engine.py and test_compensation.py."""

from app import db


def _role_with_salary(cur, *, salary_min, salary_max, currency, country, title="Senior Actuarial Analyst"):
    return str(
        db.upsert_role_instance(
            cur, None,
            {"instance_type": "observed_posting", "title": title, "salary_min": salary_min, "salary_max": salary_max, "currency": currency, "country": country},
            skills=[],
        )
    )


def test_full_pipeline_backfill_assign_rebuild_readiness(client):
    with db.db_cursor() as cur:
        role_ids = [
            _role_with_salary(cur, salary_min=m, salary_max=m, currency="GBP", country="UK")
            for m in (50000, 55000, 60000, 65000, 70000)
        ]

    backfill_resp = client.post("/api/economics/compensation-observations/backfill")
    assert backfill_resp.status_code == 200
    assert backfill_resp.json()["observations_created"] == 5

    archetype_resp = client.post("/api/archetypes", json={"canonical_name": "Pipeline Test Archetype", "role_instance_ids": role_ids})
    assert archetype_resp.status_code == 200
    archetype_id = archetype_resp.json()["id"]

    rebuild_resp = client.post("/api/economics/rebuild")
    assert rebuild_resp.status_code == 200
    body = rebuild_resp.json()
    assert body["archetype_comp"]["computed"] >= 1

    comp_listing = client.get("/api/economics/archetype-comp", params={"archetype_concept_id": archetype_id}).json()
    assert len(comp_listing) == 1
    assert comp_listing[0]["reference_comp"] == 60000
    assert comp_listing[0]["reference_source"] == "posting"

    contexts = client.get("/api/economics/gap-value/contexts").json()
    assert any(c["currency"] == "GBP" for c in contexts)

    readiness = client.get("/api/economics/readiness").json()
    assert readiness["accepted_posting_stated_observations"] >= 5
    assert readiness["archetypes_with_assigned_roles"] >= 1
    assert readiness["capability_agreement"]["measured"] is False


def test_readiness_reports_not_yet_evaluated_by_default(client):
    readiness = client.get("/api/economics/readiness").json()
    assert readiness["capability_agreement"] == {
        "measured": False, "value": None, "n": 0, "note": "no capability_gold_judgment rows labelled yet",
    }
    assert readiness["compensation_sample_sufficiency"] == "insufficient"


def test_rebuild_is_safe_to_repeat_with_no_data(client):
    first = client.post("/api/economics/rebuild").json()
    second = client.post("/api/economics/rebuild").json()
    assert first["archetype_demand"]["computed"] == 0
    assert second["archetype_demand"]["computed"] == 0


def test_gap_value_endpoint_reflects_rebuild(client):
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
            "VALUES ('capability', 'Gap Value Route Test', 'active', 'curator', now()) RETURNING id"
        )
        cap_id = str(cur.fetchone()["id"])
        cur.execute("INSERT INTO jobber.capability_detail (concept_id, demonstration_standard) VALUES (%s, %s)", (cap_id, "std"))

    archetype_resp = client.post("/api/archetypes", json={"canonical_name": "Gap Value Route Archetype"})
    archetype_id = archetype_resp.json()["id"]
    with db.db_cursor() as cur:
        role_id = str(db.upsert_role_instance(cur, None, {"instance_type": "observed_posting", "title": "x"}, skills=[]))
        cur.execute("UPDATE jobber.role_instance SET archetype_concept_id = %s WHERE id = %s", (archetype_id, role_id))
        cur.execute(
            "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis) VALUES (%s, %s, 'required', 'user_asserted')",
            (role_id, cap_id),
        )
        market_id = str(_market(cur))
        cur.execute(
            "INSERT INTO jobber.compensation_observation (source_key, archetype_concept_id, market_id, component, "
            "pay_period, currency, basis, review_status) VALUES ('gv-route-test', %s, %s, 'base', 'annual', 'GBP', 'curator_asserted', 'accepted')",
            (archetype_id, market_id),
        )

    rebuild_resp = client.post("/api/economics/rebuild")
    assert rebuild_resp.status_code == 200

    listing = client.get("/api/economics/gap-value", params={"market_id": market_id, "currency": "GBP"}).json()
    row = next(r for r in listing if r["capability_concept_id"] == cap_id)
    assert row["roles_unlocked"] == 1
    assert row["rank"] is not None

    single = client.get(f"/api/economics/gap-value/{cap_id}", params={"market_id": market_id, "currency": "GBP"}).json()
    assert single["capability_concept_id"] == cap_id
    assert "trace" in single


def _market(cur):
    cur.execute("INSERT INTO jobber.market (code, label) VALUES ('gap-value-route-market', 'Gap Value Route Market') RETURNING id")
    return cur.fetchone()["id"]


def test_gap_value_missing_row_returns_404(client):
    with db.db_cursor() as cur:
        cur.execute("INSERT INTO jobber.market (code, label) VALUES ('empty-market', 'Empty Market') RETURNING id")
        market_id = str(cur.fetchone()["id"])
    resp = client.get(f"/api/economics/gap-value/00000000-0000-0000-0000-000000000000", params={"market_id": market_id, "currency": "GBP"})
    assert resp.status_code == 404
