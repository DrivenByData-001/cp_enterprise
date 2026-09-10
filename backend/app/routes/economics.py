"""Phase 4 Economics: market dimension, compensation observations, and the
derived archetype-demand/archetype-comp/gap-value endpoints.

CRUD here follows the same inline-SQL convention `routes/capabilities.py`
already established; the actual derivation/rebuild logic lives in
`economics_engine.py`, mirroring how `capabilities.py` delegates to
`capability_engine.py`. GET endpoints here never rebuild anything silently
(prompt §15) — `/rebuild` is the one explicit, deterministic, safe-to-repeat
entrypoint.
"""

import psycopg
from fastapi import APIRouter, HTTPException

from .. import economics_engine as engine
from ..compensation_backfill import backfill_compensation_observations
from ..db import db_cursor
from ..models import CompensationObservationCreate, MarketCreate, MarketUpdate

router = APIRouter(prefix="/api/economics", tags=["economics"])


def _row(row) -> dict:
    row = dict(row)
    for key, value in list(row.items()):
        if key.endswith("_id") and value is not None:
            row[key] = str(value)
        elif key == "id" and value is not None:
            row[key] = str(value)
    return row


# --- Market (prompt §5) -----------------------------------------------------

@router.get("/markets")
def list_markets(status: str = "active"):
    query = "SELECT id, code, label, country, geography, domain_concept_id, status, notes, created_at FROM jobber.market"
    params: list = []
    if status != "all":
        query += " WHERE status = %s"
        params.append(status)
    query += " ORDER BY label"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = [_row(r) for r in cur.fetchall()]
    return rows


@router.post("/markets")
def create_market(payload: MarketCreate):
    with db_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO jobber.market (code, label, country, geography, domain_concept_id, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (payload.code, payload.label, payload.country, payload.geography, payload.domain_concept_id, payload.notes),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(400, "a market with this code already exists")
        market_id = str(cur.fetchone()["id"])
    return {"id": market_id, "status": "created"}


@router.put("/markets/{market_id}")
def update_market(market_id: str, payload: MarketUpdate):
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        return {"id": market_id, "status": "unchanged"}
    set_clause = ", ".join(f"{k} = %s" for k in fields) + ", updated_at = now()"
    with db_cursor() as cur:
        cur.execute(f"UPDATE jobber.market SET {set_clause} WHERE id = %s", [*fields.values(), market_id])
        if cur.rowcount == 0:
            raise HTTPException(404, "market not found")
    return {"id": market_id, "status": "updated"}


# --- Compensation observations -----------------------------------------------

@router.get("/compensation-observations")
def list_compensation_observations(
    basis: str | None = None,
    review_status: str | None = None,
    archetype_concept_id: str | None = None,
    market_id: str | None = None,
):
    query = (
        "SELECT id, role_instance_id, archetype_concept_id, raw_role_label, market_id, component, pay_period, "
        "employment_basis, amount_min, amount_mid, amount_max, currency, reported_p25, reported_p50, reported_p75, "
        "bonus_pct, basis, review_status, observed_at, document_id, reported_sample_size, source_note, "
        "created_at, reviewed_at FROM jobber.compensation_observation WHERE 1=1"
    )
    params: list = []
    if basis:
        query += " AND basis = %s"
        params.append(basis)
    if review_status:
        query += " AND review_status = %s"
        params.append(review_status)
    if archetype_concept_id:
        query += " AND archetype_concept_id = %s"
        params.append(archetype_concept_id)
    if market_id:
        query += " AND market_id = %s"
        params.append(market_id)
    query += " ORDER BY created_at DESC"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = [_row(r) for r in cur.fetchall()]
    return rows


@router.post("/compensation-observations")
def create_curator_observation(payload: CompensationObservationCreate):
    """A curator directly asserting a benchmark with no posting/survey
    document behind it — `basis` is always forced server-side to
    `curator_asserted`, born `review_status='accepted'` (the human making
    the assertion *is* the review)."""
    import uuid

    with db_cursor() as cur:
        if payload.archetype_concept_id:
            cur.execute(
                "SELECT 1 FROM jobber.concept WHERE id = %s AND type_code = 'role_archetype' AND status = 'active'",
                (payload.archetype_concept_id,),
            )
            if not cur.fetchone():
                raise HTTPException(400, "archetype_concept_id is not an active role_archetype")
        cur.execute("SELECT 1 FROM jobber.market WHERE id = %s", (payload.market_id,))
        if not cur.fetchone():
            raise HTTPException(400, "market_id does not exist")

        cur.execute(
            """
            INSERT INTO jobber.compensation_observation
                (source_key, role_instance_id, archetype_concept_id, raw_role_label, market_id, component,
                 pay_period, employment_basis, amount_min, amount_mid, amount_max, currency,
                 reported_p25, reported_p50, reported_p75, bonus_pct, basis, review_status,
                 observed_at, reported_sample_size, source_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'curator_asserted', 'accepted', %s, %s, %s)
            RETURNING id
            """,
            (
                f"curator_asserted:{uuid.uuid4()}", payload.role_instance_id, payload.archetype_concept_id,
                payload.raw_role_label, payload.market_id, payload.component, payload.pay_period,
                payload.employment_basis, payload.amount_min, payload.amount_mid, payload.amount_max,
                payload.currency.strip().upper(), payload.reported_p25, payload.reported_p50, payload.reported_p75,
                payload.bonus_pct, payload.observed_at, payload.reported_sample_size, payload.source_note,
            ),
        )
        observation_id = str(cur.fetchone()["id"])
    return {"id": observation_id, "status": "created"}


@router.post("/compensation-observations/backfill")
def backfill():
    """Idempotent projection of existing role_instance salary fields into
    compensation_observation (prompt §7). Safe to call repeatedly."""
    with db_cursor() as cur:
        return backfill_compensation_observations(cur)


# --- Derived: archetype demand / archetype comp / gap value ----------------

@router.get("/archetype-demand/{archetype_id}")
def get_archetype_demand(archetype_id: str):
    with db_cursor() as cur:
        cur.execute(
            "SELECT d.archetype_concept_id, d.capability_concept_id, c.canonical_name, d.roles_in_archetype, "
            "d.roles_demanding_capability, d.demand_rate, d.required_count, d.preferred_count, d.contextual_count, "
            "d.source_role_ids, d.trace, d.engine_version, d.computed_at "
            "FROM jobber.d_archetype_demand d JOIN jobber.concept c ON c.id = d.capability_concept_id "
            "WHERE d.archetype_concept_id = %s ORDER BY d.demand_rate DESC NULLS LAST, c.canonical_name",
            (archetype_id,),
        )
        rows = []
        for r in cur.fetchall():
            r = dict(r)
            r["archetype_concept_id"] = str(r["archetype_concept_id"])
            r["capability_concept_id"] = str(r["capability_concept_id"])
            r["source_role_ids"] = [str(x) for x in (r["source_role_ids"] or [])]
            rows.append(r)
    return rows


@router.get("/archetype-comp")
def list_archetype_comp(archetype_concept_id: str | None = None, market_id: str | None = None, currency: str | None = None):
    query = (
        "SELECT ac.archetype_concept_id, c.canonical_name AS archetype_name, ac.market_id, m.label AS market_label, "
        "ac.period_start, ac.period_end, ac.currency, ac.component, ac.pay_period, ac.n_observations, "
        "ac.n_posting_stated, ac.n_posting_estimated, ac.n_survey_sources, ac.posting_p25, ac.posting_p50, "
        "ac.posting_p75, ac.survey_benchmarks, ac.reference_comp, ac.reference_source, ac.reference_basis_detail, "
        "ac.trace, ac.engine_version, ac.computed_at "
        "FROM jobber.d_archetype_comp ac "
        "JOIN jobber.concept c ON c.id = ac.archetype_concept_id "
        "JOIN jobber.market m ON m.id = ac.market_id "
        "WHERE 1=1"
    )
    params: list = []
    if archetype_concept_id:
        query += " AND ac.archetype_concept_id = %s"
        params.append(archetype_concept_id)
    if market_id:
        query += " AND ac.market_id = %s"
        params.append(market_id)
    if currency:
        query += " AND ac.currency = %s"
        params.append(currency)
    query += " ORDER BY c.canonical_name, ac.component, ac.pay_period"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = [_row(r) for r in cur.fetchall()]
    return rows


@router.get("/gap-value/contexts")
def gap_value_contexts():
    """(market, currency) pairs with at least one accepted compensation
    observation — the meaningful choices for the Gap Value market/currency
    selector. Empty until compensation evidence exists; that is expected,
    not an error (prompt §21)."""
    with db_cursor() as cur:
        buckets = engine.discover_gap_value_buckets(cur)
        if not buckets:
            return []
        market_ids = list({b[0] for b in buckets})
        cur.execute("SELECT id, label FROM jobber.market WHERE id = ANY(%s::uuid[])", (market_ids,))
        labels = {str(r["id"]): r["label"] for r in cur.fetchall()}
    return [{"market_id": mid, "market_label": labels.get(mid, mid), "currency": currency} for mid, currency in buckets]


@router.get("/gap-value")
def list_gap_value(market_id: str, currency: str):
    with db_cursor() as cur:
        cur.execute(
            "SELECT gv.capability_concept_id, c.canonical_name, gv.market_id, gv.period_start, gv.period_end, "
            "gv.currency, gv.archetypes_unlocked, gv.archetypes_improved, gv.roles_unlocked, gv.roles_improved, "
            "gv.reference_comp_unlocked, gv.comp_delta_vs_best_current_reachable, gv.n_comp_observations, "
            "gv.evidence_quality, gv.rank, gv.trace, gv.engine_version, gv.computed_at "
            "FROM jobber.d_gap_value gv JOIN jobber.concept c ON c.id = gv.capability_concept_id "
            "WHERE gv.market_id = %s AND gv.currency = %s ORDER BY gv.rank",
            (market_id, currency),
        )
        rows = [_row(r) for r in cur.fetchall()]
    return rows


@router.get("/gap-value/{capability_id}")
def get_gap_value(capability_id: str, market_id: str, currency: str):
    with db_cursor() as cur:
        cur.execute(
            "SELECT gv.capability_concept_id, c.canonical_name, gv.market_id, gv.period_start, gv.period_end, "
            "gv.currency, gv.archetypes_unlocked, gv.archetypes_improved, gv.roles_unlocked, gv.roles_improved, "
            "gv.reference_comp_unlocked, gv.comp_delta_vs_best_current_reachable, gv.n_comp_observations, "
            "gv.evidence_quality, gv.rank, gv.trace, gv.engine_version, gv.computed_at "
            "FROM jobber.d_gap_value gv JOIN jobber.concept c ON c.id = gv.capability_concept_id "
            "WHERE gv.capability_concept_id = %s AND gv.market_id = %s AND gv.currency = %s",
            (capability_id, market_id, currency),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "no gap-value row for this capability/market/currency — rebuild economics, or this capability has no structural impact here")
    return _row(row)


# --- Rebuild / readiness -----------------------------------------------------

@router.post("/rebuild")
def rebuild():
    """Recomputes d_archetype_demand, d_archetype_comp, and d_gap_value
    (every discovered market/currency context) from source rows.
    Destructive-safe, not destructive — every derived row is replaced by a
    fresh computation, never emptied first."""
    with db_cursor() as cur:
        return engine.rebuild_phase4_derivations(cur)


@router.get("/readiness")
def readiness():
    with db_cursor() as cur:
        return engine.readiness_summary(cur)
