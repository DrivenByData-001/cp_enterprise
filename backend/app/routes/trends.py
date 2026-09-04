from fastapi import APIRouter, HTTPException, Query

from .. import trends
from ..db import db_cursor

router = APIRouter(prefix="/api/trends", tags=["trends"])


def _filters(
    year_from: int | None, year_to: int | None, country: str | None, seniority_level: str | None, career_track: str | None
) -> trends.TrendFilters:
    return trends.TrendFilters(
        year_from=year_from, year_to=year_to, country=country, seniority_level=seniority_level, career_track=career_track
    )


def _requirement_key(concept_id: str | None, surface_form: str | None) -> dict:
    if bool(concept_id) == bool(surface_form):
        raise HTTPException(400, "pass exactly one of concept_id or surface_form")
    return {"concept_id": concept_id} if concept_id else {"surface_form": surface_form}


@router.get("/overview")
def overview(
    year_from: int | None = None, year_to: int | None = None, country: str | None = None,
    seniority_level: str | None = None, career_track: str | None = None,
):
    with db_cursor() as cur:
        return trends.corpus_overview(cur, _filters(year_from, year_to, country, seniority_level, career_track))


@router.get("/top-requirements")
def top_requirements_route(
    year_from: int | None = None, year_to: int | None = None, country: str | None = None,
    seniority_level: str | None = None, career_track: str | None = None,
    min_sample_size: int = Query(5, ge=1), limit: int = Query(30, ge=1, le=200),
):
    with db_cursor() as cur:
        return trends.top_requirements(
            cur, _filters(year_from, year_to, country, seniority_level, career_track),
            min_sample_size=min_sample_size, limit=limit,
        )


@router.get("/requirement-trend")
def requirement_trend_route(
    concept_id: str | None = None, surface_form: str | None = None,
    year_from: int | None = None, year_to: int | None = None, country: str | None = None,
    seniority_level: str | None = None, career_track: str | None = None,
    granularity: str = Query("year", pattern="^(year|5year)$"), min_sample_size: int = Query(5, ge=1),
):
    key = _requirement_key(concept_id, surface_form)
    with db_cursor() as cur:
        return trends.requirement_trend(
            cur, key, _filters(year_from, year_to, country, seniority_level, career_track),
            granularity=granularity, min_sample_size=min_sample_size,
        )


@router.get("/cooccurrence")
def cooccurrence_route(
    concept_id: str | None = None, surface_form: str | None = None,
    year_from: int | None = None, year_to: int | None = None, country: str | None = None,
    seniority_level: str | None = None, career_track: str | None = None,
    min_count: int = Query(3, ge=1), limit: int = Query(15, ge=1, le=100),
):
    key = _requirement_key(concept_id, surface_form)
    with db_cursor() as cur:
        return trends.cooccurring_requirements(
            cur, key, _filters(year_from, year_to, country, seniority_level, career_track),
            min_count=min_count, limit=limit,
        )


@router.get("/compare")
def compare_route(
    dimension: str = Query(..., pattern="^(country|seniority_level|career_track)$"),
    concept_id: str | None = None, surface_form: str | None = None,
    year_from: int | None = None, year_to: int | None = None, country: str | None = None,
    seniority_level: str | None = None, career_track: str | None = None,
    min_sample_size: int = Query(5, ge=1),
):
    key = _requirement_key(concept_id, surface_form)
    with db_cursor() as cur:
        return trends.compare_dimension(
            cur, key, _filters(year_from, year_to, country, seniority_level, career_track),
            dimension=dimension, min_sample_size=min_sample_size,
        )


@router.get("/methodology")
def methodology_route():
    return {
        "text": trends.TREND_METHODOLOGY,
        "sparse_min_sample": trends.SPARSE_MIN_SAMPLE,
        "emerging_early_max_proportion": trends.EMERGING_EARLY_MAX_PROPORTION,
        "change_relative_threshold": trends.CHANGE_RELATIVE_THRESHOLD,
    }
