"""Pathways: the composition engine behind "where could I go from here,
what would it pay, and what would it take" (build §7/§8/§9).

This module computes nothing new. It *composes* systems that already exist,
each of which keeps its own semantics and its own basis:

    profile360 earnings state        personal_earnings.py
  + canonical capability evidence    capability_engine.py via stepping_stones
  + target requirement mapping       target_mapping.py
  + structural stepping stones       stepping_stones.assess_all_candidates
  + reviewed archetype assignment    role_instance.archetype_concept_id
  + archetype compensation benchmark jobber.d_archetype_comp
  + capability gap value             jobber.d_gap_value

There is no AI call anywhere in this module, no embedding of compensation,
and no new weighted score. Every ranking below is by a transparent, stated
input (how many of the target's outstanding gaps a route addresses, then how
much evidence supports it, then compensation where it exists) and every
number carries the basis it came from.

## Route depth: exactly two shapes, on purpose (build §7)

    You -> Target
    You -> Intermediate archetype -> Target

That is the whole search space in this version. There is no general N-hop
optimiser, no chained archetype sequence and no "best path" search. A
two-hop route would multiply the uncertainty in every hop's structural
evidence without adding any new evidence, and this corpus does not support
that. The limitation is stated in the returned `method` block so it is
visible in the product, not only in this docstring.

## Intermediate routes are grouped, not invented

An intermediate step is a *reviewed archetype* that at least one observed
posting — judged a genuine structural stepping stone by the existing
engine — is assigned to. The supporting postings are preserved underneath
each archetype and returned with it, so the claim is always inspectable back
to the individual adverts that produced it. A posting with no reviewed
archetype cannot form an intermediate node: there is nothing to say about
its compensation or its pattern beyond that single advert, and inventing a
grouping for it would be exactly the unreviewed classification build §5
forbids.

## No false confidence (build §7)

Five conditions each produce an explicit state rather than a confident
number: incomplete target requirement review, incomplete target mapping,
incomplete candidate review, insufficient compensation evidence, and a
missing archetype assignment. They are reported in `gates` (booleans a
caller can branch on) and in each node's own `state`/`state_reason`.

## `d_gap_value` stays market-level (build §8)

Nothing here writes to `d_gap_value`, and no personal figure is ever stored
in it. The personal overlay is computed at request time, in
`_gap_value_overlay`, by comparing the market-level unlocked reference
against the user's own baseline through the same compatibility rules Role
Detail uses.
"""

import logging
from time import perf_counter

from fastapi.encoders import jsonable_encoder

from . import compensation_resolver as resolver
from .db import to_json_param
from .embeddings import ensure_profile_embedding, get_embedding
from .personal_earnings import personal_compensation_fingerprint, safe_personal_earnings_state
from .stepping_stones import assess_all_candidates
from .target_cache import pathways_revision, revisions

logger = logging.getLogger(__name__)

METHOD = {
    "route_depth": "direct, plus one intermediate archetype",
    "route_depth_limitation": (
        "This version supports exactly two route shapes: you to the target directly, and you to the target "
        "via one intermediate archetype. Multi-step chains are not searched — each additional hop would "
        "compound structural uncertainty without adding evidence."
    ),
    "intermediate_basis": (
        "An intermediate archetype is a reviewed archetype that at least one observed posting assessed as a "
        "structural stepping stone belongs to. The supporting postings are listed under it and can be opened."
    ),
    "ranking": (
        "Routes are ordered by how many of the target's outstanding required gaps they address, then by the "
        "supporting evidence behind them, then by compensation where it exists. No composite score is computed."
    ),
    "compensation": (
        "Every figure carries its own basis: advert-stated, archetype market benchmark, legacy estimate, or "
        "insufficient evidence. Figures of different bases are never merged, and currencies are never converted."
    ),
    "not_a_prediction": (
        "A role that involves a capability is an opportunity to develop it, not proof you will acquire it. "
        "Historical postings describe role patterns, not confirmed vacancies. Nothing here estimates hiring "
        "probability, learning difficulty or time to transition."
    ),
}


class PathwaysTargetError(ValueError):
    """No such role to build pathways for — a 404 at the route layer."""


# --- Market/currency context ------------------------------------------------

def resolve_market_context(cur, target_id: str, market_id: str | None, currency: str | None) -> dict:
    """Decide which market/currency the economics of this request are
    expressed in.

    An explicit selection always wins. Otherwise: the target's own archetype
    benchmark context if it has one, else the (market, currency) pair with
    the most accepted compensation evidence overall, else nothing — in which
    case every compensation answer below is honestly 'insufficient evidence'
    rather than being forced into an arbitrary market."""
    if market_id and currency:
        cur.execute("SELECT id, label, code FROM jobber.market WHERE id = %s", (market_id,))
        market = cur.fetchone()
        if market:
            return {
                "market_id": str(market["id"]), "market_label": market["label"],
                "market_code": market["code"], "currency": currency.strip().upper(),
                "selected_by": "explicit",
            }

    cur.execute("SELECT archetype_concept_id FROM jobber.role_instance WHERE id = %s", (target_id,))
    row = cur.fetchone()
    archetype_id = str(row["archetype_concept_id"]) if row and row["archetype_concept_id"] else None
    if archetype_id:
        cur.execute(
            "SELECT ac.market_id, ac.currency, m.label, m.code FROM jobber.d_archetype_comp ac "
            "JOIN jobber.market m ON m.id = ac.market_id "
            "WHERE ac.archetype_concept_id = %s AND ac.component = 'base' AND ac.pay_period = 'annual' "
            "ORDER BY (ac.reference_comp IS NULL), ac.n_observations DESC, ac.period_end DESC, ac.currency "
            "LIMIT 1",
            (archetype_id,),
        )
        best = cur.fetchone()
        if best:
            return {
                "market_id": str(best["market_id"]), "market_label": best["label"],
                "market_code": best["code"], "currency": best["currency"],
                "selected_by": "target_archetype_benchmark",
            }

    cur.execute(
        "SELECT co.market_id, co.currency, m.label, m.code, COUNT(*) AS n "
        "FROM jobber.compensation_observation co JOIN jobber.market m ON m.id = co.market_id "
        "WHERE co.review_status = 'accepted' AND co.market_id IS NOT NULL "
        "GROUP BY co.market_id, co.currency, m.label, m.code "
        "ORDER BY n DESC, m.label, co.currency LIMIT 1"
    )
    fallback = cur.fetchone()
    if fallback:
        return {
            "market_id": str(fallback["market_id"]), "market_label": fallback["label"],
            "market_code": fallback["code"], "currency": fallback["currency"],
            "selected_by": "most_evidenced_context",
        }
    return {
        "market_id": None, "market_label": None, "market_code": None, "currency": None,
        "selected_by": "none_available",
    }


def list_market_contexts(cur) -> list[dict]:
    """(market, currency) pairs that actually have accepted compensation
    evidence — the only meaningful choices for the Pathways selector. Empty
    is an expected state, not an error."""
    cur.execute(
        "SELECT co.market_id, m.label, m.code, co.currency, COUNT(*) AS n "
        "FROM jobber.compensation_observation co JOIN jobber.market m ON m.id = co.market_id "
        "WHERE co.review_status = 'accepted' AND co.market_id IS NOT NULL "
        "GROUP BY co.market_id, m.label, m.code, co.currency ORDER BY m.label, co.currency"
    )
    return [
        {
            "market_id": str(r["market_id"]), "market_label": r["label"], "market_code": r["code"],
            "currency": r["currency"], "accepted_observations": r["n"],
        }
        for r in cur.fetchall()
    ]


# --- Development actions (build §9: human planning fields, never evidence) ---

def _development_actions(cur, role_ids: list[str]) -> dict[str, dict]:
    """Aggregated per role in one query. These are the user's own planning
    records: what they said they would do, when they said they would start,
    and how much effort they estimated. Nothing here is derived, and nothing
    here is evidence — a development action has no path into
    requirement_claim, person_capability_assertion or profile360, and
    completing one does not change any capability status."""
    if not role_ids:
        return {}
    cur.execute(
        "SELECT role_instance_id, status, COUNT(*) AS n, "
        "       MIN(planned_start_date) AS earliest_planned_start, "
        "       SUM(estimated_effort_hours) AS estimated_effort_hours, "
        "       COUNT(estimated_effort_hours) AS n_with_effort "
        "FROM jobber.development_action WHERE role_instance_id = ANY(%s::uuid[]) "
        "GROUP BY role_instance_id, status",
        (role_ids,),
    )
    grouped: dict[str, dict] = {}
    for row in cur.fetchall():
        key = str(row["role_instance_id"])
        bucket = grouped.setdefault(
            key,
            {"open": 0, "done": 0, "earliest_planned_start": None,
             "estimated_effort_hours": None, "actions_with_estimated_effort": 0},
        )
        bucket[row["status"]] = row["n"]
        if row["earliest_planned_start"] and (
            bucket["earliest_planned_start"] is None or row["earliest_planned_start"] < bucket["earliest_planned_start"]
        ):
            bucket["earliest_planned_start"] = row["earliest_planned_start"]
        if row["estimated_effort_hours"] is not None:
            bucket["estimated_effort_hours"] = (bucket["estimated_effort_hours"] or 0) + float(row["estimated_effort_hours"])
        bucket["actions_with_estimated_effort"] += row["n_with_effort"]
    return grouped


_TRANSITION_UNKNOWNS = [
    "No learning duration is estimated — this system has no evidence of how long a capability takes to acquire.",
    "No hiring or success probability is estimated.",
    "No universal learning-difficulty rating is applied to any capability.",
]


def _transition(assessment: dict, actions: dict | None) -> dict:
    """Conservative transition cost (build §9): only what the system
    actually knows — structural gaps, evidence coverage, which of the
    target's gaps this route addresses, and the user's own planning entries.
    Every absent quantity is named explicitly rather than filled in."""
    return {
        "blocking_required_gaps": len(assessment.get("missing_required") or []),
        "blocking_required_gap_names": assessment.get("missing_required") or [],
        "unverified_required_gaps": len(assessment.get("unverified_required") or []),
        "unverified_required_gap_names": assessment.get("unverified_required") or [],
        "evidence_coverage": assessment.get("evidence_coverage"),
        "evidenced_requirements": assessment.get("evidenced_requirements"),
        "requirements_total": assessment.get("requirements_total"),
        "target_gaps_addressed": assessment.get("target_gaps_addressed") or [],
        "development_actions": actions or {
            "open": 0, "done": 0, "earliest_planned_start": None,
            "estimated_effort_hours": None, "actions_with_estimated_effort": 0,
        },
        "not_estimated": _TRANSITION_UNKNOWNS,
    }


# --- Direct route -----------------------------------------------------------

def _direct_route(cur, target, target_requirements, statuses, target_pending, mapping, compensation,
                  earnings_state, actions) -> dict:
    """You -> target, assessed with exactly the same evidence statuses every
    other node uses (so the direct route and an intermediate route can never
    disagree about whether a capability is evidenced)."""
    unique = {}
    for row in target_requirements:
        key = row["concept_id"]
        if key not in unique or row["requirement_type"] == "required":
            unique[key] = row
    requirements = list(unique.values())
    required = [r for r in requirements if r["requirement_type"] == "required"]
    missing = [r["canonical_name"] for r in required if statuses.get(r["concept_id"]) == "not_found"]
    unverified = [
        r["canonical_name"] for r in required
        if statuses.get(r["concept_id"]) in ("partial", "user_asserted")
    ]
    evidenced = sum(statuses.get(r["concept_id"]) == "evidenced" for r in requirements)
    coverage = evidenced / len(requirements) if requirements else None

    if not requirements:
        state, reason = "insufficient_evidence", (
            "This target has no reviewed requirements, so structural fit cannot be assessed."
        )
    elif target_pending:
        state, reason = "review_incomplete", (
            f"{target_pending} requirement claim(s) on this target are still unreviewed. "
            "Complete the review before reading this as your fit."
        )
    elif not mapping["complete"]:
        state, reason = "mapping_incomplete", (
            "Some of this target's requirements are unmapped or excluded from the vocabulary. "
            "Resolve the mapping before interpreting readiness."
        )
    elif not required:
        state, reason = "no_required_requirements", (
            "No requirement on this target is marked as required, so there are no blocking gaps to assess."
        )
    elif missing:
        state, reason = "blocking_gaps", (
            f"{len(missing)} required capability/capabilities have no supporting evidence."
        )
    elif unverified:
        state, reason = "unverified_gaps", (
            f"{len(unverified)} required capability/capabilities have only partial or self-asserted evidence."
        )
    else:
        state, reason = "structurally_evidenced", (
            "Every required capability on this target has supporting evidence. That is structural fit, "
            "not a prediction about hiring."
        )

    assessment = {
        "missing_required": missing, "unverified_required": unverified,
        "evidence_coverage": coverage, "evidenced_requirements": evidenced,
        "requirements_total": len(requirements), "target_gaps_addressed": [],
    }
    return {
        "kind": "direct",
        "role_instance_id": str(target["id"]),
        "title": target["title"],
        "organisation": target["organisation"],
        "archetype": (
            {"id": str(target["archetype_concept_id"]), "name": target["archetype_name"]}
            if target["archetype_concept_id"] else None
        ),
        "state": state,
        "state_reason": reason,
        "fit": {
            "evidenced_requirements": evidenced,
            "requirements_total": len(requirements),
            "evidence_coverage": coverage,
            "blocking_required_gaps": missing,
            "unverified_required_gaps": unverified,
            "review_complete": target_pending == 0,
            "unreviewed_requirement_claims": target_pending,
            "mapping_complete": mapping["complete"],
            "unmapped_requirements": mapping["unresolved"],
        },
        "compensation": compensation,
        "personal_comparison": resolver.compare_to_personal_earnings(compensation, earnings_state),
        "transition": _transition(assessment, actions.get(str(target["id"]))),
    }


# --- Intermediate archetypes ------------------------------------------------

def _group_by_archetype(cur, ranked: list[dict], context: dict, earnings_state: dict, actions: dict) -> list[dict]:
    """Group the postings the structural engine judged genuine stepping
    stones by their *reviewed* archetype, preserving the supporting postings
    under each one.

    An archetype's aggregate figures are always accompanied by the postings
    that produced them: `supporting_postings` is the audit trail for every
    claim on the node. Postings whose archetype is unassigned are not
    silently dropped — they are reported separately by the caller as
    `unclassified_supporting_postings`, which is what makes "assign an
    archetype to unlock this" visible rather than a mystery."""
    steps = [c for c in ranked if c["assessment"] == "potential_step"]
    grouped: dict[str, list[dict]] = {}
    for candidate in steps:
        if candidate["archetype_concept_id"]:
            grouped.setdefault(candidate["archetype_concept_id"], []).append(candidate)
    if not grouped:
        return []

    archetype_ids = sorted(grouped)
    cur.execute(
        "SELECT c.id, c.canonical_name, c.status, rad.seniority_band, rad.typical_market "
        "FROM jobber.concept c LEFT JOIN jobber.role_archetype_detail rad ON rad.concept_id = c.id "
        "WHERE c.id = ANY(%s::uuid[])",
        (archetype_ids,),
    )
    archetypes = {str(r["id"]): dict(r) for r in cur.fetchall()}

    cur.execute(
        "SELECT archetype_concept_id FROM jobber.archetype_context_enrichment "
        "WHERE status = 'active' AND archetype_concept_id = ANY(%s::uuid[])",
        (archetype_ids,),
    )
    with_context = {str(r["archetype_concept_id"]) for r in cur.fetchall()}

    benchmarks = resolver.load_archetype_benchmarks(
        cur, archetype_ids, market_id=context["market_id"], currency=context["currency"]
    )

    nodes = []
    for archetype_id, postings in grouped.items():
        archetype = archetypes.get(archetype_id)
        if archetype is None or archetype["status"] != "active":
            # A posting assigned to a deprecated archetype keeps its own
            # assessment but cannot stand as a recommended route.
            continue

        target_gaps_addressed = sorted({g for p in postings for g in p["target_gaps_addressed"]})
        blocking = sorted({g for p in postings for g in p["missing_required"]})
        unverified = sorted({g for p in postings for g in p["unverified_required"]})
        # The most-evidenced supporting posting is the one that makes this
        # archetype reachable; reporting the group's *best* coverage rather
        # than its mean avoids letting one thin advert bury a strong one.
        coverages = [p["evidence_coverage"] for p in postings if p["evidence_coverage"] is not None]
        best_coverage = max(coverages) if coverages else None

        benchmark = benchmarks.get(archetype_id)
        compensation = (
            resolver.from_archetype_benchmark(benchmark)
            if benchmark is not None and benchmark["reference_comp"] is not None
            else resolver.insufficient_compensation(
                "No accepted compensation evidence qualifies as a benchmark for this archetype in the "
                "selected market yet.",
                {"tier": 4, "archetype_concept_id": archetype_id, "has_archetype_comp_row": benchmark is not None},
            )
        )

        if not target_gaps_addressed:
            state, reason = "no_target_progress", (
                "The postings in this archetype do not involve any of the target's outstanding requirements."
            )
        elif compensation["basis"] == resolver.BASIS_INSUFFICIENT:
            state, reason = "route_without_compensation", (
                f"This archetype involves {len(target_gaps_addressed)} of the target's outstanding "
                "requirement(s), but has no compensation benchmark in this market yet."
            )
        else:
            state, reason = "useful_intermediate", (
                f"This archetype involves {len(target_gaps_addressed)} of the target's outstanding "
                f"requirement(s), evidenced across {len(postings)} supporting posting(s)."
            )

        aggregate = {
            "missing_required": blocking, "unverified_required": unverified,
            "evidence_coverage": best_coverage,
            "evidenced_requirements": max((p["evidenced_requirements"] for p in postings), default=0),
            "requirements_total": max((p["requirements_total"] for p in postings), default=0),
            "target_gaps_addressed": target_gaps_addressed,
        }
        nodes.append(
            {
                "kind": "intermediate_archetype",
                "archetype_concept_id": archetype_id,
                "archetype_name": archetype["canonical_name"],
                "seniority_band": archetype["seniority_band"],
                "typical_market": archetype["typical_market"],
                "state": state,
                "state_reason": reason,
                "supporting_posting_ids": [p["id"] for p in postings],
                "supporting_posting_count": len(postings),
                "supporting_postings": [
                    {
                        "id": p["id"], "title": p["title"], "organisation": p["organisation"],
                        "posting_date": p["posting_date"], "career_track": p["career_track"],
                        "assessment": p["assessment"], "explanation": p["explanation"],
                        "evidenced_requirements": p["evidenced_requirements"],
                        "requirements_total": p["requirements_total"],
                        "evidence_coverage": p["evidence_coverage"],
                        "target_gaps_addressed": p["target_gaps_addressed"],
                        "missing_required": p["missing_required"],
                        "unverified_required": p["unverified_required"],
                        "pending_requirements": p["pending_requirements"],
                        "similarity_to_target": p["similarity_to_target"],
                        "similarity_to_profile": p["similarity_to_profile"],
                    }
                    for p in postings
                ],
                "fit": {
                    "best_evidence_coverage": best_coverage,
                    "blocking_required_gaps": blocking,
                    "unverified_required_gaps": unverified,
                    "unreviewed_requirement_claims": sum(p["pending_requirements"] for p in postings),
                    "review_complete": all(p["pending_requirements"] == 0 for p in postings),
                },
                "target_gaps_addressed": target_gaps_addressed,
                "compensation": compensation,
                "personal_comparison": resolver.compare_to_personal_earnings(compensation, earnings_state),
                "transition": _transition(
                    aggregate,
                    _merge_actions([actions.get(p["id"]) for p in postings]),
                ),
                "day_in_the_life_available": archetype_id in with_context,
                "evidence_quality": compensation["evidence_quality"],
            }
        )

    nodes.sort(
        key=lambda n: (
            -len(n["target_gaps_addressed"]),
            -n["supporting_posting_count"],
            -(n["fit"]["best_evidence_coverage"] or 0),
            -(n["compensation"]["amount_reference"] or 0),
            n["archetype_name"],
        )
    )
    return nodes


def _merge_actions(buckets: list[dict | None]) -> dict:
    merged = {
        "open": 0, "done": 0, "earliest_planned_start": None,
        "estimated_effort_hours": None, "actions_with_estimated_effort": 0,
    }
    for bucket in buckets:
        if not bucket:
            continue
        merged["open"] += bucket["open"]
        merged["done"] += bucket["done"]
        merged["actions_with_estimated_effort"] += bucket["actions_with_estimated_effort"]
        if bucket["estimated_effort_hours"] is not None:
            merged["estimated_effort_hours"] = (merged["estimated_effort_hours"] or 0) + bucket["estimated_effort_hours"]
        start = bucket["earliest_planned_start"]
        if start and (merged["earliest_planned_start"] is None or start < merged["earliest_planned_start"]):
            merged["earliest_planned_start"] = start
    return merged


# --- Gap value overlay (build §8) -------------------------------------------

def _gap_value_overlay(cur, target_requirements, statuses, context, earnings_state, intermediate_nodes) -> list[dict]:
    """For each of the target's outstanding required capabilities, the two
    distinct kinds of value the build asks to be kept apart:

    - **target value** — its relevance to the selected target: that it is
      required, and that closing it removes one of the target's own gaps.
    - **market option value** — what else it opens up, read from the
      market-level `d_gap_value` row for the selected market/currency:
      archetypes and roles unlocked or improved, and the highest qualifying
      reference compensation among them.

    The personal overlay is computed here, at request time, and never
    written back into `d_gap_value` — that table stays market-level, with no
    personal figure in it, exactly as build §8 requires."""
    outstanding = [
        r for r in target_requirements
        if r["requirement_type"] == "required" and statuses.get(r["concept_id"]) != "evidenced"
        and r["type_code"] == "capability"
    ]
    if not outstanding:
        return []

    concept_ids = sorted({r["concept_id"] for r in outstanding})
    gap_rows: dict[str, dict] = {}
    if context["market_id"] and context["currency"]:
        cur.execute(
            "SELECT gv.capability_concept_id, gv.archetypes_unlocked, gv.archetypes_improved, "
            "       gv.roles_unlocked, gv.roles_improved, gv.reference_comp_unlocked, "
            "       gv.comp_delta_vs_best_current_reachable, gv.n_comp_observations, gv.evidence_quality, "
            "       gv.rank, gv.currency, gv.trace, gv.period_end "
            "FROM jobber.d_gap_value gv "
            "WHERE gv.market_id = %s AND gv.currency = %s AND gv.capability_concept_id = ANY(%s::uuid[])",
            (context["market_id"], context["currency"], concept_ids),
        )
        gap_rows = {str(r["capability_concept_id"]): dict(r) for r in cur.fetchall()}

    addressed_by = {}
    for node in intermediate_nodes:
        for name in node["target_gaps_addressed"]:
            addressed_by.setdefault(name, []).append(
                {"archetype_concept_id": node["archetype_concept_id"], "archetype_name": node["archetype_name"]}
            )

    overlay = []
    for requirement in outstanding:
        concept_id = requirement["concept_id"]
        row = gap_rows.get(concept_id)
        status = statuses.get(concept_id)

        if row is None:
            market_option_value = {
                "available": False,
                "reason": (
                    "No market-level gap value has been computed for this capability in the selected market. "
                    "Rebuild economics, or accept compensation evidence for the archetypes that demand it."
                ),
            }
            personal_context = {"comparable": False, "reason": "There is no unlocked reference figure to compare against."}
            evidence_quality = "insufficient"
        else:
            market_option_value = {
                "available": True,
                "archetypes_unlocked": row["archetypes_unlocked"],
                "archetypes_improved": row["archetypes_improved"],
                "roles_unlocked": row["roles_unlocked"],
                "roles_improved": row["roles_improved"],
                "highest_qualifying_reference_compensation": (
                    float(row["reference_comp_unlocked"]) if row["reference_comp_unlocked"] is not None else None
                ),
                "currency": row["currency"],
                "delta_vs_best_currently_reachable": (
                    float(row["comp_delta_vs_best_current_reachable"])
                    if row["comp_delta_vs_best_current_reachable"] is not None else None
                ),
                "n_compensation_observations": row["n_comp_observations"],
                "rank": row["rank"],
                "as_of": row["period_end"],
                "scope_note": (
                    "Base annual pay only — the one canonical component gap value is computed on."
                ),
            }
            evidence_quality = row["evidence_quality"]
            personal_context = resolver.compare_to_personal_earnings(
                {
                    "basis": resolver.BASIS_MARKET_ESTIMATE,
                    "basis_label": resolver.BASIS_LABELS[resolver.BASIS_MARKET_ESTIMATE],
                    "currency": row["currency"],
                    "amount_min": None,
                    "amount_reference": (
                        float(row["reference_comp_unlocked"]) if row["reference_comp_unlocked"] is not None else None
                    ),
                    "amount_max": None,
                    "component": "base",
                    "pay_period": "annual",
                    "employment_basis": None,
                    "as_of": row["period_end"],
                },
                earnings_state,
            )

        overlay.append(
            {
                "concept_id": concept_id,
                "canonical_name": requirement["canonical_name"],
                "type_code": requirement["type_code"],
                "evidence_status": status,
                "target_relevance": {
                    "required_by_target": True,
                    "requirement_type": requirement["requirement_type"],
                    "addresses_target_gaps": 1,
                    "current_evidence_status": status,
                    "intermediate_archetypes_involving_it": addressed_by.get(requirement["canonical_name"], []),
                },
                "market_option_value": market_option_value,
                "your_context": personal_context,
                "evidence_quality": evidence_quality,
            }
        )

    # Ranked by transparent inputs only, in a stated order: capabilities that
    # block outright before ones that are merely unverified, then how much
    # the market opens up, then the money, then name. No weighted score.
    overlay.sort(
        key=lambda g: (
            0 if g["evidence_status"] == "not_found" else 1,
            -(g["market_option_value"].get("archetypes_unlocked") or 0),
            -(g["market_option_value"].get("highest_qualifying_reference_compensation") or 0),
            g["canonical_name"],
        )
    )
    return overlay


# --- Composition ------------------------------------------------------------

def _load_target(cur, target_id: str) -> dict:
    cur.execute(
        "SELECT ri.id, ri.title, ri.organisation, ri.instance_type, ri.target_basis, ri.country, "
        "       ri.archetype_concept_id, c.canonical_name AS archetype_name, c.status AS archetype_status "
        "FROM jobber.role_instance ri LEFT JOIN jobber.concept c ON c.id = ri.archetype_concept_id "
        "WHERE ri.id = %s",
        (target_id,),
    )
    row = cur.fetchone()
    if not row:
        raise PathwaysTargetError(f"role_instance {target_id!r} not found")
    return dict(row)


def pathways_for_target(cur, target_id: str, *, market_id: str | None = None, currency: str | None = None) -> dict:
    """The whole composition, cached on `jobber.d_pathways`.

    The warm path is a single indexed read plus the revision computation
    (which is itself a handful of aggregate queries) and makes no AI call —
    the cold path's cost is the structural assessment, which is already
    bulk-loaded by `stepping_stones.assess_all_candidates`."""
    started = perf_counter()
    target = _load_target(cur, target_id)
    context = resolve_market_context(cur, target_id, market_id, currency)
    context_key = f"{context['market_id'] or '-'}::{context['currency'] or '-'}"

    _, profile_vec = ensure_profile_embedding(cur)
    target_vec = get_embedding(cur, "role_instance", target_id)
    personal_fingerprint = personal_compensation_fingerprint(cur)
    revision = pathways_revision(cur, target_vec, profile_vec, context_key, personal_fingerprint)

    cur.execute(
        "SELECT result FROM jobber.d_pathways WHERE target_role_id = %s AND context_key = %s AND revision = %s",
        (target_id, context_key, revision),
    )
    cached = cur.fetchone()
    if cached:
        result = dict(cached["result"])
        # `selected_by` describes how *this request* arrived at its market
        # context, not the composition — an explicit selection and an
        # inferred one that land on the same market/currency share a cache
        # entry (the answer is identical) but must still report honestly how
        # the context was chosen, so it is re-applied rather than served
        # from whichever request happened to populate the cache.
        result["market_context"] = {**result["market_context"], "selected_by": context["selected_by"]}
        return _measured(result, started, cache_hit=True, concepts_evaluated=0)

    evidence_revision, _path_revision = revisions(cur, target_vec, profile_vec)
    assessed = assess_all_candidates(cur, target_id, target_vec, profile_vec, evidence_revision)
    earnings_state = safe_personal_earnings_state(cur)

    posting_ids = [c["id"] for c in assessed["ranked"]]
    actions = _development_actions(cur, [target_id, *posting_ids])
    target_compensation = resolver.resolve_role_compensation(
        cur, target_id, market_id=context["market_id"], currency=context["currency"]
    )

    direct = _direct_route(
        cur, target, assessed["target_requirements"], assessed["statuses"],
        assessed["target_pending_requirements"], assessed["target_mapping"],
        target_compensation, earnings_state, actions,
    )
    intermediates = _group_by_archetype(cur, assessed["ranked"], context, earnings_state, actions)
    unclassified = [
        {"id": c["id"], "title": c["title"], "organisation": c["organisation"],
         "target_gaps_addressed": c["target_gaps_addressed"]}
        for c in assessed["ranked"]
        if c["assessment"] == "potential_step" and not c["archetype_concept_id"]
    ]
    gap_value = _gap_value_overlay(
        cur, assessed["target_requirements"], assessed["statuses"], context, earnings_state, intermediates
    )

    gates = {
        "target_requirements_reviewed": assessed["target_pending_requirements"] == 0,
        "target_mapping_complete": assessed["target_mapping"]["complete"],
        "target_has_requirements": bool(assessed["target_requirements"]),
        "target_archetype_assigned": target["archetype_concept_id"] is not None,
        "compensation_context_available": context["market_id"] is not None,
        "target_compensation_available": target_compensation["basis"] != resolver.BASIS_INSUFFICIENT,
        "personal_earnings_available": earnings_state["status"] in ("current", "historical"),
        "candidate_review_complete": all(c["pending_requirements"] == 0 for c in assessed["ranked"]),
    }
    incomplete = [name for name, ok in gates.items() if not ok]

    result = jsonable_encoder(
        {
            "target": {
                "id": str(target["id"]),
                "title": target["title"],
                "organisation": target["organisation"],
                "instance_type": target["instance_type"],
                "archetype": (
                    {
                        "id": str(target["archetype_concept_id"]),
                        "name": target["archetype_name"],
                        "status": target["archetype_status"],
                    }
                    if target["archetype_concept_id"] else None
                ),
            },
            "market_context": context,
            "available_market_contexts": list_market_contexts(cur),
            "personal_earnings": earnings_state,
            "direct_route": direct,
            "intermediate_archetypes": intermediates,
            "unclassified_supporting_postings": unclassified,
            "gap_value": gap_value,
            "gates": gates,
            "incomplete": incomplete,
            "candidates_assessed": len(assessed["ranked"]),
            "distinct_concepts": assessed["distinct_concepts"],
            "method": METHOD,
        }
    )
    cur.execute(
        """
        INSERT INTO jobber.d_pathways (target_role_id, context_key, revision, result)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (target_role_id, context_key) DO UPDATE SET
            revision = EXCLUDED.revision, result = EXCLUDED.result, prepared_at = now()
        """,
        (target_id, context_key, revision, to_json_param(result)),
    )
    return _measured(result, started, cache_hit=False, concepts_evaluated=assessed["concepts_evaluated"])


def _measured(result: dict, started: float, *, cache_hit: bool, concepts_evaluated: int) -> dict:
    """Same metric vocabulary as the existing target-path metrics, so the
    two can be read side by side in logs."""
    result["metrics"] = {
        "cache_hit": cache_hit,
        "candidates": result.get("candidates_assessed", 0),
        "archetypes_assessed": len(result.get("intermediate_archetypes", [])),
        "distinct_concepts": result.get("distinct_concepts", 0),
        "concepts_evaluated": concepts_evaluated,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
    }
    logger.info("pathways_analysis %s", result["metrics"])
    return result
