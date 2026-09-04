"""Corpus trend analytics (docs/18-consolidation-and-analytical-foundation.md
§7/§8) — a reusable, read-only analytical service over the captured role
corpus, answering: *what has this corpus historically required, how has
that changed, and what appears to be emerging?*

Everything here is a descriptive statistic over `jobber.role_instance` /
`role_skill_observation`, never a prediction. Every result carries its own
sample size; nothing is ever presented without one, and every classification
in `classify_trend` is a transparent, documented rule — never an opaque
score. See `TREND_METHODOLOGY` below for the exact rules, and
`docs/18-consolidation-and-analytical-foundation.md` §8 for the write-up.

**Source of evidence, and why**: `role_skill_observation` is the primary
signal — every one of the 307 historical roles produced skill observations
through the document-processing pipeline (docs/17), while `requirement_claim`
(the Phase 2 closed-vocabulary model) is populated only for roles that have
been through the separate, later, opt-in requirement-extraction step, which
the historical corpus has not been. Both are read where available:
`canonical_concept_id` (a resolved, curated concept) is preferred when set;
an unresolved `surface_form` is used as a raw fallback key, exactly the
brief's own ordering ("canonical concept/capability once vocabulary is
accepted; raw skill observation as a fallback while vocabulary is still
being curated").

**"Region"**: derived, not stored — `_REGION_BY_COUNTRY` is a small, explicit,
best-effort lookup for the countries this corpus is realistically expected to
contain (brief: "region where defensibly derivable"). A country missing from
the table reports `region: None` rather than a guess.
"""

from collections import defaultdict
from dataclasses import dataclass

# --- filters shared by every query below ------------------------------------


@dataclass
class TrendFilters:
    year_from: int | None = None
    year_to: int | None = None
    country: str | None = None
    seniority_level: str | None = None
    career_track: str | None = None  # the practical "role family" proxy — see module docstring below on archetype

    def where_sql(self, alias: str = "ri") -> tuple[str, list]:
        clauses = [f"{alias}.instance_type = 'observed_posting'"]
        params: list = []
        if self.year_from is not None:
            clauses.append(f"EXTRACT(YEAR FROM {alias}.posting_date) >= %s")
            params.append(self.year_from)
        if self.year_to is not None:
            clauses.append(f"EXTRACT(YEAR FROM {alias}.posting_date) <= %s")
            params.append(self.year_to)
        if self.country:
            clauses.append(f"{alias}.country = %s")
            params.append(self.country)
        if self.seniority_level:
            clauses.append(f"{alias}.seniority_level = %s")
            params.append(self.seniority_level)
        if self.career_track:
            clauses.append(f"{alias}.career_track = %s")
            params.append(self.career_track)
        return " AND ".join(clauses), params


# role_archetype_detail/archetype_concept_id exist in the schema (0002) but,
# like the capability catalogue, are not populated in production — role
# family/archetype grouping is offered wherever archetype_concept_id *is*
# set (brief: "when available"), and career_track is documented here, openly,
# as the practical fallback grouping this application already uses elsewhere
# (Dashboard's own track filter) — never silently conflated with a real
# curated archetype.
_REGION_BY_COUNTRY: dict[str, str] = {
    "Ireland": "Europe", "United Kingdom": "Europe", "UK": "Europe", "Germany": "Europe",
    "France": "Europe", "Netherlands": "Europe", "Switzerland": "Europe", "Spain": "Europe",
    "Italy": "Europe", "Belgium": "Europe", "Luxembourg": "Europe", "Poland": "Europe",
    "United States": "North America", "USA": "North America", "US": "North America",
    "Canada": "North America", "Mexico": "North America",
    "Australia": "Oceania", "New Zealand": "Oceania",
    "Singapore": "Asia", "Hong Kong": "Asia", "Japan": "Asia", "India": "Asia", "China": "Asia",
    "South Africa": "Africa", "United Arab Emirates": "Middle East", "UAE": "Middle East",
}


def region_for_country(country: str | None) -> str | None:
    if not country:
        return None
    return _REGION_BY_COUNTRY.get(country.strip())


# --- corpus overview ---------------------------------------------------------


def corpus_overview(cur, filters: TrendFilters) -> dict:
    """role_count plus breakdowns by year/country/region/seniority/
    career_track for the *filtered* scope — "how has the composition of
    roles changed" at a glance, and the sample-size context every other
    query here should be read alongside."""
    where_sql, params = filters.where_sql()
    cur.execute(f"SELECT COUNT(*) AS n FROM jobber.role_instance ri WHERE {where_sql}", params)
    role_count = cur.fetchone()["n"]

    def _breakdown(column_sql: str, label_transform=None) -> list[dict]:
        cur.execute(
            f"SELECT {column_sql} AS bucket, COUNT(*) AS n FROM jobber.role_instance ri "
            f"WHERE {where_sql} GROUP BY bucket ORDER BY bucket",
            params,
        )
        rows = cur.fetchall()
        out = []
        for row in rows:
            bucket = row["bucket"]
            if label_transform:
                bucket = label_transform(bucket)
            out.append({"value": bucket, "role_count": row["n"]})
        return out

    by_year = _breakdown("EXTRACT(YEAR FROM ri.posting_date)::int", lambda v: v)
    by_country = _breakdown("ri.country")
    by_seniority = _breakdown("ri.seniority_level")
    by_career_track = _breakdown("ri.career_track")

    region_counts: dict[str, int] = defaultdict(int)
    for row in by_country:
        region = region_for_country(row["value"])
        if region:
            region_counts[region] += row["role_count"]
    by_region = [{"value": k, "role_count": v} for k, v in sorted(region_counts.items(), key=lambda kv: -kv[1])]

    return {
        "sample_size": role_count,
        "by_year": by_year,
        "by_country": by_country,
        "by_region": by_region,
        "by_seniority": by_seniority,
        "by_career_track": by_career_track,
    }


# --- requirement frequency ---------------------------------------------------


def top_requirements(cur, filters: TrendFilters, *, min_sample_size: int = 5, limit: int = 30) -> dict:
    """Ranked requirement frequency for the current filtered scope — "what
    does this corpus require" for a given slice (a year range, a country, a
    seniority band, ...). Groups by canonical concept where resolved, by
    normalised surface form otherwise (brief: canonical concept when
    accepted, raw observation as a fallback while vocabulary is still being
    curated) — the two are never merged into one row, so a curator can see
    exactly which frequent terms still await resolution.
    `insufficient_sample` flags the whole result when the scope itself is
    too small to say anything (brief: "never present tiny-sample movements
    as robust market trends") — callers should visibly warn, not silently
    hide, in that case.
    """
    where_sql, params = filters.where_sql()
    cur.execute(f"SELECT COUNT(*) AS n FROM jobber.role_instance ri WHERE {where_sql}", params)
    total_roles = cur.fetchone()["n"]

    cur.execute(
        f"""
        SELECT
            rso.canonical_concept_id, c.canonical_name, c.type_code,
            CASE WHEN rso.canonical_concept_id IS NULL THEN lower(trim(rso.surface_form)) END AS surface_key,
            COUNT(DISTINCT rso.role_instance_id) AS role_count,
            COUNT(DISTINCT rso.role_instance_id) FILTER (WHERE rso.requirement_type = 'required') AS n_required,
            COUNT(DISTINCT rso.role_instance_id) FILTER (WHERE rso.requirement_type = 'preferred') AS n_preferred,
            COUNT(DISTINCT rso.role_instance_id) FILTER (WHERE rso.requirement_type = 'inferred') AS n_inferred
        FROM jobber.role_skill_observation rso
        JOIN jobber.role_instance ri ON ri.id = rso.role_instance_id
        LEFT JOIN jobber.concept c ON c.id = rso.canonical_concept_id
        WHERE {where_sql}
        GROUP BY rso.canonical_concept_id, c.canonical_name, c.type_code, surface_key
        ORDER BY role_count DESC
        LIMIT %s
        """,
        [*params, limit],
    )
    items = []
    for row in cur.fetchall():
        items.append(
            {
                "concept_id": str(row["canonical_concept_id"]) if row["canonical_concept_id"] else None,
                "label": row["canonical_name"] or row["surface_key"],
                "type_code": row["type_code"],
                "is_canonical": row["canonical_concept_id"] is not None,
                "role_count": row["role_count"],
                "proportion": round(row["role_count"] / total_roles, 4) if total_roles else None,
                "by_requirement_type": {"required": row["n_required"], "preferred": row["n_preferred"], "inferred": row["n_inferred"]},
            }
        )

    return {
        "sample_size": total_roles,
        "insufficient_sample": total_roles < min_sample_size,
        "min_sample_size": min_sample_size,
        "items": items,
    }


def _requirement_key_clause(requirement_key: dict) -> tuple[str, list]:
    """`requirement_key` is `{"concept_id": ...}` or `{"surface_form": ...}` —
    exactly one, matching how `top_requirements` distinguishes resolved vs
    unresolved rows."""
    if requirement_key.get("concept_id"):
        return "rso.canonical_concept_id = %s", [requirement_key["concept_id"]]
    return "rso.canonical_concept_id IS NULL AND lower(trim(rso.surface_form)) = %s", [requirement_key["surface_form"].strip().lower()]


def _period_bucket_sql(granularity: str, alias: str = "ri") -> str:
    if granularity == "5year":
        return f"(FLOOR(EXTRACT(YEAR FROM {alias}.posting_date) / 5) * 5)::int"
    return f"EXTRACT(YEAR FROM {alias}.posting_date)::int"


def requirement_trend(cur, requirement_key: dict, filters: TrendFilters, *, granularity: str = "year", min_sample_size: int = 5) -> dict:
    """Per-period series for ONE requirement: role_count/proportion/sample
    size at each period, plus a deterministic `classify_trend` verdict.
    `total_roles_by_period` (the denominator) always covers every role in
    scope for that period, not only ones with any observation at all, so
    proportion is a real "fraction of roles requiring this", never inflated
    by silently excluding roles with zero extracted skills."""
    where_sql, params = filters.where_sql()
    key_sql, key_params = _requirement_key_clause(requirement_key)
    bucket_sql = _period_bucket_sql(granularity)

    cur.execute(
        f"SELECT {bucket_sql} AS period, COUNT(*) AS total FROM jobber.role_instance ri "
        f"WHERE {where_sql} AND ri.posting_date IS NOT NULL GROUP BY period ORDER BY period",
        params,
    )
    total_by_period = {row["period"]: row["total"] for row in cur.fetchall()}

    cur.execute(
        f"""
        SELECT {_period_bucket_sql(granularity)} AS period, COUNT(DISTINCT rso.role_instance_id) AS role_count
        FROM jobber.role_skill_observation rso
        JOIN jobber.role_instance ri ON ri.id = rso.role_instance_id
        WHERE {where_sql} AND ri.posting_date IS NOT NULL AND ({key_sql})
        GROUP BY period ORDER BY period
        """,
        [*params, *key_params],
    )
    matched_by_period = {row["period"]: row["role_count"] for row in cur.fetchall()}

    series = []
    for period, total in sorted(total_by_period.items()):
        matched = matched_by_period.get(period, 0)
        series.append(
            {
                "period": period,
                "role_count": matched,
                "total_roles": total,
                "proportion": round(matched / total, 4) if total else None,
                "sample_size": total,
            }
        )

    return {"granularity": granularity, "series": series, "classification": classify_trend(series, min_sample_size=min_sample_size)}


def cooccurring_requirements(cur, requirement_key: dict, filters: TrendFilters, *, min_count: int = 3, limit: int = 15) -> dict:
    """"Which skills cluster together" for one requirement: other canonical
    concepts (resolved observations only — a co-occurrence signal over raw,
    un-deduplicated surface forms would be noisy) appearing in the same
    roles, ranked by how many roles they share."""
    where_sql, params = filters.where_sql()
    key_sql, key_params = _requirement_key_clause(requirement_key)

    cur.execute(
        f"""
        SELECT ri.id FROM jobber.role_instance ri
        JOIN jobber.role_skill_observation rso ON rso.role_instance_id = ri.id
        WHERE {where_sql} AND ({key_sql})
        """,
        [*params, *key_params],
    )
    role_ids = [str(r["id"]) for r in cur.fetchall()]
    if not role_ids:
        return {"sample_size": 0, "items": []}

    cur.execute(
        """
        SELECT c.id AS concept_id, c.canonical_name, c.type_code, COUNT(DISTINCT rso.role_instance_id) AS co_count
        FROM jobber.role_skill_observation rso
        JOIN jobber.concept c ON c.id = rso.canonical_concept_id
        WHERE rso.role_instance_id = ANY(%s::uuid[])
        GROUP BY c.id, c.canonical_name, c.type_code
        HAVING COUNT(DISTINCT rso.role_instance_id) >= %s
        ORDER BY co_count DESC
        LIMIT %s
        """,
        (role_ids, min_count, limit),
    )
    sample_size = len(role_ids)
    items = [
        {
            "concept_id": str(row["concept_id"]),
            "canonical_name": row["canonical_name"],
            "type_code": row["type_code"],
            "co_count": row["co_count"],
            "proportion_of_roles": round(row["co_count"] / sample_size, 4),
        }
        for row in cur.fetchall()
        # excludes the requirement itself, when it is the resolved concept being queried
        if requirement_key.get("concept_id") != str(row["concept_id"])
    ]
    return {"sample_size": sample_size, "items": items}


def compare_dimension(cur, requirement_key: dict, filters: TrendFilters, *, dimension: str, min_sample_size: int = 5) -> dict:
    """One requirement's frequency broken down by `dimension`
    ('country'|'seniority_level'|'career_track') — "how do Ireland/UK/other
    differ", "what distinguishes senior from junior roles"."""
    if dimension not in ("country", "seniority_level", "career_track"):
        raise ValueError("dimension must be one of country, seniority_level, career_track")

    where_sql, params = filters.where_sql()
    key_sql, key_params = _requirement_key_clause(requirement_key)

    cur.execute(
        f"SELECT ri.{dimension} AS bucket, COUNT(*) AS total FROM jobber.role_instance ri "
        f"WHERE {where_sql} AND ri.{dimension} IS NOT NULL GROUP BY bucket",
        params,
    )
    total_by_bucket = {row["bucket"]: row["total"] for row in cur.fetchall()}

    cur.execute(
        f"""
        SELECT ri.{dimension} AS bucket, COUNT(DISTINCT rso.role_instance_id) AS role_count
        FROM jobber.role_skill_observation rso
        JOIN jobber.role_instance ri ON ri.id = rso.role_instance_id
        WHERE {where_sql} AND ri.{dimension} IS NOT NULL AND ({key_sql})
        GROUP BY bucket
        """,
        [*params, *key_params],
    )
    matched_by_bucket = {row["bucket"]: row["role_count"] for row in cur.fetchall()}

    items = []
    for bucket, total in sorted(total_by_bucket.items(), key=lambda kv: -kv[1]):
        matched = matched_by_bucket.get(bucket, 0)
        items.append(
            {
                "value": bucket,
                "role_count": matched,
                "sample_size": total,
                "proportion": round(matched / total, 4) if total else None,
                "insufficient_sample": total < min_sample_size,
            }
        )
    return {"dimension": dimension, "items": items}


# --- trend classification (docs/18 §8) ---------------------------------------
#
# TREND_METHODOLOGY documents every threshold below verbatim — this is
# deliberately a small set of transparent, fixed rules over the recorded
# proportions, never a trained/opaque score, and it never claims predictive
# certainty: labels describe the corpus's own recorded history only.

SPARSE_MIN_SAMPLE = 5           # a period below this sample size cannot support any classification
EMERGING_EARLY_MAX_PROPORTION = 0.02  # "essentially absent" in the early window
CHANGE_RELATIVE_THRESHOLD = 0.30      # +/-30% relative change between early/late windows

TREND_METHODOLOGY = f"""
Trend classification methodology (deterministic, not statistical inference):

1. Only periods with sample_size >= {SPARSE_MIN_SAMPLE} roles count as usable evidence.
2. If fewer than 2 usable periods exist, or the requirement was observed in
   zero usable periods, the verdict is 'sparse_insufficient_evidence' —
   there is not enough recorded history to say anything, and this label is
   preferred over a false-confidence guess.
3. Otherwise, usable periods are split chronologically into an early half
   and a late half (the earlier half gets the extra period on an odd split,
   so a 3-period series is 2-early/1-late — weighting toward more evidence
   on the harder-to-establish "was it there before" side). The mean
   proportion of each half is computed (simple mean of that half's
   per-period proportions, not weighted further by sample size beyond the
   {SPARSE_MIN_SAMPLE}-role usability floor already applied).
4. If the early-half mean proportion is <= {EMERGING_EARLY_MAX_PROPORTION}
   (essentially unseen) and the late-half mean is greater than that floor,
   the verdict is 'emerging'.
5. Otherwise, if the late-half mean is >= {1 + CHANGE_RELATIVE_THRESHOLD:.2f}x
   the early-half mean, the verdict is 'increasing'; if it is
   <= {1 - CHANGE_RELATIVE_THRESHOLD:.2f}x, 'declining'.
6. Otherwise, 'persistent' — present throughout with no large relative
   change either way.

This is extrapolation from this corpus's own recorded history, not a
forecast of the wider labour market — every classification is returned
alongside the exact per-period counts it was computed from, so its evidence
can be inspected directly rather than taken on trust.
"""


def classify_trend(series: list[dict], *, min_sample_size: int = SPARSE_MIN_SAMPLE) -> dict:
    usable = [p for p in series if p["sample_size"] >= min_sample_size and p["proportion"] is not None]
    if len(usable) < 2:
        return {
            "label": "sparse_insufficient_evidence",
            "rationale": f"fewer than 2 periods with >= {min_sample_size} roles",
            "usable_periods": len(usable),
            "total_periods": len(series),
        }

    split = (len(usable) + 1) // 2  # earlier half gets the extra period on an odd count
    early, late = usable[:split], usable[split:]
    if not late:  # degenerate: only one usable period after the split (shouldn't happen given len(usable) >= 2, kept defensive)
        early, late = usable[:1], usable[1:]

    early_mean = sum(p["proportion"] for p in early) / len(early)
    late_mean = sum(p["proportion"] for p in late) / len(late)

    if early_mean <= EMERGING_EARLY_MAX_PROPORTION and late_mean > EMERGING_EARLY_MAX_PROPORTION:
        label = "emerging"
    elif early_mean == 0 and late_mean == 0:
        label = "persistent"  # both flat at zero — not "emerging" (never crossed the floor) and not sparse (real usable evidence exists)
    elif late_mean >= early_mean * (1 + CHANGE_RELATIVE_THRESHOLD):
        label = "increasing"
    elif late_mean <= early_mean * (1 - CHANGE_RELATIVE_THRESHOLD):
        label = "declining"
    else:
        label = "persistent"

    return {
        "label": label,
        "rationale": f"early-period mean proportion {early_mean:.3f} ({len(early)} period(s)) vs. late-period mean {late_mean:.3f} ({len(late)} period(s))",
        "usable_periods": len(usable),
        "total_periods": len(series),
        "early_mean_proportion": round(early_mean, 4),
        "late_mean_proportion": round(late_mean, 4),
    }
