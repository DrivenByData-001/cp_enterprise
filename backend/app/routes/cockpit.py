"""Phase 9: Career Cockpit (docs/40) — the Home page's one composed read,
plus the explicit progress-checkpoint actions. Every GET here is read-only,
no AI, no side effects (build §16/§37); the only writes in this router are
the explicit POST below and the promotion endpoints in routes/applications.py.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import career_progress, career_directions
from ..career_cockpit import build_cockpit
from ..db import db_cursor

router = APIRouter(prefix="/api/cockpit", tags=["cockpit"])


@router.get("")
def get_cockpit():
    return build_cockpit()


@router.get("/progress")
def get_progress():
    with db_cursor() as cur:
        direction = career_directions.get_selected_direction_summary(cur)
        return career_progress.read_progress(cur, direction)


class CheckpointCreateInput(BaseModel):
    label: str | None = Field(default=None, max_length=200)


@router.post("/progress/checkpoints")
def create_progress_checkpoint(payload: CheckpointCreateInput = CheckpointCreateInput()):
    """Explicit user action only (build §5) — never called from a GET. State
    is always derived server-side from the currently selected Direction/
    Target; the client may only supply an optional label."""
    with db_cursor() as cur:
        direction = career_directions.get_selected_direction_summary(cur)
        if direction is None:
            raise HTTPException(409, "select a Career Direction before recording a progress checkpoint")
        try:
            return career_progress.record_checkpoint(cur, direction, label=payload.label)
        except career_progress.CareerProgressTargetError as e:
            raise HTTPException(409, str(e)) from e
