"""Phase 5: Application lifecycle events (docs/36).

`jobber.application_event` (migration 0029) is a user-recorded timeline of
what happened to an application — submission, interviews, offers, closure.
Every function here is a plain SELECT/INSERT/UPDATE/DELETE; nothing calls AI,
and nothing here reads or writes `jobber.application.status` — status stays
exactly what Phase 3 made it: set only through the existing status control
(`routes/applications.py::update_application_status`). An event and the
current status may legitimately disagree for a while (build §3) — e.g. an
interview gets scheduled while status is still 'preparing' — the timeline
records what happened, status records how the user currently categorises the
application.

`next_scheduled_interview`/`latest_event` are pure functions over an
already-fetched event list — used by the Application workspace/generation
context, which already hold a full bounded list from `list_events`. The
Applications-index aggregate (`routes/applications.py::list_applications`)
expresses the same "soonest still-upcoming interview_scheduled event" rule
directly in SQL instead, via a `LEFT JOIN LATERAL` — see that function's own
comment for why: a per-row Python pass over every application's events would
be exactly the N+1 pattern build §21 rules out.
"""

from datetime import datetime, timezone
from typing import get_args

from .db import to_json_param
from .models import ApplicationEventType

EVENT_TYPES: tuple[str, ...] = get_args(ApplicationEventType)

# One person's application history is naturally bounded (never paginated) —
# same reasoning as application_generation.EPISODE_LIMIT.
EVENT_LIST_LIMIT = 200


def list_events(cur, application_id: str, *, limit: int = EVENT_LIST_LIMIT) -> list[dict]:
    """Newest-first, bounded — the one request the workspace needs on load
    (build §4/§21), never one request per row."""
    cur.execute(
        "SELECT * FROM jobber.application_event WHERE application_id = %s "
        "ORDER BY event_at DESC, created_at DESC LIMIT %s",
        (application_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def create_event(cur, application_id: str, payload) -> dict:
    cur.execute(
        """
        INSERT INTO jobber.application_event
            (application_id, event_type, event_at, label, notes, details)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (application_id, payload.event_type, payload.event_at, payload.label, payload.notes, to_json_param(payload.details)),
    )
    return dict(cur.fetchone())


def update_event(cur, application_id: str, event_id: str, fields: dict) -> dict | None:
    """`fields` is already an exclude_unset dict from ApplicationEventUpdate
    (same convention as routes/applications.py's note update) — only the
    keys actually supplied change."""
    if "details" in fields:
        fields["details"] = to_json_param(fields["details"])
    if not fields:
        cur.execute(
            "SELECT * FROM jobber.application_event WHERE id = %s AND application_id = %s",
            (event_id, application_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    set_clause = ", ".join(f"{key} = %s" for key in fields)
    cur.execute(
        f"UPDATE jobber.application_event SET {set_clause}, updated_at = now() "
        "WHERE id = %s AND application_id = %s RETURNING *",
        [*fields.values(), event_id, application_id],
    )
    row = cur.fetchone()
    return dict(row) if row else None


def delete_event(cur, application_id: str, event_id: str) -> bool:
    """A user-created event may be deleted when it was recorded incorrectly
    (build §4) — scoped by both ids, same ownership discipline as every other
    Application mutation. Never touches the Application row or any
    application_artifact."""
    cur.execute(
        "DELETE FROM jobber.application_event WHERE id = %s AND application_id = %s",
        (event_id, application_id),
    )
    return cur.rowcount > 0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def next_scheduled_interview(events: list[dict], *, now: datetime | None = None) -> dict | None:
    """The soonest still-upcoming 'interview_scheduled' event, or None if
    there isn't one. Deliberately ignores 'interview_completed' rows — this
    answers "what's next", not "what happened"."""
    now = now or datetime.now(timezone.utc)
    upcoming = [e for e in events if e["event_type"] == "interview_scheduled" and _aware(e["event_at"]) >= now]
    if not upcoming:
        return None
    return min(upcoming, key=lambda e: e["event_at"])


def latest_event(events: list[dict]) -> dict | None:
    """`events` is already newest-first (`list_events`'s own ORDER BY) — this
    just names that first element so callers don't need to know the ordering
    convention themselves."""
    return events[0] if events else None
