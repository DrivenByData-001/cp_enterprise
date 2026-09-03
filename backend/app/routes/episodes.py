"""Read-only: profile360 is the authoritative person-side store (docs/14 §9)
— jobber.episode does not exist, and this app no longer accepts episode
writes here. Authoring episodes is profile360's own tool's job now; this
page only browses its episodes.

The derived timeline/duration math doc 11 §5.4 specified (union-of-spans
years of experience, per-episode duration) is deliberately not rebuilt here —
`start_date`/`end_date` are confirmed columns (docs/14 §5), but that
derivation is new functionality, out of scope for this read path.
"""

from fastapi import APIRouter, HTTPException

from .. import profile360_reader as p360
from ..db import db_cursor


def _with_display(row: dict) -> dict:
    return {**row, "id": str(row["id"]), "_display": p360.episode_display(row)}


router = APIRouter(prefix="/api/episodes", tags=["episodes"])


@router.get("")
def list_episodes():
    with db_cursor() as cur:
        try:
            episodes = p360.list_episodes(cur)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    return [_with_display(e) for e in episodes]


@router.get("/{episode_id}")
def get_episode(episode_id: str):
    with db_cursor() as cur:
        try:
            episode = p360.get_episode(cur, episode_id)
        except p360.Profile360UnavailableError as e:
            raise HTTPException(503, str(e)) from e
    if not episode:
        raise HTTPException(404, "episode not found")
    return _with_display(episode)
