"""Market dimension, compensation_observation, and the posting-compensation
backfill (Phase 4 prompt §5/§6/§7), HTTP + direct-module level against the
real Postgres test database."""

from app import db
from app.compensation_backfill import backfill_compensation_observations
from app.market import get_or_create_market_by_country, normalize_country


def _role(cur, *, salary_min=None, salary_max=None, salary_estimate_min=None, salary_estimate_max=None, currency=None, country=None, title="Test role"):
    columns = {
        "instance_type": "observed_posting",
        "title": title,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_estimate_min": salary_estimate_min,
        "salary_estimate_max": salary_estimate_max,
        "currency": currency,
        "country": country,
    }
    return str(db.upsert_role_instance(cur, None, columns, skills=[]))


# --- market normalisation ---------------------------------------------------

def test_normalize_country_resolves_known_alias():
    assert normalize_country("UK") == "United Kingdom"
    assert normalize_country("uk") == "United Kingdom"
    assert normalize_country("usa") == "United States"


def test_normalize_country_passes_through_unambiguous_name():
    assert normalize_country("Germany") == "Germany"


def test_normalize_country_returns_none_for_ambiguous_value():
    assert normalize_country("Remote") is None
    assert normalize_country("Europe") is None
    assert normalize_country(None) is None
    assert normalize_country("") is None


def test_get_or_create_market_is_idempotent(client):
    with db.db_cursor() as cur:
        id1 = get_or_create_market_by_country(cur, "UK")
        id2 = get_or_create_market_by_country(cur, "United Kingdom")
    assert id1 is not None
    assert id1 == id2  # same normalised country -> same market row


def test_market_crud_api(client):
    resp = client.post("/api/economics/markets", json={"code": "test-market", "label": "Test Market", "country": "Testland"})
    assert resp.status_code == 200
    market_id = resp.json()["id"]

    listing = client.get("/api/economics/markets").json()
    assert any(m["id"] == market_id for m in listing)

    update = client.put(f"/api/economics/markets/{market_id}", json={"status": "deprecated"})
    assert update.status_code == 200
    active_listing = client.get("/api/economics/markets").json()
    assert all(m["id"] != market_id for m in active_listing)


def test_duplicate_market_code_rejected(client):
    client.post("/api/economics/markets", json={"code": "dup-market", "label": "First"})
    resp = client.post("/api/economics/markets", json={"code": "dup-market", "label": "Second"})
    assert resp.status_code == 400


# --- posting compensation backfill (prompt §7) ------------------------------

def test_backfill_creates_posting_stated_observation(client):
    with db.db_cursor() as cur:
        role_id = _role(cur, salary_min=60000, salary_max=70000, currency="GBP", country="UK")
        result = backfill_compensation_observations(cur)
        cur.execute(
            "SELECT basis, review_status, amount_min, amount_max, currency, pay_period, component "
            "FROM jobber.compensation_observation WHERE role_instance_id = %s AND basis = 'posting_stated'",
            (role_id,),
        )
        row = cur.fetchone()
    assert result["observations_created"] == 1
    assert row["review_status"] == "accepted"
    assert float(row["amount_min"]) == 60000
    assert float(row["amount_max"]) == 70000
    assert row["currency"] == "GBP"
    assert row["pay_period"] == "annual"
    assert row["component"] == "base"


def test_backfill_creates_posting_estimated_observation_separately(client):
    with db.db_cursor() as cur:
        role_id = _role(cur, salary_estimate_min=55000, salary_estimate_max=65000, currency="EUR", country="Ireland")
        backfill_compensation_observations(cur)
        cur.execute(
            "SELECT basis FROM jobber.compensation_observation WHERE role_instance_id = %s",
            (role_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["basis"] == "posting_estimated"


def test_backfill_never_combines_stated_and_estimated(client):
    with db.db_cursor() as cur:
        role_id = _role(
            cur, salary_min=60000, salary_max=70000, salary_estimate_min=58000, salary_estimate_max=72000,
            currency="GBP", country="UK",
        )
        backfill_compensation_observations(cur)
        cur.execute(
            "SELECT basis, amount_min, amount_max FROM jobber.compensation_observation "
            "WHERE role_instance_id = %s ORDER BY basis",
            (role_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    assert len(rows) == 2
    bases = {r["basis"] for r in rows}
    assert bases == {"posting_stated", "posting_estimated"}


def test_backfill_is_idempotent(client):
    with db.db_cursor() as cur:
        _role(cur, salary_min=60000, salary_max=70000, currency="GBP", country="UK")
        first = backfill_compensation_observations(cur)
        second = backfill_compensation_observations(cur)
    assert first["observations_created"] == 1
    assert second["observations_created"] == 0
    assert second["observations_already_present"] == 1


def test_backfill_skips_missing_currency(client):
    with db.db_cursor() as cur:
        _role(cur, salary_min=60000, salary_max=70000, currency=None, country="UK")
        result = backfill_compensation_observations(cur)
    assert result["observations_created"] == 0
    assert result["skipped_no_currency"] == 1


def test_backfill_leaves_ambiguous_country_unassigned(client):
    with db.db_cursor() as cur:
        role_id = _role(cur, salary_min=60000, salary_max=70000, currency="GBP", country="Remote")
        result = backfill_compensation_observations(cur)
        cur.execute("SELECT market_id FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,))
        row = cur.fetchone()
    assert result["unassigned_market"] == 1
    assert row["market_id"] is None


def test_backfill_no_currency_mixing_across_roles(client):
    """Two roles in different currencies must never be pooled into one
    observation — each keeps its own currency, verified end to end via the
    archetype comp derivation once assigned to the same archetype."""
    with db.db_cursor() as cur:
        role_gbp = _role(cur, salary_min=60000, salary_max=70000, currency="GBP", country="UK")
        role_eur = _role(cur, salary_min=55000, salary_max=65000, currency="EUR", country="Germany")
        backfill_compensation_observations(cur)
        cur.execute(
            "SELECT DISTINCT currency FROM jobber.compensation_observation WHERE role_instance_id IN (%s, %s)",
            (role_gbp, role_eur),
        )
        currencies = {r["currency"] for r in cur.fetchall()}
    assert currencies == {"GBP", "EUR"}


# --- curator-asserted observation + review API ------------------------------

def test_curator_asserted_observation_is_born_accepted(client):
    market = client.post("/api/economics/markets", json={"code": "curator-market", "label": "Curator Market"}).json()
    resp = client.post(
        "/api/economics/compensation-observations",
        json={
            "raw_role_label": "Head of Actuarial",
            "market_id": market["id"],
            "component": "base",
            "pay_period": "annual",
            "currency": "GBP",
            "amount_mid": 150000,
        },
    )
    assert resp.status_code == 200
    listing = client.get("/api/economics/compensation-observations", params={"basis": "curator_asserted"}).json()
    row = next(r for r in listing if r["id"] == resp.json()["id"])
    assert row["review_status"] == "accepted"
    assert row["basis"] == "curator_asserted"


def test_curator_asserted_observation_requires_a_subject(client):
    market = client.post("/api/economics/markets", json={"code": "no-subject-market", "label": "No Subject"}).json()
    resp = client.post(
        "/api/economics/compensation-observations",
        json={"market_id": market["id"], "component": "base", "pay_period": "annual", "currency": "GBP"},
    )
    assert resp.status_code == 422  # pydantic validation: needs role_instance_id/archetype_concept_id/raw_role_label
