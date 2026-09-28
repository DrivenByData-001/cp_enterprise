"""Phase 6: Career Direction / property-first target discovery API (docs/37).
Own dedicated router under /api/career-directions — same "one small router
per feature" precedent routes/concept_dossier.py and routes/application_
artifacts.py already follow. Protected by the same central auth policy as
every other router (app/main.py).

Literal sub-paths (`/selected`, `/discover`, `/discovery-runs...`) are
registered before the `/{direction_id}` family so FastAPI never mistakes one
of them for a direction id.
"""

from fastapi import APIRouter, HTTPException

from .. import career_directions as directions
from ..db import db_cursor
from ..models import (
    CareerDirectionAdoptRequest,
    CareerDirectionCreate,
    CareerDirectionDiscoverRequest,
    CareerDirectionUpdate,
)

router = APIRouter(prefix="/api/career-directions", tags=["career-directions"])


# --- reads (never call AI) --------------------------------------------------


@router.get("")
def list_career_directions(state: str | None = None):
    with db_cursor() as cur:
        return {"items": directions.list_directions(cur, state=state)}


@router.get("/selected")
def read_selected_career_direction():
    """The one cheap read Home/`/future`/the Opportunity workspace each need
    to show the current direction — `null` when none is selected, never an
    error (build §23/§24)."""
    with db_cursor() as cur:
        return {"direction": directions.get_selected_direction_summary(cur)}


@router.get("/preference-summary")
def read_preference_summary():
    """Deterministic, AI-free preference summary (build §9) — the builder's
    "use my recorded preferences as a starting point" action reads this to
    populate its draft form. Never creates or saves anything."""
    with db_cursor() as cur:
        return {"dimensions": directions.preference_summary(cur)}


@router.get("/discovery-runs/{run_id}")
def read_discovery_run(run_id: str):
    with db_cursor() as cur:
        try:
            return directions.get_discovery_run(cur, run_id)
        except directions.CareerDirectionSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.get("/discovery-runs")
def list_recent_discovery_runs(limit: int = 20):
    with db_cursor() as cur:
        return {"items": directions.list_discovery_runs(cur, limit=limit)}


@router.get("/{direction_id}")
def read_career_direction(direction_id: str):
    with db_cursor() as cur:
        try:
            return directions.get_direction(cur, direction_id)
        except directions.CareerDirectionSubjectError as e:
            raise HTTPException(404, str(e)) from e


# --- manual create / update (never call AI; build §19) ---------------------


@router.post("")
def create_career_direction(payload: CareerDirectionCreate):
    with db_cursor() as cur:
        try:
            return directions.create_direction(cur, payload)
        except directions.CareerDirectionLinkError as e:
            raise HTTPException(400, str(e)) from e


@router.put("/{direction_id}")
@router.patch("/{direction_id}")
def update_career_direction(direction_id: str, payload: CareerDirectionUpdate):
    with db_cursor() as cur:
        try:
            return directions.update_direction(cur, direction_id, payload)
        except directions.CareerDirectionSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except directions.CareerDirectionLinkError as e:
            raise HTTPException(400, str(e)) from e


# --- explicit selection / archive / reopen (human-only; build §3/§7) -------


@router.post("/{direction_id}/select")
def select_career_direction(direction_id: str):
    with db_cursor() as cur:
        try:
            return directions.select_direction(cur, direction_id)
        except directions.CareerDirectionSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except directions.CareerDirectionStateError as e:
            raise HTTPException(409, str(e)) from e


@router.post("/{direction_id}/archive")
def archive_career_direction(direction_id: str):
    with db_cursor() as cur:
        try:
            return directions.archive_direction(cur, direction_id)
        except directions.CareerDirectionSubjectError as e:
            raise HTTPException(404, str(e)) from e


@router.post("/{direction_id}/reopen")
def reopen_career_direction(direction_id: str):
    with db_cursor() as cur:
        try:
            return directions.reopen_direction(cur, direction_id)
        except directions.CareerDirectionSubjectError as e:
            raise HTTPException(404, str(e)) from e
        except directions.CareerDirectionStateError as e:
            raise HTTPException(409, str(e)) from e


# --- explicit AI discovery (the only thing that calls AI; build §11) -------


@router.post("/discover")
def discover_career_directions(payload: CareerDirectionDiscoverRequest):
    """Normal GET endpoints above never call AI — this is the one explicit,
    user-triggered exception. A provider/config/schema/grounding failure
    creates no Career Direction, selects nothing, changes no Target, and
    leaves every existing direction untouched (build §11/§28)."""
    try:
        return directions.discover_candidates(payload)
    except directions.CareerDirectionLinkError as e:
        raise HTTPException(400, str(e)) from e
    except directions.CareerDirectionValidationError as e:
        raise HTTPException(422, str(e)) from e
    except directions.CareerDirectionGenerationError as e:
        directions.raise_for_generation_error(e)


@router.post("/discovery-runs/{run_id}/candidates/{candidate_id}/adopt")
def adopt_discovery_candidate(run_id: str, candidate_id: str, payload: CareerDirectionAdoptRequest = CareerDirectionAdoptRequest()):
    """Save one reviewed candidate as a Career Direction (build §18) — never
    calls AI, never selects automatically."""
    try:
        return directions.adopt_candidate(run_id, candidate_id, payload)
    except directions.CareerDirectionSubjectError as e:
        raise HTTPException(404, str(e)) from e
