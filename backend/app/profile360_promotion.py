"""Best-effort promotion of a jobber-local person-capability assertion into
profile360's own review pipeline (`profile360.manual_import_queue`).

Context (docs/14 §6, and the Phase 2 production-schema reconciliation pass):
`jobber.person_capability_assertion` is a deliberately minimal, evidence-free
"the user says so" flag — a TEMPORARY NAVIGATION OVERRIDE for the comparison
UI's "I have done this" action, never itself capability evidence, and never
allowed to outrank a real profile360 mapping (see comparison.py's status
ordering: evidenced > partial > user_asserted > not_found). The preferred
long-term home for that assertion is profile360 itself, as a claim profile360
can review and confirm on its own terms.

`profile360.manual_import_queue` is presumed to exist for exactly this kind
of external proposal (its name and its already-enabled RLS, confirmed by live
inspection, are the only two things known about it — its column shape was
never inspected). This module never guesses that shape blindly: it
introspects the live columns and only inserts if it can identify a
plausible JSONB-ish payload column to carry the proposal without violating a
NOT NULL constraint it can't see. If it can't, it raises rather than risk a
malformed row silently entering someone else's review queue.
"""

import psycopg

_PAYLOAD_COLUMN_CANDIDATES = ("payload", "data", "content", "details", "body")
_STATUS_COLUMN_CANDIDATES = ("status", "state")


class Profile360PromotionUnsupportedError(RuntimeError):
    """profile360.manual_import_queue's live shape doesn't have a column this
    module can confidently write a proposal into without guessing at
    constraints it can't see."""


def _table_columns(cur, schema: str, table: str) -> list[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return [r["column_name"] for r in cur.fetchall()]


def promote_assertion_to_profile360(cur, assertion_id: str) -> dict:
    """Looks up the jobber assertion + the concept it names, and writes a
    best-effort proposal row into profile360.manual_import_queue. Marks the
    assertion's `promoted_to_profile360_at` on success. Never deletes the
    jobber-local row — the UI keeps showing "you asserted this" alongside
    "queued for profile360 to confirm"."""
    cur.execute(
        """
        SELECT a.id, a.note, a.created_at, c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.person_capability_assertion a
        JOIN jobber.concept c ON c.id = a.jobber_concept_id
        WHERE a.id = %s
        """,
        (assertion_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError("assertion not found")

    columns = _table_columns(cur, "profile360", "manual_import_queue")
    if not columns:
        raise Profile360PromotionUnsupportedError(
            "profile360.manual_import_queue was not found on this connection — cannot promote."
        )

    payload_column = next((c for c in _PAYLOAD_COLUMN_CANDIDATES if c in columns), None)
    if payload_column is None:
        raise Profile360PromotionUnsupportedError(
            "profile360.manual_import_queue has no recognisable JSONB payload column "
            f"(tried {_PAYLOAD_COLUMN_CANDIDATES}); refusing to guess. Its real shape "
            "should be inspected and this module updated — see docs/14 §11."
        )
    status_column = next((c for c in _STATUS_COLUMN_CANDIDATES if c in columns), None)

    payload = {
        "source": "jobber.person_capability_assertion",
        "jobber_assertion_id": str(row["id"]),
        "jobber_concept_id": str(row["concept_id"]),
        "concept_canonical_name": row["canonical_name"],
        "concept_type": row["type_code"],
        "proposed_claim_text": f"Asserts capability with: {row['canonical_name']}" + (f" — {row['note']}" if row["note"] else ""),
        "note": row["note"],
        "asserted_at": row["created_at"].isoformat() if row["created_at"] else None,
    }

    insert_columns = [payload_column]
    insert_values = [psycopg.types.json.Json(payload)]
    if status_column:
        insert_columns.append(status_column)
        insert_values.append("pending")

    placeholders = ", ".join(["%s"] * len(insert_columns))
    try:
        cur.execute(
            f"INSERT INTO profile360.manual_import_queue ({', '.join(insert_columns)}) VALUES ({placeholders}) RETURNING id",
            insert_values,
        )
    except psycopg.Error as e:
        raise Profile360PromotionUnsupportedError(
            f"profile360.manual_import_queue insert failed ({e}) — its real constraints are unknown from here; "
            "see docs/14 §11."
        ) from e
    queue_id = cur.fetchone()["id"]

    cur.execute(
        "UPDATE jobber.person_capability_assertion SET promoted_to_profile360_at = now() WHERE id = %s",
        (assertion_id,),
    )
    return {"status": "ok", "profile360_manual_import_queue_id": str(queue_id)}
