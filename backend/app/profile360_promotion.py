"""Promotion of a jobber-local person-capability assertion into profile360's
own review pipeline (`profile360.manual_import_queue`).

Context (docs/14 §6): `jobber.person_capability_assertion` is a deliberately
minimal, evidence-free "the user says so" flag — a TEMPORARY NAVIGATION
OVERRIDE for the comparison UI's "I have done this" action, never itself
capability evidence, and never allowed to outrank a real profile360 mapping
(see comparison.py's status ordering: evidenced > partial > user_asserted >
not_found). The preferred long-term home for that assertion is profile360
itself, as a claim profile360 can review and confirm on its own terms.

`profile360.manual_import_queue`'s real shape was confirmed by live
inspection (docs/14 §5/§6):

    source_key       TEXT PRIMARY KEY
    imported_at      TIMESTAMPTZ DEFAULT now()
    source_label     TEXT NOT NULL
    payload          JSONB NOT NULL
    processed        BOOLEAN DEFAULT false
    processed_at     TIMESTAMPTZ
    processing_notes TEXT

There is no `id` column — identity is `source_key`, so this module derives a
deterministic one from the assertion's own id
(`jobber_person_capability_assertion:<uuid>`) and upserts on it: re-promoting
the same assertion updates that same queue row instead of creating a
duplicate, and resets `processed`/`processed_at` so profile360's own tool
picks it up again as fresh.
"""

import psycopg

SOURCE_LABEL = "cp_enterprise person capability assertion"


class Profile360PromotionError(RuntimeError):
    """The write to profile360.manual_import_queue failed — a genuine
    database failure (table missing, connection error, constraint
    violation), not a guess about an unknown shape. The assertion is never
    marked promoted when this is raised."""


def _queue_source_key(assertion_id: str) -> str:
    return f"jobber_person_capability_assertion:{assertion_id}"


def promote_assertion_to_profile360(cur, assertion_id: str) -> dict:
    """Looks up the jobber assertion + the concept it names, and upserts a
    proposal row into profile360.manual_import_queue keyed by a deterministic
    source_key derived from the assertion's own id. Marks the assertion's
    `promoted_to_profile360_at` only once that write has actually succeeded —
    never on a failed insert. Never deletes the jobber-local row — the UI
    keeps showing "you asserted this" alongside "queued for profile360 to
    confirm"."""
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

    source_key = _queue_source_key(str(row["id"]))
    payload = {
        "source": "jobber.person_capability_assertion",
        "jobber_assertion_id": str(row["id"]),
        "jobber_concept_id": str(row["concept_id"]),
        "concept_canonical_name": row["canonical_name"],
        "concept_type": row["type_code"],
        "proposed_claim_text": f"Asserts capability with: {row['canonical_name']}"
        + (f" — {row['note']}" if row["note"] else ""),
        "note": row["note"],
        "asserted_at": row["created_at"].isoformat() if row["created_at"] else None,
    }

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
            RETURNING source_key
            """,
            (source_key, SOURCE_LABEL, psycopg.types.json.Json(payload)),
        )
        queue_source_key = cur.fetchone()["source_key"]
    except psycopg.Error as e:
        raise Profile360PromotionError(
            f"profile360.manual_import_queue write failed ({e}) — the assertion was not marked promoted."
        ) from e

    cur.execute(
        "UPDATE jobber.person_capability_assertion SET promoted_to_profile360_at = now() WHERE id = %s",
        (assertion_id,),
    )
    return {"status": "ok", "profile360_manual_import_source_key": queue_source_key}
