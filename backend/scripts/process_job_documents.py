"""Batch-process existing raw `jobber.document` rows (kind='job_posting')
through `job_posting_extract` -> `jobber.role_instance`
(docs/17-document-processing-pipeline.md).

Safe to interrupt and re-run: each document's own `extraction_run` history
decides whether it is (re)attempted (`app.document_processing`) — a document
that already succeeded is always skipped, never reprocessed.

Does NOT process the whole corpus by default just because you omit --limit —
pass it to bound a run. This script deliberately does not add a --force
option that would duplicate an already-successful role; use --retry-failed to
retry documents whose only prior attempts failed.

Usage:

    cd backend
    python -m scripts.process_job_documents --source-prefix historical_roles:v1: --limit 10
    python -m scripts.process_job_documents --source-prefix historical_roles:v1: --dry-run
    python -m scripts.process_job_documents --source-prefix historical_roles:v1: --retry-failed --limit 5
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import db_cursor  # noqa: E402
from app.document_processing import list_eligible_documents, process_job_posting_document  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Process at most N documents.")
    parser.add_argument("--source-prefix", default=None, help="Only documents whose source_key starts with this.")
    parser.add_argument("--date-from", default=None, help="Only documents with source_date >= this (YYYY-MM-DD).")
    parser.add_argument("--date-to", default=None, help="Only documents with source_date <= this (YYYY-MM-DD).")
    parser.add_argument("--retry-failed", action="store_true", help="Also retry documents whose latest attempt failed.")
    parser.add_argument("--dry-run", action="store_true", help="List what would be processed; make no changes.")
    args = parser.parse_args()

    with db_cursor() as cur:
        eligible = list_eligible_documents(
            cur,
            source_prefix=args.source_prefix,
            date_from=args.date_from,
            date_to=args.date_to,
            retry_failed=args.retry_failed,
            limit=args.limit,
        )

    selected = len(eligible)
    print(f"selected {selected} document(s)" + (" [dry-run]" if args.dry_run else ""))

    if args.dry_run:
        for doc in eligible:
            date_label = str(doc["source_date"]) if doc["source_date"] else "?"
            title = (doc["title"] or "untitled")[:60]
            print(f"  {date_label:<10} | {title}")
        print(f"\nselected={selected} processed=0 (dry-run — nothing was processed)")
        return 0

    totals = {
        "processed": 0,
        "succeeded": 0,
        "partial": 0,
        "failed": 0,
        "skipped_existing": 0,
        "embedding_pending": 0,
    }

    for i, doc in enumerate(eligible, start=1):
        date_label = str(doc["source_date"]) if doc["source_date"] else "?"
        title = (doc["title"] or "untitled")[:60]

        try:
            result = process_job_posting_document(str(doc["id"]))
        except Exception as e:  # noqa: BLE001 - one bad document must not abort the batch
            print(f"[{i}/{selected}] {date_label} | {title} | error: {e}")
            totals["failed"] += 1
            continue

        status = result["status"]
        print(f"[{i}/{selected}] {date_label} | {title} | {status}")

        if status in ("already_analysed", "already_processing"):
            totals["skipped_existing"] += 1
            continue

        totals["processed"] += 1
        if status == "ok":
            totals["succeeded"] += 1
        elif status == "partial":
            totals["partial"] += 1
        else:
            totals["failed"] += 1
        if result.get("embedding_error"):
            totals["embedding_pending"] += 1

    print(
        f"\nselected={selected} processed={totals['processed']} succeeded={totals['succeeded']} "
        f"partial={totals['partial']} failed={totals['failed']} "
        f"skipped_existing={totals['skipped_existing']} embedding_pending={totals['embedding_pending']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
