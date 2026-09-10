"""Idempotent posting-compensation backfill (Phase 4 prompt §7).

Projects existing `role_instance` salary fields into the new
`compensation_observation` evidence layer. This is a projection, not a
migration: `role_instance.salary_min/max/salary_estimate_min/max/currency`
are never modified, and re-running this against unchanged source data
creates zero new rows (idempotent via `source_key`, `ON CONFLICT DO
NOTHING` — a rerun never overwrites a row a curator may since have
corrected via the compensation-observation review endpoints).

`salary_min`/`salary_max` and `salary_estimate_min`/`salary_estimate_max`
always become two separate observations (`posting_stated` /
`posting_estimated`), never combined — stated and estimated must stay
distinguishable forever (prompt §16), and only `posting_stated` may ever
satisfy the benchmark-selection sample-size gate (§10/§11 in
`economics_engine.py`).

Backfilled rows are born `review_status='accepted'`: they are a
deterministic, mechanical projection of already-captured structured fields,
not a new AI claim requiring human review — unlike survey extraction
(`market_data_processing.py`), which always writes `unreviewed` rows.

Market assignment is explicit and deterministic (`market.py`): derived from
`role_instance.country` through a documented alias map for obvious cases
only. A role whose country cannot be safely resolved keeps
`market_id = NULL` — retained as an unassigned/unusable-for-aggregation
observation rather than guessed, per prompt §7.

Posting salary fields carry no stored pay-period marker in this schema, and
every extraction prompt in this codebase (`prompts/extract_job_posting.md`)
asks for a plain salary range with no unit distinction — the overwhelming
convention for a salaried posting. This backfill therefore treats every
projected row as `pay_period='annual'`; a genuinely day-rate historical
posting, if one exists in the corpus, is not detected here and would need
correcting via the compensation-observation review UI. This is a stated
limitation, not a silent assumption — see
docs/24-phase4-economics-and-vocabulary-overview.md.
"""

from .market import get_or_create_market_by_country


def _insert_observation(cur, *, source_key, role_instance_id, document_id, market_id, currency, amount_min, amount_max, basis, observed_at) -> bool:
    """Returns True if a new row was inserted, False if `source_key` already
    existed (idempotent — never overwrites)."""
    cur.execute(
        """
        INSERT INTO jobber.compensation_observation
            (source_key, role_instance_id, document_id, market_id, component, pay_period, currency,
             amount_min, amount_max, basis, review_status, observed_at)
        VALUES (%s, %s, %s, %s, 'base', 'annual', %s, %s, %s, %s, 'accepted', %s)
        ON CONFLICT (source_key) DO NOTHING
        RETURNING id
        """,
        (source_key, role_instance_id, document_id, market_id, currency, amount_min, amount_max, basis, observed_at),
    )
    return cur.fetchone() is not None


def backfill_compensation_observations(cur) -> dict:
    """Safe to repeat. Returns a summary dict — never raises for
    already-processed data."""
    cur.execute(
        "SELECT id, document_id, salary_min, salary_max, salary_estimate_min, salary_estimate_max, "
        "currency, country, posting_date "
        "FROM jobber.role_instance "
        "WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL "
        "OR salary_estimate_min IS NOT NULL OR salary_estimate_max IS NOT NULL"
    )
    roles = [dict(r) for r in cur.fetchall()]

    created = 0
    already_present = 0
    skipped_no_currency = 0
    unassigned_market = 0

    for role in roles:
        role_id = str(role["id"])
        document_id = str(role["document_id"]) if role["document_id"] else None
        currency = (role["currency"] or "").strip().upper() or None
        if not currency:
            skipped_no_currency += 1
            continue

        market_id = get_or_create_market_by_country(cur, role["country"])
        if market_id is None:
            unassigned_market += 1

        observed_at = role["posting_date"]

        if role["salary_min"] is not None or role["salary_max"] is not None:
            inserted = _insert_observation(
                cur,
                source_key=f"posting_stated:{role_id}",
                role_instance_id=role_id,
                document_id=document_id,
                market_id=market_id,
                currency=currency,
                amount_min=role["salary_min"],
                amount_max=role["salary_max"],
                basis="posting_stated",
                observed_at=observed_at,
            )
            created += 1 if inserted else 0
            already_present += 0 if inserted else 1

        if role["salary_estimate_min"] is not None or role["salary_estimate_max"] is not None:
            inserted = _insert_observation(
                cur,
                source_key=f"posting_estimated:{role_id}",
                role_instance_id=role_id,
                document_id=document_id,
                market_id=market_id,
                currency=currency,
                amount_min=role["salary_estimate_min"],
                amount_max=role["salary_estimate_max"],
                basis="posting_estimated",
                observed_at=observed_at,
            )
            created += 1 if inserted else 0
            already_present += 0 if inserted else 1

    return {
        "roles_considered": len(roles),
        "observations_created": created,
        "observations_already_present": already_present,
        "skipped_no_currency": skipped_no_currency,
        "unassigned_market": unassigned_market,
    }
