"""Phase 1 migration: seed the vocabulary and run concept-linking Pass B.

docs/11-capability-model-design.md §10.1 describes `job_role_skills` -> vocabulary
via `concept_proposal`, resolved into claims. Phase 1 (§11) stops short of claim
tables (those are Phase 2) — this script groups distinct `job_role_skills.name`
values into `concept_proposal` rows (or auto-resolves them via exact match against
an already-curated concept) and sets `job_role_skills.resolved_concept_id`. See
`backend/app/concept_linking.py` for the actual cascade; this script just applies
the schema/seed to the target connection and calls it.

Unlike migrate_phase0.py, this is NOT a one-time backfill — it's designed to be
re-run any time after new postings are imported (via the normal app, not this
script), so the vocabulary keeps converging as the corpus grows. Re-running is
always safe: already-resolved skills are skipped, and an existing pending
proposal for a surface form is updated in place rather than duplicated.

Usage:
    cd backend && python3 scripts/migrate_phase1.py [path/to/career_nav.db]

With no argument, targets the default DB path used by the running app
(backend/data/career_nav.db). Before pointing this at a real captured database,
run it against a **copy** first and check the printed counts look sane — same
caution as migrate_phase0.py.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.concept_linking import run_pass_b  # noqa: E402
from app.db import DB_PATH, SCHEMA, _migrate, seed_vocabulary  # noqa: E402


def run(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Additive only, IF NOT EXISTS — deliberately not app.db.init_db(), which
    # always targets the app's default DB_PATH regardless of what path this
    # script was pointed at (same reasoning as migrate_phase0.py).
    conn.executescript(SCHEMA)
    _migrate(conn)
    seed_vocabulary(conn)
    conn.commit()

    counts = run_pass_b(conn)
    conn.close()

    print(f"skills auto-resolved (exact match): {counts['auto_resolved']}")
    print(f"proposals created:                  {counts['proposals_created']}")
    print(f"proposals updated (already pending): {counts['proposals_updated']}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    if not target.exists():
        print(f"no database found at {target}", file=sys.stderr)
        sys.exit(1)
    run(target)
