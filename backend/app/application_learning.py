"""Phase 9: explicit promotion of application-local notes/events into
Profile360's own review pipeline (docs/40 — the "learning loop" half of
Career Cockpit). Same promotion pattern as `profile360_promotion.py`
(person_capability_assertion): the only write anywhere in this module is to
`profile360.manual_import_queue`, keyed by a deterministic source_key
derived from the source row's own id, upserted on repeat promotion.

A queued item is not yet evidence (build §10/§34). Nothing here writes to
profile360.claims, profile360.capabilities, a profile360 episode, or any
jobber Profile360 mapping table; nothing here changes an Application's
status, artifact state, or capability/Target-gap status. The application_note
/application_event source rows are never mutated or deleted by promotion —
they remain ordinary Application history (build §13).
"""

import hashlib
import json

import psycopg

from .db import to_json_param

SOURCE_LABEL = "cp_enterprise application learning"

NOT_QUEUED = "not_queued"
QUEUED_PENDING = "queued_pending"
PROCESSED = "processed_by_profile360"
SOURCE_CHANGED = "source_changed_since_queue"


class LearningSourceError(ValueError):
    """No such note/event on this application, or it has no promotable text
    — 404/400 at the route layer."""


class LearningPromotionError(RuntimeError):
    """The write to profile360.manual_import_queue failed — a genuine
    database failure, never a guess about an unknown shape. Nothing is ever
    reported queued when this is raised (build §12's "errors never falsely
    report success")."""


def note_source_key(note_id: str) -> str:
    return f"cp_enterprise_application_note:{note_id}"


def event_source_key(event_id: str) -> str:
    return f"cp_enterprise_application_event:{event_id}"


def _fingerprint(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _iso(value) -> str | None:
    return value.isoformat() if value else None


# --- Payload construction (pure — no DB) ------------------------------------
#
# Kept separate from row-loading so both the single-item promote path and
# the bulk Cockpit/Application-workspace status path build the exact same
# payload/hash from the same inputs (build §11/§33) — never two independently
# maintained shapes that could disagree about a source's content hash.


def note_payload(note: dict, *, role: dict, concept: dict | None) -> tuple[dict, str]:
    """`note` is an application_note row; `role` is that note's Application's
    role_instance (id/title/organisation); `concept` is the linked concept
    row (id/canonical_name/type_code) when note['concept_id'] is set, else
    None. Raises LearningSourceError if note_text is blank (defensive only —
    the API layer already requires non-blank text at creation)."""
    text = (note["note_text"] or "").strip()
    if not text:
        raise LearningSourceError("note text is blank")
    concept_id = str(note["concept_id"]) if note.get("concept_id") else None
    fields = {"note_text": text, "concept_id": concept_id, "note_type": note["note_type"]}
    content_hash = _fingerprint(fields)
    payload = {
        "source": "jobber.application_note",
        "application_id": str(note["application_id"]),
        "application_note_id": str(note["id"]),
        "role_instance_id": str(role["id"]),
        "role_title": role.get("title"),
        "organisation": role.get("organisation"),
        "note_type": note["note_type"],
        "note_text": text,
        "jobber_concept_id": concept_id,
        "concept_canonical_name": concept["canonical_name"] if concept else None,
        "concept_type_code": concept.get("type_code") if concept else None,
        "source_created_at": _iso(note.get("created_at")),
        "source_updated_at": _iso(note.get("updated_at")),
        "content_sha256": content_hash,
    }
    return payload, content_hash


def event_payload(event: dict, *, role: dict) -> tuple[dict, str]:
    """`event` is an application_event row. Raises LearningSourceError if
    `notes` is blank — an event may only be queued when it carries a
    nonblank user reflection (build §9's "may be queued only when it has
    nonblank notes"); the event itself is never turned into evidence."""
    notes = (event["notes"] or "").strip()
    if not notes:
        raise LearningSourceError("this event has no notes to send for review")
    event_at = event.get("event_at")
    fields = {"notes": notes, "event_type": event["event_type"], "event_at": _iso(event_at)}
    content_hash = _fingerprint(fields)
    payload = {
        "source": "jobber.application_event",
        "application_id": str(event["application_id"]),
        "application_event_id": str(event["id"]),
        "role_instance_id": str(role["id"]),
        "role_title": role.get("title"),
        "organisation": role.get("organisation"),
        "event_type": event["event_type"],
        "event_at": _iso(event_at),
        "notes": notes,
        "source_created_at": _iso(event.get("created_at")),
        "source_updated_at": _iso(event.get("updated_at")),
        "content_sha256": content_hash,
    }
    return payload, content_hash


def is_event_eligible(event: dict) -> bool:
    return bool((event.get("notes") or "").strip())


# --- Queue status (build §11) -----------------------------------------------


def queue_rows_bulk(cur, source_keys: list[str]) -> dict[str, dict]:
    """One bulk lookup for many source_keys at once (build §12's "a
    read-only learning-state helper... rather than one GET per note/event")
    — used by both the Application workspace (one application's notes/
    events) and the Cockpit learning section (recent items across every
    application)."""
    keys = list(dict.fromkeys(source_keys))
    if not keys:
        return {}
    cur.execute(
        "SELECT source_key, payload, processed, processed_at FROM profile360.manual_import_queue WHERE source_key = ANY(%s)",
        (keys,),
    )
    return {row["source_key"]: dict(row) for row in cur.fetchall()}


def status_for(queue_row: dict | None, current_content_hash: str) -> str:
    """The four queue statuses (build §11), derived purely from a queue row
    (or its absence) plus the *current* source row's own content hash —
    never mutates anything, so this is safe to call from a GET (build §11's
    "Do not silently requeue on GET")."""
    if queue_row is None:
        return NOT_QUEUED
    stored_hash = (queue_row.get("payload") or {}).get("content_sha256")
    if stored_hash != current_content_hash:
        return SOURCE_CHANGED
    return PROCESSED if queue_row.get("processed") else QUEUED_PENDING


# --- Bulk per-application learning state (Application workspace + Cockpit) -


def _concepts_bulk(cur, concept_ids: list[str]) -> dict[str, dict]:
    ids = list({c for c in concept_ids if c})
    if not ids:
        return {}
    cur.execute("SELECT id, canonical_name, type_code FROM jobber.concept WHERE id = ANY(%s::uuid[])", (ids,))
    return {str(r["id"]): dict(r) for r in cur.fetchall()}


def application_learning_state(cur, *, role: dict, notes: list[dict], events: list[dict]) -> dict:
    """Bulk, single-round-trip queue-status lookup for every note/event on
    one Application (build §12) — one concept query, one queue query,
    regardless of how many notes/events the Application has. `role` is the
    Application's own role_instance summary (id/title/organisation),
    supplied once by the caller rather than re-joined per row, since every
    note/event on one Application shares the same role."""
    concepts = _concepts_bulk(cur, [n.get("concept_id") for n in notes])
    eligible_events = [e for e in events if is_event_eligible(e)]

    hashes: dict[str, str] = {}
    for n in notes:
        _, content_hash = note_payload(n, role=role, concept=concepts.get(str(n["concept_id"])) if n.get("concept_id") else None)
        hashes[note_source_key(str(n["id"]))] = content_hash
    for e in eligible_events:
        _, content_hash = event_payload(e, role=role)
        hashes[event_source_key(str(e["id"]))] = content_hash

    queue_rows = queue_rows_bulk(cur, list(hashes))

    note_items = [
        {
            "source_type": "note",
            "source_id": str(n["id"]),
            "note_type": n["note_type"],
            "concept_id": str(n["concept_id"]) if n.get("concept_id") else None,
            "queue_source_key": note_source_key(str(n["id"])),
            "status": status_for(queue_rows.get(note_source_key(str(n["id"]))), hashes[note_source_key(str(n["id"]))]),
        }
        for n in notes
    ]
    event_items = [
        {
            "source_type": "event",
            "source_id": str(e["id"]),
            "event_type": e["event_type"],
            "queue_source_key": event_source_key(str(e["id"])),
            "status": status_for(queue_rows.get(event_source_key(str(e["id"]))), hashes[event_source_key(str(e["id"]))]),
        }
        for e in eligible_events
    ]
    return {"notes": note_items, "events": event_items}


# --- Promotion (mutating; explicit user action only) ------------------------


def _upsert_queue(cur, source_key: str, payload: dict) -> None:
    """Same upsert-and-reset-processed convention as
    `profile360_promotion.promote_assertion_to_profile360` (build §9): every
    explicit promote call — first queue or requeue after an edit — writes
    the latest payload and resets `processed`/`processed_at` so profile360's
    own tool picks it up again as fresh."""
    try:
        cur.execute(
            """
            INSERT INTO profile360.manual_import_queue (source_key, source_label, payload)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_key) DO UPDATE
            SET source_label = EXCLUDED.source_label,
                payload = EXCLUDED.payload,
                processed = false,
                processed_at = NULL
            """,
            (source_key, SOURCE_LABEL, to_json_param(payload)),
        )
    except psycopg.Error as e:
        raise LearningPromotionError(f"profile360.manual_import_queue write failed ({e}) — nothing was queued.") from e


def promote_note(cur, note: dict, *, role: dict, concept: dict | None) -> dict:
    payload, _content_hash = note_payload(note, role=role, concept=concept)
    source_key = note_source_key(str(note["id"]))
    _upsert_queue(cur, source_key, payload)
    return {"status": QUEUED_PENDING, "queue_source_key": source_key}


def promote_event(cur, event: dict, *, role: dict) -> dict:
    payload, _content_hash = event_payload(event, role=role)
    source_key = event_source_key(str(event["id"]))
    _upsert_queue(cur, source_key, payload)
    return {"status": QUEUED_PENDING, "queue_source_key": source_key}
