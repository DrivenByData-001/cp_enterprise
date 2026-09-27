"""Phase 3: persistent Application workspace backbone (docs/34).

An application is a user workflow record tied to exactly one observed
posting — never a target (build §1), never a second copy of Profile360 or
comparison evidence. `status` is set by the user, never derived here; the
structural preparation picture the workspace shows is assembled live from
the existing capability/comparison engines (see `..comparison_service`) plus
this module's own persistence, not stored or scored.

No AI call anywhere in this module, no N+1 query patterns: the list endpoint
joins role metadata in one query, notes are always loaded in bulk per
application, and the evidence endpoint reuses the shared comparison service
rather than recomputing anything.
"""

from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from .. import application_events as events
from ..comparison_service import build_role_comparison
from ..db import db_cursor
from ..models import ApplicationEventInput, ApplicationEventUpdate

router = APIRouter(prefix="/api/applications", tags=["applications"])

# Active statuses (build §1) — the same set the partial unique index in
# migrations/0027_application_workspace.sql is defined over. Built into a
# literal SQL tuple once here rather than re-spelled at each call site, so
# the Python set and the index predicate can never quietly drift apart.
ACTIVE_STATUSES = ("preparing", "ready", "submitted", "interviewing")
_ACTIVE_STATUS_SQL = "(" + ", ".join(f"'{s}'" for s in ACTIVE_STATUSES) + ")"

ApplicationStatus = Literal["preparing", "ready", "submitted", "interviewing", "closed", "withdrawn"]
NoteType = Literal["general", "evidence_example"]


# --- shared helpers ----------------------------------------------------------


def _require_application(cur, application_id: UUID) -> dict:
    cur.execute("SELECT * FROM jobber.application WHERE id = %s", (str(application_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "application not found")
    return row


def _notes_for_application(cur, application_id: UUID) -> list[dict]:
    cur.execute(
        "SELECT * FROM jobber.application_note WHERE application_id = %s ORDER BY created_at",
        (str(application_id),),
    )
    return cur.fetchall()


def _require_active_concept(cur, concept_id) -> None:
    """Validates a supplied concept exists and is active without ever
    mutating jobber.concept/vocabulary state (build §2/§3) — the same
    presence+status check `routes/comparison.py::create_action` already uses
    for development-action concepts."""
    cur.execute("SELECT 1 FROM jobber.concept WHERE id = %s AND status = 'active'", (str(concept_id),))
    if not cur.fetchone():
        raise HTTPException(400, "concept does not exist or is not active")


# --- create / reopen ----------------------------------------------------------


class ApplicationCreateInput(BaseModel):
    role_instance_id: UUID


@router.post("")
def create_or_reopen_application(payload: ApplicationCreateInput):
    """Idempotent create-or-reopen (build §1/§7): only an observed posting
    may become an application; a role that already has an active application
    returns that one instead of creating a duplicate. Safe under concurrent
    duplicate requests — the actual uniqueness guarantee is the partial
    unique index in migration 0027, not this check-then-insert (Postgres's
    `ON CONFLICT ... WHERE ...` targets that index directly and never raises
    on a genuine race; two simultaneous callers for the same role always
    converge on the same single active row)."""
    role_id = str(payload.role_instance_id)
    with db_cursor() as cur:
        cur.execute("SELECT instance_type FROM jobber.role_instance WHERE id = %s", (role_id,))
        role = cur.fetchone()
        if not role:
            raise HTTPException(404, "role not found")
        if role["instance_type"] != "observed_posting":
            raise HTTPException(400, "Only an observed opportunity can become an application — targets cannot be applied to.")

        cur.execute(
            f"""
            INSERT INTO jobber.application (role_instance_id, status)
            VALUES (%s, 'preparing')
            ON CONFLICT (role_instance_id) WHERE status IN {_ACTIVE_STATUS_SQL}
            DO NOTHING
            RETURNING *
            """,
            (role_id,),
        )
        row = cur.fetchone()
        created = row is not None
        if row is None:
            cur.execute(
                f"SELECT * FROM jobber.application WHERE role_instance_id = %s AND status IN {_ACTIVE_STATUS_SQL}",
                (role_id,),
            )
            row = cur.fetchone()

    result = dict(row)
    result["created"] = created
    return result


# --- list ----------------------------------------------------------------


@router.get("")
def list_applications(
    status: str | None = Query(None, pattern="^(preparing|ready|submitted|interviewing|closed|withdrawn)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Bounded, server-joined list (build §3/§8/§13): role summary comes back
    in the same query, so the frontend never fetches each role individually.

    Phase 5 (docs/36 §6) adds a lifecycle summary — latest event and next
    scheduled interview — via two `LEFT JOIN LATERAL`s evaluated per row
    inside this one query, not a second request per application (build §21):
    the "soonest still-upcoming interview_scheduled event" rule this computes
    in SQL is the same rule `application_events.next_scheduled_interview`
    computes in Python over an already-fetched event list elsewhere."""
    filters = ""
    params: list = []
    if status:
        filters = " AND a.status = %s"
        params.append(status)

    with db_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM jobber.application a WHERE TRUE{filters}", params)
        total = cur.fetchone()["n"]

        cur.execute(
            f"""
            SELECT a.id, a.role_instance_id, a.status, a.created_at, a.updated_at,
                   ri.title, ri.organisation, ri.location, ri.posting_date,
                   latest.event_type AS latest_event_type, latest.event_at AS latest_event_at,
                   nxt.event_at AS next_interview_at, nxt.label AS next_interview_label
            FROM jobber.application a
            JOIN jobber.role_instance ri ON ri.id = a.role_instance_id
            LEFT JOIN LATERAL (
                SELECT event_type, event_at FROM jobber.application_event e
                WHERE e.application_id = a.id
                ORDER BY e.event_at DESC, e.created_at DESC
                LIMIT 1
            ) latest ON TRUE
            LEFT JOIN LATERAL (
                SELECT event_at, label FROM jobber.application_event e
                WHERE e.application_id = a.id AND e.event_type = 'interview_scheduled' AND e.event_at >= now()
                ORDER BY e.event_at ASC
                LIMIT 1
            ) nxt ON TRUE
            WHERE TRUE{filters}
            ORDER BY a.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        rows = cur.fetchall()

    items = [
        {
            "id": r["id"],
            "role_instance_id": r["role_instance_id"],
            "status": r["status"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "role": {
                "id": r["role_instance_id"],
                "title": r["title"],
                "organisation": r["organisation"],
                "location": r["location"],
                "posting_date": r["posting_date"],
            },
            "latest_event": (
                {"event_type": r["latest_event_type"], "event_at": r["latest_event_at"]}
                if r["latest_event_type"] is not None else None
            ),
            "next_interview": (
                {"event_at": r["next_interview_at"], "label": r["next_interview_label"]}
                if r["next_interview_at"] is not None else None
            ),
        }
        for r in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# --- detail ----------------------------------------------------------------


@router.get("/{application_id}")
def get_application(application_id: UUID):
    with db_cursor() as cur:
        application = _require_application(cur, application_id)
        # url lives on the linked document, not role_instance itself (see
        # db.build_role_view/list_roles, which join the same way).
        cur.execute(
            "SELECT ri.id, ri.title, ri.organisation, ri.location, ri.country, ri.remote_type, "
            "ri.posting_date, ri.instance_type, d.url AS url "
            "FROM jobber.role_instance ri LEFT JOIN jobber.document d ON d.id = ri.document_id "
            "WHERE ri.id = %s",
            (application["role_instance_id"],),
        )
        role = cur.fetchone()
        notes = _notes_for_application(cur, application_id)
    return {"application": application, "role": role, "notes": notes}


# --- status ----------------------------------------------------------------


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus


@router.patch("/{application_id}")
def update_application_status(application_id: UUID, payload: ApplicationStatusUpdate):
    """User-controlled only (build §10) — nothing here derives status from
    evidence/readiness, and nothing in the readiness/evidence endpoints ever
    calls this. The uniqueness guard below only ever fires if this status
    change would create a second active application for the same role (e.g.
    manually reopening a withdrawn attempt while another is already active),
    which the migration's partial unique index — not this code — is what
    actually prevents."""
    with db_cursor() as cur:
        try:
            cur.execute(
                "UPDATE jobber.application SET status = %s, updated_at = now() WHERE id = %s RETURNING *",
                (payload.status, str(application_id)),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "Another active application already exists for this role.")
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "application not found")
        return row


# --- notes ----------------------------------------------------------------


class ApplicationNoteInput(BaseModel):
    concept_id: UUID | None = None
    note_type: NoteType = "general"
    note_text: str = Field(min_length=1, max_length=10000)

    @field_validator("note_text")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Note text is required")
        return value.strip()


class ApplicationNoteUpdate(BaseModel):
    """Every field optional; only supplied fields change (same pattern as
    comparison.py's DevelopmentActionUpdate)."""

    concept_id: UUID | None = None
    note_type: NoteType | None = None
    note_text: str | None = Field(default=None, min_length=1, max_length=10000)


@router.post("/{application_id}/notes")
def create_application_note(application_id: UUID, payload: ApplicationNoteInput):
    with db_cursor() as cur:
        _require_application(cur, application_id)
        if payload.concept_id is not None:
            _require_active_concept(cur, payload.concept_id)
        cur.execute(
            """
            INSERT INTO jobber.application_note (application_id, concept_id, note_type, note_text)
            VALUES (%s, %s, %s, %s) RETURNING *
            """,
            (str(application_id), str(payload.concept_id) if payload.concept_id else None, payload.note_type, payload.note_text),
        )
        return cur.fetchone()


@router.patch("/{application_id}/notes/{note_id}")
def update_application_note(application_id: UUID, note_id: UUID, payload: ApplicationNoteUpdate):
    fields = payload.model_dump(exclude_unset=True)
    if "note_text" in fields:
        text = (fields["note_text"] or "").strip()
        if not text:
            raise HTTPException(400, "Note text cannot be blank")
        fields["note_text"] = text
    if "concept_id" in fields:
        fields["concept_id"] = str(fields["concept_id"]) if fields["concept_id"] else None

    with db_cursor() as cur:
        _require_application(cur, application_id)
        if fields.get("concept_id") is not None:
            _require_active_concept(cur, fields["concept_id"])

        if not fields:
            cur.execute(
                "SELECT * FROM jobber.application_note WHERE id = %s AND application_id = %s",
                (str(note_id), str(application_id)),
            )
        else:
            set_clause = ", ".join(f"{key} = %s" for key in fields)
            cur.execute(
                f"UPDATE jobber.application_note SET {set_clause}, updated_at = now() "
                "WHERE id = %s AND application_id = %s RETURNING *",
                [*fields.values(), str(note_id), str(application_id)],
            )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "note not found on this application")
        return row


@router.delete("/{application_id}/notes/{note_id}")
def delete_application_note(application_id: UUID, note_id: UUID):
    with db_cursor() as cur:
        cur.execute(
            "DELETE FROM jobber.application_note WHERE id = %s AND application_id = %s",
            (str(note_id), str(application_id)),
        )
        if not cur.rowcount:
            raise HTTPException(404, "note not found on this application")
    return {"status": "deleted"}


# --- evidence pack ----------------------------------------------------------


@router.get("/{application_id}/evidence")
def get_application_evidence(application_id: UUID):
    """The stable evidence/readiness input Phase 4's grounded generation will
    read from (build §4) — a live derived view over the same comparison
    semantics `/api/comparison/role/{id}` uses, plus this application's own
    notes. Never a second capability-comparison engine, never a copy of
    Profile360 data.

    Every item also carries `role_requirement_reviewed` (build §5): true for
    a `claim`-sourced (human-reviewed) requirement, false for a legacy
    `role_skill_observation` fallback — so a consumer can tell an accepted
    role requirement apart from unreviewed legacy extraction without
    re-deriving that distinction itself.
    """
    with db_cursor() as cur:
        application = _require_application(cur, application_id)
        comparison = build_role_comparison(cur, str(application["role_instance_id"]))
        notes = _notes_for_application(cur, application_id)

    notes_by_concept: dict[str, list[dict]] = {}
    for note in notes:
        if note["concept_id"] is not None:
            notes_by_concept.setdefault(str(note["concept_id"]), []).append(note)

    items = [
        {
            **item,
            "role_requirement_reviewed": item["role_side"]["requirement_source"] == "claim",
            "notes": notes_by_concept.get(item["concept"]["id"], []),
        }
        for item in comparison["items"]
    ]

    return {
        "application_id": str(application_id),
        "role_instance_id": str(application["role_instance_id"]),
        "role": comparison["role"],
        "review_summary": comparison["review_summary"],
        "counts": comparison["counts"],
        "blocking_gaps": comparison["blocking_gaps"],
        "unverified_required": comparison["unverified_required"],
        "items": items,
        "notes": notes,
        "engine_version": comparison["engine_version"],
    }


# --- lifecycle events (docs/36) ---------------------------------------------
#
# jobber.application_event is user-recorded history — submission, interviews,
# offers, closure. It is never an AI judgment and never changes
# jobber.application.status (build §3): the user still sets status only
# through PATCH /{application_id} above. An event and the current status may
# legitimately disagree for a while (e.g. an interview is scheduled before
# the user updates status to 'interviewing') — the timeline records what
# happened, status records how the user currently categorises the
# application. See application_events.py for the shared list/CRUD/derivation
# helpers this router calls.


@router.get("/{application_id}/events")
def list_application_events(application_id: UUID):
    """Newest-first, bounded — the one request the workspace needs on load
    (build §4/§21), never one request per row."""
    with db_cursor() as cur:
        _require_application(cur, application_id)
        return {"application_id": str(application_id), "events": events.list_events(cur, str(application_id))}


@router.post("/{application_id}/events")
def create_application_event(application_id: UUID, payload: ApplicationEventInput):
    with db_cursor() as cur:
        _require_application(cur, application_id)
        return events.create_event(cur, str(application_id), payload)


@router.patch("/{application_id}/events/{event_id}")
def update_application_event(application_id: UUID, event_id: UUID, payload: ApplicationEventUpdate):
    fields = payload.model_dump(exclude_unset=True)
    with db_cursor() as cur:
        _require_application(cur, application_id)
        row = events.update_event(cur, str(application_id), str(event_id), fields)
        if not row:
            raise HTTPException(404, "event not found on this application")
        return row


@router.delete("/{application_id}/events/{event_id}")
def delete_application_event(application_id: UUID, event_id: UUID):
    """A user-created lifecycle event may be deleted when it was recorded
    incorrectly (build §4). Scoped by both application_id and event_id, like
    every other Application mutation — never deletes the Application itself
    or any application_artifact."""
    with db_cursor() as cur:
        _require_application(cur, application_id)
        if not events.delete_event(cur, str(application_id), str(event_id)):
            raise HTTPException(404, "event not found on this application")
    return {"status": "deleted"}
