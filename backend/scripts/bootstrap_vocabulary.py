"""Bootstrap canonical vocabulary + candidate capabilities from the captured
role corpus (docs/18-consolidation-and-analytical-foundation.md §3,
app/vocabulary_bootstrap.py).

Deliberately a CLI, not a one-click API button — same reasoning as
backend/scripts/process_job_documents.py: a batch operation that touches a
lot of rows should be something an operator runs deliberately against a
DATABASE_URL they've chosen, not something reachable from the frontend
(this build has no additional auth layer on mutating endpoints, docs/15 §1).

Everything this writes is a *proposal*: `jobber.concept` rows with
`status='proposed'` and `jobber.concept_edge` rows with `status='proposed'`.
Nothing here is ever `active`/`accepted` — matching, coverage, and
comparison all filter on `active`/`accepted` and are completely blind to
what this script writes until a curator reviews and accepts each one
(Vocabulary.tsx for atomic-concept clusters, Capabilities.tsx's "Proposed"
filter for candidate capabilities/component edges).

Usage:

    cd backend
    python -m scripts.bootstrap_vocabulary --dry-run          # report only, writes nothing
    python -m scripts.bootstrap_vocabulary                    # writes proposals
    python -m scripts.bootstrap_vocabulary --min-pair-support 8 --max-candidates 60

Safe to re-run: atomic-concept clustering only ever fills a NULL cluster_key
or updates an already-pending proposal's live occurrence_count (via Pass B,
itself idempotent); candidate-capability persistence is duplicate-tolerant
(a capability name a curator already accepted/rejected under is skipped, not
recreated) and component-edge inserts are ON CONFLICT DO NOTHING.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_module  # noqa: E402
from app.db import db_cursor  # noqa: E402
from app.vocabulary_bootstrap import run_bootstrap  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Compute and report candidates; write nothing.")
    parser.add_argument("--min-concept-support", type=int, default=3, help="Min distinct roles an atomic concept must appear in to seed a candidate capability (default 3).")
    parser.add_argument("--min-pair-support", type=int, default=5, help="Min distinct roles a candidate capability's core must co-occur in (default 5).")
    parser.add_argument("--max-candidates", type=int, default=150, help="Cap on candidate capabilities produced, most-evidenced first (default 150).")
    args = parser.parse_args()

    try:
        with db_cursor() as cur:
            result = run_bootstrap(
                cur,
                min_concept_support=args.min_concept_support,
                min_pair_support=args.min_pair_support,
                max_candidates=args.max_candidates,
                dry_run=args.dry_run,
            )
    finally:
        db_module.reset_pool()

    cluster = result["atomic_concept_clustering"]
    print(
        f"{'[dry-run] ' if args.dry_run else ''}atomic concepts: "
        f"auto_resolved={cluster['auto_resolved']} proposals_created={cluster['proposals_created']} "
        f"proposals_updated={cluster['proposals_updated']} proposals_keyed={cluster.get('proposals_keyed', 0)} "
        f"pending_clusters={cluster.get('pending_clusters', 0)}"
    )
    print(
        f"candidate capabilities: found={result['candidate_capabilities_found']} "
        f"created={result['persisted']['capabilities_created']} "
        f"skipped_existing_name={result['persisted']['capabilities_skipped_existing_name']} "
        f"component_edges_proposed={result['persisted']['component_edges_proposed']}"
    )
    if args.dry_run:
        print("\ncandidate capabilities (most-evidenced first):")
        for c in result["candidate_capabilities"]:
            print(
                f"  [{c['support_role_count']} roles] {c['suggested_name']} "
                f"({c['naming_source']}"
                + (f", sim={c['naming_similarity']:.2f}" if c["naming_similarity"] else "")
                + ") — core: "
                + ", ".join(c["core"])
            )
    else:
        print("\nfull report (JSON):")
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
