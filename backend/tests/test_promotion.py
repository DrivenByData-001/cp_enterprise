"""Promotion of a jobber-local person_capability_assertion into profile360's
manual_import_queue (docs/14 §6), against the confirmed live schema
(source_key TEXT PRIMARY KEY — no id column — source_label, payload,
processed, processed_at, processing_notes)."""

import pytest

from app import db, profile360_promotion


def _active_concept(cur, name: str, type_code: str = "tool") -> str:
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _make_assertion(cur, concept_id: str, note: str | None = None) -> str:
    cur.execute(
        "INSERT INTO jobber.person_capability_assertion (jobber_concept_id, note) VALUES (%s, %s) RETURNING id",
        (concept_id, note),
    )
    return str(cur.fetchone()["id"])


def test_promote_creates_queue_record_with_deterministic_source_key(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        assertion_id = _make_assertion(cur, concept_id, note="5 years")

        result = profile360_promotion.promote_assertion_to_profile360(cur, assertion_id)

        expected_key = f"jobber_person_capability_assertion:{assertion_id}"
        assert result == {"status": "ok", "profile360_manual_import_source_key": expected_key}
        assert "id" not in result  # the queue has no id column — never claim one

        cur.execute("SELECT * FROM profile360.manual_import_queue WHERE source_key = %s", (expected_key,))
        row = cur.fetchone()
        assert row is not None
        assert row["source_label"] == profile360_promotion.SOURCE_LABEL
        assert row["processed"] is False
        assert row["payload"]["jobber_concept_id"] == concept_id
        assert row["payload"]["concept_canonical_name"] == "Python"
        assert row["payload"]["note"] == "5 years"

        cur.execute(
            "SELECT promoted_to_profile360_at FROM jobber.person_capability_assertion WHERE id = %s", (assertion_id,)
        )
        assert cur.fetchone()["promoted_to_profile360_at"] is not None


def test_repromote_updates_same_row_and_resets_processed(client):
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        assertion_id = _make_assertion(cur, concept_id)
        first = profile360_promotion.promote_assertion_to_profile360(cur, assertion_id)
        source_key = first["profile360_manual_import_source_key"]

        # Simulate profile360's own tool having already processed it.
        cur.execute(
            "UPDATE profile360.manual_import_queue SET processed = true, processed_at = now() WHERE source_key = %s",
            (source_key,),
        )

        second = profile360_promotion.promote_assertion_to_profile360(cur, assertion_id)
        assert second == first  # same deterministic source_key, not a new one

        cur.execute("SELECT COUNT(*) AS n FROM profile360.manual_import_queue WHERE source_key = %s", (source_key,))
        assert cur.fetchone()["n"] == 1  # updated in place, never duplicated

        cur.execute(
            "SELECT processed, processed_at FROM profile360.manual_import_queue WHERE source_key = %s", (source_key,)
        )
        row = cur.fetchone()
        assert row["processed"] is False
        assert row["processed_at"] is None


def test_promote_missing_assertion_raises_value_error(client):
    import uuid

    with db.db_cursor() as cur:
        with pytest.raises(ValueError):
            profile360_promotion.promote_assertion_to_profile360(cur, str(uuid.uuid4()))


def test_promotion_db_failure_does_not_mark_assertion_promoted(client):
    """A genuine database failure (here: the queue table temporarily renamed
    away, to force a real psycopg error rather than a mocked one) must never
    leave the assertion looking promoted — `Profile360PromotionError` is
    raised before the UPDATE on person_capability_assertion is ever reached."""
    with db.db_cursor() as cur:
        concept_id = _active_concept(cur, "Python")
        assertion_id = _make_assertion(cur, concept_id)

    with db.db_cursor() as cur:
        cur.execute("ALTER TABLE profile360.manual_import_queue RENAME TO manual_import_queue_tmp")

    try:
        with db.db_cursor() as cur:
            with pytest.raises(profile360_promotion.Profile360PromotionError):
                profile360_promotion.promote_assertion_to_profile360(cur, assertion_id)
            # The transaction is now aborted server-side (the failed INSERT
            # above) — no further statement can run on this cursor, so the
            # block ends here and the pool rolls the (empty, unwritten) rest
            # of it back on exit.
    finally:
        with db.db_cursor() as cur:
            cur.execute("ALTER TABLE profile360.manual_import_queue_tmp RENAME TO manual_import_queue")

    with db.db_cursor() as cur:
        cur.execute(
            "SELECT promoted_to_profile360_at FROM jobber.person_capability_assertion WHERE id = %s", (assertion_id,)
        )
        assert cur.fetchone()["promoted_to_profile360_at"] is None
