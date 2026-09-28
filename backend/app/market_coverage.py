"""Phase 8: Market Coverage & Confidence (docs/39).

The single canonical, read-only, deterministic answer to: *what data is a
market-derived conclusion in this app based on, how current and complete is
that data, and what important limitations remain?* Every other feature that
wants to say something about corpus coverage — Trends, Market Summary,
Career Direction discovery, Opportunity alignment, Pathways — composes the
functions in this module rather than computing its own version, so the
definitions of "coverage", "evidence depth" and "representativeness" cannot
drift between pages (build §2/§21/§22/§23/§24).

Three distinct concepts, never collapsed into one (build's central
distinction, §29):

- **Coverage** — measurable facts about the corpus this app actually holds
  (how many roles, how complete their metadata, how much requirement review
  has happened, how much compensation evidence exists).
- **Evidence depth** — transparent, named, rule-based sufficiency/freshness
  states for a *specific* market-derived claim (one archetype, one
  compensation context, one trend). Never a numeric score; see
  `structural_evidence_depth`/`compensation_evidence_depth`/
  `trend_evidence_depth` below and the threshold constants they cite.
- **Representativeness** — whether the corpus reflects the external labour
  market. This app has no known probability sampling frame, so this is
  always `known: false` (`REPRESENTATIVENESS_UNKNOWN`), regardless of how
  complete or large the corpus is. Nothing in this module — no metadata
  completeness, no role count, no source diversity — is ever allowed to flip
  it to `known: true` (build §14).

No AI, no writes, no automatic data repair anywhere in this module (build
§30). Every function below is either a bounded read (the `fetch_*`
functions, the only impure ones) or a pure transform of already-fetched data
— the same "fetch once, transform in Python" shape `market_analytics.py`
uses, chosen for the same reason: the analytical logic is then unit-testable
without a database, and the whole summary is a handful of bounded aggregate
queries rather than a per-role loop (build §31).
"""

from dataclasses import dataclass, replace
from datetime import date

from . import market_analytics
from .archetype_classification import active_archetype_catalogue
from .economics_freshness import economics_freshness
from .role_requirements import load_requirement_review_summary_bulk
from .trends import SPARSE_MIN_SAMPLE, region_for_country

# --- Bounded-response limits (build §16/§31: no unbounded lists) ------------

COUNTRY_DISTRIBUTION_LIMIT = 50
ORGANISATION_CONCENTRATION_LIMIT = 10
CONCENTRATION_TOP_N = 5

# A bucket holding this share or more of an "in scope" total is called out as
# observed concentration (build §26) — never "bias", since no external target
# distribution exists here to measure bias against.
CONCENTRATION_NOTE_THRESHOLD = 0.5


def _fraction(count: int, total: int, meaning: str) -> dict:
    """The one coverage-proportion shape used throughout this module (build
    §4/§5): numerator, denominator, and the human-readable meaning of the
    ratio, so nothing here is ever an unexplained percentage. `meaning`
    always states what was counted, in scope-relative terms ("N of M
    captured postings..."), never a claim about the wider market."""
    return {"count": count, "total": total, "proportion": round(count / total, 4) if total else None, "meaning": meaning}


def _distribution(counter: dict, *, limit: int | None = None) -> list[dict]:
    """The `{value, label, count}` shape `market_analytics._facet` already
    established, reused here so a distribution reads identically wherever it
    appears in this app. Sorted by count desc, then value, for a stable
    display order; `limit` bounds the list without hiding the true total,
    which every caller reports alongside it."""
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if limit is not None:
        items = items[:limit]
    return [{"value": value, "label": str(value), "count": count} for value, count in items]


def _bump(counter: dict, key) -> None:
    if key is not None and key != "":
        counter[key] = counter.get(key, 0) + 1


# --- Representativeness (build §14) ------------------------------------------
#
# A constant, not a computation — nothing in this module ever derives a
# `known: true` from corpus facts. Internal completeness, many roles, many
# capture sources and broad metadata coverage are all explicitly named in
# the build brief as facts that must NOT be allowed to change this.

REPRESENTATIVENESS_UNKNOWN = {
    "known": False,
    "reason": (
        "This is a user-collected/captured corpus with no known probability sampling frame. Internal "
        "completeness, corpus size, capture-source diversity and metadata coverage cannot establish "
        "representativeness of the external labour market."
    ),
}


# --- Scope (build §3) --------------------------------------------------------


@dataclass(frozen=True)
class CoverageScope:
    """Role-corpus scope. Year filters apply to `posting_date` only — a role
    with no posting date never silently enters a dated scope; it is instead
    counted separately (`excluded_undated_count`, `_apply_date_scope` below)
    so the exclusion is disclosed rather than hidden. Compensation-market
    dimensions (market/currency/component/...) are a different, existing
    scope (`market_analytics.MarketAnalyticsFilters`) — this dataclass is
    never used to filter compensation evidence (build §3/§12)."""

    year_from: int | None = None
    year_to: int | None = None
    country: str | None = None
    seniority_level: str | None = None
    archetype_id: str | None = None

    @property
    def dated_scope_active(self) -> bool:
        return self.year_from is not None or self.year_to is not None

    def to_public(self) -> dict:
        return {
            "year_from": self.year_from,
            "year_to": self.year_to,
            "country": self.country,
            "seniority_level": self.seniority_level,
            "archetype_id": self.archetype_id,
            "dated_scope_active": self.dated_scope_active,
        }


# --- Fetch (the only impure functions in this module) -----------------------


def fetch_scope_candidate_roles(cur, scope: CoverageScope) -> list[dict]:
    """Every observed posting matching the *non-date* scope filters —
    posting_date is deliberately NOT filtered in SQL here (see
    `_apply_date_scope`), so a role with an unknown posting date is still
    fetched and can be reported as excluded, rather than silently vanishing
    from a dated scope. One query, LEFT JOIN to the linked document so
    source/provenance/capture-date facts come back in the same round trip —
    no second per-role document lookup anywhere downstream."""
    clauses = ["ri.instance_type = 'observed_posting'"]
    params: list = []
    if scope.country:
        clauses.append("ri.country = %s")
        params.append(scope.country)
    if scope.seniority_level:
        clauses.append("ri.seniority_level = %s")
        params.append(scope.seniority_level)
    if scope.archetype_id:
        clauses.append("ri.archetype_concept_id = %s")
        params.append(scope.archetype_id)
    where_sql = " AND ".join(clauses)
    cur.execute(
        f"""
        SELECT ri.id, ri.organisation, ri.country, ri.seniority_level, ri.employment_type, ri.remote_type,
               ri.posting_date, ri.archetype_concept_id, ri.document_id,
               d.source AS capture_source, d.provenance_quality, d.kind AS document_kind,
               d.url AS document_url, d.captured_at AS document_captured_at
        FROM jobber.role_instance ri
        LEFT JOIN jobber.document d ON d.id = ri.document_id
        WHERE {where_sql}
        """,
        params,
    )
    rows = []
    for raw in cur.fetchall():
        raw = dict(raw)
        raw["id"] = str(raw["id"])
        raw["archetype_concept_id"] = str(raw["archetype_concept_id"]) if raw["archetype_concept_id"] else None
        raw["document_id"] = str(raw["document_id"]) if raw["document_id"] else None
        rows.append(raw)
    return rows


def _fetch_legacy_fallback_counts(cur, role_ids: list[str]) -> dict[str, dict]:
    """Per role_instance_id: role_skill_observation counts, split into
    `resolved` (canonical_concept_id resolves to an *active* concept — the
    same eligibility condition `role_requirements.py`'s own fallback branch
    requires) and `unresolved`. A simplified corpus-processing proxy for
    "legacy fallback available" — it deliberately does not replay
    `role_requirements.py`'s curator-veto logic (a rejected/corrected claim
    suppressing a specific concept's observation), since this module
    discloses corpus-processing *state*, not requirement-review authority;
    replaying the veto here would need a second per-role veto set for a
    distinction Phase 8 has no use for. One bulk query, never per-role."""
    if not role_ids:
        return {}
    cur.execute(
        """
        SELECT rso.role_instance_id,
               COUNT(*) FILTER (WHERE rso.canonical_concept_id IS NOT NULL AND c.status = 'active') AS resolved,
               COUNT(*) FILTER (WHERE rso.canonical_concept_id IS NULL) AS unresolved
        FROM jobber.role_skill_observation rso
        LEFT JOIN jobber.concept c ON c.id = rso.canonical_concept_id
        WHERE rso.role_instance_id = ANY(%s::uuid[])
        GROUP BY rso.role_instance_id
        """,
        (role_ids,),
    )
    return {
        str(r["role_instance_id"]): {"resolved": r["resolved"], "unresolved": r["unresolved"]}
        for r in cur.fetchall()
    }


def _fetch_role_linked_compensation_ids(cur, role_ids: list[str]) -> set[str]:
    """Which of `role_ids` have at least one *accepted* role-linked
    compensation_observation — role-level compensation coverage (build §12),
    kept separate from the corpus-wide market-evidence coverage below."""
    if not role_ids:
        return set()
    cur.execute(
        "SELECT DISTINCT role_instance_id FROM jobber.compensation_observation "
        "WHERE role_instance_id = ANY(%s::uuid[]) AND review_status = 'accepted'",
        (role_ids,),
    )
    return {str(r["role_instance_id"]) for r in cur.fetchall()}


def _fetch_archetype_derivation_availability(cur, archetype_ids: list[str]) -> tuple[set[str], set[str]]:
    """Which archetypes have any `d_archetype_demand` row, and which have a
    usable `d_archetype_comp` benchmark (`reference_comp IS NOT NULL`). Two
    small bounded queries, never per archetype."""
    if not archetype_ids:
        return set(), set()
    cur.execute(
        "SELECT DISTINCT archetype_concept_id FROM jobber.d_archetype_demand WHERE archetype_concept_id = ANY(%s::uuid[])",
        (archetype_ids,),
    )
    with_demand = {str(r["archetype_concept_id"]) for r in cur.fetchall()}
    cur.execute(
        "SELECT DISTINCT archetype_concept_id FROM jobber.d_archetype_comp "
        "WHERE archetype_concept_id = ANY(%s::uuid[]) AND reference_comp IS NOT NULL",
        (archetype_ids,),
    )
    with_comp = {str(r["archetype_concept_id"]) for r in cur.fetchall()}
    return with_demand, with_comp


# --- Date scope (build §3/§6) -------------------------------------------------


def _apply_date_scope(rows: list[dict], scope: CoverageScope) -> tuple[list[dict], list[dict]]:
    """Splits `rows` (already filtered on every *non-date* dimension) into
    (in_scope, excluded_as_undated). When no date filter is active every row
    is in scope and nothing is excluded — there is no dated scope to be
    excluded from. When a date filter is active, a row with no posting_date
    can never satisfy it (build §3: "must not silently enter a dated
    scope"), so it is reported separately rather than merely dropped."""
    if not scope.dated_scope_active:
        return list(rows), []
    in_scope, excluded = [], []
    for row in rows:
        posting_date = row.get("posting_date")
        if posting_date is None:
            excluded.append(row)
            continue
        year = posting_date.year
        if scope.year_from is not None and year < scope.year_from:
            continue
        if scope.year_to is not None and year > scope.year_to:
            continue
        in_scope.append(row)
    return in_scope, excluded


# --- Role-corpus completeness (build §5) -------------------------------------


def build_role_corpus_block(rows: list[dict], excluded_undated: list[dict]) -> dict:
    total = len(rows)
    with_doc = sum(1 for r in rows if r["document_id"])
    with_date = sum(1 for r in rows if r["posting_date"])
    with_country = sum(1 for r in rows if r["country"])
    with_seniority = sum(1 for r in rows if r["seniority_level"])
    with_employment_type = sum(1 for r in rows if r["employment_type"])
    with_remote_type = sum(1 for r in rows if r["remote_type"])
    return {
        "total": total,
        "excluded_undated_count": len(excluded_undated),
        "with_source_document": _fraction(with_doc, total, f"{with_doc} of {total} captured postings link to a source document"),
        "without_source_document": _fraction(total - with_doc, total, f"{total - with_doc} of {total} captured postings have no linked source document"),
        "known_posting_date": _fraction(with_date, total, f"{with_date} of {total} captured postings have a known posting date"),
        "unknown_posting_date": _fraction(total - with_date, total, f"{total - with_date} of {total} captured postings have an unknown posting date"),
        "known_country": _fraction(with_country, total, f"{with_country} of {total} captured postings have a known country"),
        "unknown_country": _fraction(total - with_country, total, f"{total - with_country} of {total} captured postings have an unknown country"),
        "known_seniority": _fraction(with_seniority, total, f"{with_seniority} of {total} captured postings have a known seniority level"),
        "unknown_seniority": _fraction(total - with_seniority, total, f"{total - with_seniority} of {total} captured postings have an unknown seniority level"),
        "known_employment_type": _fraction(with_employment_type, total, f"{with_employment_type} of {total} captured postings have a known employment type"),
        "unknown_employment_type": _fraction(total - with_employment_type, total, f"{total - with_employment_type} of {total} captured postings have an unknown employment type"),
        "known_remote_type": _fraction(with_remote_type, total, f"{with_remote_type} of {total} captured postings have a known remote-work type"),
        "unknown_remote_type": _fraction(total - with_remote_type, total, f"{total - with_remote_type} of {total} captured postings have an unknown remote-work type"),
    }


# --- Time coverage / freshness (build §6) ------------------------------------
#
# Capture recency is not posting recency (module-wide invariant): every
# figure below is built from `posting_date` alone, except the two fields
# explicitly named for `captured_at` (`freshly_captured_but_posting_date_
# unknown_count`, `document_capture_date_range`) — `captured_at` is never
# substituted for a missing `posting_date` anywhere in this function.


def build_time_block(rows: list[dict], excluded_undated: list[dict], *, today: date | None = None) -> dict:
    today = today or date.today()
    dated = [r for r in rows if r["posting_date"]]
    total = len(rows)
    by_year: dict[int, int] = {}
    for r in dated:
        by_year[r["posting_date"].year] = by_year.get(r["posting_date"].year, 0) + 1
    current_year_count = by_year.get(today.year, 0)
    previous_year_count = by_year.get(today.year - 1, 0)
    older_count = sum(n for year, n in by_year.items() if year < today.year - 1)

    # Every undated role this scope could have carried a date for — both the
    # ones actually in scope (no date filter active) and the ones a dated
    # filter excluded — since either way, "captured recently but posting
    # date unknown" describes the same underlying gap.
    undated_candidates = [r for r in rows if not r["posting_date"]] + excluded_undated
    freshly_captured_undated = sum(
        1 for r in undated_candidates
        if r["document_captured_at"] and r["document_captured_at"].year == today.year
    )

    capture_dates = [r["document_captured_at"] for r in rows if r["document_captured_at"]]

    known = len(dated)
    unknown = total - known
    return {
        "earliest_known_posting_date": min((r["posting_date"] for r in dated), default=None),
        "latest_known_posting_date": max((r["posting_date"] for r in dated), default=None),
        "known_posting_date": _fraction(known, total, f"{known} of {total} captured postings have a known posting date"),
        "unknown_posting_date": _fraction(unknown, total, f"{unknown} of {total} captured postings have an unknown posting date"),
        "by_year": [{"value": year, "count": n} for year, n in sorted(by_year.items())],
        "current_calendar_year_count": current_year_count,
        "previous_calendar_year_count": previous_year_count,
        "older_count": older_count,
        # Exact label per build §6: never implies the posting itself is current.
        "freshly_captured_but_posting_date_unknown_count": freshly_captured_undated,
        "document_capture_date_range": {
            "earliest": min(capture_dates, default=None),
            "latest": max(capture_dates, default=None),
        },
    }


# --- Geography coverage (build §7) -------------------------------------------


def build_geography_block(rows: list[dict]) -> dict:
    total = len(rows)
    country_counts: dict[str, int] = {}
    for r in rows:
        _bump(country_counts, r["country"])
    known = sum(country_counts.values())
    region_counts: dict[str, int] = {}
    for country, n in country_counts.items():
        region = region_for_country(country)
        if region:
            region_counts[region] = region_counts.get(region, 0) + n
    return {
        "known_country": _fraction(known, total, f"{known} of {total} captured postings in this scope have a known country"),
        "unknown_country": _fraction(total - known, total, f"{total - known} of {total} captured postings in this scope have an unknown country"),
        "distinct_known_country_count": len(country_counts),
        "country_distribution": _distribution(country_counts, limit=COUNTRY_DISTRIBUTION_LIMIT),
        "region_distribution": _distribution(region_counts),
    }


# --- Source / provenance coverage (build §8) ---------------------------------
#
# `document.source` is called "capture source" throughout this module — the
# app's own ingestion captures a posting from wherever the user found it;
# nothing in this codebase's ingestion path confirms it is an independent
# external publisher/provider in the market-research sense (build §8), so
# this module never calls capture-source diversity "independent sources".


def build_sources_block(rows: list[dict]) -> dict:
    total = len(rows)
    with_doc = sum(1 for r in rows if r["document_id"])
    source_counts: dict[str, int] = {}
    provenance_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    with_url = 0
    for r in rows:
        if not r["document_id"]:
            continue
        _bump(source_counts, r["capture_source"])
        _bump(provenance_counts, r["provenance_quality"])
        _bump(kind_counts, r["document_kind"])
        if r["document_url"]:
            with_url += 1
    return {
        "with_source_document": _fraction(with_doc, total, f"{with_doc} of {total} captured postings link to a source document"),
        "without_source_document": _fraction(total - with_doc, total, f"{total - with_doc} of {total} captured postings have no linked source document"),
        "capture_source_distribution": _distribution(source_counts),
        "provenance_quality_distribution": _distribution(provenance_counts),
        "document_kind_distribution": _distribution(kind_counts),
        "url_present": _fraction(with_url, with_doc, f"{with_url} of {with_doc} linked source documents have a captured URL"),
        "url_absent": _fraction(with_doc - with_url, with_doc, f"{with_doc - with_url} of {with_doc} linked source documents have no captured URL"),
    }


# --- Requirement-review coverage (build §9) ----------------------------------
#
# Precedence documented, centralised, and tested (build §9/§34/§37) — never
# recomputed ad hoc elsewhere. First match wins.

REQUIREMENT_STATE_NEEDS_REEXTRACTION = "needs_reextraction"
REQUIREMENT_STATE_UNRESOLVED_VOCABULARY = "unresolved_vocabulary"
REQUIREMENT_STATE_REVIEW_PENDING = "review_pending"
REQUIREMENT_STATE_REVIEWED = "reviewed"
REQUIREMENT_STATE_LEGACY_ONLY = "legacy_only"
REQUIREMENT_STATE_NOT_EXTRACTED = "not_extracted"

REQUIREMENT_STATE_PRECEDENCE = [
    REQUIREMENT_STATE_NEEDS_REEXTRACTION,
    REQUIREMENT_STATE_UNRESOLVED_VOCABULARY,
    REQUIREMENT_STATE_REVIEW_PENDING,
    REQUIREMENT_STATE_REVIEWED,
    REQUIREMENT_STATE_LEGACY_ONLY,
    REQUIREMENT_STATE_NOT_EXTRACTED,
]


def classify_requirement_review_state(summary: dict, *, has_legacy_fallback: bool) -> str:
    """`summary` is one role's entry from
    `role_requirements.load_requirement_review_summary_bulk` (keys: accepted/
    unreviewed/rejected/unresolved_proposals/extraction_attempted/
    needs_reextraction/complete). Exactly one state, first match wins —
    see REQUIREMENT_STATE_PRECEDENCE."""
    if summary["needs_reextraction"] > 0:
        return REQUIREMENT_STATE_NEEDS_REEXTRACTION
    if summary["unresolved_proposals"] > 0:
        return REQUIREMENT_STATE_UNRESOLVED_VOCABULARY
    if summary["unreviewed"] > 0:
        return REQUIREMENT_STATE_REVIEW_PENDING
    if summary["accepted"] > 0:
        return REQUIREMENT_STATE_REVIEWED
    if has_legacy_fallback:
        return REQUIREMENT_STATE_LEGACY_ONLY
    return REQUIREMENT_STATE_NOT_EXTRACTED


def build_requirements_block(role_ids: list[str], review_summaries: dict[str, dict], legacy_counts: dict[str, dict]) -> dict:
    total = len(role_ids)
    review_complete = accepted_present = unreviewed_present = 0
    unresolved_vocab_present = needs_reextraction_present = 0
    extraction_incomplete = extraction_never = legacy_only = no_usable = 0
    state_distribution = {state: 0 for state in REQUIREMENT_STATE_PRECEDENCE}

    for role_id in role_ids:
        summary = review_summaries[role_id]
        has_legacy = legacy_counts.get(role_id, {}).get("resolved", 0) > 0
        if summary["complete"]:
            review_complete += 1
        if summary["accepted"] > 0:
            accepted_present += 1
        if summary["unreviewed"] > 0:
            unreviewed_present += 1
        if summary["unresolved_proposals"] > 0:
            unresolved_vocab_present += 1
        if summary["needs_reextraction"] > 0:
            needs_reextraction_present += 1
        if summary["extraction_attempted"] and not summary["complete"]:
            extraction_incomplete += 1
        if not summary["extraction_attempted"]:
            extraction_never += 1
        if summary["accepted"] == 0 and has_legacy:
            legacy_only += 1
        if summary["accepted"] == 0 and not has_legacy:
            no_usable += 1
        state_distribution[classify_requirement_review_state(summary, has_legacy_fallback=has_legacy)] += 1

    return {
        "review_complete": _fraction(review_complete, total, f"{review_complete} of {total} postings in scope have complete requirement review"),
        "accepted_present": _fraction(accepted_present, total, f"{accepted_present} of {total} postings in scope have at least one accepted, current requirement"),
        "unreviewed_present": _fraction(unreviewed_present, total, f"{unreviewed_present} of {total} postings in scope have at least one unreviewed requirement claim"),
        "unresolved_vocabulary_present": _fraction(unresolved_vocab_present, total, f"{unresolved_vocab_present} of {total} postings in scope contributed to a still-unresolved vocabulary proposal"),
        "needs_reextraction_present": _fraction(needs_reextraction_present, total, f"{needs_reextraction_present} of {total} postings in scope need requirement re-extraction"),
        "extraction_attempted_incomplete": _fraction(extraction_incomplete, total, f"{extraction_incomplete} of {total} postings in scope had extraction attempted but review is not yet complete"),
        "extraction_never_attempted": _fraction(extraction_never, total, f"{extraction_never} of {total} postings in scope have never had requirement extraction attempted"),
        "legacy_only": _fraction(legacy_only, total, f"{legacy_only} of {total} postings in scope rely only on legacy skill-observation evidence"),
        "no_usable_evidence": _fraction(no_usable, total, f"{no_usable} of {total} postings in scope have no usable requirement evidence of any kind"),
        "state_precedence": list(REQUIREMENT_STATE_PRECEDENCE),
        "state_distribution": state_distribution,
    }


# --- Vocabulary coverage (build §10) -----------------------------------------


def build_vocabulary_block(role_ids: list[str], review_summaries: dict[str, dict], legacy_counts: dict[str, dict]) -> dict:
    total = len(role_ids)
    with_accepted = sum(1 for rid in role_ids if review_summaries[rid]["accepted"] > 0)
    relying_on_legacy = sum(
        1 for rid in role_ids
        if review_summaries[rid]["accepted"] == 0 and legacy_counts.get(rid, {}).get("resolved", 0) > 0
    )
    resolved_obs = sum(v.get("resolved", 0) for v in legacy_counts.values())
    unresolved_obs = sum(v.get("unresolved", 0) for v in legacy_counts.values())
    total_obs = resolved_obs + unresolved_obs
    unresolved_proposal_occurrences = sum(review_summaries[rid]["unresolved_proposals"] for rid in role_ids)
    return {
        "roles_with_accepted_canonical_requirements": _fraction(with_accepted, total, f"{with_accepted} of {total} postings in scope have an accepted canonical requirement"),
        "roles_relying_on_legacy_fallback": _fraction(relying_on_legacy, total, f"{relying_on_legacy} of {total} postings in scope rely on legacy role_skill_observation fallback"),
        "role_skill_observations_resolved": _fraction(resolved_obs, total_obs, f"{resolved_obs} of {total_obs} legacy skill observations in scope resolve to an active canonical concept"),
        "role_skill_observations_unresolved_count": unresolved_obs,
        "unresolved_vocabulary_proposal_occurrence_count": unresolved_proposal_occurrences,
    }


# --- Archetype coverage (build §11) ------------------------------------------


def _aggregate_archetype_rows(rows: list[dict], review_summaries: dict[str, dict]) -> dict:
    """Pure aggregation over one archetype's already-fetched, already-scoped
    role rows — shared by the bounded per-archetype table below and by
    `archetype_coverage_detail`'s single-archetype lookup, so the two can
    never disagree (build §2)."""
    assigned = len(rows)
    known_date = [r for r in rows if r["posting_date"]]
    countries = {r["country"] for r in rows if r["country"]}
    reviewed = sum(1 for r in rows if review_summaries.get(r["id"], {}).get("accepted", 0) > 0)
    return {
        "assigned_posting_count": assigned,
        "reviewed_requirement_posting_count": reviewed,
        "known_posting_date_count": len(known_date),
        "unknown_posting_date_count": assigned - len(known_date),
        "latest_known_posting_date": max((r["posting_date"] for r in known_date), default=None),
        "distinct_country_count": len(countries),
        "countries": sorted(countries)[:CONCENTRATION_TOP_N],
    }


def build_archetypes_block(
    cur,
    rows: list[dict],
    review_summaries: dict[str, dict],
    freshness: dict,
    *,
    catalogue: list[dict] | None = None,
) -> dict:
    total = len(rows)
    catalogue = catalogue if catalogue is not None else active_archetype_catalogue(cur)
    active_ids = [a["id"] for a in catalogue]
    by_id = {a["id"]: a for a in catalogue}
    freshness_summary = {"state": freshness["state"], "fresh": freshness["fresh"], "reason": freshness["reason"]}

    assigned_active = assigned_deprecated = unassigned = 0
    by_archetype: dict[str, list[dict]] = {}
    active_id_set = set(active_ids)
    for r in rows:
        aid = r["archetype_concept_id"]
        if aid is None:
            unassigned += 1
            continue
        if aid in active_id_set:
            assigned_active += 1
            by_archetype.setdefault(aid, []).append(r)
        else:
            assigned_deprecated += 1

    with_demand, with_comp = _fetch_archetype_derivation_availability(cur, list(by_archetype.keys()))

    support = []
    for aid, archetype_rows in by_archetype.items():
        archetype = by_id.get(aid, {})
        agg = _aggregate_archetype_rows(archetype_rows, review_summaries)
        depth = structural_evidence_depth(
            archetype_active=True,
            posting_count=agg["assigned_posting_count"],
            reviewed_requirement_count=agg["reviewed_requirement_posting_count"],
            distinct_countries=agg["distinct_country_count"],
        )
        support.append(
            {
                "archetype_concept_id": aid,
                "canonical_name": archetype.get("canonical_name", aid),
                "status": "active",
                "seniority_band": archetype.get("seniority_band"),
                "typical_market": archetype.get("typical_market"),
                "demand_derivation_available": aid in with_demand,
                "compensation_benchmark_available": aid in with_comp,
                "economics_freshness": freshness_summary,
                "evidence_depth": depth,
                **agg,
            }
        )
    support.sort(key=lambda a: (-a["assigned_posting_count"], a["canonical_name"]))

    supported_ids = set(by_archetype.keys())
    unsupported = [{"archetype_concept_id": a["id"], "canonical_name": a["canonical_name"]} for a in catalogue if a["id"] not in supported_ids]

    active_total = len(catalogue)
    supported_total = len(supported_ids)
    return {
        "active_archetype_count": active_total,
        "assigned_active": _fraction(assigned_active, total, f"{assigned_active} of {total} postings in scope are assigned to an active archetype"),
        "assigned_deprecated": {"count": assigned_deprecated, "meaning": f"{assigned_deprecated} of {total} postings in scope are assigned to a deprecated archetype"},
        "unassigned": _fraction(unassigned, total, f"{unassigned} of {total} postings in scope have no archetype assignment"),
        "active_archetypes_with_support": _fraction(supported_total, active_total, f"{supported_total} of {active_total} active archetypes have at least one supporting posting in this scope"),
        "active_archetypes_without_support_count": active_total - supported_total,
        "unsupported_active_archetypes": unsupported,
        "support": support,
    }


def archetype_support_bulk(cur, archetype_ids: list[str], *, freshness: dict | None = None) -> dict[str, dict]:
    """Unscoped (whole-corpus) support for several archetypes at once, keyed
    by archetype_concept_id — the shape `archetype_coverage_detail` returns
    for one, computed here without a per-archetype round trip. Built for
    `pathways.py`'s intermediate-archetype nodes, whose archetype count is
    bounded by the structural stepping-stone engine's own output (never
    role-count-scaled), so one bulk role fetch plus one bulk review-summary
    load replaces what would otherwise be an N-archetype fan-out — the same
    "fetch once, aggregate in Python" shape `_aggregate_archetype_rows`
    already gives `build_archetypes_block`'s scoped table, reused here
    unscoped so the two can never define "archetype support" differently."""
    if not archetype_ids:
        return {}
    cur.execute(
        "SELECT id, canonical_name, status, rad.seniority_band, rad.typical_market "
        "FROM jobber.concept c LEFT JOIN jobber.role_archetype_detail rad ON rad.concept_id = c.id "
        "WHERE c.id = ANY(%s::uuid[])",
        (archetype_ids,),
    )
    concepts = {str(r["id"]): dict(r) for r in cur.fetchall()}

    cur.execute(
        "SELECT id, posting_date, country, archetype_concept_id FROM jobber.role_instance "
        "WHERE instance_type = 'observed_posting' AND archetype_concept_id = ANY(%s::uuid[])",
        (archetype_ids,),
    )
    rows_by_archetype: dict[str, list[dict]] = {aid: [] for aid in archetype_ids}
    all_role_ids = []
    for raw in cur.fetchall():
        row = dict(raw)
        row["id"] = str(row["id"])
        aid = str(row["archetype_concept_id"])
        rows_by_archetype.setdefault(aid, []).append(row)
        all_role_ids.append(row["id"])

    review_summaries = load_requirement_review_summary_bulk(cur, all_role_ids)
    with_demand, with_comp = _fetch_archetype_derivation_availability(cur, archetype_ids)
    freshness = freshness if freshness is not None else economics_freshness(cur)
    freshness_summary = {"state": freshness["state"], "fresh": freshness["fresh"], "reason": freshness["reason"]}

    result = {}
    for aid in archetype_ids:
        concept = concepts.get(aid)
        agg = _aggregate_archetype_rows(rows_by_archetype.get(aid, []), review_summaries)
        depth = structural_evidence_depth(
            archetype_active=(concept or {}).get("status") == "active",
            posting_count=agg["assigned_posting_count"],
            reviewed_requirement_count=agg["reviewed_requirement_posting_count"],
            distinct_countries=agg["distinct_country_count"],
        )
        result[aid] = {
            "archetype_concept_id": aid,
            "found": concept is not None,
            "canonical_name": (concept or {}).get("canonical_name"),
            "status": (concept or {}).get("status"),
            "seniority_band": (concept or {}).get("seniority_band"),
            "typical_market": (concept or {}).get("typical_market"),
            "demand_derivation_available": aid in with_demand,
            "compensation_benchmark_available": aid in with_comp,
            "economics_freshness": freshness_summary,
            "evidence_depth": depth,
            **agg,
        }
    return result


def archetype_coverage_detail(
    cur,
    archetype_concept_id: str,
    scope: CoverageScope | None = None,
    *,
    freshness: dict | None = None,
) -> dict:
    """One archetype's bounded support (build §16/§25): the same aggregation
    `build_archetypes_block`'s table row uses, computed standalone for one
    archetype so Opportunity alignment, Pathways and the Career Direction
    detail page can ask "what backs this archetype" without pulling the
    whole Market Coverage summary. `scope=None` (the default, and what every
    non-Market-Coverage caller uses) means the whole corpus — Opportunity
    alignment and Pathways ask about market-pattern support for an
    archetype, not about whatever year/country filter a different page
    happens to have active. The Market Coverage page's own archetype
    drill-down passes its active `scope` (with `archetype_id` overridden to
    the one being inspected) so the detail matches the summary table it was
    opened from."""
    effective_scope = replace(scope or CoverageScope(), archetype_id=archetype_concept_id)
    candidate_rows = fetch_scope_candidate_roles(cur, effective_scope)
    rows, _excluded = _apply_date_scope(candidate_rows, effective_scope)

    cur.execute(
        "SELECT id, canonical_name, status, rad.seniority_band, rad.typical_market "
        "FROM jobber.concept c LEFT JOIN jobber.role_archetype_detail rad ON rad.concept_id = c.id "
        "WHERE c.id = %s AND c.type_code = 'role_archetype'",
        (archetype_concept_id,),
    )
    concept = cur.fetchone()
    if not concept:
        return {
            "archetype_concept_id": archetype_concept_id,
            "found": False,
            "canonical_name": None,
            "status": None,
        }

    role_ids = [r["id"] for r in rows]
    review_summaries = load_requirement_review_summary_bulk(cur, role_ids)
    agg = _aggregate_archetype_rows(rows, review_summaries)
    with_demand, with_comp = _fetch_archetype_derivation_availability(cur, [archetype_concept_id])
    freshness = freshness if freshness is not None else economics_freshness(cur)
    depth = structural_evidence_depth(
        archetype_active=concept["status"] == "active",
        posting_count=agg["assigned_posting_count"],
        reviewed_requirement_count=agg["reviewed_requirement_posting_count"],
        distinct_countries=agg["distinct_country_count"],
    )
    return {
        "archetype_concept_id": archetype_concept_id,
        "found": True,
        "canonical_name": concept["canonical_name"],
        "status": concept["status"],
        "seniority_band": concept["seniority_band"],
        "typical_market": concept["typical_market"],
        "demand_derivation_available": archetype_concept_id in with_demand,
        "compensation_benchmark_available": archetype_concept_id in with_comp,
        "economics_freshness": {"state": freshness["state"], "fresh": freshness["fresh"], "reason": freshness["reason"]},
        "evidence_depth": depth,
        **agg,
    }


# --- Compensation coverage (build §12) ---------------------------------------
#
# Corpus-wide, composed from `market_analytics` rather than duplicated
# (build §12/§21) — never filtered by the role-corpus scope above. Role-corpus
# filters (year/country/seniority/archetype on `role_instance`) and
# compensation-market dimensions (`market_analytics.MarketAnalyticsFilters`)
# are two different scopes; conflating them would silently narrow "the
# market's" evidence by a role-corpus filter that has no equivalent meaning
# on survey-sourced compensation rows (build §3).


def build_compensation_block(cur, role_ids: list[str], roles_total: int) -> dict:
    comp_rows = market_analytics.fetch_accepted_evidence_rows(cur)
    coverage = market_analytics.build_coverage(comp_rows)
    facets = market_analytics.build_facets(comp_rows)

    basis_counts: dict[str, int] = {}
    for r in comp_rows:
        _bump(basis_counts, r["basis"])

    role_linked_ids = _fetch_role_linked_compensation_ids(cur, role_ids)
    with_role_comp = len(role_linked_ids & set(role_ids))
    return {
        "coverage": coverage,
        "source_kind_distribution": facets["source_kinds"],
        "basis_distribution": _distribution(basis_counts),
        "top_providers": facets["providers"][:CONCENTRATION_TOP_N],
        "role_linked_coverage": {
            "with_accepted_role_linked_compensation": _fraction(with_role_comp, roles_total, f"{with_role_comp} of {roles_total} postings in scope have accepted, role-linked compensation evidence"),
            "without_accepted_role_linked_compensation": _fraction(roles_total - with_role_comp, roles_total, f"{roles_total - with_role_comp} of {roles_total} postings in scope have no role-linked compensation evidence — this does not imply low compensation"),
        },
        "scope_note": (
            "This section covers every accepted market/survey observation in the whole corpus, independent of "
            "the role-corpus scope above (year/country/seniority/archetype filters do not apply to it) — role-"
            "corpus dates and geography are not the same dimension as compensation evidence periods and markets."
        ),
    }


# --- Concentration (build §26) ------------------------------------------------


def build_concentration_block(rows: list[dict], comp_rows_facets: dict) -> dict:
    source_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    org_counts: dict[str, int] = {}
    for r in rows:
        _bump(source_counts, r["capture_source"])
        _bump(country_counts, r["country"])
        _bump(org_counts, r["organisation"])
    return {
        "top_capture_sources": _distribution(source_counts, limit=CONCENTRATION_TOP_N),
        "top_countries": _distribution(country_counts, limit=CONCENTRATION_TOP_N),
        "top_organisations": _distribution(org_counts, limit=ORGANISATION_CONCENTRATION_LIMIT),
        "top_compensation_providers": comp_rows_facets["providers"][:CONCENTRATION_TOP_N],
        "note": (
            "These show where the captured corpus is concentrated, not a measured bias — no external target "
            "distribution exists for this corpus to be compared against."
        ),
    }


def _concentration_limitation(distribution: list[dict], total: int, subject: str) -> str | None:
    if total == 0 or not distribution:
        return None
    top = distribution[0]
    share = top["count"] / total
    if share < CONCENTRATION_NOTE_THRESHOLD:
        return None
    return f"The captured corpus in this scope is concentrated in {subject} {top['label']} ({round(share * 100)}% of postings)."


# --- Evidence depth (build §15/§37) ------------------------------------------
#
# Three independent, small, rule-based families — never one universal
# threshold pretending posting counts, compensation observations and dated
# trend periods are statistically equivalent (build §15). Every threshold is
# a named constant here, not tuned to flatter current production data
# (build §37), and every result carries the facts it was computed from
# alongside a plain-English reason — never a numeric confidence score.

EVIDENCE_DEPTH_INSUFFICIENT = "insufficient"
EVIDENCE_DEPTH_THIN = "thin"
EVIDENCE_DEPTH_SUPPORTED = "supported"
EVIDENCE_DEPTH_BROADER_SUPPORT = "broader_support"

# Structural/archetype support (build §15 "Structural/archetype support").
STRUCTURAL_THIN_MIN_POSTINGS = 1
STRUCTURAL_SUPPORTED_MIN_POSTINGS = 3
STRUCTURAL_BROADER_MIN_POSTINGS = 8
STRUCTURAL_BROADER_MIN_COUNTRIES = 2


def structural_evidence_depth(*, archetype_active: bool, posting_count: int, reviewed_requirement_count: int, distinct_countries: int) -> dict:
    facts = {
        "archetype_active": archetype_active,
        "posting_count": posting_count,
        "reviewed_requirement_posting_count": reviewed_requirement_count,
        "distinct_countries": distinct_countries,
    }
    if not archetype_active or posting_count < STRUCTURAL_THIN_MIN_POSTINGS:
        return {"state": EVIDENCE_DEPTH_INSUFFICIENT, "reason": "No active archetype with a supporting posting in this scope.", **facts}
    if posting_count < STRUCTURAL_SUPPORTED_MIN_POSTINGS:
        return {"state": EVIDENCE_DEPTH_THIN, "reason": f"Only {posting_count} supporting posting(s) in this scope.", **facts}
    if posting_count >= STRUCTURAL_BROADER_MIN_POSTINGS and distinct_countries >= STRUCTURAL_BROADER_MIN_COUNTRIES:
        return {"state": EVIDENCE_DEPTH_BROADER_SUPPORT, "reason": f"{posting_count} supporting postings across {distinct_countries} countries.", **facts}
    return {"state": EVIDENCE_DEPTH_SUPPORTED, "reason": f"{posting_count} supporting posting(s) in this scope.", **facts}


# Compensation support (build §15 "Compensation support").
COMPENSATION_THIN_MIN_OBSERVATIONS = 1
COMPENSATION_SUPPORTED_MIN_OBSERVATIONS = 3
COMPENSATION_SUPPORTED_MIN_DOCUMENTS = 2
COMPENSATION_BROADER_MIN_OBSERVATIONS = 8
COMPENSATION_BROADER_MIN_PROVIDERS = 2


def compensation_evidence_depth(*, observation_count: int, distinct_document_count: int, distinct_provider_count: int, economics_fresh: bool) -> dict:
    facts = {
        "observation_count": observation_count,
        "distinct_document_count": distinct_document_count,
        "distinct_provider_count": distinct_provider_count,
        "economics_fresh": economics_fresh,
    }
    if observation_count < COMPENSATION_THIN_MIN_OBSERVATIONS:
        return {"state": EVIDENCE_DEPTH_INSUFFICIENT, "reason": "No accepted compensation observations qualify in this context.", **facts}
    if observation_count < COMPENSATION_SUPPORTED_MIN_OBSERVATIONS or distinct_document_count < COMPENSATION_SUPPORTED_MIN_DOCUMENTS:
        return {"state": EVIDENCE_DEPTH_THIN, "reason": f"Only {observation_count} observation(s) from {distinct_document_count} document(s).", **facts}
    state = EVIDENCE_DEPTH_SUPPORTED
    reason = f"{observation_count} observations from {distinct_document_count} document(s)."
    if observation_count >= COMPENSATION_BROADER_MIN_OBSERVATIONS and distinct_provider_count >= COMPENSATION_BROADER_MIN_PROVIDERS:
        # Stale derived economics can never present as the strongest state
        # (build §37) — a figure withheld by economics_freshness must not be
        # framed as having broad support behind it.
        if economics_fresh:
            state = EVIDENCE_DEPTH_BROADER_SUPPORT
            reason = f"{observation_count} observations from {distinct_document_count} document(s) across {distinct_provider_count} providers."
    return {"state": state, "reason": reason, **facts}


# Trend support (build §15 "Trend support") — reuses trends.py's own
# small-sample rule (SPARSE_MIN_SAMPLE) rather than inventing a second one.
TREND_SUPPORTED_MIN_USABLE_PERIODS = 3
TREND_BROADER_MIN_USABLE_PERIODS = 5


def trend_evidence_depth(classification: dict) -> dict:
    """`classification` is `trends.classify_trend`'s own return value —
    this never re-derives a trend label, only reframes its already-computed
    usable-period count as an evidence-depth state."""
    usable = classification["usable_periods"]
    if classification["label"] == "sparse_insufficient_evidence" or usable < 1:
        return {"state": EVIDENCE_DEPTH_INSUFFICIENT, "reason": classification["rationale"], "usable_periods": usable}
    if usable < TREND_SUPPORTED_MIN_USABLE_PERIODS:
        state = EVIDENCE_DEPTH_THIN
    elif usable < TREND_BROADER_MIN_USABLE_PERIODS:
        state = EVIDENCE_DEPTH_SUPPORTED
    else:
        state = EVIDENCE_DEPTH_BROADER_SUPPORT
    return {
        "state": state,
        "reason": f"{usable} usable period(s) (>= {SPARSE_MIN_SAMPLE} roles each) behind this trend.",
        "usable_periods": usable,
    }


# --- Orchestrator (build §2/§16) ---------------------------------------------


def build_coverage_summary(cur, scope: CoverageScope) -> dict:
    """The one canonical Market Coverage response (build §4). Bounded
    aggregate SQL only — see the module docstring; no per-role loop, no N+1
    review-summary lookups (build §31)."""
    candidate_rows = fetch_scope_candidate_roles(cur, scope)
    rows, excluded_undated = _apply_date_scope(candidate_rows, scope)
    role_ids = [r["id"] for r in rows]

    review_summaries = load_requirement_review_summary_bulk(cur, role_ids)
    legacy_counts = _fetch_legacy_fallback_counts(cur, role_ids)
    freshness = economics_freshness(cur)

    roles_block = build_role_corpus_block(rows, excluded_undated)
    time_block = build_time_block(rows, excluded_undated)
    geography_block = build_geography_block(rows)
    sources_block = build_sources_block(rows)
    requirements_block = build_requirements_block(role_ids, review_summaries, legacy_counts)
    vocabulary_block = build_vocabulary_block(role_ids, review_summaries, legacy_counts)
    archetypes_block = build_archetypes_block(cur, rows, review_summaries, freshness)
    compensation_block = build_compensation_block(cur, role_ids, roles_block["total"])
    comp_facets_for_concentration = {"providers": compensation_block["top_providers"]}
    concentration_block = build_concentration_block(rows, comp_facets_for_concentration)

    limitations = [
        "This is a user-collected/captured corpus with no known labour-market sampling frame; representativeness "
        "of the wider market is unknown.",
        "Absence of a role, requirement or archetype from this corpus is not evidence of its absence from the "
        "market.",
        "High internal metadata completeness never establishes external representativeness.",
    ]
    if scope.dated_scope_active and roles_block["excluded_undated_count"] > 0:
        limitations.append(
            f"{roles_block['excluded_undated_count']} posting(s) matching every other filter were excluded from "
            "this dated scope because their posting date is unknown."
        )
    for note in (
        _concentration_limitation(concentration_block["top_capture_sources"], len(rows), "capture source"),
        _concentration_limitation(concentration_block["top_countries"], len(rows), "country"),
    ):
        if note:
            limitations.append(note)
    if not freshness["fresh"]:
        limitations.append(freshness["reason"])

    return {
        "scope": scope.to_public(),
        "roles": roles_block,
        "time": time_block,
        "geography": geography_block,
        "sources": sources_block,
        "requirements": requirements_block,
        "vocabulary": vocabulary_block,
        "archetypes": archetypes_block,
        "compensation": compensation_block,
        "derived_economics": freshness,
        "representativeness": REPRESENTATIVENESS_UNKNOWN,
        "concentration": concentration_block,
        "limitations": limitations,
    }


# --- Career Direction discovery's compact subset (build §22) ----------------


def discovery_corpus_disclosure(cur) -> dict:
    """The compact, bounded facts `career_direction_discovery.py` hands to
    the model and the frontend's post-generation disclosure (build §22) —
    built from the exact same whole-corpus summary this module's other
    consumers use, never a second hand-rolled set of counts. Never send the
    full Market Coverage response to the model; this is deliberately a small
    projection of it."""
    summary = build_coverage_summary(cur, CoverageScope())
    return {
        "total_observed_postings": summary["roles"]["total"],
        "postings_with_archetype_assignment": summary["archetypes"]["assigned_active"]["count"],
        "postings_with_reviewed_requirements": summary["requirements"]["accepted_present"]["count"],
        "posting_date_range": {
            "earliest": summary["time"]["earliest_known_posting_date"],
            "latest": summary["time"]["latest_known_posting_date"],
        },
        "postings_with_unknown_posting_date": summary["time"]["unknown_posting_date"]["count"],
        "country_concentration": summary["concentration"]["top_countries"][:3],
        "active_archetypes_total": summary["archetypes"]["active_archetype_count"],
        "supported_archetypes": summary["archetypes"]["active_archetypes_with_support"]["count"],
        "archetypes_with_compensation_evidence": sum(
            1 for a in summary["archetypes"]["support"] if a["compensation_benchmark_available"]
        ),
        "compensation_evidence_available": summary["compensation"]["coverage"]["accepted_observation_count"] > 0,
        "economics_freshness": {
            "state": summary["derived_economics"]["state"],
            "fresh": summary["derived_economics"]["fresh"],
            "reason": summary["derived_economics"]["reason"],
        },
        "representativeness": summary["representativeness"],
    }
