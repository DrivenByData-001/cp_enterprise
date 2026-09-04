"""Read-only first-tranche diagnostic report (Vocabulary Proposal
Prioritisation and Curation UX brief §13).

Reports what the deterministic prioritisation methodology
(`app/vocabulary_priority.py`) produces over the CURRENT pending proposal
queue: band distribution, the top-N clusters by priority, noise/sparse
diagnostics, and the cumulative role-skill-observation coverage accepting
the top 25/50/100/150 clusters would unlock — assuming every one of them is
ultimately judged valid, which this report does not decide.

**Read-only, always.** This script never imports or calls `accept_cluster`,
`reject_cluster`, `merge_cluster`, or `execute_batch` — every function it
touches (`build_pending_cluster_index`, `cluster_summary`, `get_progress`)
issues SELECTs only. Running this against a production DATABASE_URL is safe
diagnostics (brief §12: "Production proposal rows may be read for
diagnostics if needed"), never curation, and this report is never itself
permission to accept anything it lists (brief §13's own words) — any actual
acceptance still requires a human to call the accept/reject/merge/batch
endpoints explicitly through the reviewed UI.

Usage (same DATABASE_URL convention as every other script in backend/scripts —
an operator chooses what it points at; this script does not know or care
whether that is a disposable local database or production):

    cd backend
    DATABASE_URL=postgresql://... python -m scripts.vocabulary_priority_report
    DATABASE_URL=postgresql://... python -m scripts.vocabulary_priority_report --top 100 --json
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_module  # noqa: E402
from app import vocabulary_curation as curation  # noqa: E402
from app.db import db_cursor  # noqa: E402
from app.vocabulary_priority import (  # noqa: E402
    NOISE_SINGLE_OBSERVATION,
    NOISE_SINGLE_ROLE,
    PRIORITY_BANDS,
)

TOP_N_CUTS = (25, 50, 100, 150)


def build_report(cur, *, top_display: int = 50) -> dict:
    current_year = datetime.datetime.now(datetime.timezone.utc).year
    evidence = curation.build_pending_cluster_index(cur, example_limit=curation.DETAIL_EXAMPLE_ROLE_LIMIT)

    summaries = [curation.cluster_summary(ev, current_year=current_year) for ev in evidence.values()]
    summaries.sort(
        key=lambda r: (-(r["priority_score"] or 0), -(r["role_count"] or 0), -(r["observation_count"] or 0), r["cluster_key"])
    )
    # Evidence objects in the same final order, for the exact-coverage union below.
    ordered_evidence = [evidence[r["cluster_key"]] for r in summaries]

    band_counts = {b: 0 for b in PRIORITY_BANDS}
    for r in summaries:
        band_counts[r["priority_band"]] += 1

    single_role = sum(1 for r in summaries if r["role_count"] == 1)
    single_observation = sum(1 for r in summaries if r["observation_count"] == 1)
    noise_flagged = sum(
        1 for r in summaries if any(f not in (NOISE_SINGLE_ROLE, NOISE_SINGLE_OBSERVATION) for f in r["flags"])
    )

    cur.execute("SELECT COUNT(*) AS n FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL")
    total_unresolved_observations = cur.fetchone()["n"]
    cur.execute(
        "SELECT COUNT(DISTINCT role_instance_id) AS n FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL"
    )
    total_roles_with_unresolved_observations = cur.fetchone()["n"]

    coverage = {}
    for n in TOP_N_CUTS:
        top_slice = ordered_evidence[:n]
        obs_covered = sum(ev.observation_count for ev in top_slice)
        role_union: set[str] = set()
        for ev in top_slice:
            role_union |= ev.role_ids
        coverage[n] = {
            "clusters": len(top_slice),
            "observations_covered": obs_covered,
            "observations_covered_pct": round(100 * obs_covered / total_unresolved_observations, 1) if total_unresolved_observations else None,
            "distinct_roles_covered": len(role_union),
            "distinct_roles_covered_pct": round(100 * len(role_union) / total_roles_with_unresolved_observations, 1) if total_roles_with_unresolved_observations else None,
        }

    top_display_rows = []
    for r in summaries[:top_display]:
        top_display_rows.append(
            {
                "cluster_key": r["cluster_key"],
                "label": r["suggested_canonical_label"],
                "aliases": r["surface_forms"],
                "priority_score": r["priority_score"],
                "priority_band": r["priority_band"],
                "role_count": r["role_count"],
                "observation_count": r["observation_count"],
                "year_span": f"{r['first_observed']}..{r['last_observed']}" if r["first_observed"] else None,
                "countries_count": len(r.get("countries") or []),
                "seniority_count": len(r.get("seniority_levels") or []),
                "flags": r["flags"],
            }
        )

    return {
        "total_clusters_ranked": len(summaries),
        "band_counts": band_counts,
        "single_role_clusters": single_role,
        "single_observation_clusters": single_observation,
        "noise_flagged_clusters": noise_flagged,
        "total_unresolved_observations": total_unresolved_observations,
        "total_roles_with_unresolved_observations": total_roles_with_unresolved_observations,
        "cumulative_coverage_if_accepted": coverage,
        "top": top_display_rows,
    }


def _print_human(report: dict, progress: dict) -> None:
    print("=== Vocabulary curation priority report (READ-ONLY diagnostic — not an acceptance) ===\n")
    print(f"Total clusters ranked: {report['total_clusters_ranked']}")
    print(f"Total pending clusters (progress endpoint): {progress['pending_clusters']}")
    print("Band distribution:")
    for band in PRIORITY_BANDS:
        print(f"  {band:8s}: {report['band_counts'][band]}")
    print(f"\nSingle-role clusters: {report['single_role_clusters']}")
    print(f"Single-observation clusters: {report['single_observation_clusters']}")
    print(f"Noise-flagged clusters (beyond single_role/single_observation): {report['noise_flagged_clusters']}")

    print("\nCumulative coverage if the top N clusters were ultimately accepted (diagnostic estimate only):")
    for n, c in report["cumulative_coverage_if_accepted"].items():
        print(
            f"  top {n:4d} ({c['clusters']:4d} available): "
            f"{c['observations_covered']} observations ({c['observations_covered_pct']}%), "
            f"{c['distinct_roles_covered']} distinct roles ({c['distinct_roles_covered_pct']}%)"
        )

    print(f"\nTop {len(report['top'])} clusters by priority:")
    for i, row in enumerate(report["top"], 1):
        alias_note = f" [{', '.join(row['aliases'][:4])}]" if len(row["aliases"]) > 1 else ""
        flag_note = f" flags={row['flags']}" if row["flags"] else ""
        print(
            f"  {i:3d}. [{row['priority_band']:6s} {row['priority_score']:6.2f}] {row['label']}{alias_note} — "
            f"{row['role_count']} roles, {row['observation_count']} obs, {row['year_span']}, "
            f"{row['countries_count']} countries, {row['seniority_count']} seniority levels{flag_note}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=50, help="How many top clusters to list in detail (default 50).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of the human-readable report.")
    args = parser.parse_args()

    try:
        with db_cursor() as cur:
            report = build_report(cur, top_display=args.top)
            progress = curation.get_progress(cur)
    finally:
        db_module.reset_pool()

    if args.json:
        print(json.dumps({"report": report, "progress": progress}, indent=2, default=str))
    else:
        _print_human(report, progress)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
