"""Phase 7: `You -> Opportunity -> Target` opportunity alignment (docs/38).

Connects three things that already exist on their own — the selected Career
Direction (Phase 6), the canonical Target/stepping-stone machinery, and the
canonical person-vs-role Comparison — into one deterministic answer for one
observed posting: *how does this role relate to where I am now, and to the
Target my selected Career Direction points at?*

This module computes nothing new about a person's evidence or a role's
requirements. It composes:

    You -> Opportunity     comparison_service.build_role_comparison
    Opportunity -> Target  stepping_stones.assess_specific_candidate
    Direction constraints  jobber.career_direction.constraints (direct checks only)
    Economics              compensation_resolver + personal_earnings

Decision support, not a recommendation engine (build §objective): nothing
here says whether the user should apply, and there is no composite
opportunity score, fit percentage, hiring probability or ranking anywhere in
this module. `relationship` is a small, named, rule-based vocabulary for UI
framing (build §8) — never a number.

## Two ways to reach a Target

`build_opportunity_alignment` is the single entry point both consumers use
(build §2/§16/§17 — "do not scatter the same alignment logic"):

- **Direction-driven** (Role Detail, Applications): `target_id_override` is
  omitted. The Target comes from the selected Career Direction, and the top-
  level `state` machine (`no_selected_direction` / `direction_without_target`
  / `insufficient_target_evidence` / `target_available`, build §3) reflects
  Career Direction/Target state honestly rather than inventing one.
- **Explicit-target** (Pathways' `?opportunity_id=` overlay, build §17): a
  `target_id_override` is supplied — the Target the user is already looking
  at in Pathways, which need not be the selected Direction's Target at all.
  The `no_selected_direction`/`direction_without_target` states never apply
  here (a target is already known); only `insufficient_target_evidence` or
  `target_available` are reached. `direction`/`direction_constraints`/
  `direction_dimensions` are still populated when a Direction happens to be
  selected (context, never required), so Pathways and Role Detail share
  exactly one service and one endpoint rather than two independently
  maintained ones.

## Relationship precedence (build §8/§9)

`_classify_relationship` picks exactly one state, first match wins:

1. The stepping-stone engine could not reach a verdict (candidate review
   incomplete, target review incomplete, or target mapping incomplete) ->
   `relationship_unclear`. Legacy/review incompleteness always wins over a
   confident-looking label (build §23).
2. The user's accepted evidence already covers every mapped Target
   requirement -> `target_already_evidenced` (the stepping-stone question
   stops being meaningful).
3. The opportunity carries the *same reviewed archetype* as the Target, or
   as the selected Direction's own archetype anchor -> `same_destination_family`.
   Checked before the stepping-stone verdict on purpose (build §8's "use
   only when there is a reviewed structural anchor" is a stronger, more
   concrete fact than a gap-coverage judgment) but never suppresses the
   other sections of the response (build §9 — gaps/constraints/economics are
   always returned alongside it).
4. `potential_step` / `no_target_progress` / (`needs_evidence` or
   `not_an_intermediate_step` -> `not_more_reachable_than_target`), taken
   directly from `stepping_stones.assess_candidate`'s own state machine,
   which already encodes "structurally easier/more evidenced than the
   target" (see that module).

Semantic similarity (`embedding_similarity`/`similarity_to_target`/
`similarity_to_profile`) never participates in this precedence — it is
returned separately, under `semantic_similarity`, as secondary context only
(build §22).

## No AI, no writes, no new cache (build §1/§25/§26)

Every read here is a bounded composition over already-derived structures
(`jobber.d_target_evidence`, `jobber.d_archetype_comp`, profile360) for
exactly two roles (the opportunity and the target) — never a corpus scan
(see `stepping_stones.assess_specific_candidate`) and never another
`jobber.d_*` cache table. Nothing is written anywhere.
"""

from . import career_directions
from . import comparison_service
from . import compensation_resolver as resolver
from . import market_coverage
from .economics_freshness import economics_freshness
from .embeddings import ensure_profile_embedding, get_embedding
from .personal_earnings import safe_personal_earnings_state
from .stepping_stones import assess_specific_candidate, review_blockers, review_is_complete
from .target_cache import revisions as target_cache_revisions

STATE_NO_SELECTED_DIRECTION = "no_selected_direction"
STATE_DIRECTION_WITHOUT_TARGET = "direction_without_target"
STATE_INSUFFICIENT_TARGET_EVIDENCE = "insufficient_target_evidence"
STATE_TARGET_AVAILABLE = "target_available"

REL_SAME_DESTINATION_FAMILY = "same_destination_family"
REL_POTENTIAL_STEP = "potential_step"
REL_NO_IDENTIFIED_TARGET_PROGRESS = "no_identified_target_progress"
REL_NOT_MORE_REACHABLE_THAN_TARGET = "not_more_reachable_than_target"
REL_RELATIONSHIP_UNCLEAR = "relationship_unclear"
REL_TARGET_ALREADY_EVIDENCED = "target_already_evidenced"

_RELATIONSHIP_LABELS = {
    REL_SAME_DESTINATION_FAMILY: "Same destination family",
    REL_POTENTIAL_STEP: "Potential stepping stone",
    REL_NO_IDENTIFIED_TARGET_PROGRESS: "No identified target progress",
    REL_NOT_MORE_REACHABLE_THAN_TARGET: "Not more reachable than the target",
    REL_RELATIONSHIP_UNCLEAR: "Relationship unclear",
    REL_TARGET_ALREADY_EVIDENCED: "Target already evidenced",
}

METHOD = {
    "what_this_is": (
        "A structural comparison of one observed opportunity against where you are now and, when a Target is "
        "linked to your selected Career Direction (or a Target is otherwise supplied), against that Target. "
        "This is decision support, not a recommendation."
    ),
    "not_a_recommendation": (
        "Nothing here says whether you should apply. No composite opportunity score, fit percentage, hiring "
        "probability, offer probability, learning-time estimate or ranking is computed anywhere in this response."
    ),
    "involvement_not_acquisition": (
        "A Target requirement this opportunity involves is an opportunity to develop it, not proof you will "
        "acquire it. Historical postings describe role patterns, not confirmed vacancies."
    ),
    "relationship_caveat": (
        "same_destination_family, potential_step and no_identified_target_progress are each independent, "
        "rule-based structural facts, not verdicts — none is a recommendation to apply, and "
        "no_identified_target_progress is not a statement that this role has no value outside this Target."
    ),
    "semantic_similarity": (
        "Semantic similarity to the target/profile is secondary context only, shown separately under "
        "semantic_similarity — it never decides a relationship state."
    ),
    "market_evidence_context": (
        "market_evidence_context (Phase 8, docs/39) describes how much corpus evidence backs this opportunity's "
        "own archetype pattern. It is context around the relationship above, never a second classifier — it "
        "never changes same_destination_family, potential_step, no_identified_target_progress or any other "
        "relationship state."
    ),
    "compensation": (
        "Every compensation figure carries its own basis. Figures of different bases are never merged, and "
        "currencies are never converted."
    ),
}


class OpportunityNotFoundError(LookupError):
    """No such role — 404 at the route layer."""


class NotAnOpportunityError(ValueError):
    """The subject role is a Target (or other non-posting role) — 400 at the
    route layer. Alignment is for an observed posting only (build §4)."""


class TargetNotFoundError(LookupError):
    """`target_id` (the explicit-target override) does not exist — 404."""


class NotATargetError(ValueError):
    """`target_id` (the explicit-target override) is not a Target — 400. A
    Target may never masquerade as an opportunity, and an opportunity may
    never masquerade as a Target, merely because both use role_instance."""


_ROLE_FIELDS = (
    "id, title, organisation, instance_type, target_basis, archetype_concept_id, "
    "location, country, remote_type, employment_type, seniority_level"
)


def _role_or_none(cur, role_id: str) -> dict | None:
    cur.execute(f"SELECT {_ROLE_FIELDS} FROM jobber.role_instance WHERE id = %s", (role_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _require_opportunity(cur, role_id: str) -> dict:
    role = _role_or_none(cur, role_id)
    if role is None:
        raise OpportunityNotFoundError(f"role {role_id!r} not found")
    if role["instance_type"] != "observed_posting":
        raise NotAnOpportunityError(
            "Career alignment is for an observed opportunity. A Target cannot be evaluated against itself."
        )
    return role


def _require_target(cur, target_id: str) -> dict:
    role = _role_or_none(cur, target_id)
    if role is None:
        raise TargetNotFoundError(f"role {target_id!r} not found")
    if role["instance_type"] != "user_defined_target":
        raise NotATargetError("target_id must refer to a Target, not an observed posting.")
    return role


def _role_summary(role: dict) -> dict:
    return {
        "id": str(role["id"]),
        "title": role["title"],
        "organisation": role["organisation"],
        "archetype_concept_id": str(role["archetype_concept_id"]) if role.get("archetype_concept_id") else None,
    }


# --- Archetype relationship (build §12) -------------------------------------

def _archetype_names(cur, concept_ids: list[str]) -> dict[str, dict]:
    ids = [c for c in concept_ids if c]
    if not ids:
        return {}
    cur.execute("SELECT id, canonical_name, status FROM jobber.concept WHERE id = ANY(%s::uuid[])", (ids,))
    return {str(r["id"]): {"id": str(r["id"]), "canonical_name": r["canonical_name"], "status": r["status"]}
            for r in cur.fetchall()}


def _archetype_relationship(cur, opportunity_role: dict, target_role: dict | None, direction: dict | None) -> dict:
    opp_id = str(opportunity_role["archetype_concept_id"]) if opportunity_role.get("archetype_concept_id") else None
    target_id = (
        str(target_role["archetype_concept_id"])
        if target_role and target_role.get("archetype_concept_id") else None
    )
    names = _archetype_names(cur, [opp_id, target_id])

    opp_archetype = names.get(opp_id) if opp_id else None
    target_archetype = names.get(target_id) if target_id else None
    direction_archetype = (direction or {}).get("archetype")

    # A shared archetype only counts as a structural anchor while it is
    # still active — a concept deprecated after assignment is stale
    # vocabulary, and must not be able to drive same_destination_family,
    # the strongest relationship state. `opp_archetype["id"] ==
    # target_archetype["id"]` means both dicts describe the same concept
    # row, so either side's `status` is the same value; checking one
    # suffices. Displayed archetype facts themselves are shown regardless
    # of status — only the same-family classification is gated.
    same_as_target = bool(
        opp_archetype and target_archetype
        and opp_archetype["id"] == target_archetype["id"]
        and opp_archetype["status"] == "active"
    )
    same_as_direction = bool(
        opp_archetype and direction_archetype
        and opp_archetype["id"] == direction_archetype["id"]
        and opp_archetype["status"] == "active"
    )

    return {
        "opportunity_archetype": opp_archetype,
        "target_archetype": target_archetype,
        "direction_archetype": direction_archetype,
        "same_as_target_archetype": same_as_target,
        "same_as_direction_archetype": same_as_direction,
        "note": None if opp_archetype else "No reviewed archetype is assigned to this posting.",
    }


_NO_ARCHETYPE_MARKET_EVIDENCE_MESSAGE = "No reviewed archetype assignment — market-pattern support is unavailable for this role."


def _market_evidence_context(cur, opportunity_role: dict, freshness: dict) -> dict:
    """Phase 8 (build §23): concise corpus support for the archetype pattern
    this opportunity relies on — composed from the shared Market Coverage
    service (`market_coverage.archetype_coverage_detail`), never a second,
    independently-computed archetype-support calculation. Context only: it
    is never consulted by `_classify_relationship` and can never change
    `same_destination_family`/`potential_step`/`no_identified_target_progress`
    or any other Phase 7 relationship state."""
    archetype_id = opportunity_role.get("archetype_concept_id")
    if not archetype_id:
        return {"available": False, "message": _NO_ARCHETYPE_MARKET_EVIDENCE_MESSAGE}
    detail = market_coverage.archetype_coverage_detail(cur, archetype_id, freshness=freshness)
    if not detail["found"]:
        return {"available": False, "message": _NO_ARCHETYPE_MARKET_EVIDENCE_MESSAGE}
    return {
        "available": True,
        "archetype_concept_id": archetype_id,
        "canonical_name": detail["canonical_name"],
        "status": detail["status"],
        "seniority_band": detail["seniority_band"],
        "typical_market": detail["typical_market"],
        "assigned_posting_count": detail["assigned_posting_count"],
        "reviewed_requirement_posting_count": detail["reviewed_requirement_posting_count"],
        "known_posting_date_count": detail["known_posting_date_count"],
        "unknown_posting_date_count": detail["unknown_posting_date_count"],
        "latest_known_posting_date": detail["latest_known_posting_date"],
        "distinct_country_count": detail["distinct_country_count"],
        "countries": detail["countries"],
        "demand_derivation_available": detail["demand_derivation_available"],
        "compensation_benchmark_available": detail["compensation_benchmark_available"],
        "economics_freshness": detail["economics_freshness"],
        "evidence_depth": detail["evidence_depth"],
    }


# --- Relationship classification (build §8/§9) ------------------------------

def _classify_relationship(step: dict, archetype_match: bool) -> dict:
    state_key = step["assessment"]
    if state_key in ("insufficient_evidence", "incomplete_target_mapping"):
        state = REL_RELATIONSHIP_UNCLEAR
    elif state_key == "target_evidenced":
        state = REL_TARGET_ALREADY_EVIDENCED
    elif archetype_match:
        state = REL_SAME_DESTINATION_FAMILY
    elif state_key == "potential_step":
        state = REL_POTENTIAL_STEP
    elif state_key == "no_target_progress":
        state = REL_NO_IDENTIFIED_TARGET_PROGRESS
    elif state_key in ("needs_evidence", "not_an_intermediate_step"):
        state = REL_NOT_MORE_REACHABLE_THAN_TARGET
    else:
        state = REL_RELATIONSHIP_UNCLEAR
    return {"state": state, "label": _RELATIONSHIP_LABELS[state], "reason": step["explanation"]}


# --- You -> Opportunity (build §6) ------------------------------------------

def _you_to_opportunity_summary(comparison: dict) -> dict:
    """Structural counts/statuses only — never `fit_score` (build §6: "Do not
    expose the old opaque fit_score as the Phase 7 decision signal")."""
    legacy_count = sum(
        1 for item in comparison["items"] if item["role_side"]["requirement_source"] == "role_skill_observation"
    )
    return {
        "counts": comparison["counts"],
        "requirements_reviewed": len(comparison["items"]),
        "legacy_requirement_count": legacy_count,
        "review_summary": comparison["review_summary"],
        "review_blockers": review_blockers(comparison["review_summary"]),
        "blocking_gaps": comparison["blocking_gaps"],
        "unverified_required": comparison["unverified_required"],
        "embedding_similarity": comparison["embedding_similarity"],
    }


# --- Opportunity -> Target gap movement (build §7/§10) ----------------------

def _opportunity_to_target(assessed: dict) -> dict:
    step = assessed["step"]
    statuses = assessed["statuses"]

    target_by_concept = {r["concept_id"]: r for r in assessed["target_requirements"]}
    opportunity_by_concept: dict[str, dict] = {}
    for row in assessed["candidate_requirements"]:
        key = row["concept_id"]
        if key not in opportunity_by_concept or row["requirement_type"] == "required":
            opportunity_by_concept[key] = row

    target_gaps = {cid for cid in target_by_concept if statuses.get(cid) != "evidenced"}

    involved = []
    for cid in target_gaps:
        opp_row = opportunity_by_concept.get(cid)
        if opp_row is None:
            continue
        target_row = target_by_concept[cid]
        involved.append({
            "concept_id": cid,
            "canonical_name": target_row["canonical_name"],
            "target_requirement_type": target_row["requirement_type"],
            "opportunity_requirement_type": opp_row["requirement_type"],
            "person_evidence_status": statuses.get(cid),
            "target_requirement_source": target_row["source"],
            "opportunity_requirement_source": opp_row["source"],
        })
    involved.sort(key=lambda x: x["canonical_name"] or "")

    not_touched = [
        {
            "concept_id": cid,
            "canonical_name": target_by_concept[cid]["canonical_name"],
            "target_requirement_type": target_by_concept[cid]["requirement_type"],
            "person_evidence_status": statuses.get(cid),
            "target_requirement_source": target_by_concept[cid]["source"],
        }
        for cid in target_gaps if cid not in opportunity_by_concept
    ]
    not_touched.sort(key=lambda x: x["canonical_name"] or "")

    additional_demands = [
        {
            "concept_id": cid,
            "canonical_name": row["canonical_name"],
            "opportunity_requirement_type": row["requirement_type"],
            "person_evidence_status": statuses.get(cid),
            "opportunity_requirement_source": row["source"],
        }
        for cid, row in opportunity_by_concept.items()
        if row["requirement_type"] == "required" and cid not in target_by_concept
    ]
    additional_demands.sort(key=lambda x: x["canonical_name"] or "")

    legacy_involved = sum(
        1 for i in involved
        if i["target_requirement_source"] == "role_skill_observation"
        or i["opportunity_requirement_source"] == "role_skill_observation"
    )

    return {
        "target_gaps_involved": involved,
        "target_gaps_not_touched": not_touched,
        "additional_opportunity_demands": additional_demands,
        "legacy_requirements_involved": legacy_involved,
        "is_potential_step": step["assessment"] == "potential_step",
        "candidate_required_gaps": len(step["missing_required"]) + len(step["unverified_required"]),
        "candidate_missing_required": step["missing_required"],
        "candidate_unverified_required": step["unverified_required"],
        "target_required_evidence_gaps": step["target_required_evidence_gaps"],
        "candidate_review_complete": step["review_complete"],
        "candidate_review_blockers": step["review_blockers"],
        "target_review_complete": step["target_review_complete"],
        "target_review_blockers": step["target_review_blockers"],
        "target_mapping_complete": assessed["target_mapping"]["complete"],
        "target_mapping_unresolved": assessed["target_mapping"]["unresolved"],
    }


# --- Direction constraints (build §11) --------------------------------------

_DIRECT_CONSTRAINT_FIELDS = (
    ("remote_types", "remote_type", "Remote type"),
    ("employment_types", "employment_type", "Employment type"),
    ("seniority_levels", "seniority_level", "Seniority level"),
)


def _text_matches(desired: list[str], *observed_values: str | None) -> bool:
    """Case/whitespace-insensitive equality or containment — for the short,
    effectively-controlled vocabularies (remote type, employment type,
    seniority level) where a false-positive containment match is
    implausible (this app has no "senior" vs "junior senior" collision).
    Never used for geography — see `_location_matches`."""
    normalized_desired = [v.strip().casefold() for v in desired if v and v.strip()]
    for observed in observed_values:
        if not observed:
            continue
        norm_observed = observed.strip().casefold()
        for d in normalized_desired:
            if d == norm_observed or d in norm_observed or norm_observed in d:
                return True
    return False


def _location_matches(desired: list[str], *observed_values: str | None) -> bool:
    """Exact, normalized match only — never containment. `location`/
    `country` are plain, unnormalized free-text columns with no canonical
    geography mapping in this repository, so containment produces real
    false positives ("Ireland" containment-matching "Northern Ireland",
    "York" containment-matching "New York") — exactly the "guess that two
    differently named locations are equivalent" build §11 forbids. Exact
    case/whitespace-insensitive string equality is the only comparison
    defensible without geocoding or a canonical location table."""
    normalized_desired = {v.strip().casefold() for v in desired if v and v.strip()}
    for observed in observed_values:
        if not observed:
            continue
        if observed.strip().casefold() in normalized_desired:
            return True
    return False


def _one_constraint(key: str, label: str, desired: list[str], observed_values: tuple, *, observed,
                    matcher=_text_matches) -> dict:
    if not desired:
        return {"constraint": key, "label": label, "status": "not_specified",
                "observed_value": observed, "desired_value": desired,
                "reason": "No preference stated for this dimension."}
    if not any(observed_values):
        return {"constraint": key, "label": label, "status": "unknown",
                "observed_value": observed, "desired_value": desired,
                "reason": "This opportunity does not record a value for this field."}
    matched = matcher(desired, *observed_values)
    return {"constraint": key, "label": label, "status": "matches" if matched else "conflicts",
            "observed_value": observed, "desired_value": desired,
            "reason": (f"{observed!r} matches one of your stated preferences." if matched
                       else f"{observed!r} does not match any of your stated preferences.")}


def _compensation_floor_constraint(floor: dict | None, opportunity_compensation: dict) -> dict:
    label = "Compensation floor"
    if not floor:
        return {"constraint": "compensation_floor", "label": label, "status": "not_specified",
                "observed_value": None, "desired_value": None, "reason": "No compensation floor stated."}
    if (opportunity_compensation.get("basis") == resolver.BASIS_INSUFFICIENT
            or opportunity_compensation.get("amount_reference") is None):
        return {"constraint": "compensation_floor", "label": label, "status": "unknown",
                "observed_value": None, "desired_value": floor,
                "reason": "There is no compensation figure for this opportunity to compare against."}

    opp_currency = (opportunity_compensation.get("currency") or "").upper()
    floor_currency = (floor.get("currency") or "").upper()
    if opp_currency != floor_currency:
        return {"constraint": "compensation_floor", "label": label, "status": "unknown",
                "observed_value": opportunity_compensation.get("amount_reference"), "desired_value": floor,
                "reason": (f"This opportunity is quoted in {opp_currency}; your floor is set in {floor_currency}. "
                           "Amounts are never converted between currencies.")}

    # The floor has no `component` of its own (CareerDirectionCompensationFloor,
    # app/models.py) — it is always a base-pay figure, same as a
    # compensation_observation's own base-pay grouping. `_component_family`
    # is compensation_resolver's private helper, reused here rather than
    # re-deriving the same annual-base/day-rate distinction a second time.
    opp_family = resolver._component_family(opportunity_compensation.get("component"),
                                            opportunity_compensation.get("pay_period"), None)
    floor_family = resolver._component_family("base", floor.get("pay_period"), None)
    if opp_family is None or floor_family is None or opp_family != floor_family:
        return {"constraint": "compensation_floor", "label": label, "status": "unknown",
                "observed_value": opportunity_compensation.get("amount_reference"), "desired_value": floor,
                "reason": ("This opportunity's compensation is not the same kind of figure as your stated floor "
                           "(for example a day rate against an annual floor). No annualisation is assumed.")}

    opp_basis = opportunity_compensation.get("employment_basis")
    floor_basis = floor.get("employment_basis")
    if opp_basis and floor_basis and opp_basis != "unknown" and floor_basis != "unknown" and opp_basis != floor_basis:
        return {"constraint": "compensation_floor", "label": label, "status": "unknown",
                "observed_value": opportunity_compensation.get("amount_reference"), "desired_value": floor,
                "reason": (f"Your floor assumes {floor_basis} employment and this opportunity is {opp_basis}. "
                           "Employment bases are never treated as equivalent.")}

    reference = opportunity_compensation["amount_reference"]
    meets = reference >= floor["amount"]
    return {"constraint": "compensation_floor", "label": label, "status": "matches" if meets else "conflicts",
            "observed_value": reference, "desired_value": floor,
            "reason": (f"{reference:,.0f} {opp_currency} meets your stated floor of {floor['amount']:,.0f}."
                       if meets else
                       f"{reference:,.0f} {opp_currency} is below your stated floor of {floor['amount']:,.0f}.")}


def _direct_constraints(direction: dict, opportunity_role: dict, opportunity_compensation: dict) -> list[dict]:
    constraints = direction.get("constraints") or {}
    out = [_one_constraint(
        "locations", "Location", constraints.get("locations") or [],
        (opportunity_role.get("location"), opportunity_role.get("country")),
        observed=opportunity_role.get("location") or opportunity_role.get("country"),
        matcher=_location_matches,
    )]
    for key, role_field, label in _DIRECT_CONSTRAINT_FIELDS:
        observed = opportunity_role.get(role_field)
        out.append(_one_constraint(key, label, constraints.get(key) or [], (observed,), observed=observed))
    out.append(_compensation_floor_constraint(constraints.get("compensation_floor"), opportunity_compensation))
    return out


def _qualitative_dimensions(direction: dict) -> list[dict]:
    """Never fabricated from a title/keyword guess (build §11) — every
    dimension reports the same honest `not_structurally_assessed` verdict
    until a genuinely deterministic role-field mapping exists for it."""
    return [
        {
            "dimension_code": d["dimension_code"],
            "desired_direction": d["desired_direction"],
            "importance": d["importance"],
            "note": d["note"],
            "assessment": "not_structurally_assessed",
            "reason": "No deterministic mapping from a posting's structured fields to this dimension exists yet.",
        }
        for d in (direction.get("dimensions") or [])
    ]


# --- State responses (build §3) ---------------------------------------------

def _no_selected_direction(opportunity_role: dict) -> dict:
    return {
        "state": STATE_NO_SELECTED_DIRECTION,
        "direction": None,
        "target": None,
        "message": "Select a Career Direction to evaluate this opportunity against it.",
        "opportunity": _role_summary(opportunity_role),
        "method": METHOD,
    }


def _direction_without_target(cur, opportunity_role: dict, direction: dict, opportunity_compensation: dict,
                              freshness: dict) -> dict:
    return {
        "state": STATE_DIRECTION_WITHOUT_TARGET,
        "direction": direction,
        "target": None,
        "message": ("This direction has no linked Target yet, so structural Opportunity -> Target analysis is not "
                    "available. Direction constraints can still be checked directly."),
        "direction_constraints": _direct_constraints(direction, opportunity_role, opportunity_compensation),
        "direction_dimensions": _qualitative_dimensions(direction),
        "archetype_relationship": _archetype_relationship(cur, opportunity_role, None, direction),
        "market_evidence_context": _market_evidence_context(cur, opportunity_role, freshness),
        "opportunity": _role_summary(opportunity_role),
        "method": METHOD,
    }


def _economics_block(cur, opportunity_id: str, target_id: str | None, opportunity_compensation: dict,
                     freshness: dict) -> dict:
    earnings_state = safe_personal_earnings_state(cur)
    target_compensation = resolver.resolve_role_compensation(cur, target_id, freshness=freshness) if target_id else None
    return {
        "opportunity": opportunity_compensation,
        "vs_personal_earnings": resolver.compare_to_personal_earnings(opportunity_compensation, earnings_state),
        # Side by side, each with its own basis (build §13) — never a
        # synthetic "which pays better" verdict computed from the two.
        "target": target_compensation,
    }


def _insufficient_target_evidence(cur, opportunity_role: dict, target_role: dict, direction: dict | None,
                                  opportunity_compensation: dict, freshness: dict) -> dict:
    opportunity_id = str(opportunity_role["id"])
    comparison = comparison_service.build_role_comparison(cur, opportunity_id)
    return {
        "state": STATE_INSUFFICIENT_TARGET_EVIDENCE,
        "direction": direction,
        "target": _role_summary(target_role),
        "message": ("This target has no reviewed, mapped requirements yet, so a structural Opportunity -> Target "
                    "comparison is not available. The facts below do not depend on that gap."),
        "you_to_opportunity": _you_to_opportunity_summary(comparison),
        "economics": _economics_block(cur, opportunity_id, str(target_role["id"]), opportunity_compensation, freshness),
        "market_evidence_context": _market_evidence_context(cur, opportunity_role, freshness),
        "opportunity": _role_summary(opportunity_role),
        "method": METHOD,
    }


def _target_available(cur, opportunity_role: dict, target_role: dict, target_id: str, direction: dict | None,
                      opportunity_compensation: dict, freshness: dict, assessed: dict) -> dict:
    opportunity_id = str(opportunity_role["id"])
    step = assessed["step"]

    archetype_relationship = _archetype_relationship(cur, opportunity_role, target_role, direction)
    relationship = _classify_relationship(
        step, archetype_relationship["same_as_target_archetype"] or archetype_relationship["same_as_direction_archetype"]
    )

    comparison = comparison_service.build_role_comparison(cur, opportunity_id)
    you_to_opportunity = _you_to_opportunity_summary(comparison)
    opportunity_to_target = _opportunity_to_target(assessed)
    economics = _economics_block(cur, opportunity_id, target_id, opportunity_compensation, freshness)

    review = {
        "opportunity": {
            "complete": comparison["review_summary"]["complete"],
            "blockers": you_to_opportunity["review_blockers"],
            "legacy_requirement_count": you_to_opportunity["legacy_requirement_count"],
        },
        "target": {
            "complete": review_is_complete(assessed["target_review"]),
            "blockers": review_blockers(assessed["target_review"]),
            "mapping_complete": assessed["target_mapping"]["complete"],
            "mapping_unresolved": assessed["target_mapping"]["unresolved"],
        },
    }

    return {
        "state": STATE_TARGET_AVAILABLE,
        "direction": direction,
        "target": _role_summary(target_role),
        "relationship": relationship,
        "you_to_opportunity": you_to_opportunity,
        "opportunity_to_target": opportunity_to_target,
        "direction_constraints": _direct_constraints(direction, opportunity_role, opportunity_compensation) if direction else [],
        "direction_dimensions": _qualitative_dimensions(direction) if direction else [],
        "archetype_relationship": archetype_relationship,
        "market_evidence_context": _market_evidence_context(cur, opportunity_role, freshness),
        "economics": economics,
        "review": review,
        "semantic_similarity": {
            "to_target": step.get("similarity_to_target"),
            "to_profile": step.get("similarity_to_profile"),
        },
        "opportunity": _role_summary(opportunity_role),
        "method": METHOD,
    }


# --- Entry point -------------------------------------------------------------

def build_opportunity_alignment(cur, opportunity_id: str, *, target_id_override: str | None = None) -> dict:
    """One bounded, deterministic `You -> Opportunity -> Target` answer for
    one observed posting. See module docstring for the state machine and the
    relationship-classification precedence. No AI, no writes, no rebuild
    side effects (build §14/§25/§26)."""
    opportunity_role = _require_opportunity(cur, opportunity_id)
    opportunity_id = str(opportunity_role["id"])
    direction = career_directions.get_selected_direction_summary(cur)

    target_role: dict | None
    target_id: str | None

    if target_id_override:
        target_role = _require_target(cur, target_id_override)
        target_id = str(target_role["id"])
    elif direction is None:
        return _no_selected_direction(opportunity_role)
    elif direction["target"] is None:
        freshness = economics_freshness(cur)
        opportunity_compensation = resolver.resolve_role_compensation(cur, opportunity_id, freshness=freshness)
        return _direction_without_target(cur, opportunity_role, direction, opportunity_compensation, freshness)
    else:
        target_id = direction["target"]["id"]
        target_role = _role_or_none(cur, target_id)
        if target_role is None:
            # `career_direction.target_role_instance_id` is ON DELETE SET
            # NULL, so `direction["target"]` should never dangle — this is a
            # defensive fallback, not an expected path.
            freshness = economics_freshness(cur)
            opportunity_compensation = resolver.resolve_role_compensation(cur, opportunity_id, freshness=freshness)
            return _direction_without_target(cur, opportunity_role, direction, opportunity_compensation, freshness)

    freshness = economics_freshness(cur)
    opportunity_compensation = resolver.resolve_role_compensation(cur, opportunity_id, freshness=freshness)

    _, profile_vec = ensure_profile_embedding(cur)
    target_vec = get_embedding(cur, "role_instance", target_id)
    evidence_revision, _ = target_cache_revisions(cur, target_vec, profile_vec)

    assessed = assess_specific_candidate(cur, target_id, opportunity_id, target_vec, profile_vec, evidence_revision)

    if not assessed["target_requirements"]:
        return _insufficient_target_evidence(cur, opportunity_role, target_role, direction,
                                             opportunity_compensation, freshness)

    return _target_available(cur, opportunity_role, target_role, target_id, direction,
                             opportunity_compensation, freshness, assessed)
