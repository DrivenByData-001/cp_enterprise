"""Vocabulary proposal prioritisation and curation API (Vocabulary Proposal
Prioritisation and Curation UX brief). Mounted at `/api/vocabulary`, distinct
from the legacy per-surface-form/per-cluster endpoints under
`/api/concepts/proposals*` (routes/concepts.py, unchanged) — this is the new
prioritised, evidence-rich, cluster-level review workflow described in the
brief; the legacy endpoints remain exactly as they were for backward
compatibility and are not used by the new Vocabulary UI going forward.

All mutating endpoints here are plain, explicit, user-triggered HTTP calls —
there is no scheduled job, startup hook, or "accept based on priority"
pathway anywhere in this router (brief §6/§12): nothing here ever curates
anything unless a human calls one of these endpoints directly.
"""

from fastapi import APIRouter, HTTPException, Query

from .. import vocabulary_curation as curation
from ..db import db_cursor
from ..models import ClusterAcceptRequest, ClusterBatchRequest, ClusterMergeRequest, ClusterRejectRequest
from ..vocabulary_priority import METHODOLOGY_TEXT, PRIORITY_BANDS

router = APIRouter(prefix="/api/vocabulary", tags=["vocabulary"])


@router.get("/methodology")
def methodology():
    """The full, deterministic prioritisation methodology as text (brief §2:
    "the ranking must be deterministic and documented"), plus the weight/
    threshold constants themselves so a caller never has to trust prose
    alone. Mirrors the existing `/api/trends/methodology` pattern."""
    from .. import vocabulary_priority as vp

    return {
        "text": METHODOLOGY_TEXT,
        "bands": list(PRIORITY_BANDS),
        "weights": {
            "role_count": vp.WEIGHT_ROLE_COUNT,
            "observation_count": vp.WEIGHT_OBSERVATION_COUNT,
            "year_count": vp.WEIGHT_YEAR_COUNT,
            "seniority_count": vp.WEIGHT_SENIORITY_COUNT,
            "country_count": vp.WEIGHT_COUNTRY_COUNT,
            "career_track_count": vp.WEIGHT_CAREER_TRACK_COUNT,
            "recency": vp.WEIGHT_RECENCY,
        },
        "recency_half_life_years": vp.RECENCY_HALF_LIFE_YEARS,
        "band_thresholds": {
            "sparse_max_role_count": vp.SPARSE_MAX_ROLE_COUNT,
            "medium_min_role_count": vp.MEDIUM_MIN_ROLE_COUNT,
            "high_min_role_count": vp.HIGH_MIN_ROLE_COUNT,
            "high_min_breadth_dimensions": vp.HIGH_MIN_BREADTH_DIMENSIONS,
            "breadth_dimension_min_distinct": vp.BREADTH_DIMENSION_MIN_DISTINCT,
        },
    }


@router.get("/progress")
def progress():
    """Curation progress counters (brief §11) — total/pending/accepted/
    rejected clusters, high-priority-pending count, and observation mapping
    coverage. `canonical_vocabulary_curated` is the one flag every other
    canonical-vocabulary-dependent view in this app should read before
    rendering a "no match" style message (brief §10)."""
    with db_cursor() as cur:
        return curation.get_progress(cur)


@router.get("/clusters")
def list_clusters(
    status: str = Query("pending", pattern="^(pending|accepted|rejected|all)$"),
    q: str | None = None,
    min_role_count: int | None = Query(None, ge=0),
    min_observation_count: int | None = Query(None, ge=0),
    observed_from: str | None = None,
    observed_to: str | None = None,
    country: str | None = None,
    seniority: str | None = None,
    type_code: str | None = None,
    band: str | None = Query(None, pattern="^(high|medium|low|sparse)$"),
    sort: str = Query("priority", pattern="^(priority|occurrence|role_count|recent|alphabetical)$"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """The curation queue: server-side filtered/sorted/paginated (brief §3 —
    "Do not load all 1,525 proposals into the browser"). Default view is
    `pending + highest priority first`, matching the brief's stated default
    exactly."""
    with db_cursor() as cur:
        return curation.list_clusters(
            cur, status=status, q=q, min_role_count=min_role_count, min_observation_count=min_observation_count,
            observed_from=observed_from, observed_to=observed_to, country=country, seniority=seniority,
            type_code=type_code, band=band, sort=sort, limit=limit, offset=offset,
        )


@router.get("/clusters/{cluster_key}")
def cluster_detail(cluster_key: str):
    """The expanded review card (brief §4) — same evidence as the queue row
    plus a larger sample of example roles."""
    with db_cursor() as cur:
        detail = curation.get_cluster_detail(cur, cluster_key)
    if detail is None:
        raise HTTPException(404, "unknown cluster_key")
    return detail


@router.post("/clusters/accept")
def accept_cluster(payload: ClusterAcceptRequest):
    with db_cursor() as cur:
        return curation.accept_cluster(
            cur, cluster_key=payload.cluster_key, type_code=payload.type_code,
            canonical_name=payload.canonical_name, definition=payload.definition,
        )


@router.post("/clusters/reject")
def reject_cluster(payload: ClusterRejectRequest):
    with db_cursor() as cur:
        return curation.reject_cluster(cur, cluster_key=payload.cluster_key)


@router.post("/clusters/merge")
def merge_cluster(payload: ClusterMergeRequest):
    with db_cursor() as cur:
        return curation.merge_cluster(cur, cluster_key=payload.cluster_key, concept_id=payload.concept_id)


@router.post("/clusters/batch/preview")
def preview_batch(payload: ClusterBatchRequest):
    """Read-only confirmation counts (brief §6) — call this before
    `/clusters/batch` and show the result to the user; never writes."""
    with db_cursor() as cur:
        return curation.preview_batch(cur, action=payload.action, items=[item.model_dump() for item in payload.items])


@router.post("/clusters/batch")
def execute_batch(payload: ClusterBatchRequest):
    """Executes the batch for real, inside one transaction (see
    `vocabulary_curation.execute_batch`'s docstring for the whole-batch
    atomicity guarantee: any failure rolls back every item in this call, not
    just the one that failed)."""
    with db_cursor() as cur:
        return curation.execute_batch(cur, action=payload.action, items=[item.model_dump() for item in payload.items])
