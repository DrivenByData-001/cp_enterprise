"""Evidence-backed role comparison (brief §11/§16/§17/§28), upgraded to
Phase 3's capability engine. Status assignment lives in `app.capability_engine`
only — this route is presentation: it enriches the engine's trace with the
role-side document detail (unchanged Phase 2 shape) and returns the
structural picture (per-requirement status + blocking/unverified gaps)
before any score. `fit_score`/`embedding_similarity` are included but
deliberately secondary (brief §18/§19) — the frontend must not present them
as the headline result.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from datetime import date
from typing import Literal
from uuid import UUID

from ..comparison_service import build_role_comparison
from ..db import db_cursor
from ..profile360_promotion import Profile360PromotionError, promote_assertion_to_profile360

router = APIRouter(prefix="/api/comparison", tags=["comparison"])


class DevelopmentActionInput(BaseModel):
    concept_id: UUID
    title: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=10000)
    due_date: date | None = None
    # Build §9 (conservative transition cost): human planning fields only.
    # Nothing derives, estimates or defaults these, and recording them never
    # makes a development action into capability evidence — it has no path
    # into requirement_claim, person_capability_assertion or profile360.
    planned_start_date: date | None = None
    estimated_effort_hours: float | None = Field(default=None, gt=0, le=100000)

    @field_validator("title")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("An action title is required")
        return value.strip()


class DevelopmentActionUpdate(BaseModel):
    """Every field optional; only supplied fields change, so an existing
    caller that sends `status` alone keeps working unchanged."""

    status: Literal["open", "done"] | None = None
    planned_start_date: date | None = None
    estimated_effort_hours: float | None = Field(default=None, gt=0, le=100000)


@router.get("/role/{role_id}/actions")
def list_actions(role_id: UUID):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM jobber.development_action WHERE role_instance_id = %s ORDER BY created_at, id", (role_id,))
        return cur.fetchall()


@router.post("/role/{role_id}/actions")
def create_action(role_id: UUID, payload: DevelopmentActionInput):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Role not found")
        cur.execute("SELECT id FROM jobber.concept WHERE id = %s AND status = 'active'", (payload.concept_id,))
        if not cur.fetchone():
            raise HTTPException(400, "Choose an active concept")
        cur.execute("""INSERT INTO jobber.development_action
                           (role_instance_id, concept_id, title, note, due_date,
                            planned_start_date, estimated_effort_hours)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                    (role_id, payload.concept_id, payload.title, payload.note, payload.due_date,
                     payload.planned_start_date, payload.estimated_effort_hours))
        return cur.fetchone()


@router.patch("/role/{role_id}/actions/{action_id}")
def update_action(role_id: UUID, action_id: UUID, payload: DevelopmentActionUpdate):
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        with db_cursor() as cur:
            cur.execute("SELECT * FROM jobber.development_action WHERE id = %s AND role_instance_id = %s",
                        (action_id, role_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Action not found on this role")
            return row
    set_clause = ", ".join(f"{key} = %s" for key in fields)
    with db_cursor() as cur:
        cur.execute(f"UPDATE jobber.development_action SET {set_clause}, updated_at = now() "
                    "WHERE id = %s AND role_instance_id = %s RETURNING *",
                    [*fields.values(), action_id, role_id])
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Action not found on this role")
        return row


@router.delete("/role/{role_id}/actions/{action_id}")
def delete_action(role_id: UUID, action_id: UUID):
    with db_cursor() as cur:
        cur.execute("DELETE FROM jobber.development_action WHERE id = %s AND role_instance_id = %s", (action_id, role_id))
        if not cur.rowcount:
            raise HTTPException(404, "Action not found on this role")
    return {"status": "deleted"}


@router.get("/role/{role_instance_id}")
def compare_role(role_instance_id: str):
    with db_cursor() as cur:
        return build_role_comparison(cur, role_instance_id)


class AssertCapability(BaseModel):
    concept_id: str
    note: str | None = None


@router.post("/assert")
def assert_capability(payload: AssertCapability):
    """The one-click "I have done this" action (doc 11 §5.3) — records that
    the user asserts this concept with no document behind it. Not a claim:
    see jobber.person_capability_assertion / docs/14 §6 for why this is
    deliberately not profile360's concern, and a TEMPORARY navigation
    override only — see `promote` below for the path into profile360's own
    review pipeline. Unchanged from Phase 2."""
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM jobber.concept WHERE id = %s AND status = 'active'", (payload.concept_id,))
        if not cur.fetchone():
            raise HTTPException(400, "concept does not exist or is not active")
        cur.execute(
            """
            INSERT INTO jobber.person_capability_assertion (jobber_concept_id, asserted, note)
            VALUES (%s, TRUE, %s)
            ON CONFLICT (jobber_concept_id) DO UPDATE SET asserted = TRUE, note = EXCLUDED.note
            RETURNING id
            """,
            (payload.concept_id, payload.note),
        )
        new_id = str(cur.fetchone()["id"])
    return {"id": new_id, "status": "asserted"}


@router.delete("/assert/{concept_id}")
def retract_assertion(concept_id: str):
    with db_cursor() as cur:
        cur.execute("DELETE FROM jobber.person_capability_assertion WHERE jobber_concept_id = %s", (concept_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "no assertion for this concept")
    return {"status": "retracted"}


@router.post("/assert/{concept_id}/promote")
def promote_assertion(concept_id: str):
    """Promotes a jobber-local assertion into profile360's own review
    pipeline (profile360.manual_import_queue, confirmed schema — see
    app/profile360_promotion.py) so profile360 can review and confirm it on
    its own terms."""
    with db_cursor() as cur:
        cur.execute("SELECT id FROM jobber.person_capability_assertion WHERE jobber_concept_id = %s", (concept_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "no assertion for this concept")
        try:
            return promote_assertion_to_profile360(cur, str(row["id"]))
        except Profile360PromotionError as e:
            raise HTTPException(503, str(e)) from e
