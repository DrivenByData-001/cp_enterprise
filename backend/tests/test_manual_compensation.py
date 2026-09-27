"""Phase 3 addendum: manually-recorded posting compensation — salary
discovered after a posting was captured, with no passage to quote. Reuses
the `compensation_observation` model (never legacy salary_min/salary_max)
and the same component/pay-period/amount/currency shape validation the
source-backed review path uses, but skips the source-corroboration checks
that don't apply when there is no quote at all.
"""

from app import db


def _posting(cur, *, country=None, currency=None, document_id=None, title="Head of Capital") -> str:
    columns = {
        "instance_type": "observed_posting", "title": title, "country": country,
        "currency": currency, "document_id": document_id,
    }
    return str(db.upsert_role_instance(cur, None, columns, skills=[]))


def test_accept_manual_compensation_becomes_the_role_headline(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur, country="United Kingdom")

    resp = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 120000, "amount_max": 145000, "note": "Told by recruiter on a call."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["basis"] == "posting_stated"
    assert body["review_status"] == "accepted"
    assert body["market_id"] is not None  # auto-resolved from the role's country

    resolved = client.get(f"/api/role-instances/{role_id}/compensation").json()
    assert resolved["compensation"]["basis"] == "advert_stated"
    assert resolved["compensation"]["amount_min"] == 120000
    assert resolved["compensation"]["amount_max"] == 145000
    assert resolved["compensation"]["currency"] == "GBP"


def test_manual_compensation_never_writes_legacy_salary_columns(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur, country="United Kingdom")

    client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 120000, "amount_max": 145000},
    )

    with db.db_cursor() as cur:
        cur.execute("SELECT salary_min, salary_max FROM jobber.role_instance WHERE id = %s", (role_id,))
        row = cur.fetchone()
    assert row["salary_min"] is None
    assert row["salary_max"] is None


def test_manual_compensation_requires_no_document(client):
    """The defining case: a role with no linked source document at all can
    still receive a manually-recorded figure — unlike the source-backed
    accept path, which requires one."""
    with db.db_cursor() as cur:
        role_id = _posting(cur, document_id=None)
    resp = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "day_rate", "pay_period": "daily", "currency": "GBP", "amount_min": 650, "amount_max": 650},
    )
    assert resp.status_code == 200


def test_manual_compensation_is_idempotent_on_resubmission(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    payload = {"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 100000, "amount_max": 110000}
    first = client.post(f"/api/role-instances/{role_id}/compensation/manual", json=payload).json()
    second = client.post(f"/api/role-instances/{role_id}/compensation/manual", json=payload).json()
    assert first["id"] == second["id"]
    assert first["created"] is True
    assert second["created"] is False
    with db.db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE role_instance_id = %s", (role_id,))
        assert cur.fetchone()["n"] == 1


def test_manual_compensation_supersedes_a_prior_manual_entry(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    first = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 100000, "amount_max": 110000},
    ).json()
    second = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 115000, "amount_max": 125000},
    ).json()
    assert second["id"] != first["id"]
    assert first["id"] in second["superseded_observation_ids"]

    observations = client.get(f"/api/role-instances/{role_id}/compensation").json()["observations"]
    statuses = {o["id"]: o["review_status"] for o in observations}
    assert statuses[first["id"]] == "rejected"
    assert statuses[second["id"]] == "accepted"


def test_manual_compensation_rejects_malformed_shape(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    resp = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 150000, "amount_max": 100000},
    )
    assert resp.status_code == 400
    assert "amount_min cannot be greater than amount_max" in resp.json()["detail"]


def test_manual_compensation_rejects_bonus_with_cash_amount(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    resp = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "bonus_pct", "pay_period": "annual", "bonus_pct": 15, "amount_min": 5000},
    )
    assert resp.status_code == 400


def test_manual_compensation_bonus_borrows_role_currency(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "EUR", "amount_min": 90000, "amount_max": 100000},
    )
    resp = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "bonus_pct", "pay_period": "annual", "bonus_pct": 10},
    )
    assert resp.status_code == 200
    with db.db_cursor() as cur:
        cur.execute(
            "SELECT currency FROM jobber.compensation_observation WHERE id = %s", (resp.json()["id"],),
        )
        assert cur.fetchone()["currency"] == "EUR"


def test_manual_compensation_on_missing_role_is_404(client):
    resp = client.post(
        "/api/role-instances/11111111-1111-1111-1111-111111111111/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 100000},
    )
    assert resp.status_code == 404


def test_correct_manual_compensation(client):
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    created = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 100000, "amount_max": 110000, "note": "Recruiter call."},
    ).json()

    corrected = client.patch(
        f"/api/role-instances/{role_id}/compensation/manual/{created['id']}",
        json={"amount_min": 105000, "amount_max": 115000},
    )
    assert corrected.status_code == 200
    body = corrected.json()
    assert body["status"] == "corrected"
    assert body["corrected_from_observation_id"] == created["id"]

    observations = client.get(f"/api/role-instances/{role_id}/compensation").json()["observations"]
    by_id = {o["id"]: o for o in observations}
    assert by_id[created["id"]]["review_status"] == "rejected"
    accepted = [o for o in observations if o["review_status"] == "accepted"]
    assert len(accepted) == 1
    assert float(accepted[0]["amount_min"]) == 105000
    assert accepted[0]["source_note"] == "Recruiter call."  # unset field preserved from the stored row


def test_correct_manual_compensation_refuses_a_quoted_observation(client, monkeypatch):
    """The manual correction endpoint is for quote-less observations only —
    a source-backed one is corrected through the standard endpoint instead,
    which keeps re-anchoring it against its own quote."""
    from app import ai, posting_compensation

    with db.db_cursor() as cur:
        document_id, _ = db.create_document(cur, kind="job_posting", content_text="Salary: £120,000 - £145,000 per annum.", provenance_quality="original")
        role_id = _posting(cur, document_id=document_id)

    accepted = client.post(
        f"/api/role-instances/{role_id}/compensation/accept",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 120000, "amount_max": 145000, "evidence_span": "£120,000 - £145,000 per annum"},
    )
    assert accepted.status_code == 200
    observation_id = accepted.json()["id"]

    resp = client.patch(
        f"/api/role-instances/{role_id}/compensation/manual/{observation_id}",
        json={"amount_min": 121000},
    )
    assert resp.status_code == 409
    assert "source quote" in resp.json()["detail"]


def test_manual_compensation_note_field_is_free_text_not_a_verbatim_quote(client):
    """The whole point: no evidence_span field exists on this path, and a
    note is never checked against any document."""
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    resp = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={
            "component": "total_package", "pay_period": "annual", "currency": "USD",
            "amount_min": 200000, "amount_max": 220000,
            "note": "Hiring manager confirmed band verbally; not written anywhere.",
        },
    )
    assert resp.status_code == 200
    with db.db_cursor() as cur:
        cur.execute("SELECT source_note, evidence_span FROM jobber.compensation_observation WHERE id = %s", (resp.json()["id"],))
        row = cur.fetchone()
    assert row["source_note"] == "Hiring manager confirmed band verbally; not written anywhere."
    assert row["evidence_span"] is None


def test_reject_endpoint_works_on_a_manual_observation(client):
    """The existing generic reject endpoint is basis-agnostic — no changes
    were needed there for manual observations to be rejectable."""
    with db.db_cursor() as cur:
        role_id = _posting(cur)
    created = client.post(
        f"/api/role-instances/{role_id}/compensation/manual",
        json={"component": "base", "pay_period": "annual", "currency": "GBP", "amount_min": 100000, "amount_max": 110000},
    ).json()
    resp = client.post(f"/api/role-instances/{role_id}/compensation/{created['id']}/reject")
    assert resp.status_code == 200
    assert resp.json()["review_status"] == "rejected"
