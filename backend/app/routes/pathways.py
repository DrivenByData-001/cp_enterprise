"""Pathways API (build §7/§13) and the personal-earnings / planning-assumption
endpoints the whole economic layer reads from (build §1).

Every GET here is a pure read that never calls an AI provider — Pathways is
composed from already-derived structures, so opening it can never fan out to
the model. The one mutating endpoint is the planning assumption, which the
user sets explicitly.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import db_cursor
from ..pathways import PathwaysTargetError, list_market_contexts, pathways_for_target
from ..personal_earnings import (
    PersonalEarningsUnavailableError,
    load_planning_assumption,
    personal_earnings_state,
    save_planning_assumption,
)

router = APIRouter(prefix="/api/pathways", tags=["pathways"])


class PlanningAssumptionUpdate(BaseModel):
    """`contract_billable_days_per_year = None` clears the assumption, which
    immediately withdraws every derived planning equivalent. Bounded at the
    schema level as well as by the table's CHECK constraint so a bad value
    fails as a 422 with a readable message rather than a database error."""

    contract_billable_days_per_year: float | None = Field(default=None, gt=0, le=366)
    note: str | None = Field(default=None, max_length=500)


@router.get("/market-contexts")
def market_contexts():
    """(market, currency) pairs with accepted compensation evidence — the
    meaningful choices for the Pathways market selector. Declared before the
    dynamic route below so it is never swallowed by it."""
    with db_cursor() as cur:
        return list_market_contexts(cur)


@router.get("/personal-earnings")
def get_personal_earnings():
    """Current or latest-known personal earnings, derived read-only from
    accepted profile360 compensation evidence."""
    with db_cursor() as cur:
        try:
            return personal_earnings_state(cur)
        except PersonalEarningsUnavailableError as e:
            raise HTTPException(503, str(e)) from e


@router.get("/planning-assumptions")
def get_planning_assumptions():
    with db_cursor() as cur:
        return load_planning_assumption(cur)


@router.put("/planning-assumptions")
def put_planning_assumptions(payload: PlanningAssumptionUpdate):
    with db_cursor() as cur:
        return save_planning_assumption(
            cur,
            contract_billable_days_per_year=payload.contract_billable_days_per_year,
            note=payload.note,
        )


@router.get("/{target_id}")
def get_pathways(target_id: str, market_id: str | None = None, currency: str | None = None):
    """The composed Pathways answer for one target: the direct route, useful
    one-step intermediate archetypes with their supporting postings, the
    economic comparison against the user's own earnings, and the market
    option value of the target's outstanding capability gaps.

    A cold request recomputes and caches; a warm one is a single indexed
    read. Neither calls the model."""
    with db_cursor() as cur:
        try:
            return pathways_for_target(cur, target_id, market_id=market_id, currency=currency)
        except PathwaysTargetError as e:
            raise HTTPException(404, str(e)) from e
