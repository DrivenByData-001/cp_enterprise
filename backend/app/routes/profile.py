"""Read-only: profile360 is the authoritative person-side store (docs/14 §9)
— jobber.profile_snapshots does not exist, and this app no longer accepts a
narrative write here. Writing/updating the narrative is profile360's own
tool's job now; this page only displays its current snapshot. The embedding
used for similarity elsewhere in this app (Dashboard/Space/Targets) is
computed from this same snapshot's text on demand — see
`embeddings.ensure_profile_embedding` — not stored as a fact here.
"""

from fastapi import APIRouter, HTTPException

from .. import profile360_reader as p360
from ..db import db_cursor


def _with_display(row: dict) -> dict:
    return {**row, "id": str(row["id"]), "_display": p360.display_text(row)}


router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
def get_current_profile():
    with db_cursor() as cur:
        try:
            snapshot = p360.get_current_snapshot(cur)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    return _with_display(snapshot) if snapshot else None


@router.get("/history")
def get_profile_history():
    with db_cursor() as cur:
        try:
            snapshots = p360.list_snapshots(cur)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    return [_with_display(s) for s in snapshots]
