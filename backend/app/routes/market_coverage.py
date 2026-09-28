"""Phase 8: Market Coverage & Confidence API (docs/39). Every route here is
a bounded, read-only GET over `app/market_coverage.py` — no AI, no writes,
no automatic rebuild (build §16/§30)."""

from fastapi import APIRouter, HTTPException

from ..db import db_cursor
from ..market_coverage import CoverageScope, archetype_coverage_detail, build_coverage_summary

router = APIRouter(prefix="/api/market-coverage", tags=["market-coverage"])


def _scope(year_from: int | None, year_to: int | None, country: str | None, seniority_level: str | None, archetype_id: str | None) -> CoverageScope:
    return CoverageScope(year_from=year_from, year_to=year_to, country=country, seniority_level=seniority_level, archetype_id=archetype_id)


@router.get("/summary")
def get_summary(
    year_from: int | None = None,
    year_to: int | None = None,
    country: str | None = None,
    seniority_level: str | None = None,
    archetype_id: str | None = None,
):
    with db_cursor() as cur:
        return build_coverage_summary(cur, _scope(year_from, year_to, country, seniority_level, archetype_id))


@router.get("/archetypes/{archetype_concept_id}")
def get_archetype_detail(
    archetype_concept_id: str,
    year_from: int | None = None,
    year_to: int | None = None,
    country: str | None = None,
    seniority_level: str | None = None,
):
    """Bounded single-archetype drill-down (build §16), scoped by the same
    filters as `/summary` (minus `archetype_id`, which this route's own path
    parameter supplies) so a detail view opened from the summary table never
    disagrees with the row it was opened from."""
    scope = _scope(year_from, year_to, country, seniority_level, None)
    with db_cursor() as cur:
        detail = archetype_coverage_detail(cur, archetype_concept_id, scope)
    if not detail["found"]:
        raise HTTPException(404, "no such archetype")
    return detail
