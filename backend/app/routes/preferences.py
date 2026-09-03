from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import db_cursor

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class PreferenceObservationCreate(BaseModel):
    dimension_code: str
    direction: str  # toward | away | neutral
    strength: int  # 1-3
    basis: str  # observed_behavior | user_stated | repeated_episode_evidence | validated_psychometric | typology_hypothesis
    source_label: Optional[str] = None
    episode_id: Optional[int] = None
    confidence: str = "low"  # low | medium | high
    occurred_at: Optional[str] = None
    note: Optional[str] = None


@router.get("/dimensions")
def list_dimensions():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.preference_dimension ORDER BY sort_order")
        return cur.fetchall()


@router.get("")
def list_observations(dimension_code: str | None = None):
    query = "SELECT * FROM jobber.preference_observation"
    params: list = []
    if dimension_code:
        query += " WHERE dimension_code = %s"
        params.append(dimension_code)
    query += " ORDER BY recorded_at DESC"
    with db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


@router.post("")
def create_observation(payload: PreferenceObservationCreate):
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.preference_dimension WHERE code = %s", (payload.dimension_code,))
        if not cur.fetchone():
            raise HTTPException(400, "unknown dimension_code")
        cur.execute(
            """
            INSERT INTO jobber.preference_observation
                (dimension_code, direction, strength, basis, source_label, episode_id,
                 confidence, occurred_at, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                payload.dimension_code, payload.direction, payload.strength, payload.basis,
                payload.source_label, payload.episode_id, payload.confidence, payload.occurred_at, payload.note,
            ),
        )
        new_id = cur.fetchone()["id"]
    return {"id": new_id, "status": "created"}
