"""Phase 0 migration: backfill `document` + seed `person` from the legacy schema.

docs/11-capability-model-design.md §10 describes `job_roles` -> `role_instance` +
`document` and `profile_snapshots` -> `person` + `document`. Phase 0 (§11) builds
only the document/person/episode layer — `role_instance` is deferred, so this
script creates `document` rows only and leaves `job_roles` / `profile_snapshots`
completely untouched (read-only access to both).

Idempotent by content: `document.body_sha256` is UNIQUE, so re-running this script
against a DB it has already migrated inserts nothing new — a duplicate insert hits
the unique constraint and is skipped, not treated as an error. It is a one-time
backfill, not a continuous sync: if a job_roles/profile_snapshots row is edited
after migration, re-running will add a *new* document for the changed content
rather than updating the old one (documents are immutable by design, §4.1) — it
will not retroactively fix an already-migrated document.

Usage:
    cd backend && python3 scripts/migrate_phase0.py [path/to/career_nav.db]

With no argument, targets the default DB path used by the running app
(backend/data/career_nav.db).
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SCHEMA, DB_PATH, _migrate, get_or_create_person  # noqa: E402


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _insert_document(cur: sqlite3.Cursor, **fields) -> tuple[int | None, bool]:
    """Returns (document_id, created). created=False means it already existed
    (same content previously migrated) — id is None in that case since callers
    in this script don't need it, only the counts."""
    body_sha256 = _sha256(fields["body"])
    try:
        cur.execute(
            """
            INSERT INTO document
                (kind, title, body, body_sha256, source, url, document_date, ingested_at, notes)
            VALUES (:kind, :title, :body, :body_sha256, :source, :url, :document_date, :ingested_at, :notes)
            """,
            {**fields, "body_sha256": body_sha256},
        )
        return cur.lastrowid, True
    except sqlite3.IntegrityError:
        return None, False


def _compose_role_body(role: dict) -> str:
    if role["node_type"] == "posting":
        parts = [role.get("description"), role.get("requirements"), role.get("responsibilities")]
    else:
        # targets have no posting-style text; typical_tasks/skill_decomposition
        # (already JSON strings in the raw column) are the only substantive text.
        parts = [role.get("summary"), role.get("description"), role.get("typical_tasks"), role.get("skill_decomposition")]
    body = "\n\n".join(p for p in parts if p)
    return body or role["title"]  # title fallback so body is never empty


def migrate_job_roles(cur: sqlite3.Cursor) -> tuple[int, int]:
    cur.execute("SELECT * FROM job_roles")
    rows = [dict(r) for r in cur.fetchall()]

    created = skipped = 0
    for role in rows:
        kind = "job_posting" if role["node_type"] == "posting" else "narrative"
        _, was_created = _insert_document(
            cur,
            kind=kind,
            title=role["title"],
            body=_compose_role_body(role),
            source=role.get("source"),
            url=role.get("url"),
            document_date=role.get("posting_date") or role.get("captured_at"),
            ingested_at=role.get("captured_at") or role.get("created_at"),
            notes=role.get("raw_json"),
        )
        created += was_created
        skipped += not was_created
    return created, skipped


def migrate_profile_snapshots(cur: sqlite3.Cursor) -> tuple[int, int]:
    cur.execute("SELECT * FROM profile_snapshots")
    rows = [dict(r) for r in cur.fetchall()]

    created = skipped = 0
    for snap in rows:
        _, was_created = _insert_document(
            cur,
            kind="narrative",
            title=f"Profile snapshot {snap['created_at']}",
            body=snap["narrative_text"],
            source="user_paste",
            url=None,
            document_date=snap["created_at"],
            ingested_at=snap["created_at"],
            notes=json.dumps({"profile_snapshot_id": snap["id"], "is_current": bool(snap["is_current"])}),
        )
        created += was_created
        skipped += not was_created
    return created, skipped


def run(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Apply the schema (additive only, IF NOT EXISTS) to *this* connection —
    # deliberately not app.db.init_db(), which always targets the app's default
    # DB_PATH regardless of what path this script was pointed at.
    conn.executescript(SCHEMA)
    _migrate(conn)

    cur = conn.cursor()

    role_created, role_skipped = migrate_job_roles(cur)
    snap_created, snap_skipped = migrate_profile_snapshots(cur)
    person_id = get_or_create_person(cur)

    conn.commit()
    conn.close()

    print(f"job_roles         -> document: {role_created} created, {role_skipped} already present")
    print(f"profile_snapshots -> document: {snap_created} created, {snap_skipped} already present")
    print(f"person seeded: id={person_id}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    if not target.exists():
        print(f"no database found at {target}", file=sys.stderr)
        sys.exit(1)
    run(target)
