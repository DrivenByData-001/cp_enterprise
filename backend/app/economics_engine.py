"""Phase 4 derivation engine: archetype demand, archetype compensation, and
capability gap value (prompt §4/§9/§10/§11/§12).

Follows the Phase 3 `capability_engine.py` convention exactly: every
function here is a pure function of accepted evidence rows plus the current
archetype/capability catalogue; nothing here is an AI judgment call. Derived
tables are disposable/rebuildable — every row carries `engine_version` and
`computed_at`, and rebuilding recomputes fresh then deletes anything stale,
never truncate-first.

## Period (prompt §5/§11, doc 12 §2.3)

This build computes one rolling "all accepted evidence to date" period per
bucket: `period_start` is a fixed epoch (`PERIOD_START`, 2000-01-01) and
`period_end` is the date of the rebuild that produced the row. This is a
deliberate v1 simplification, not a hidden default — the UI's period
selector shows exactly this one computed period, labelled "through
<period_end>". True multi-period (e.g. year-over-year) comparison is
deferred; see docs/24-phase4-economics-and-vocabulary-overview.md. Because
`period_end` advances on every rebuild day, cleanup always deletes any row
whose `period_end` does not match the rebuild just performed — this is what
keeps the table from accumulating one stale row per rebuild day forever.

## Gap value's fixed component/pay_period (prompt §11)

`d_gap_value`'s key (capability, market, period, currency) deliberately
carries no component/pay_period — comparing "which reachable role pays
more" needs one canonical, comparable figure. This engine always uses
`component='base', pay_period='annual'` for the compensation side of gap
value. A capability's demand/coverage counts (`d_archetype_demand`,
structural reachability) are unaffected by this choice; only the monetary
delta is scoped to base/annual pay.
"""

import statistics
from datetime import date

from .capability_engine import derive_role_fit
from .concept_linking import get_or_create_current_vocabulary_version
from .db import to_json_param
from .role_requirements import load_role_requirements

ENGINE_VERSION = "economics-engine-v1"

PERIOD_START = date(2000, 1, 1)

_SURVEY_SAMPLE_MIN = 5
_POSTING_SAMPLE_MIN = 5

# The one canonical component/pay_period gap value's monetary side uses —
# see module docstring.
_GAP_VALUE_COMPONENT = "base"
_GAP_VALUE_PAY_PERIOD = "annual"


def _current_period_end() -> date:
    return date.today()


def _active_archetypes(cur) -> list[dict]:
    cur.execute("SELECT id, canonical_name FROM jobber.concept WHERE type_code = 'role_archetype' AND status = 'active'")
    return [{"id": str(r["id"]), "canonical_name": r["canonical_name"]} for r in cur.fetchall()]


# --- d_archetype_demand (prompt §4) -----------------------------------------

def derive_archetype_demand(cur, archetype_concept_id: str) -> list[dict]:
    """One row per capability demanded by at least one role assigned to this
    archetype, derived from `role_requirements.load_role_requirements` — the
    same canonical loader role fit uses, so archetype demand and role fit
    never disagree about what a role requires. Only capability-typed
    concepts participate (prompt §4); atomic requirements stay valid in
    role fit but are out of scope here."""
    cur.execute("SELECT id FROM jobber.role_instance WHERE archetype_concept_id = %s", (archetype_concept_id,))
    role_ids = [str(r["id"]) for r in cur.fetchall()]
    roles_in_archetype = len(role_ids)

    per_capability: dict[str, dict] = {}
    for role_id in role_ids:
        for item in load_role_requirements(cur, role_id):
            if item["type_code"] != "capability":
                continue
            cap_id = item["concept_id"]
            bucket = per_capability.setdefault(
                cap_id,
                {
                    "canonical_name": item["canonical_name"],
                    "roles_demanding_capability": 0,
                    "required_count": 0,
                    "preferred_count": 0,
                    "contextual_count": 0,
                    "source_role_ids": [],
                },
            )
            if item["requirement_type"] == "required":
                bucket["required_count"] += 1
            elif item["requirement_type"] == "preferred":
                bucket["preferred_count"] += 1
            elif item["requirement_type"] == "contextual":
                bucket["contextual_count"] += 1
            if role_id not in bucket["source_role_ids"]:
                bucket["roles_demanding_capability"] += 1
                bucket["source_role_ids"].append(role_id)

    rows = []
    for cap_id, bucket in per_capability.items():
        demand_rate = bucket["roles_demanding_capability"] / roles_in_archetype if roles_in_archetype else None
        rows.append(
            {
                "archetype_concept_id": archetype_concept_id,
                "capability_concept_id": cap_id,
                "roles_in_archetype": roles_in_archetype,
                "roles_demanding_capability": bucket["roles_demanding_capability"],
                "demand_rate": demand_rate,
                "required_count": bucket["required_count"],
                "preferred_count": bucket["preferred_count"],
                "contextual_count": bucket["contextual_count"],
                "source_role_ids": bucket["source_role_ids"],
                "trace": {
                    "capability_name": bucket["canonical_name"],
                    "roles_in_archetype": roles_in_archetype,
                    "roles_demanding_capability": bucket["roles_demanding_capability"],
                },
            }
        )
    return rows


def _persist_archetype_demand(cur, row: dict, vocabulary_version_id) -> None:
    cur.execute(
        """
        INSERT INTO jobber.d_archetype_demand (
            archetype_concept_id, capability_concept_id, roles_in_archetype, roles_demanding_capability,
            demand_rate, required_count, preferred_count, contextual_count, source_role_ids, trace,
            vocabulary_version_id, engine_version, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::uuid[], %s, %s, %s, now())
        ON CONFLICT (archetype_concept_id, capability_concept_id) DO UPDATE SET
            roles_in_archetype = EXCLUDED.roles_in_archetype,
            roles_demanding_capability = EXCLUDED.roles_demanding_capability,
            demand_rate = EXCLUDED.demand_rate, required_count = EXCLUDED.required_count,
            preferred_count = EXCLUDED.preferred_count, contextual_count = EXCLUDED.contextual_count,
            source_role_ids = EXCLUDED.source_role_ids, trace = EXCLUDED.trace,
            vocabulary_version_id = EXCLUDED.vocabulary_version_id, engine_version = EXCLUDED.engine_version,
            computed_at = now()
        """,
        (
            row["archetype_concept_id"], row["capability_concept_id"], row["roles_in_archetype"],
            row["roles_demanding_capability"], row["demand_rate"], row["required_count"],
            row["preferred_count"], row["contextual_count"], row["source_role_ids"],
            to_json_param(row["trace"]), vocabulary_version_id, ENGINE_VERSION,
        ),
    )


def rebuild_archetype_demand(cur) -> dict:
    vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
    archetypes = _active_archetypes(cur)
    active_ids = [a["id"] for a in archetypes]
    total_computed = total_removed = 0

    for archetype in archetypes:
        rows = derive_archetype_demand(cur, archetype["id"])
        for row in rows:
            _persist_archetype_demand(cur, row, vocabulary_version_id)
        kept_capability_ids = [r["capability_concept_id"] for r in rows]
        if kept_capability_ids:
            cur.execute(
                "DELETE FROM jobber.d_archetype_demand WHERE archetype_concept_id = %s AND capability_concept_id != ALL(%s::uuid[])",
                (archetype["id"], kept_capability_ids),
            )
        else:
            cur.execute("DELETE FROM jobber.d_archetype_demand WHERE archetype_concept_id = %s", (archetype["id"],))
        total_removed += cur.rowcount
        total_computed += len(rows)

    if active_ids:
        cur.execute("DELETE FROM jobber.d_archetype_demand WHERE archetype_concept_id != ALL(%s::uuid[])", (active_ids,))
    else:
        cur.execute("DELETE FROM jobber.d_archetype_demand")
    total_removed += cur.rowcount
    return {"computed": total_computed, "removed_stale": total_removed, "engine_version": ENGINE_VERSION}


# --- d_archetype_comp / reference compensation rule (prompt §9/§10) --------

def _posting_midpoint(observation: dict) -> float | None:
    """Deterministic advertised-range midpoint — labelled as such, never
    'market median salary' (prompt §9)."""
    lo, hi = observation["amount_min"], observation["amount_max"]
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2.0
    if lo is not None:
        return float(lo)
    if hi is not None:
        return float(hi)
    return None


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Linear-interpolation percentile over an already-sorted list.
    Deterministic; not a claim about the underlying distribution's shape."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = fraction * (len(sorted_values) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _distinct_buckets(cur) -> list[tuple[str, str, str, str]]:
    """(market_id, currency, component, pay_period) combinations with at
    least one accepted observation."""
    cur.execute(
        "SELECT DISTINCT market_id, currency, component, pay_period FROM jobber.compensation_observation "
        "WHERE review_status = 'accepted' AND market_id IS NOT NULL"
    )
    return [(str(r["market_id"]), r["currency"], r["component"], r["pay_period"]) for r in cur.fetchall()]


def _accepted_observations_for_archetype(cur, archetype_concept_id: str, market_id: str, currency: str, component: str, pay_period: str) -> list[dict]:
    """Accepted observations tied to this archetype, either directly
    (survey rows keyed straight to the archetype) or via one of its
    currently assigned roles (posting rows)."""
    cur.execute(
        """
        SELECT co.id, co.basis, co.amount_min, co.amount_max, co.amount_mid,
               co.reported_p25, co.reported_p50, co.reported_p75, co.reported_sample_size,
               co.document_id, co.period_end, co.source_note
        FROM jobber.compensation_observation co
        WHERE co.review_status = 'accepted' AND co.market_id = %s AND co.currency = %s
          AND co.component = %s AND co.pay_period = %s
          AND (
              co.archetype_concept_id = %s
              OR co.role_instance_id IN (SELECT id FROM jobber.role_instance WHERE archetype_concept_id = %s)
          )
        """,
        (market_id, currency, component, pay_period, archetype_concept_id, archetype_concept_id),
    )
    return [dict(r) for r in cur.fetchall()]


def select_reference_compensation(observations: list[dict]) -> dict:
    """The deterministic benchmark-selection rule (prompt §10):

    1. Prefer the most recent accepted survey benchmark with an explicit
       p50/mid figure, reported_sample_size >= 5, and a linked source
       document — never a posting_estimated row, never a survey with a
       missing/sub-5 sample.
    2. Else use the posting-derived benchmark only with >=5 posting_stated
       observations (posting_estimated never counts toward this gate).
    3. Else no monetary benchmark — the caller keeps structural information
       only.

    Ties among qualifying surveys break by most recent period_end, then
    reported_sample_size, then a stable id ordering.
    """
    surveys = [o for o in observations if o["basis"] == "survey"]
    qualifying_surveys = [
        o
        for o in surveys
        if (o["reported_p50"] is not None or o["amount_mid"] is not None)
        and o["reported_sample_size"] is not None
        and o["reported_sample_size"] >= _SURVEY_SAMPLE_MIN
        and o["document_id"] is not None
    ]
    if qualifying_surveys:
        def _key(o):
            return (o["period_end"] or date.min, o["reported_sample_size"], str(o["id"]))

        chosen = max(qualifying_surveys, key=_key)
        reference_comp = chosen["reported_p50"] if chosen["reported_p50"] is not None else chosen["amount_mid"]
        return {
            "reference_comp": float(reference_comp),
            "reference_source": "survey",
            "reference_basis_detail": {
                "observation_id": str(chosen["id"]),
                "sample_size": chosen["reported_sample_size"],
                "period_end": chosen["period_end"].isoformat() if chosen["period_end"] else None,
            },
        }

    posting_stated = [o for o in observations if o["basis"] == "posting_stated"]
    if len(posting_stated) >= _POSTING_SAMPLE_MIN:
        midpoints = sorted(m for o in posting_stated if (m := _posting_midpoint(o)) is not None)
        if midpoints:
            return {
                "reference_comp": statistics.median(midpoints),
                "reference_source": "posting",
                "reference_basis_detail": {
                    "n_posting_stated": len(posting_stated),
                    "label": "median advertised-range midpoint",
                },
            }

    return {"reference_comp": None, "reference_source": None, "reference_basis_detail": None}


def derive_archetype_comp(cur, archetype_concept_id: str, market_id: str, currency: str, component: str, pay_period: str) -> dict | None:
    observations = _accepted_observations_for_archetype(cur, archetype_concept_id, market_id, currency, component, pay_period)
    if not observations:
        return None

    n_posting_stated = sum(1 for o in observations if o["basis"] == "posting_stated")
    n_posting_estimated = sum(1 for o in observations if o["basis"] == "posting_estimated")
    n_survey_sources = sum(1 for o in observations if o["basis"] == "survey")

    posting_midpoints = sorted(m for o in observations if o["basis"] == "posting_stated" and (m := _posting_midpoint(o)) is not None)
    posting_p25 = posting_p50 = posting_p75 = None
    if posting_midpoints:
        posting_p50 = statistics.median(posting_midpoints)
        posting_p25 = _percentile(posting_midpoints, 0.25)
        posting_p75 = _percentile(posting_midpoints, 0.75)

    survey_benchmarks = [
        {
            "observation_id": str(o["id"]),
            "reported_p25": float(o["reported_p25"]) if o["reported_p25"] is not None else None,
            "reported_p50": float(o["reported_p50"]) if o["reported_p50"] is not None else None,
            "reported_p75": float(o["reported_p75"]) if o["reported_p75"] is not None else None,
            "amount_mid": float(o["amount_mid"]) if o["amount_mid"] is not None else None,
            "reported_sample_size": o["reported_sample_size"],
            "source_note": o["source_note"],
        }
        for o in observations
        if o["basis"] == "survey"
    ]

    reference = select_reference_compensation(observations)

    return {
        "archetype_concept_id": archetype_concept_id,
        "market_id": market_id,
        "currency": currency,
        "component": component,
        "pay_period": pay_period,
        "n_observations": len(observations),
        "n_posting_stated": n_posting_stated,
        "n_posting_estimated": n_posting_estimated,
        "n_survey_sources": n_survey_sources,
        "posting_p25": posting_p25,
        "posting_p50": posting_p50,
        "posting_p75": posting_p75,
        "survey_benchmarks": survey_benchmarks,
        "reference_comp": reference["reference_comp"],
        "reference_source": reference["reference_source"],
        "reference_basis_detail": reference["reference_basis_detail"],
        "trace": {
            "n_observations": len(observations),
            "n_posting_stated": n_posting_stated,
            "n_survey_sources": n_survey_sources,
            "reference": reference,
        },
    }


def _persist_archetype_comp(cur, row: dict, period_start: date, period_end: date) -> None:
    cur.execute(
        """
        INSERT INTO jobber.d_archetype_comp (
            archetype_concept_id, market_id, period_start, period_end, currency, component, pay_period,
            n_observations, n_posting_stated, n_posting_estimated, n_survey_sources,
            posting_p25, posting_p50, posting_p75, survey_benchmarks,
            reference_comp, reference_source, reference_basis_detail, trace, engine_version, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (archetype_concept_id, market_id, period_start, period_end, currency, component, pay_period) DO UPDATE SET
            n_observations = EXCLUDED.n_observations, n_posting_stated = EXCLUDED.n_posting_stated,
            n_posting_estimated = EXCLUDED.n_posting_estimated, n_survey_sources = EXCLUDED.n_survey_sources,
            posting_p25 = EXCLUDED.posting_p25, posting_p50 = EXCLUDED.posting_p50, posting_p75 = EXCLUDED.posting_p75,
            survey_benchmarks = EXCLUDED.survey_benchmarks, reference_comp = EXCLUDED.reference_comp,
            reference_source = EXCLUDED.reference_source, reference_basis_detail = EXCLUDED.reference_basis_detail,
            trace = EXCLUDED.trace, engine_version = EXCLUDED.engine_version, computed_at = now()
        """,
        (
            row["archetype_concept_id"], row["market_id"], period_start, period_end, row["currency"],
            row["component"], row["pay_period"], row["n_observations"], row["n_posting_stated"],
            row["n_posting_estimated"], row["n_survey_sources"], row["posting_p25"], row["posting_p50"],
            row["posting_p75"], to_json_param(row["survey_benchmarks"]), row["reference_comp"],
            row["reference_source"], to_json_param(row["reference_basis_detail"]), to_json_param(row["trace"]),
            ENGINE_VERSION,
        ),
    )


def rebuild_archetype_comp(cur) -> dict:
    period_end = _current_period_end()
    archetypes = _active_archetypes(cur)
    buckets = _distinct_buckets(cur)
    computed = 0
    for archetype in archetypes:
        for market_id, currency, component, pay_period in buckets:
            row = derive_archetype_comp(cur, archetype["id"], market_id, currency, component, pay_period)
            if row:
                _persist_archetype_comp(cur, row, PERIOD_START, period_end)
                computed += 1
    # Any row not from this rebuild's period_end is stale (an archetype/
    # bucket combination that no longer has qualifying evidence, a
    # deprecated archetype, or simply yesterday's rolling snapshot).
    cur.execute("DELETE FROM jobber.d_archetype_comp WHERE period_end != %s", (period_end,))
    return {"computed": computed, "removed_stale": cur.rowcount, "engine_version": ENGINE_VERSION}


# --- d_gap_value (prompt §11) -----------------------------------------------

def derive_gap_value_for_bucket(cur, market_id: str, currency: str) -> list[dict]:
    """The counterfactual: for every capability that blocks or only-partially
    covers at least one role, what would closing it unlock/improve, and
    what does that look like against the best currently-reachable
    benchmark in this (market, currency) context. Uses only the current
    structural model (`capability_engine.derive_role_fit`), never
    embeddings, never Phase 5 learning-difficulty judgment."""
    cur.execute("SELECT id, archetype_concept_id FROM jobber.role_instance")
    all_roles = [(str(r["id"]), str(r["archetype_concept_id"]) if r["archetype_concept_id"] else None) for r in cur.fetchall()]

    role_fits = {role_id: derive_role_fit(cur, role_id) for role_id, _archetype_id in all_roles}

    reachable_archetypes = {
        archetype_id
        for role_id, archetype_id in all_roles
        if archetype_id and not role_fits[role_id]["blocking_gaps"]
    }

    cur.execute(
        "SELECT archetype_concept_id, reference_comp, n_observations FROM jobber.d_archetype_comp "
        "WHERE market_id = %s AND currency = %s AND component = %s AND pay_period = %s",
        (market_id, currency, _GAP_VALUE_COMPONENT, _GAP_VALUE_PAY_PERIOD),
    )
    archetype_comp: dict[str, dict] = {
        str(r["archetype_concept_id"]): {
            "reference_comp": float(r["reference_comp"]) if r["reference_comp"] is not None else None,
            "n_observations": r["n_observations"],
        }
        for r in cur.fetchall()
    }

    best_current_reachable = None
    for aid in reachable_archetypes:
        comp = archetype_comp.get(aid)
        if comp and comp["reference_comp"] is not None:
            best_current_reachable = comp["reference_comp"] if best_current_reachable is None else max(best_current_reachable, comp["reference_comp"])

    # Candidate capabilities: anything that is a *required* gap (blocking or
    # unverified) for at least one role — a capability nobody requires can
    # neither unlock nor improve anything.
    candidate_capabilities: dict[str, str] = {}
    for role_id, _archetype_id in all_roles:
        fit = role_fits[role_id]
        for gap in fit["blocking_gaps"] + fit["unverified_required"]:
            if gap["type_code"] == "capability":
                candidate_capabilities[gap["id"]] = gap["canonical_name"]

    rows = []
    for cap_id, cap_name in candidate_capabilities.items():
        roles_unlocked = roles_improved = 0
        unlocked_archetypes: set[str] = set()
        improved_archetypes: set[str] = set()
        trace_roles = []

        for role_id, archetype_id in all_roles:
            fit = role_fits[role_id]
            blocking_ids = {g["id"] for g in fit["blocking_gaps"]}
            unverified_ids = {g["id"] for g in fit["unverified_required"]}

            if cap_id in blocking_ids:
                if blocking_ids == {cap_id}:
                    roles_unlocked += 1
                    if archetype_id:
                        unlocked_archetypes.add(archetype_id)
                    trace_roles.append({"role_instance_id": role_id, "effect": "unlocked"})
                else:
                    roles_improved += 1
                    if archetype_id:
                        improved_archetypes.add(archetype_id)
                    trace_roles.append({"role_instance_id": role_id, "effect": "improved"})
            elif cap_id in unverified_ids:
                roles_improved += 1
                if archetype_id:
                    improved_archetypes.add(archetype_id)
                trace_roles.append({"role_instance_id": role_id, "effect": "improved"})

        # An archetype with at least one unlocked role is reported as
        # unlocked, never double-counted as also improved.
        improved_archetypes -= unlocked_archetypes

        reference_comp_unlocked = None
        for aid in unlocked_archetypes:
            comp = archetype_comp.get(aid)
            if comp and comp["reference_comp"] is not None:
                reference_comp_unlocked = comp["reference_comp"] if reference_comp_unlocked is None else max(reference_comp_unlocked, comp["reference_comp"])

        comp_delta = None
        if reference_comp_unlocked is not None and best_current_reachable is not None:
            comp_delta = reference_comp_unlocked - best_current_reachable

        n_comp_observations = sum(
            (archetype_comp.get(aid) or {}).get("n_observations") or 0 for aid in (unlocked_archetypes | improved_archetypes)
        )

        if reference_comp_unlocked is None:
            evidence_quality = "insufficient"
        elif n_comp_observations >= 25:
            evidence_quality = "good"
        elif n_comp_observations >= 10:
            evidence_quality = "moderate"
        else:
            evidence_quality = "thin"

        rows.append(
            {
                "capability_concept_id": cap_id,
                "market_id": market_id,
                "currency": currency,
                "archetypes_unlocked": len(unlocked_archetypes),
                "archetypes_improved": len(improved_archetypes),
                "roles_unlocked": roles_unlocked,
                "roles_improved": roles_improved,
                "reference_comp_unlocked": reference_comp_unlocked,
                "comp_delta_vs_best_current_reachable": comp_delta,
                "n_comp_observations": n_comp_observations,
                "evidence_quality": evidence_quality,
                "trace": {
                    "capability_name": cap_name,
                    "best_current_reachable": best_current_reachable,
                    "unlocked_archetype_ids": sorted(unlocked_archetypes),
                    "improved_archetype_ids": sorted(improved_archetypes),
                    "component": _GAP_VALUE_COMPONENT,
                    "pay_period": _GAP_VALUE_PAY_PERIOD,
                    "roles": trace_roles,
                },
            }
        )

    def _rank_key(row):
        delta = row["comp_delta_vs_best_current_reachable"]
        return (0 if delta is not None else 1, -(delta or 0.0), -row["archetypes_unlocked"], -row["roles_unlocked"], row["capability_concept_id"])

    rows.sort(key=_rank_key)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _persist_gap_value(cur, row: dict, period_start: date, period_end: date) -> None:
    cur.execute(
        """
        INSERT INTO jobber.d_gap_value (
            capability_concept_id, market_id, period_start, period_end, currency,
            archetypes_unlocked, archetypes_improved, roles_unlocked, roles_improved,
            reference_comp_unlocked, comp_delta_vs_best_current_reachable, n_comp_observations,
            evidence_quality, rank, trace, engine_version, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (capability_concept_id, market_id, period_start, period_end, currency) DO UPDATE SET
            archetypes_unlocked = EXCLUDED.archetypes_unlocked, archetypes_improved = EXCLUDED.archetypes_improved,
            roles_unlocked = EXCLUDED.roles_unlocked, roles_improved = EXCLUDED.roles_improved,
            reference_comp_unlocked = EXCLUDED.reference_comp_unlocked,
            comp_delta_vs_best_current_reachable = EXCLUDED.comp_delta_vs_best_current_reachable,
            n_comp_observations = EXCLUDED.n_comp_observations, evidence_quality = EXCLUDED.evidence_quality,
            rank = EXCLUDED.rank, trace = EXCLUDED.trace, engine_version = EXCLUDED.engine_version, computed_at = now()
        """,
        (
            row["capability_concept_id"], row["market_id"], period_start, period_end, row["currency"],
            row["archetypes_unlocked"], row["archetypes_improved"], row["roles_unlocked"], row["roles_improved"],
            row["reference_comp_unlocked"], row["comp_delta_vs_best_current_reachable"], row["n_comp_observations"],
            row["evidence_quality"], row["rank"], to_json_param(row["trace"]), ENGINE_VERSION,
        ),
    )


def rebuild_gap_value(cur, market_id: str, currency: str) -> dict:
    period_end = _current_period_end()
    rows = derive_gap_value_for_bucket(cur, market_id, currency)
    for row in rows:
        _persist_gap_value(cur, row, PERIOD_START, period_end)

    cur.execute("DELETE FROM jobber.d_gap_value WHERE market_id = %s AND currency = %s AND period_end != %s", (market_id, currency, period_end))
    removed = cur.rowcount
    kept_ids = [r["capability_concept_id"] for r in rows]
    if kept_ids:
        cur.execute(
            "DELETE FROM jobber.d_gap_value WHERE market_id = %s AND currency = %s AND period_end = %s "
            "AND capability_concept_id != ALL(%s::uuid[])",
            (market_id, currency, period_end, kept_ids),
        )
    else:
        cur.execute("DELETE FROM jobber.d_gap_value WHERE market_id = %s AND currency = %s AND period_end = %s", (market_id, currency, period_end))
    removed += cur.rowcount
    return {"computed": len(rows), "removed_stale": removed, "engine_version": ENGINE_VERSION, "market_id": market_id, "currency": currency}


def discover_gap_value_buckets(cur) -> list[tuple[str, str]]:
    """(market_id, currency) pairs with at least one accepted compensation
    observation (any component/pay_period) — the contexts worth computing
    gap value for. A market/currency with zero compensation evidence has no
    gap-value rows yet; that is expected, not an error."""
    cur.execute("SELECT DISTINCT market_id, currency FROM jobber.compensation_observation WHERE review_status = 'accepted' AND market_id IS NOT NULL")
    return [(str(r["market_id"]), r["currency"]) for r in cur.fetchall()]


def rebuild_all_gap_value(cur) -> dict:
    buckets = discover_gap_value_buckets(cur)
    total_computed = total_removed = 0
    for market_id, currency in buckets:
        result = rebuild_gap_value(cur, market_id, currency)
        total_computed += result["computed"]
        total_removed += result["removed_stale"]
    return {"computed": total_computed, "removed_stale": total_removed, "engine_version": ENGINE_VERSION, "buckets": len(buckets)}


def rebuild_phase4_derivations(cur) -> dict:
    """Safe to repeat — see module docstring. Order matters: archetype
    comp must be rebuilt before gap value, since gap value reads
    d_archetype_comp rather than recomputing it inline (unlike role fit,
    which capability_engine.py always recomputes fresh)."""
    return {
        "engine_version": ENGINE_VERSION,
        "archetype_demand": rebuild_archetype_demand(cur),
        "archetype_comp": rebuild_archetype_comp(cur),
        "gap_value": rebuild_all_gap_value(cur),
    }


# --- Phase 4 readiness indicator (prompt §12) -------------------------------

def readiness_summary(cur) -> dict:
    """Informational only — never a reason to disable the Economics page,
    and never a fabricated evaluation result. `capability_agreement` reuses
    the exact same live computation the existing Evaluation page already
    shows (`evaluation.capability_agreement`), so this can never silently
    diverge from that number."""
    from . import evaluation  # local import: avoids a module-load cycle with capability_engine at import time

    cur.execute(
        "SELECT COUNT(*) AS n FROM jobber.concept WHERE type_code = 'role_archetype' AND status = 'active'"
    )
    total_active_archetypes = cur.fetchone()["n"]

    cur.execute(
        "SELECT COUNT(DISTINCT ri.archetype_concept_id) AS n FROM jobber.role_instance ri "
        "JOIN jobber.concept c ON c.id = ri.archetype_concept_id "
        "WHERE c.type_code = 'role_archetype' AND c.status = 'active'"
    )
    archetypes_with_assigned_roles = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE review_status = 'accepted'")
    accepted_compensation_observations = cur.fetchone()["n"]

    cur.execute(
        "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE review_status = 'accepted' AND basis = 'posting_stated'"
    )
    accepted_posting_stated = cur.fetchone()["n"]

    cur.execute(
        "SELECT COUNT(*) AS n FROM jobber.compensation_observation WHERE review_status = 'accepted' AND basis = 'survey'"
    )
    accepted_survey = cur.fetchone()["n"]

    capability_agreement = evaluation.capability_agreement(cur)

    return {
        "capability_agreement": capability_agreement,
        "total_active_archetypes": total_active_archetypes,
        "archetypes_with_assigned_roles": archetypes_with_assigned_roles,
        "accepted_compensation_observations": accepted_compensation_observations,
        "accepted_posting_stated_observations": accepted_posting_stated,
        "accepted_survey_observations": accepted_survey,
        "compensation_sample_sufficiency": (
            "sufficient" if (accepted_posting_stated >= _POSTING_SAMPLE_MIN or accepted_survey >= 1) else "insufficient"
        ),
        "engine_version": ENGINE_VERSION,
    }
