"""Evidence-based role/archetype compensation resolution (build §3) and the
personal current-vs-opportunity comparison (build §4).

This restores the *function* of the old opaque `salary_estimate_*` field —
"what would this role pay?" — without restoring its implementation. There is
no model call anywhere in this module and no new statistical aggregate: the
market tier reads `jobber.d_archetype_comp`, the derived benchmark
`economics_engine.py` already computes, rather than recomputing a parallel
benchmark in route code.

## Precedence (build §3)

    1. accepted *stated* compensation on this role   -> basis 'advert_stated'
    2. otherwise the role's archetype + market benchmark -> 'market_estimate'
    3. otherwise the legacy posting estimate, if present -> 'legacy_estimate'
    4. otherwise                                     -> 'insufficient_evidence'

Each tier produces a different, never-interchangeable `basis`, and every
returned figure carries it. The UI is required to render these as four
visibly distinct things ("Advert salary" / "Market estimate" / "Legacy
estimate" / "Insufficient evidence"); nothing here merges them, and there is
no code path that promotes a lower tier's number into a higher tier's label.

Within tier 1, a *reviewed* stated observation (one carrying a verbatim
`evidence_span` quoted from the immutable source document — see
app/posting_compensation.py) outranks the mechanical 0013 backfill
projection of the legacy `role_instance.salary_min/max` columns, which has
no quote behind it. Both are genuinely "stated on the advert"; the reviewed
one simply has better provenance, so it is the one shown.

## Comparison compatibility (build §4)

A delta is computed only where the comparison is genuinely like-for-like:
same currency, same component, same pay period, and a compatible employment
basis. Where it is not, `comparable` is false and `reason` says exactly
which check failed. No currency is ever converted, no employment basis is
ever equated, and a contract day rate is only compared against an annual
salary through an explicit, user-stated planning equivalent — which is then
flagged as such on the result.
"""

from datetime import date

from .personal_earnings import EMPLOYMENT_BASIS_EQUIVALENT

BASIS_ADVERT_STATED = "advert_stated"
BASIS_MARKET_ESTIMATE = "market_estimate"
BASIS_LEGACY_ESTIMATE = "legacy_estimate"
BASIS_INSUFFICIENT = "insufficient_evidence"

# Human-facing label per basis. Defined once here so backend traces and the
# frontend cannot drift into describing the same basis differently.
BASIS_LABELS = {
    BASIS_ADVERT_STATED: "Advert salary",
    BASIS_MARKET_ESTIMATE: "Market estimate",
    BASIS_LEGACY_ESTIMATE: "Legacy estimate",
    BASIS_INSUFFICIENT: "Insufficient compensation evidence",
}

_DEFAULT_COMPONENT = "base"
_DEFAULT_PAY_PERIOD = "annual"


def _insufficient(reason: str, trace: dict | None = None) -> dict:
    return {
        "basis": BASIS_INSUFFICIENT,
        "basis_label": BASIS_LABELS[BASIS_INSUFFICIENT],
        "currency": None,
        "amount_min": None,
        "amount_reference": None,
        "amount_max": None,
        "component": None,
        "pay_period": None,
        "employment_basis": None,
        "market": None,
        "period": None,
        "as_of": None,
        "archetype": None,
        "evidence": {"n_observations": 0, "n_posting_stated": 0, "n_survey_sources": 0},
        "reference_source": None,
        "evidence_quality": "insufficient",
        "reason": reason,
        "trace": trace or {},
    }


def _evidence_quality_for_market(row: dict) -> str:
    """Same four-level vocabulary `d_gap_value.evidence_quality` uses, so one
    word never means two different things in this app. Derived from what the
    benchmark actually rests on — a survey source with a published sample is
    stronger evidence than five advertised ranges."""
    if row.get("reference_comp") is None:
        return "insufficient"
    if row.get("reference_source") == "survey":
        detail = row.get("reference_basis_detail") or {}
        return "good" if detail.get("quality_tier") == "explicit_sample_n_ge_5" else "moderate"
    n = row.get("n_posting_stated") or 0
    if n >= 25:
        return "good"
    if n >= 10:
        return "moderate"
    return "thin"


# --- Bulk loaders (build "avoid N+1"): one query per concern, never per role --

def _load_roles(cur, role_ids: list[str]) -> dict[str, dict]:
    if not role_ids:
        return {}
    cur.execute(
        "SELECT ri.id, ri.title, ri.country, ri.currency, ri.archetype_concept_id, "
        "       ri.salary_min, ri.salary_max, ri.salary_estimate_min, ri.salary_estimate_max, "
        "       c.canonical_name AS archetype_name, c.status AS archetype_status "
        "FROM jobber.role_instance ri "
        "LEFT JOIN jobber.concept c ON c.id = ri.archetype_concept_id "
        "WHERE ri.id = ANY(%s::uuid[])",
        (role_ids,),
    )
    return {str(r["id"]): dict(r) for r in cur.fetchall()}


def _load_role_observations(cur, role_ids: list[str]) -> dict[str, list[dict]]:
    """Every accepted compensation observation attached directly to these
    roles, in one query. Ordered so the caller can take the first row per
    (role, basis) without re-sorting: reviewed source-quoted evidence first,
    then the most recently observed."""
    if not role_ids:
        return {}
    cur.execute(
        """
        SELECT co.id, co.role_instance_id, co.basis, co.component, co.pay_period, co.employment_basis,
               co.amount_min, co.amount_mid, co.amount_max, co.currency, co.evidence_span,
               co.observed_at, co.document_id, co.source_note, co.market_id, m.label AS market_label
        FROM jobber.compensation_observation co
        LEFT JOIN jobber.market m ON m.id = co.market_id
        WHERE co.review_status = 'accepted' AND co.role_instance_id = ANY(%s::uuid[])
        ORDER BY (co.evidence_span IS NULL), co.observed_at DESC NULLS LAST, co.id
        """,
        (role_ids,),
    )
    grouped: dict[str, list[dict]] = {rid: [] for rid in role_ids}
    for row in cur.fetchall():
        grouped.setdefault(str(row["role_instance_id"]), []).append(dict(row))
    return grouped


def load_archetype_benchmarks(
    cur, archetype_ids: list[str], *, market_id: str | None = None, currency: str | None = None,
    component: str = _DEFAULT_COMPONENT, pay_period: str = _DEFAULT_PAY_PERIOD,
) -> dict[str, dict]:
    """Best available `d_archetype_comp` row per archetype, in one query.

    When no market/currency is pinned, the row with an actual reference
    figure wins, then the larger evidence base, then the most recent period
    — deterministic, and never an average across markets (which would be
    exactly the kind of fabricated cross-context figure this build refuses)."""
    if not archetype_ids:
        return {}
    query = (
        "SELECT ac.*, c.canonical_name AS archetype_name, m.label AS market_label, m.code AS market_code "
        "FROM jobber.d_archetype_comp ac "
        "JOIN jobber.concept c ON c.id = ac.archetype_concept_id "
        "JOIN jobber.market m ON m.id = ac.market_id "
        "WHERE ac.archetype_concept_id = ANY(%s::uuid[]) AND ac.component = %s AND ac.pay_period = %s"
    )
    params: list = [archetype_ids, component, pay_period]
    if market_id:
        query += " AND ac.market_id = %s"
        params.append(market_id)
    if currency:
        query += " AND ac.currency = %s"
        params.append(currency)
    query += (
        " ORDER BY ac.archetype_concept_id, (ac.reference_comp IS NULL), "
        "ac.n_observations DESC, ac.period_end DESC, ac.currency"
    )
    cur.execute(query, params)
    best: dict[str, dict] = {}
    for row in cur.fetchall():
        key = str(row["archetype_concept_id"])
        if key not in best:  # first row per archetype is the best by the ORDER BY above
            best[key] = dict(row)
    return best


# --- Resolution -------------------------------------------------------------

def _from_stated_observation(observation: dict, role: dict) -> dict:
    lo = float(observation["amount_min"]) if observation["amount_min"] is not None else None
    hi = float(observation["amount_max"]) if observation["amount_max"] is not None else None
    mid = float(observation["amount_mid"]) if observation["amount_mid"] is not None else None
    reference = mid if mid is not None else ((lo + hi) / 2 if lo is not None and hi is not None else (lo if lo is not None else hi))
    return {
        "basis": BASIS_ADVERT_STATED,
        "basis_label": BASIS_LABELS[BASIS_ADVERT_STATED],
        "currency": observation["currency"],
        "amount_min": lo,
        "amount_reference": reference,
        "amount_max": hi,
        "component": observation["component"],
        "pay_period": observation["pay_period"],
        "employment_basis": observation["employment_basis"],
        "market": (
            {"id": str(observation["market_id"]), "label": observation["market_label"]}
            if observation["market_id"] else None
        ),
        "period": None,
        "as_of": observation["observed_at"],
        "archetype": (
            {"id": str(role["archetype_concept_id"]), "name": role["archetype_name"]}
            if role.get("archetype_concept_id") else None
        ),
        "evidence": {"n_observations": 1, "n_posting_stated": 1, "n_survey_sources": 0},
        "reference_source": "posting_stated",
        "evidence_quality": "good" if observation["evidence_span"] else "moderate",
        "reason": (
            "Compensation stated on this posting, reviewed against a verbatim quote from the source document."
            if observation["evidence_span"]
            else "Compensation stated on this posting (projected from the captured salary fields; no source quote recorded)."
        ),
        "trace": {
            "tier": 1,
            "observation_id": str(observation["id"]),
            "source_quoted": bool(observation["evidence_span"]),
            "evidence_span": observation["evidence_span"],
            "document_id": str(observation["document_id"]) if observation["document_id"] else None,
        },
    }


def _from_archetype_benchmark(benchmark: dict, role: dict | None = None) -> dict:
    reference = float(benchmark["reference_comp"]) if benchmark["reference_comp"] is not None else None
    lo = float(benchmark["posting_p25"]) if benchmark["posting_p25"] is not None else None
    hi = float(benchmark["posting_p75"]) if benchmark["posting_p75"] is not None else None
    return {
        "basis": BASIS_MARKET_ESTIMATE,
        "basis_label": BASIS_LABELS[BASIS_MARKET_ESTIMATE],
        "currency": benchmark["currency"],
        "amount_min": lo,
        "amount_reference": reference,
        "amount_max": hi,
        "component": benchmark["component"],
        "pay_period": benchmark["pay_period"],
        # A market benchmark is an archetype-level aggregate; it says nothing
        # about whether a particular engagement is permanent or contract.
        "employment_basis": None,
        "market": {"id": str(benchmark["market_id"]), "label": benchmark["market_label"], "code": benchmark["market_code"]},
        "period": {"start": benchmark["period_start"], "end": benchmark["period_end"]},
        "as_of": benchmark["period_end"],
        "archetype": {"id": str(benchmark["archetype_concept_id"]), "name": benchmark["archetype_name"]},
        "evidence": {
            "n_observations": benchmark["n_observations"],
            "n_posting_stated": benchmark["n_posting_stated"],
            "n_posting_estimated": benchmark["n_posting_estimated"],
            "n_survey_sources": benchmark["n_survey_sources"],
        },
        "reference_source": benchmark["reference_source"],
        "evidence_quality": _evidence_quality_for_market(benchmark),
        "reason": (
            f"No compensation stated on this role; using the {benchmark['archetype_name']} archetype benchmark "
            f"for {benchmark['market_label']}."
            if role is not None
            else f"{benchmark['archetype_name']} archetype benchmark for {benchmark['market_label']}."
        ),
        "trace": {
            "tier": 2,
            "reference_basis_detail": benchmark["reference_basis_detail"],
            "engine_version": benchmark["engine_version"],
            "computed_at": benchmark["computed_at"],
            "source": "jobber.d_archetype_comp",
        },
    }


def _from_legacy_estimate(role: dict, observations: list[dict]) -> dict | None:
    """The pre-existing opaque estimate, preserved as a clearly-labelled last
    resort rather than revived as authority. Read from an accepted
    `posting_estimated` observation where the 0013 backfill created one, and
    otherwise straight off the legacy `role_instance.salary_estimate_*`
    columns so a role that was never backfilled still shows what it has."""
    estimated = next((o for o in observations if o["basis"] == "posting_estimated"), None)
    if estimated is not None:
        lo = float(estimated["amount_min"]) if estimated["amount_min"] is not None else None
        hi = float(estimated["amount_max"]) if estimated["amount_max"] is not None else None
        currency = estimated["currency"]
        observation_id = str(estimated["id"])
        market = {"id": str(estimated["market_id"]), "label": estimated["market_label"]} if estimated["market_id"] else None
        as_of = estimated["observed_at"]
        component, pay_period = estimated["component"], estimated["pay_period"]
    else:
        lo = float(role["salary_estimate_min"]) if role["salary_estimate_min"] is not None else None
        hi = float(role["salary_estimate_max"]) if role["salary_estimate_max"] is not None else None
        currency = role["currency"]
        observation_id = None
        market = None
        as_of = None
        component, pay_period = _DEFAULT_COMPONENT, _DEFAULT_PAY_PERIOD
    if lo is None and hi is None:
        return None
    if not currency:
        # A number with no currency is not a usable economic fact, and this
        # build never guesses one from the role's country.
        return None
    reference = (lo + hi) / 2 if lo is not None and hi is not None else (lo if lo is not None else hi)
    return {
        "basis": BASIS_LEGACY_ESTIMATE,
        "basis_label": BASIS_LABELS[BASIS_LEGACY_ESTIMATE],
        "currency": currency,
        "amount_min": lo,
        "amount_reference": reference,
        "amount_max": hi,
        "component": component,
        "pay_period": pay_period,
        "employment_basis": None,
        "market": market,
        "period": None,
        "as_of": as_of,
        "archetype": (
            {"id": str(role["archetype_concept_id"]), "name": role["archetype_name"]}
            if role.get("archetype_concept_id") else None
        ),
        "evidence": {"n_observations": 1, "n_posting_stated": 0, "n_survey_sources": 0},
        "reference_source": "legacy_estimate",
        "evidence_quality": "thin",
        "reason": (
            "No stated compensation and no archetype benchmark available. This is the legacy estimate captured "
            "with the original posting — an unexplained historical figure, shown for continuity only."
        ),
        "trace": {"tier": 3, "observation_id": observation_id, "source": "role_instance.salary_estimate_*"},
    }


def resolve_role_compensation_bulk(
    cur, role_ids: list[str], *, market_id: str | None = None, currency: str | None = None,
) -> dict[str, dict]:
    """The precedence rule applied to many roles in a fixed number of
    queries (three, regardless of how many roles) — Pathways resolves
    compensation for every candidate archetype's supporting postings and must
    not issue one query per role."""
    role_ids = [str(r) for r in role_ids]
    if not role_ids:
        return {}
    roles = _load_roles(cur, role_ids)
    observations = _load_role_observations(cur, role_ids)
    archetype_ids = sorted({str(r["archetype_concept_id"]) for r in roles.values() if r["archetype_concept_id"]})
    benchmarks = load_archetype_benchmarks(cur, archetype_ids, market_id=market_id, currency=currency)

    resolved: dict[str, dict] = {}
    for role_id in role_ids:
        role = roles.get(role_id)
        if role is None:
            resolved[role_id] = _insufficient("This role no longer exists.")
            continue
        role_observations = observations.get(role_id, [])

        stated = next((o for o in role_observations if o["basis"] == "posting_stated"), None)
        if stated is not None:
            resolved[role_id] = _from_stated_observation(stated, role)
            continue

        archetype_id = str(role["archetype_concept_id"]) if role["archetype_concept_id"] else None
        benchmark = benchmarks.get(archetype_id) if archetype_id else None
        if benchmark is not None and benchmark["reference_comp"] is not None:
            resolved[role_id] = _from_archetype_benchmark(benchmark, role)
            continue

        legacy = _from_legacy_estimate(role, role_observations)
        if legacy is not None:
            resolved[role_id] = legacy
            continue

        if archetype_id is None:
            reason = (
                "No compensation is stated on this role, and it has no reviewed archetype, so no market "
                "benchmark applies. Assign an archetype, or review stated compensation from the source."
            )
        elif benchmark is None:
            reason = (
                f"No compensation is stated on this role, and no compensation evidence has been accepted for the "
                f"{role['archetype_name']} archetype in this market yet."
            )
        else:
            reason = (
                f"No compensation is stated on this role, and the {role['archetype_name']} archetype has "
                "compensation evidence but not enough for a reference benchmark."
            )
        resolved[role_id] = _insufficient(
            reason,
            {"tier": 4, "archetype_concept_id": archetype_id, "has_archetype_comp_row": benchmark is not None},
        )
    return resolved


def resolve_role_compensation(cur, role_id: str, *, market_id: str | None = None, currency: str | None = None) -> dict:
    return resolve_role_compensation_bulk(cur, [role_id], market_id=market_id, currency=currency)[str(role_id)]


# Public names for the two builders Pathways needs directly: it has already
# bulk-loaded the benchmark rows for every candidate archetype and must not
# re-query one archetype at a time.
from_archetype_benchmark = _from_archetype_benchmark
insufficient_compensation = _insufficient


def resolve_archetype_compensation(
    cur, archetype_concept_id: str, *, market_id: str | None = None, currency: str | None = None,
) -> dict:
    """An archetype has no advert of its own, so its resolution starts at
    tier 2 and falls straight to 'insufficient evidence'."""
    benchmarks = load_archetype_benchmarks(cur, [str(archetype_concept_id)], market_id=market_id, currency=currency)
    benchmark = benchmarks.get(str(archetype_concept_id))
    if benchmark is not None and benchmark["reference_comp"] is not None:
        return _from_archetype_benchmark(benchmark)
    return _insufficient(
        "No accepted compensation evidence qualifies as a benchmark for this archetype in this market yet.",
        {"tier": 4, "archetype_concept_id": str(archetype_concept_id), "has_archetype_comp_row": benchmark is not None},
    )


# --- Personal current-vs-opportunity comparison (build §4) ------------------

def _component_family(component: str | None, pay_period: str | None, unit: str | None) -> str | None:
    """Reduce the jobber-side and profile360-side component vocabularies to
    the one thing a comparison needs: is this annual base pay, or a day rate?
    Anything else (bonus percentage, total package, an allowance) has no
    counterpart on the other side and is deliberately not mapped."""
    if component in ("annual_base",) or (component == "base" and pay_period == "annual"):
        return "annual_base"
    if component in ("day_rate",) or (component == "base" and pay_period == "daily"):
        return "day_rate"
    if component == "periodic_base" and unit == "annual":
        return "annual_base"
    return None


def _not_comparable(reason: str, baseline: dict | None = None) -> dict:
    return {
        "comparable": False,
        "reason": reason,
        "baseline": baseline,
        "uses_planning_equivalent": False,
        "difference_min": None,
        "difference_reference": None,
        "difference_max": None,
    }


def compare_to_personal_earnings(opportunity: dict, earnings_state: dict) -> dict:
    """Compare one resolved opportunity compensation against the personal
    earnings state, and refuse to when the two are not genuinely comparable.

    Checked, in order: the opportunity has a figure at all; a personal
    baseline exists; currency matches exactly; the component/pay-period
    family matches (directly, or via a stated planning equivalent for a day
    rate against an annual figure); employment basis is compatible where both
    sides state one. The first failing check is the reported reason — never a
    generic "not comparable"."""
    if opportunity.get("basis") == BASIS_INSUFFICIENT or opportunity.get("amount_reference") is None:
        return _not_comparable("There is no compensation figure for this opportunity to compare against.")

    baselines = earnings_state.get("baselines") or []
    if not baselines:
        if earnings_state.get("status") == "profile360_unavailable":
            return _not_comparable("Your own compensation evidence is not reachable, so no comparison can be made.")
        return _not_comparable(
            "No accepted personal compensation evidence exists yet, so there is no baseline to compare against."
        )

    currency = opportunity.get("currency")
    same_currency = [b for b in baselines if b["currency"] == currency]
    if not same_currency:
        held = ", ".join(sorted({b["currency"] for b in baselines if b["currency"]})) or "none"
        return _not_comparable(
            f"This opportunity is quoted in {currency}; your accepted evidence is in {held}. "
            "Amounts are never converted between currencies."
        )

    opportunity_family = _component_family(opportunity.get("component"), opportunity.get("pay_period"), None)
    if opportunity_family is None:
        return _not_comparable(
            f"This figure is a {opportunity.get('component')} / {opportunity.get('pay_period')} amount, "
            "which has no directly comparable counterpart in your own evidence."
        )

    # Direct, like-for-like match first; only fall back to a planning
    # equivalent when nothing directly comparable exists.
    direct = [
        b for b in same_currency
        if _component_family(b["component"], None, b["unit"]) == opportunity_family
    ]
    chosen, uses_planning_equivalent, baseline_amount = None, False, None
    for baseline in direct:
        chosen, baseline_amount = baseline, baseline["amount"]
        break
    if chosen is None and opportunity_family == "annual_base":
        for baseline in same_currency:
            equivalent = baseline.get("planning_equivalent")
            if equivalent is not None:
                chosen, uses_planning_equivalent, baseline_amount = baseline, True, equivalent["amount"]
                break
    if chosen is None:
        contract_day_rate = any(_component_family(b["component"], None, b["unit"]) == "day_rate" for b in same_currency)
        if opportunity_family == "annual_base" and contract_day_rate:
            return _not_comparable(
                "Your latest evidence is a contract day rate and this opportunity is an annual salary. "
                "Set a billable-days-per-year planning assumption to compare them; no annualisation is assumed."
            )
        return _not_comparable(
            "Your accepted evidence has no baseline of the same kind as this figure "
            f"({opportunity_family.replace('_', ' ')})."
        )

    personal_basis = chosen.get("employment_basis_equivalent")
    opportunity_basis = opportunity.get("employment_basis")
    if (
        personal_basis and opportunity_basis
        and personal_basis != "unknown" and opportunity_basis != "unknown"
        and personal_basis != opportunity_basis
        and not uses_planning_equivalent
    ):
        return _not_comparable(
            f"Your baseline is {chosen['employment_basis']} and this opportunity is {opportunity_basis}. "
            "Employment bases are never treated as equivalent.",
            _baseline_summary(chosen, baseline_amount, uses_planning_equivalent),
        )

    def _delta(value):
        return None if value is None else value - baseline_amount

    return {
        "comparable": True,
        "reason": None,
        "baseline": _baseline_summary(chosen, baseline_amount, uses_planning_equivalent),
        "uses_planning_equivalent": uses_planning_equivalent,
        "difference_min": _delta(opportunity.get("amount_min")),
        "difference_reference": _delta(opportunity.get("amount_reference")),
        "difference_max": _delta(opportunity.get("amount_max")),
        "opportunity": {
            "basis": opportunity.get("basis"),
            "basis_label": opportunity.get("basis_label"),
            "currency": currency,
            "amount_min": opportunity.get("amount_min"),
            "amount_reference": opportunity.get("amount_reference"),
            "amount_max": opportunity.get("amount_max"),
        },
        "limitations": _limitations(opportunity, chosen, uses_planning_equivalent),
    }


def _baseline_summary(baseline: dict, amount: float, uses_planning_equivalent: bool) -> dict:
    return {
        "observation_id": baseline["observation_id"],
        "component": baseline["component"],
        "amount": amount,
        "source_amount": baseline["amount"],
        "currency": baseline["currency"],
        "unit": "annual" if uses_planning_equivalent else baseline["unit"],
        "employment_basis": baseline["employment_basis"],
        "evidence_status": baseline["evidence_status"],
        "evidence_period": baseline["evidence_period"],
        "label": baseline["label"],
        "planning_equivalent": baseline.get("planning_equivalent") if uses_planning_equivalent else None,
    }


def _limitations(opportunity: dict, baseline: dict, uses_planning_equivalent: bool) -> list[str]:
    limitations = []
    if uses_planning_equivalent:
        limitations.append(
            "Compared using your stated billable-days planning equivalent, not a salary. "
            "Contracting and employment carry different costs, benefits and risk."
        )
    if baseline["evidence_status"] == "historical":
        limitations.append(
            f"Your baseline is historical evidence ({baseline['evidence_period']}), not current pay."
        )
    if opportunity.get("basis") == BASIS_MARKET_ESTIMATE:
        limitations.append(
            "The opportunity figure is an archetype market benchmark, not this role's advertised salary."
        )
    if opportunity.get("basis") == BASIS_LEGACY_ESTIMATE:
        limitations.append(
            "The opportunity figure is a legacy estimate with no recorded derivation."
        )
    as_of = opportunity.get("as_of")
    if isinstance(as_of, date) and baseline.get("effective_from") and as_of.year != baseline["effective_from"].year:
        limitations.append(
            f"The two figures are from different years ({as_of.year} vs {baseline['effective_from'].year}); "
            "no inflation or market adjustment is applied."
        )
    limitations.append("Base pay only — bonus, pension and benefits are not included on either side.")
    return limitations
