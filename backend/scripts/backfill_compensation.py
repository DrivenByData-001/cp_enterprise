"""Operational entrypoint for projecting existing role_instance salary
fields into jobber.compensation_observation (Phase 4 prompt §7).

Idempotent and safe to repeat — never touches role_instance's own salary
columns, never overwrites a compensation_observation row that already
exists (source_key is unique). This never runs implicitly on any GET
endpoint; invoke it explicitly, either via this script or the equivalent
POST /api/economics/compensation-observations/backfill endpoint.

Usage (against DATABASE_URL — local dev or the real Supabase project alike):

    cd backend
    python -m scripts.backfill_compensation
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_module  # noqa: E402
from app.compensation_backfill import backfill_compensation_observations  # noqa: E402
from app.db import db_cursor  # noqa: E402


def main() -> None:
    try:
        with db_cursor() as cur:
            result = backfill_compensation_observations(cur)
    finally:
        db_module.reset_pool()

    print(
        f"roles_considered={result['roles_considered']} "
        f"observations_created={result['observations_created']} "
        f"observations_already_present={result['observations_already_present']} "
        f"skipped_no_currency={result['skipped_no_currency']} "
        f"unassigned_market={result['unassigned_market']}"
    )


if __name__ == "__main__":
    main()
