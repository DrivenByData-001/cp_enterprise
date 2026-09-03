"""Operational entrypoint for backfilling/rebuilding jobber.d_embedding rows.

Fixes the migrated-role Space regression: role_instance rows that existed
before Phase 2 moved embeddings into d_embedding (owner_kind='role_instance')
were never backfilled, so Space silently excludes them from its projection.
This never runs implicitly — GET /api/space stays a pure read/project
operation — so it must be invoked explicitly, either via this script or the
equivalent POST /api/space/rebuild-role-embeddings endpoint.

Usage (against DATABASE_URL — local dev or the real Supabase project alike;
this only ever touches jobber.d_embedding rows with owner_kind='role_instance',
nothing else):

    cd backend
    python -m scripts.rebuild_embeddings --roles                # missing only (safe, default)
    python -m scripts.rebuild_embeddings --roles --force         # recompute every current-model role embedding

Currently only --roles is implemented (this is specifically the Space
regression fix); the flag is required and explicit rather than assumed, so a
future concept/document backfill mode has an unambiguous place to be added
without silently changing what a bare invocation does.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import db_cursor  # noqa: E402
from app.embeddings import rebuild_role_embeddings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--roles", action="store_true", required=True, help="Backfill/rebuild role_instance embeddings.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute every role's current-model embedding, not only roles missing one.",
    )
    args = parser.parse_args()

    with db_cursor() as cur:
        result = rebuild_role_embeddings(cur, missing_only=not args.force)

    print(
        f"model={result['model']} roles_scanned={result['roles_scanned']} "
        f"embeddings_created={result['embeddings_created']} "
        f"embeddings_updated={result['embeddings_updated']} skipped={result['skipped']}"
    )


if __name__ == "__main__":
    main()
