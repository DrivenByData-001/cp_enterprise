"""Market analytics read model (docs/25 prompt): a descriptive analytical
layer over **every accepted** `jobber.compensation_observation` row,
whether or not it carries an `archetype_concept_id`. This is deliberately
separate from `economics_engine.py`'s archetype-benchmark/gap-value
machinery — the two are independent consumers of the same evidence layer:

    accepted market evidence -> market analytics (this module)
    accepted archetype-linked evidence -> archetype benchmarks -> Gap Value

Nothing here writes to the database, recomputes an archetype benchmark, or
changes Gap Value semantics. Every function below is a pure function of an
already-fetched evidence-row list except `fetch_accepted_evidence_rows`
itself, so the analytical logic can be unit-tested without a database
(docs/25 §4, §11.1).

## Design posture (docs/25 §3/§8)

Display what the source actually reported; never coerce unlike statistics
into a common one. A reported median stays a median, a reported mean stays
a mean, a range stays a range. Where a midpoint is computed for sorting or
positioning (role ranges), it is always a separate field explicitly named
`derived_range_midpoint` and never overwrites `reported_p50`. No synthetic
cross-provider consensus is computed anywhere in this module — providers
and documents are kept statistically separate throughout (comparisons only
ever combine rows sharing one document/provider and the same segmentation).
"""

import re
from dataclasses import dataclass
from datetime import date

# --- Practice-area analytical grouping (docs/25 §5) -------------------------
#
# A small, deterministic, centrally-tested normalisation for *analysis and
# display only* — `domain_or_practice_area` is never mutated in storage, and
# this grouping is not written anywhere as ontology/vocabulary truth. Any
# label not recognised below is preserved as its own analytical group rather
# than forced into one of these buckets.

PRACTICE_GROUP_UNSPECIFIED = "Unspecified"
PRACTICE_GROUP_ALL = "All practice areas"

_PRACTICE_GROUP_MAP: dict[str, str] = {
    "life": "Life",
    "life insurance": "Life",
    "non-life": "Non-Life / GI",
    "general insurance": "Non-Life / GI",
    "pensions": "Pensions",
    "reinsurance & london market": "Reinsurance / London Market",
    "all practice areas": PRACTICE_GROUP_ALL,
}

# Practice "groups" that must never be treated as one side of a same-context
# practice comparison (docs/25 §4.2 practice_comparisons: "Do not treat 'All
# practice areas' as a specific practice") — comparing against "Unspecified"
# would be equally meaningless, since it means no practice was reported at
# all for that row.
_EXCLUDED_COMPARISON_PRACTICE_GROUPS = {PRACTICE_GROUP_UNSPECIFIED, PRACTICE_GROUP_ALL}

# Only base vs total_package is a meaningful component comparison (docs/25
# §4.2/§8.4) — day_rate and bonus_pct are never compared against either.
_COMPONENT_COMPARISON_COMPONENTS = {"base", "total_package"}

_TREND_MIN_PERIODS = 2

_BAND_NUMERIC_RE = re.compile(r"(\d+(?:\.\d+)?)")


def group_practice_area(raw: str | None) -> str:
    """Deterministic, presentation-only analytical grouping. Case/whitespace
    -insensitive match against the known synonym map; a blank/null label
    becomes `Unspecified`; any other non-empty label is preserved verbatim
    as its own group (never forced into an unrelated bucket)."""
    if raw is None or not raw.strip():
        return PRACTICE_GROUP_UNSPECIFIED
    key = re.sub(r"\s+", " ", raw.strip().lower())
    return _PRACTICE_GROUP_MAP.get(key, raw.strip())


# --- Filters (docs/25 §4.1) --------------------------------------------------


@dataclass
class MarketAnalyticsFilters:
    market_id: str | None = None
    currency: str | None = None
    component: str | None = None
    pay_period: str | None = None
    practice_group: str | None = None
    provider: str | None = None
    source_kind: str | None = None
    employment_basis: str | None = None
    period_from: date | None = None
    period_to: date | None = None


def apply_filters(rows: list[dict], filters: MarketAnalyticsFilters) -> list[dict]:
    """Every dimension here genuinely exists in the evidence (docs/25 §4.1)
    — archetype is deliberately never one of them, so unmapped evidence can
    never be filtered out by construction. Order-preserving: callers rely on
    `rows` already being sorted (most-recent-period first)."""
    out = []
    for r in rows:
        if filters.market_id and r["market_id"] != filters.market_id:
            continue
        if filters.currency and r["currency"] != filters.currency:
            continue
        if filters.component and r["component"] != filters.component:
            continue
        if filters.pay_period and r["pay_period"] != filters.pay_period:
            continue
        if filters.practice_group and r["practice_group"] != filters.practice_group:
            continue
        if filters.provider and r["provider"] != filters.provider:
            continue
        if filters.source_kind and r["source_kind"] != filters.source_kind:
            continue
        if filters.employment_basis and r["employment_basis"] != filters.employment_basis:
            continue
        if filters.period_from and (not r["period_key"] or r["period_key"] < filters.period_from):
            continue
        if filters.period_to and (not r["period_key"] or r["period_key"] > filters.period_to):
            continue
        out.append(r)
    return out


# --- Fetch (the one impure function in this module) -------------------------


def _num(value) -> float | None:
    return float(value) if value is not None else None


def fetch_accepted_evidence_rows(cur, limit: int = 20000) -> list[dict]:
    """Every `review_status='accepted'` compensation observation, regardless
    of `archetype_concept_id` (docs/25 §1/§4: archetype assignment must not
    be required for inclusion). `limit` is a defensive cap only (docs/25
    §14) — historical backfill is expected to grow this table well below
    that bound for the foreseeable future."""
    cur.execute(
        """
        SELECT co.id, co.raw_role_label, co.market_id, m.label AS market_label, co.archetype_concept_id,
               co.component, co.pay_period, co.employment_basis, co.currency,
               co.amount_min, co.amount_mid, co.amount_max,
               co.reported_p25, co.reported_p50, co.reported_p75, co.reported_mean, co.bonus_pct,
               co.reported_sample_size, co.source_quality, co.source_kind, co.basis,
               co.geography_reported, co.domain_or_practice_area, co.seniority_band_reported,
               co.experience_band, co.pqe_band, co.document_id, co.page_reference, co.table_reference,
               co.source_note, co.observed_at, co.period_end, co.created_at,
               d.source AS provider, d.title AS document_title, d.source_date AS report_date,
               d.source_payload AS document_source_payload
        FROM jobber.compensation_observation co
        LEFT JOIN jobber.market m ON m.id = co.market_id
        LEFT JOIN jobber.document d ON d.id = co.document_id
        WHERE co.review_status = 'accepted'
        ORDER BY COALESCE(co.period_end, co.observed_at) DESC NULLS LAST, co.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = []
    for raw in cur.fetchall():
        raw = dict(raw)
        document_title = raw.get("document_title") or (raw.get("document_source_payload") or {}).get("report_title")
        period_key = raw["period_end"] or raw["observed_at"]
        rows.append(
            {
                "id": str(raw["id"]),
                "raw_role_label": raw["raw_role_label"],
                "market_id": str(raw["market_id"]) if raw["market_id"] else None,
                "market_label": raw["market_label"],
                "archetype_concept_id": str(raw["archetype_concept_id"]) if raw["archetype_concept_id"] else None,
                "component": raw["component"],
                "pay_period": raw["pay_period"],
                "employment_basis": raw["employment_basis"],
                "currency": raw["currency"],
                "amount_min": _num(raw["amount_min"]),
                "amount_mid": _num(raw["amount_mid"]),
                "amount_max": _num(raw["amount_max"]),
                "reported_p25": _num(raw["reported_p25"]),
                "reported_p50": _num(raw["reported_p50"]),
                "reported_p75": _num(raw["reported_p75"]),
                "reported_mean": _num(raw["reported_mean"]),
                "bonus_pct": _num(raw["bonus_pct"]),
                "reported_sample_size": raw["reported_sample_size"],
                "source_quality": raw["source_quality"],
                "source_kind": raw["source_kind"],
                "basis": raw["basis"],
                "geography_reported": raw["geography_reported"],
                "domain_or_practice_area": raw["domain_or_practice_area"],
                "practice_group": group_practice_area(raw["domain_or_practice_area"]),
                "seniority_band_reported": raw["seniority_band_reported"],
                "experience_band": raw["experience_band"],
                "pqe_band": raw["pqe_band"],
                "document_id": str(raw["document_id"]) if raw["document_id"] else None,
                "page_reference": raw["page_reference"],
                "table_reference": raw["table_reference"],
                "source_note": raw["source_note"],
                "observed_at": raw["observed_at"],
                "period_end": raw["period_end"],
                "period_key": period_key,
                "created_at": raw["created_at"],
                "provider": raw["provider"],
                "document_title": document_title,
                "report_date": raw["report_date"],
            }
        )
    return rows


# --- coverage (docs/25 §4.2) -------------------------------------------------


def build_coverage(rows: list[dict]) -> dict:
    documents = {r["document_id"] for r in rows if r["document_id"]}
    providers = {r["provider"] for r in rows if r["provider"]}
    periods = [r["period_key"] for r in rows if r["period_key"]]
    return {
        "accepted_observation_count": len(rows),
        "distinct_source_document_count": len(documents),
        "distinct_provider_count": len(providers),
        "earliest_period": min(periods) if periods else None,
        "latest_period": max(periods) if periods else None,
        "with_reported_median_count": sum(1 for r in rows if r["reported_p50"] is not None),
        "with_reported_mean_count": sum(1 for r in rows if r["reported_mean"] is not None),
        "with_range_count": sum(1 for r in rows if r["amount_min"] is not None or r["amount_max"] is not None),
        "with_sample_size_count": sum(1 for r in rows if r["reported_sample_size"] is not None),
        "archetype_linked_count": sum(1 for r in rows if r["archetype_concept_id"] is not None),
        "not_archetype_linked_count": sum(1 for r in rows if r["archetype_concept_id"] is None),
    }


# --- facets (docs/25 §4.2) — always computed from *every* accepted row, ----
# never the currently-filtered subset, so the filter bar's own options never
# shrink out from under the user as they filter (§4.2: "stable labels/counts").


def _facet(rows: list[dict], key: str, label_key: str | None = None) -> list[dict]:
    counts: dict = {}
    labels: dict = {}
    for r in rows:
        value = r.get(key)
        if value is None or value == "":
            continue
        counts[value] = counts.get(value, 0) + 1
        if label_key:
            labels[value] = r.get(label_key) or value
    return sorted(
        (
            {"value": value, "label": labels.get(value, value), "count": count}
            for value, count in counts.items()
        ),
        key=lambda item: (-item["count"], str(item["label"])),
    )


def _year_facet(rows: list[dict]) -> list[dict]:
    counts: dict[int, int] = {}
    for r in rows:
        period_key = r.get("period_key")
        if not period_key:
            continue
        counts[period_key.year] = counts.get(period_key.year, 0) + 1
    return sorted(
        ({"value": year, "label": str(year), "count": count} for year, count in counts.items()),
        key=lambda item: -item["value"],
    )


def build_facets(rows: list[dict]) -> dict:
    return {
        "markets": _facet(rows, "market_id", "market_label"),
        "currencies": _facet(rows, "currency"),
        "components": _facet(rows, "component"),
        "pay_periods": _facet(rows, "pay_period"),
        "practice_groups": _facet(rows, "practice_group"),
        "providers": _facet(rows, "provider"),
        "source_kinds": _facet(rows, "source_kind"),
        "years": _year_facet(rows),
    }


# --- pqe_series / experience_series (docs/25 §4.2) --------------------------
#
# One point per accepted observation carrying that band — never aggregated
# or interpolated across rows/bands. `_band_sort_key` gives a deterministic
# display order by numeric lower bound where parseable, while an unparseable
# band stays visible (sorted after parseable ones) rather than being dropped.


def _band_sort_key(band: str | None) -> tuple:
    if not band:
        return (2, 0.0, "")
    match = _BAND_NUMERIC_RE.search(band)
    if match:
        return (0, float(match.group(1)), band)
    return (1, 0.0, band)


def _to_point(row: dict, band_value: str) -> dict:
    return {
        "observation_id": row["id"],
        "provider": row["provider"],
        "document_id": row["document_id"],
        "document_title": row["document_title"],
        "report_date": row["report_date"],
        "market_id": row["market_id"],
        "market_label": row["market_label"],
        "geography_reported": row["geography_reported"],
        "raw_role_label": row["raw_role_label"],
        "practice_reported": row["domain_or_practice_area"],
        "practice_group": row["practice_group"],
        "band": band_value,
        "component": row["component"],
        "pay_period": row["pay_period"],
        "currency": row["currency"],
        "reported_p50": row["reported_p50"],
        "reported_mean": row["reported_mean"],
        "amount_min": row["amount_min"],
        "amount_max": row["amount_max"],
        "reported_sample_size": row["reported_sample_size"],
        "source_kind": row["source_kind"],
        "source_quality": row["source_quality"],
        "archetype_concept_id": row["archetype_concept_id"],
    }


def build_pqe_series(rows: list[dict]) -> list[dict]:
    items = [r for r in rows if r.get("pqe_band")]
    items.sort(key=lambda r: (_band_sort_key(r["pqe_band"]), str(r["provider"]), r["id"]))
    return [_to_point(r, r["pqe_band"]) for r in items]


def build_experience_series(rows: list[dict]) -> list[dict]:
    items = [r for r in rows if r.get("experience_band")]
    items.sort(key=lambda r: (_band_sort_key(r["experience_band"]), str(r["provider"]), r["id"]))
    return [_to_point(r, r["experience_band"]) for r in items]


# --- practice_comparisons / component_comparisons (docs/25 §4.2) -----------
#
# Both only ever compare rows sharing one *document* (a document implies one
# provider) plus the segmentation the spec names explicitly — never rows
# merely sharing a market or a loosely-matching label.


def _band_type_value(row: dict) -> tuple[str | None, str | None]:
    if row.get("pqe_band"):
        return "pqe", row["pqe_band"]
    if row.get("experience_band"):
        return "experience", row["experience_band"]
    return None, None


def build_practice_comparisons(rows: list[dict]) -> list[dict]:
    """Same document + component + pay_period + currency + band(type/value)
    held constant; practice_group is the one thing allowed to vary — that is
    the comparison. Requires >=2 distinct *real* practices (excluding "All
    practice areas"/"Unspecified", neither of which is a specific practice)."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if not r["document_id"]:
            continue
        band_type, band = _band_type_value(r)
        key = (r["document_id"], r["component"], r["pay_period"], r["currency"], band_type, band)
        groups.setdefault(key, []).append(r)

    comparisons = []
    for (document_id, component, pay_period, currency, band_type, band), items in groups.items():
        comparable = [r for r in items if r["practice_group"] not in _EXCLUDED_COMPARISON_PRACTICE_GROUPS]
        distinct_practices = {r["practice_group"] for r in comparable}
        if len(distinct_practices) < 2:
            continue
        sample = items[0]
        comparisons.append(
            {
                "context": {
                    "document_id": document_id,
                    "provider": sample["provider"],
                    "document_title": sample["document_title"],
                    "report_date": sample["report_date"],
                    "component": component,
                    "pay_period": pay_period,
                    "currency": currency,
                    "band_type": band_type,
                    "band": band,
                },
                "practices": [
                    {
                        "observation_id": r["id"],
                        "practice_reported": r["domain_or_practice_area"],
                        "practice_group": r["practice_group"],
                        "reported_p50": r["reported_p50"],
                        "reported_mean": r["reported_mean"],
                        "amount_min": r["amount_min"],
                        "amount_max": r["amount_max"],
                        "reported_sample_size": r["reported_sample_size"],
                        "source_kind": r["source_kind"],
                        "source_quality": r["source_quality"],
                    }
                    for r in comparable
                ],
            }
        )
    comparisons.sort(key=lambda c: (c["context"]["document_id"], c["context"]["component"], c["context"]["pay_period"], str(c["context"]["band"])))
    return comparisons


def build_component_comparisons(rows: list[dict]) -> list[dict]:
    """Same document + practice_group + pay_period + currency + band held
    constant; component (restricted to base/total_package — never day_rate)
    is the one thing allowed to vary."""
    eligible = [r for r in rows if r["component"] in _COMPONENT_COMPARISON_COMPONENTS]
    groups: dict[tuple, list[dict]] = {}
    for r in eligible:
        if not r["document_id"]:
            continue
        band_type, band = _band_type_value(r)
        key = (r["document_id"], r["practice_group"], r["pay_period"], r["currency"], band_type, band)
        groups.setdefault(key, []).append(r)

    comparisons = []
    for (document_id, practice_group, pay_period, currency, band_type, band), items in groups.items():
        distinct_components = {r["component"] for r in items}
        if len(distinct_components) < 2:
            continue
        sample = items[0]
        comparisons.append(
            {
                "context": {
                    "document_id": document_id,
                    "provider": sample["provider"],
                    "document_title": sample["document_title"],
                    "report_date": sample["report_date"],
                    "practice_reported": sample["domain_or_practice_area"],
                    "practice_group": practice_group,
                    "pay_period": pay_period,
                    "currency": currency,
                    "band_type": band_type,
                    "band": band,
                },
                "components": [
                    {
                        "observation_id": r["id"],
                        "component": r["component"],
                        "reported_p50": r["reported_p50"],
                        "reported_mean": r["reported_mean"],
                        "amount_min": r["amount_min"],
                        "amount_max": r["amount_max"],
                        "reported_sample_size": r["reported_sample_size"],
                        "source_kind": r["source_kind"],
                        "source_quality": r["source_quality"],
                    }
                    for r in items
                ],
            }
        )
    comparisons.sort(key=lambda c: (c["context"]["document_id"], str(c["context"]["practice_group"]), str(c["context"]["band"])))
    return comparisons


# --- role_ranges (docs/25 §4.2) ----------------------------------------------


def build_role_ranges(rows: list[dict]) -> list[dict]:
    """Named-role range evidence — any accepted row with a stated
    min/max/single bound, most notably recruiter benchmarks with no reported
    median/mean/sample-size (e.g. Cavehill). `derived_range_midpoint` is
    computed only for sorting/positioning and is always separate from
    `reported_p50` — never a substitute for a missing median."""
    items = [r for r in rows if r["amount_min"] is not None or r["amount_max"] is not None]

    def _midpoint(r: dict) -> float | None:
        lo, hi = r["amount_min"], r["amount_max"]
        if lo is not None and hi is not None:
            return (lo + hi) / 2.0
        return lo if lo is not None else hi

    ranges = [
        {
            "observation_id": r["id"],
            "raw_role_label": r["raw_role_label"],
            "provider": r["provider"],
            "document_id": r["document_id"],
            "document_title": r["document_title"],
            "report_date": r["report_date"],
            "market_id": r["market_id"],
            "market_label": r["market_label"],
            "geography_reported": r["geography_reported"],
            "practice_reported": r["domain_or_practice_area"],
            "practice_group": r["practice_group"],
            "seniority_band_reported": r["seniority_band_reported"],
            "experience_band": r["experience_band"],
            "pqe_band": r["pqe_band"],
            "amount_min": r["amount_min"],
            "amount_max": r["amount_max"],
            "derived_range_midpoint": _midpoint(r),
            "reported_p50": r["reported_p50"],
            "reported_mean": r["reported_mean"],
            "component": r["component"],
            "pay_period": r["pay_period"],
            "currency": r["currency"],
            "reported_sample_size": r["reported_sample_size"],
            "source_kind": r["source_kind"],
            "source_quality": r["source_quality"],
            "archetype_concept_id": r["archetype_concept_id"],
        }
        for r in items
    ]
    ranges.sort(key=lambda r: (r["derived_range_midpoint"] is None, r["derived_range_midpoint"] or 0.0, r["raw_role_label"] or ""))
    return ranges


# --- trends (docs/25 §4.2) ---------------------------------------------------
#
# Emitted only when >=2 distinct comparable periods exist for one fully-held-
# constant key. Median, mean, and range are always separate series — never
# equated with one another, and a range series requires both bounds present
# on the same row.


def build_trends(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        band_type, band = _band_type_value(r)
        if band_type is None or not r["period_key"]:
            continue
        key = (
            r["provider"], r["market_id"], r["geography_reported"], r["currency"],
            r["component"], r["pay_period"], r["practice_group"], band_type, band,
        )
        groups.setdefault(key, []).append(r)

    series_list = []
    for key, items in groups.items():
        provider, market_id, geography_reported, currency, component, pay_period, practice_group, band_type, band = key
        context = {
            "provider": provider,
            "market_id": market_id,
            "geography_reported": geography_reported,
            "currency": currency,
            "component": component,
            "pay_period": pay_period,
            "practice_group": practice_group,
            "band_type": band_type,
            "band": band,
        }

        for statistic, field in (("median", "reported_p50"), ("mean", "reported_mean")):
            by_period: dict = {}
            for r in items:
                value = r.get(field)
                if value is None:
                    continue
                by_period.setdefault(r["period_key"], r)
            if len(by_period) < _TREND_MIN_PERIODS:
                continue
            points = [
                {
                    "period": period,
                    "value": row[field],
                    "observation_id": row["id"],
                    "reported_sample_size": row["reported_sample_size"],
                }
                for period, row in sorted(by_period.items())
            ]
            series_list.append({"context": {**context, "statistic": statistic}, "points": points})

        by_period_range: dict = {}
        for r in items:
            if r.get("amount_min") is not None and r.get("amount_max") is not None:
                by_period_range.setdefault(r["period_key"], r)
        if len(by_period_range) >= _TREND_MIN_PERIODS:
            points = [
                {
                    "period": period,
                    "amount_min": row["amount_min"],
                    "amount_max": row["amount_max"],
                    "observation_id": row["id"],
                    "reported_sample_size": row["reported_sample_size"],
                }
                for period, row in sorted(by_period_range.items())
            ]
            series_list.append({"context": {**context, "statistic": "range"}, "points": points})

    series_list.sort(
        key=lambda s: (str(s["context"]["provider"]), str(s["context"]["practice_group"]), str(s["context"]["band"]), s["context"]["statistic"])
    )
    return series_list


# --- evidence_rows (docs/25 §4.2/§14) ---------------------------------------

_EVIDENCE_ROWS_MAX_LIMIT = 500


def _to_evidence_row(row: dict) -> dict:
    return {
        "observation_id": row["id"],
        "provider": row["provider"],
        "document_id": row["document_id"],
        "document_title": row["document_title"],
        "report_date": row["report_date"],
        "market_id": row["market_id"],
        "market_label": row["market_label"],
        "geography_reported": row["geography_reported"],
        "practice_reported": row["domain_or_practice_area"],
        "practice_group": row["practice_group"],
        "raw_role_label": row["raw_role_label"],
        "seniority_band_reported": row["seniority_band_reported"],
        "experience_band": row["experience_band"],
        "pqe_band": row["pqe_band"],
        "component": row["component"],
        "pay_period": row["pay_period"],
        "currency": row["currency"],
        "reported_p50": row["reported_p50"],
        "reported_mean": row["reported_mean"],
        "amount_min": row["amount_min"],
        "amount_max": row["amount_max"],
        "reported_sample_size": row["reported_sample_size"],
        "source_kind": row["source_kind"],
        "source_quality": row["source_quality"],
        "basis": row["basis"],
        "archetype_concept_id": row["archetype_concept_id"],
    }


def build_evidence_rows(rows: list[dict], limit: int = 100, offset: int = 0) -> dict:
    limit = max(1, min(limit, _EVIDENCE_ROWS_MAX_LIMIT))
    offset = max(0, offset)
    page = rows[offset : offset + limit]
    return {
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "items": [_to_evidence_row(r) for r in page],
    }


# --- orchestrator -------------------------------------------------------------


def build_market_analytics_summary(
    cur,
    filters: MarketAnalyticsFilters,
    *,
    evidence_limit: int = 100,
    evidence_offset: int = 0,
) -> dict:
    all_rows = fetch_accepted_evidence_rows(cur)
    facets = build_facets(all_rows)
    filtered = apply_filters(all_rows, filters)
    return {
        "coverage": build_coverage(filtered),
        "facets": facets,
        "pqe_series": build_pqe_series(filtered),
        "experience_series": build_experience_series(filtered),
        "practice_comparisons": build_practice_comparisons(filtered),
        "component_comparisons": build_component_comparisons(filtered),
        "role_ranges": build_role_ranges(filtered),
        "trends": build_trends(filtered),
        "evidence_rows": build_evidence_rows(filtered, evidence_limit, evidence_offset),
    }
