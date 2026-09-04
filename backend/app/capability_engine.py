"""Phase 3 deterministic capability-coverage / role-fit derivation engine.

This module is the single place status logic lives (brief §11: "Do not
duplicate status logic between API routes"). Routes call into it; nothing in
`routes/*.py` computes a coverage or fit status itself. AI is never consulted
here — every function below is a pure function of accepted claims, accepted
mappings, the curated capability catalogue, and the engine version (brief
§1's "AI is not the derivation engine").

Read-only towards `profile360`: every query against that schema below is a
SELECT. Nothing here writes to `profile360` (docs/15 §4's invariant, unbroken
— the one exception anywhere in this codebase is `profile360_promotion.py`).

## Direct vs. compositional evidence (brief §9/§10)

A capability's status can be established two ways:

- **Direct** — an accepted/unreviewed profile360 mapping points straight at
  the capability concept, via a `profile360_claim_mapping` (the claim itself
  carries a `depth`; its episode carries `autonomy`) or a
  `profile360_capability_mapping` (a synthesized profile360 capability row —
  carries neither modifier, see `_direct_evidence` below). Only a direct,
  *accepted* mapping whose modifiers meet the capability's thresholds can
  reach `evidenced`.
- **Compositional** — the capability's curated `component_of` core/
  supporting/contextual atoms have their own accepted evidence, grouped by
  `profile360.episode_id` so co-occurrence is only credited *within* one
  episode, never stitched together across a career (brief §10). Composition
  alone can never exceed `partial` — this is an absolute invariant, checked
  by `test_capability_engine.py::test_composition_alone_cannot_reach_evidenced`.

## Depth/autonomy: reading profile360's confirmed columns honestly

`profile360.claims.depth` is a confirmed column (docs/14 §5) but its *value*
vocabulary is not — it belongs to a different tool. `profile360.episodes`
has no `depth` column at all, but does have `autonomy` (docs/14 §5), so
autonomy is read from the claim's episode, not the claim itself — a
deliberate adaptation, documented in docs/16 §5. Rather than guess a mapping
from profile360's free-text vocabulary onto this engine's four-level scales,
`normalize_depth`/`normalize_autonomy` recognise only an exact (case-folded)
match to the shared canonical tokens and treat anything else — including a
genuinely different but valid profile360 term this engine simply doesn't
know — as unknown. Brief §7 is explicit that this is the correct default:
"If a capability requires a modifier and the underlying evidence has no
trustworthy value for that modifier, treat that threshold as not
demonstrated... Do not fabricate missing modifiers."
"""

from datetime import date, datetime

from . import profile360_reader as p360
from .concept_linking import get_or_create_current_vocabulary_version
from .db import to_json_param
from .embeddings import cosine_similarity, ensure_profile_embedding, get_embedding

ENGINE_VERSION = "capability-engine-v1"

DEPTH_LEVELS = ["exposed", "applied", "owned", "set_standard"]
AUTONOMY_LEVELS = ["assisted", "independent", "directed_others", "accountable"]


class CapabilityNotFoundError(LookupError):
    """No active `capability` concept + `capability_detail` row for this id."""


class RoleInstanceNotFoundError(LookupError):
    """No `role_instance` row for this id."""


# --- Ordinal comparison (brief §7) ------------------------------------------

def normalize_depth(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in DEPTH_LEVELS else None


def normalize_autonomy(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in AUTONOMY_LEVELS else None


def _meets(levels: list[str], value: str | None, minimum: str | None) -> bool:
    """minimum=None means the capability has no requirement for this modifier
    -> vacuously satisfied. value=None means no trustworthy evidence for it
    -> never satisfied once a minimum is set, regardless of how low that
    minimum is (brief §7: unknown must never be promoted, not even to the
    lowest ordinal)."""
    if minimum is None:
        return True
    if value is None:
        return False
    return levels.index(value) >= levels.index(minimum)


def depth_meets(value: str | None, minimum: str | None) -> bool:
    return _meets(DEPTH_LEVELS, value, minimum)


def autonomy_meets(value: str | None, minimum: str | None) -> bool:
    return _meets(AUTONOMY_LEVELS, value, minimum)


def _stronger(levels: list[str], a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if levels.index(a) >= levels.index(b) else b


# --- Temporal derivation (brief §13) ----------------------------------------

def _coerce_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _effective_end(episode: dict | None) -> date | None:
    """COALESCE(end_date, current_date) — but only for an episode we can
    actually place in time at all. An episode with no start_date and no
    end_date carries nothing to derive from; per §13's "prefer NULL/
    uncertain over false precision" it is excluded entirely rather than
    defaulting to today."""
    if not episode:
        return None
    end = _coerce_date(episode.get("end_date"))
    if end is not None:
        return end
    start = _coerce_date(episode.get("start_date"))
    return date.today() if start is not None else None


def union_years_active(episodes: list[dict]) -> float | None:
    """Union (not sum) of qualifying episode date spans, per brief §13's
    worked example (two overlapping 2-year spans = 3 years, not 4)."""
    spans = []
    for ep in episodes:
        start = _coerce_date(ep.get("start_date"))
        if start is None:
            continue
        end = _effective_end(ep) or start
        if end < start:
            continue
        spans.append((start, end))
    if not spans:
        return None
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:  # overlapping or touching -> merge, no double count
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    total_days = sum((end - start).days for start, end in merged)
    return round(total_days / 365.25, 2)


def last_demonstrated(episodes: list[dict]) -> date | None:
    ends = [e for e in (_effective_end(ep) for ep in episodes) if e is not None]
    return max(ends) if ends else None


# --- Loaders -----------------------------------------------------------------

def _capability_row(cur, capability_concept_id: str) -> dict | None:
    cur.execute(
        """
        SELECT c.id, c.canonical_name, c.type_code, c.status AS concept_status,
               cd.demonstration_standard, cd.min_depth, cd.min_autonomy,
               cd.requires_all_core, cd.min_core_required, cd.economic_salience, cd.notes
        FROM jobber.concept c
        JOIN jobber.capability_detail cd ON cd.concept_id = c.id
        WHERE c.id = %s AND c.type_code = 'capability'
        """,
        (capability_concept_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    row = dict(row)
    row["id"] = str(row["id"])
    return row


def _components(cur, capability_concept_id: str) -> dict[str, list[dict]]:
    """Accepted component_of edges only (brief §6: "Do not let AI-created
    proposals become accepted edges automatically")."""
    cur.execute(
        """
        SELECT ce.id AS edge_id, ce.necessity, c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.concept_edge ce
        JOIN jobber.concept c ON c.id = ce.from_concept_id
        WHERE ce.to_concept_id = %s AND ce.relation = 'component_of' AND ce.status = 'accepted'
        ORDER BY c.canonical_name
        """,
        (capability_concept_id,),
    )
    out: dict[str, list[dict]] = {"core": [], "supporting": [], "contextual": []}
    for row in cur.fetchall():
        row = dict(row)
        row["edge_id"] = str(row["edge_id"])
        row["concept_id"] = str(row["concept_id"])
        necessity = row.get("necessity") or "supporting"  # defensive only — the API always requires one of the three
        out.setdefault(necessity, []).append(row)
    return out


load_components = _components  # public alias — routes/capabilities.py reuses this rather than re-querying edges itself


def load_proposed_components(cur, capability_concept_id: str) -> dict[str, list[dict]]:
    """The curation-review counterpart of `load_components`: 'proposed'
    component_of edges only (from app/vocabulary_bootstrap.py, or any future
    proposer) — kept in a fully separate function, never merged with
    `_components`/`load_components`, so there is no code path by which a
    'proposed' edge could be mistaken for an 'accepted' one anywhere the
    engine computes coverage/fit (brief §6's invariant, unchanged: only
    accepted edges are ever read by the engine). Used only by
    routes/capabilities.py's review endpoints."""
    cur.execute(
        """
        SELECT ce.id AS edge_id, ce.necessity, ce.origin, c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.concept_edge ce
        JOIN jobber.concept c ON c.id = ce.from_concept_id
        WHERE ce.to_concept_id = %s AND ce.relation = 'component_of' AND ce.status = 'proposed'
        ORDER BY c.canonical_name
        """,
        (capability_concept_id,),
    )
    out: dict[str, list[dict]] = {"core": [], "supporting": [], "contextual": []}
    for row in cur.fetchall():
        row = dict(row)
        row["edge_id"] = str(row["edge_id"])
        row["concept_id"] = str(row["concept_id"])
        necessity = row.get("necessity") or "supporting"
        out.setdefault(necessity, []).append(row)
    return out


def is_valid_edge(cur, relation: str, from_type: str, to_type: str) -> bool:
    """Server-side grammar check against jobber.concept_edge_rule (brief §6:
    "Do not rely only on frontend validation. Reject invalid relation/type
    combinations server-side.")."""
    cur.execute(
        "SELECT 1 FROM jobber.concept_edge_rule WHERE relation = %s AND from_type = %s AND to_type = %s",
        (relation, from_type, to_type),
    )
    return cur.fetchone() is not None


def _episodes_by_id(cur, episode_ids: list[str]) -> dict[str, dict]:
    if not episode_ids:
        return {}
    cur.execute(
        "SELECT id, start_date, end_date, date_precision, autonomy, accountability, status "
        "FROM profile360.episodes WHERE id = ANY(%s::uuid[])",
        (episode_ids,),
    )
    return {str(row["id"]): dict(row) for row in cur.fetchall()}


def _assertion(cur, concept_id: str) -> dict | None:
    cur.execute(
        "SELECT id, note, created_at, promoted_to_profile360_at "
        "FROM jobber.person_capability_assertion WHERE jobber_concept_id = %s",
        (concept_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    row = dict(row)
    row["id"] = str(row["id"])
    # Stringify timestamps — this dict is embedded verbatim into
    # d_capability_coverage.trace (JSONB), and psycopg's Json wrapper uses
    # plain json.dumps with no datetime support.
    row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
    row["promoted_to_profile360_at"] = row["promoted_to_profile360_at"].isoformat() if row["promoted_to_profile360_at"] else None
    return row


def capabilities_containing(cur, atomic_concept_id: str) -> list[dict]:
    """Context only (brief §16): which curated capabilities this atom is a
    component of. Never used to manufacture a role capability requirement —
    display only."""
    cur.execute(
        """
        SELECT c.id, c.canonical_name, ce.necessity
        FROM jobber.concept_edge ce
        JOIN jobber.concept c ON c.id = ce.to_concept_id
        WHERE ce.from_concept_id = %s AND ce.relation = 'component_of' AND ce.status = 'accepted'
        ORDER BY c.canonical_name
        """,
        (atomic_concept_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["id"] = str(r["id"])
    return rows


# --- Direct evidence (brief §9) ---------------------------------------------

def _direct_evidence(cur, capability_concept_id: str) -> list[dict]:
    items: list[dict] = []

    cur.execute(
        """
        SELECT m.id AS mapping_id, m.review_status, m.mapping_basis, m.profile360_claim_id,
               cl.claim_text, cl.depth AS raw_depth, cl.episode_id, cl.evidence_class
        FROM jobber.profile360_claim_mapping m
        JOIN profile360.claims cl ON cl.id = m.profile360_claim_id
        WHERE m.jobber_concept_id = %s AND m.review_status IN ('accepted', 'unreviewed')
        """,
        (capability_concept_id,),
    )
    claim_rows = [dict(r) for r in cur.fetchall()]
    episode_ids = [str(r["episode_id"]) for r in claim_rows if r["episode_id"] is not None]
    episodes_by_id = _episodes_by_id(cur, episode_ids)

    for r in claim_rows:
        episode_id = str(r["episode_id"]) if r["episode_id"] else None
        episode = episodes_by_id.get(episode_id) if episode_id else None
        items.append(
            {
                "source_kind": "claim",
                "mapping_id": str(r["mapping_id"]),
                "review_status": r["review_status"],
                "mapping_basis": r["mapping_basis"],
                "profile360_claim_id": str(r["profile360_claim_id"]),
                "display": r["claim_text"],
                "depth": normalize_depth(r["raw_depth"]),
                "autonomy": normalize_autonomy(episode.get("autonomy")) if episode else None,
                "episode_id": episode_id,
                "episode": episode,
            }
        )

    cur.execute(
        """
        SELECT m.id AS mapping_id, m.review_status, m.mapping_basis, m.profile360_capability_id,
               cap.name, cap.current_assessment
        FROM jobber.profile360_capability_mapping m
        JOIN profile360.capabilities cap ON cap.id = m.profile360_capability_id
        WHERE m.jobber_capability_concept_id = %s AND m.review_status IN ('accepted', 'unreviewed')
        """,
        (capability_concept_id,),
    )
    for r in cur.fetchall():
        items.append(
            {
                "source_kind": "capability",
                "mapping_id": str(r["mapping_id"]),
                "review_status": r["review_status"],
                "mapping_basis": r["mapping_basis"],
                "profile360_capability_id": str(r["profile360_capability_id"]),
                "display": r["name"],
                # profile360.capabilities carries no structured depth/autonomy
                # modifier at all — never fabricated (brief §7). This is why a
                # capability-mapping-only direct evidence item can never alone
                # reach `evidenced` once any threshold is set — see docs/16 §5.
                "depth": None,
                "autonomy": None,
                "episode_id": None,
                "episode": None,
                "current_assessment": r["current_assessment"],
            }
        )
    return items


def _evaluate_direct(direct_evidence: list[dict], min_depth: str, min_autonomy: str | None) -> tuple[str | None, list[dict]]:
    evaluated = []
    any_accepted = any_unreviewed = fully_met_accepted = False
    for item in direct_evidence:
        meets_depth = depth_meets(item["depth"], min_depth)
        meets_autonomy = autonomy_meets(item["autonomy"], min_autonomy)
        evaluated.append({**item, "meets_depth": meets_depth, "meets_autonomy": meets_autonomy})
        if item["review_status"] == "accepted":
            any_accepted = True
            fully_met_accepted = fully_met_accepted or (meets_depth and meets_autonomy)
        elif item["review_status"] == "unreviewed":
            any_unreviewed = True
    if fully_met_accepted:
        return "evidenced", evaluated
    if any_accepted or any_unreviewed:
        return "partial", evaluated
    return None, evaluated


# --- Compositional evidence (brief §10/§12) ---------------------------------

def _compositional_evidence(cur, components: dict[str, list[dict]]) -> dict:
    core, supporting, contextual = components["core"], components["supporting"], components["contextual"]
    all_components = core + supporting + contextual
    if not all_components:
        return {"has_components": False, "episodes": [], "best": None, "all_claim_rows": []}

    core_ids = {c["concept_id"] for c in core}
    supporting_ids = {c["concept_id"] for c in supporting}
    contextual_ids = {c["concept_id"] for c in contextual}
    concept_names = {c["concept_id"]: c["canonical_name"] for c in all_components}

    cur.execute(
        """
        SELECT m.jobber_concept_id AS concept_id, cl.id AS claim_id, cl.claim_text,
               cl.depth AS raw_depth, cl.episode_id
        FROM jobber.profile360_claim_mapping m
        JOIN profile360.claims cl ON cl.id = m.profile360_claim_id
        WHERE m.review_status = 'accepted' AND m.jobber_concept_id = ANY(%s::uuid[])
        """,
        ([c["concept_id"] for c in all_components],),
    )
    rows = []
    for r in cur.fetchall():
        r = dict(r)
        r["concept_id"] = str(r["concept_id"])
        r["claim_id"] = str(r["claim_id"])
        r["episode_id"] = str(r["episode_id"]) if r["episode_id"] else None
        rows.append(r)

    by_episode: dict[str, list[dict]] = {}
    ungrouped_claim_ids = []
    for r in rows:
        if r["episode_id"] is None:
            # No episode to group by -> cannot participate in "within an
            # episode" composition (brief §10). Still real accepted evidence,
            # so its claim id is still surfaced via ungrouped_claim_ids.
            ungrouped_claim_ids.append(r["claim_id"])
            continue
        by_episode.setdefault(r["episode_id"], []).append(r)

    episodes_by_id = _episodes_by_id(cur, list(by_episode.keys()))

    episode_summaries = []
    for episode_id, ep_rows in by_episode.items():
        met_ids = {r["concept_id"] for r in ep_rows}
        core_met, supporting_met, contextual_met = met_ids & core_ids, met_ids & supporting_ids, met_ids & contextual_ids
        episode = episodes_by_id.get(episode_id, {})
        episode_summaries.append(
            {
                "episode_id": episode_id,
                "episode": episode,
                "claim_ids": [r["claim_id"] for r in ep_rows],
                "depths": [normalize_depth(r["raw_depth"]) for r in ep_rows],
                "core_met": sorted(concept_names[i] for i in core_met),
                "core_missing": sorted(concept_names[i] for i in core_ids - core_met),
                "supporting_met": sorted(concept_names[i] for i in supporting_met),
                "supporting_missing": sorted(concept_names[i] for i in supporting_ids - supporting_met),
                "contextual_met": sorted(concept_names[i] for i in contextual_met),
                "contextual_missing": sorted(concept_names[i] for i in contextual_ids - contextual_met),
                "core_met_count": len(core_met),
                "supporting_met_count": len(supporting_met),
                "contextual_met_count": len(contextual_met),
            }
        )

    def _sort_key(summary):
        end = _effective_end(summary["episode"])
        return (summary["core_met_count"], summary["supporting_met_count"], summary["contextual_met_count"], end or date.min)

    episode_summaries.sort(key=_sort_key, reverse=True)

    return {
        "has_components": True,
        "core_total": len(core_ids),
        "supporting_total": len(supporting_ids),
        "contextual_total": len(contextual_ids),
        "episodes": episode_summaries,
        "best": episode_summaries[0] if episode_summaries else None,
        "all_claim_rows": rows,
        "ungrouped_claim_ids": ungrouped_claim_ids,
    }


def _core_required(capability_row: dict, core_total: int) -> int:
    """The deterministic completeness rule for capability_detail.
    requires_all_core=false (brief §12): a curated `min_core_required` count
    if the curator set one, else the documented default of "at least one
    core component" — transparent, no invented percentage."""
    if capability_row["requires_all_core"]:
        return core_total
    if capability_row.get("min_core_required") is not None:
        return max(0, min(int(capability_row["min_core_required"]), core_total))
    return min(1, core_total)


def _composition_verdict(capability_row: dict, comp: dict) -> dict:
    if not comp["has_components"]:
        return {"meaningful": False, "core_complete": True, "core_required": 0, "reason": "no components are curated for this capability"}

    best = comp["best"]
    core_total = comp["core_total"]
    required = _core_required(capability_row, core_total)

    if best is None:
        return {"meaningful": False, "core_complete": core_total == 0, "core_required": required, "reason": "no accepted component evidence found"}

    if core_total > 0:
        # doc11 §12.6: core components are what makes a capability claim
        # compositionally plausible at all — supporting/contextual evidence
        # alone (zero core components touched) must never be "meaningful".
        meaningful = best["core_met_count"] >= 1
        core_complete = best["core_met_count"] >= required
    else:
        meaningful = best["supporting_met_count"] > 0 or best["contextual_met_count"] > 0
        core_complete = True

    return {"meaningful": meaningful, "core_complete": core_complete, "core_required": required}


# --- Coverage combiner (brief §11) ------------------------------------------

def derive_capability_coverage(cur, capability_concept_id: str) -> dict:
    """Pure derivation — no writes. Raises CapabilityNotFoundError if
    `capability_concept_id` is not an active capability concept with a
    capability_detail row."""
    capability_row = _capability_row(cur, capability_concept_id)
    if capability_row is None:
        raise CapabilityNotFoundError(capability_concept_id)

    min_depth = capability_row["min_depth"]
    min_autonomy = capability_row["min_autonomy"]

    direct_evidence = _direct_evidence(cur, capability_concept_id)
    direct_status, direct_evaluated = _evaluate_direct(direct_evidence, min_depth, min_autonomy)

    components = _components(cur, capability_concept_id)
    comp = _compositional_evidence(cur, components)
    comp_verdict = _composition_verdict(capability_row, comp)

    assertion = _assertion(cur, capability_concept_id)

    if direct_status == "evidenced":
        status, reason_code = "evidenced", "direct_evidenced"
    elif direct_status == "partial":
        status, reason_code = "partial", "direct_unmet_or_unreviewed"
    elif comp_verdict["meaningful"]:
        status, reason_code = "partial", "compositional"
    elif assertion is not None:
        status, reason_code = "user_asserted", "user_asserted"
    else:
        status, reason_code = "not_found", "not_found"

    message = _status_message(
        status, reason_code, capability_row, direct_evaluated, comp, comp_verdict, min_depth, min_autonomy
    )

    # strongest_depth/strongest_autonomy: informational, over *accepted*
    # evidence only (direct accepted + all compositional, which is
    # accepted-only by construction) — never blends in unreviewed material.
    strongest_depth = None
    strongest_autonomy = None
    for item in direct_evaluated:
        if item["review_status"] != "accepted":
            continue
        strongest_depth = _stronger(DEPTH_LEVELS, strongest_depth, item["depth"])
        strongest_autonomy = _stronger(AUTONOMY_LEVELS, strongest_autonomy, item["autonomy"])
    if comp["has_components"]:
        for ep in comp["episodes"]:
            for d in ep["depths"]:
                strongest_depth = _stronger(DEPTH_LEVELS, strongest_depth, d)
            ep_autonomy = normalize_autonomy((ep["episode"] or {}).get("autonomy"))
            strongest_autonomy = _stronger(AUTONOMY_LEVELS, strongest_autonomy, ep_autonomy)

    qualifying_episodes = [item["episode"] for item in direct_evaluated if item.get("episode")]
    if comp["has_components"]:
        qualifying_episodes += [ep["episode"] for ep in comp["episodes"] if ep["episode"]]

    claim_ids = {item["profile360_claim_id"] for item in direct_evaluated if item.get("profile360_claim_id")}
    if comp["has_components"]:
        claim_ids |= {r["claim_id"] for r in comp["all_claim_rows"]}
        claim_ids |= set(comp["ungrouped_claim_ids"])

    core_total = comp["core_total"] if comp["has_components"] else 0
    core_met = comp["best"]["core_met_count"] if comp["has_components"] and comp["best"] else 0

    trace = {
        "capability": {"id": capability_row["id"], "canonical_name": capability_row["canonical_name"]},
        "requirement": {
            "min_depth": min_depth,
            "min_autonomy": min_autonomy,
            "requires_all_core": capability_row["requires_all_core"],
            "min_core_required": capability_row.get("min_core_required"),
        },
        "direct_evidence": [
            {k: v for k, v in item.items() if k != "episode"} for item in direct_evaluated
        ],
        "compositional": (
            {
                "core_total": comp["core_total"],
                "supporting_total": comp["supporting_total"],
                "contextual_total": comp["contextual_total"],
                "best_episode": (
                    {k: v for k, v in comp["best"].items() if k not in ("episode", "claim_ids")}
                    if comp["best"]
                    else None
                ),
                "episodes_considered": len(comp["episodes"]),
                "core_complete": comp_verdict["core_complete"],
                "core_required": comp_verdict["core_required"],
                "meaningful": comp_verdict["meaningful"],
            }
            if comp["has_components"]
            else {"core_total": 0, "supporting_total": 0, "contextual_total": 0, "note": comp_verdict["reason"]}
        ),
        "assertion": assertion,
        "status_reason": {"code": reason_code, "message": message},
    }

    return {
        "capability_concept_id": capability_row["id"],
        "status": status,
        "coverage_score": {"evidenced": 1.0, "partial": 0.5, "user_asserted": 0.25, "not_found": 0.0}[status],
        "core_components_total": core_total,
        "core_components_met": core_met,
        "strongest_depth": strongest_depth,
        "strongest_autonomy": strongest_autonomy,
        "directly_claimed": len(direct_evidence) > 0,
        "last_demonstrated": last_demonstrated(qualifying_episodes),
        "years_active": union_years_active(qualifying_episodes),
        "supporting_profile360_claim_ids": sorted(claim_ids),
        "trace": trace,
    }


def _status_message(status, reason_code, capability_row, direct_evaluated, comp, comp_verdict, min_depth, min_autonomy) -> str:
    name = capability_row["canonical_name"]
    if reason_code == "direct_evidenced":
        return f"Evidence directly states “{name}” and meets its demonstration standard."
    if reason_code == "direct_unmet_or_unreviewed":
        accepted_unmet = [i for i in direct_evaluated if i["review_status"] == "accepted" and not (i["meets_depth"] and i["meets_autonomy"])]
        if accepted_unmet:
            best = max(accepted_unmet, key=lambda i: (DEPTH_LEVELS.index(i["depth"]) if i["depth"] else -1))
            shortfall = []
            if not best["meets_depth"]:
                shortfall.append(f"requires depth ≥ {min_depth}, strongest evidence found is {best['depth'] or 'unknown'}")
            if not best["meets_autonomy"]:
                shortfall.append(f"requires autonomy ≥ {min_autonomy}, strongest evidence found is {best['autonomy'] or 'unknown'}")
            return f"Evidence directly names “{name}” but the modifier threshold is unmet: {'; '.join(shortfall)}."
        return f"Evidence directly names “{name}” but has not yet been reviewed."
    if reason_code == "compositional":
        best = comp["best"]
        parts = [f"{best['core_met_count']}/{comp['core_total']} core component(s) found together in one episode"]
        if best["core_missing"]:
            parts.append(f"missing: {', '.join(best['core_missing'])}")
        return f"No direct evidence for “{name}”, but {'; '.join(parts)} — compositional evidence only, so this is not counted as fully evidenced."
    if reason_code == "user_asserted":
        return f"No accepted evidence currently shows “{name}” — the user has asserted it without supporting evidence."
    return f"No accepted evidence currently shows “{name}”."


def derive_all_capability_coverage(cur) -> list[dict]:
    cur.execute("SELECT id FROM jobber.concept WHERE type_code = 'capability' AND status = 'active' ORDER BY canonical_name")
    ids = [str(r["id"]) for r in cur.fetchall()]
    results = []
    for capability_id in ids:
        try:
            results.append(derive_capability_coverage(cur, capability_id))
        except CapabilityNotFoundError:
            continue
    return results


def _persist_capability_coverage(cur, row: dict, vocabulary_version_id: str | None) -> None:
    cur.execute(
        """
        INSERT INTO jobber.d_capability_coverage (
            capability_concept_id, status, coverage_score, core_components_total, core_components_met,
            strongest_depth, strongest_autonomy, directly_claimed, last_demonstrated, years_active,
            supporting_profile360_claim_ids, trace, vocabulary_version_id, engine_version, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid[], %s, %s, %s, now())
        ON CONFLICT (capability_concept_id) DO UPDATE SET
            status = EXCLUDED.status, coverage_score = EXCLUDED.coverage_score,
            core_components_total = EXCLUDED.core_components_total, core_components_met = EXCLUDED.core_components_met,
            strongest_depth = EXCLUDED.strongest_depth, strongest_autonomy = EXCLUDED.strongest_autonomy,
            directly_claimed = EXCLUDED.directly_claimed, last_demonstrated = EXCLUDED.last_demonstrated,
            years_active = EXCLUDED.years_active, supporting_profile360_claim_ids = EXCLUDED.supporting_profile360_claim_ids,
            trace = EXCLUDED.trace, vocabulary_version_id = EXCLUDED.vocabulary_version_id,
            engine_version = EXCLUDED.engine_version, computed_at = now()
        """,
        (
            row["capability_concept_id"], row["status"], row["coverage_score"],
            row["core_components_total"], row["core_components_met"],
            row["strongest_depth"], row["strongest_autonomy"], row["directly_claimed"],
            row["last_demonstrated"], row["years_active"], row["supporting_profile360_claim_ids"],
            to_json_param(row["trace"]), vocabulary_version_id, ENGINE_VERSION,
        ),
    )


def rebuild_capability_coverage(cur) -> dict:
    vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
    rows = derive_all_capability_coverage(cur)
    for row in rows:
        _persist_capability_coverage(cur, row, vocabulary_version_id)
    kept_ids = [row["capability_concept_id"] for row in rows]
    if kept_ids:
        cur.execute("DELETE FROM jobber.d_capability_coverage WHERE capability_concept_id != ALL(%s::uuid[])", (kept_ids,))
    else:
        cur.execute("DELETE FROM jobber.d_capability_coverage")
    return {"computed": len(rows), "removed_stale": cur.rowcount, "engine_version": ENGINE_VERSION}


# --- Role fit (brief §15/§16/§17/§18) ---------------------------------------

_STATUS_POINTS = {"evidenced": 1.0, "partial": 0.5, "user_asserted": 0.25, "not_found": 0.0}
_REQUIREMENT_WEIGHT = {"required": 2.0, "preferred": 1.0, "contextual": 0.5}


def atomic_concept_evidence(cur, concept_id: str) -> dict:
    """Direct-mapping status for a non-capability concept requirement (brief
    §16: "Continue to surface direct concept evidence where appropriate...
    Do not pretend every atomic requirement is automatically a capability").
    Same evidenced > partial > user_asserted > not_found ordering as the
    capability path, reused rather than duplicated (brief §11)."""
    cur.execute(
        """
        SELECT id, profile360_claim_id AS profile360_id, review_status, mapping_basis, 'claim' AS mapping_kind
        FROM jobber.profile360_claim_mapping WHERE jobber_concept_id = %s
        UNION ALL
        SELECT id, profile360_capability_id AS profile360_id, review_status, mapping_basis, 'capability' AS mapping_kind
        FROM jobber.profile360_capability_mapping WHERE jobber_capability_concept_id = %s
        """,
        (concept_id, concept_id),
    )
    mappings = [dict(r) for r in cur.fetchall()]
    for m in mappings:
        m["id"] = str(m["id"])
        m["profile360_id"] = str(m["profile360_id"])
        try:
            source = p360.get_claim(cur, m["profile360_id"]) if m["mapping_kind"] == "claim" else p360.get_capability(cur, m["profile360_id"])
            m["display"] = p360.display_text(source) if source else None
        except p360.Profile360UnavailableError:
            m["display"] = None

    assertion = _assertion(cur, concept_id)
    context = capabilities_containing(cur, concept_id)

    accepted = [m for m in mappings if m["review_status"] == "accepted"]
    if accepted:
        return {"status": "evidenced", "mappings": accepted, "assertion": None, "component_of": context}
    pending = [m for m in mappings if m["review_status"] == "unreviewed"]
    if pending:
        return {"status": "partial", "mappings": pending, "assertion": None, "component_of": context}
    if assertion:
        return {"status": "user_asserted", "mappings": [], "assertion": assertion, "component_of": context}
    return {"status": "not_found", "mappings": [], "assertion": None, "component_of": context}


def _fit_score(items: list[dict]) -> float | None:
    """Secondary, transparent signal only (brief §18) — never trained, never
    an AI judgment. score = weighted mean of per-item status points, weight
    by requirement_type (required counts twice a preferred, four times a
    contextual). Documented here and in docs/16; never shown as the
    dominant result in the UI."""
    if not items:
        return None
    numerator = denominator = 0.0
    for item in items:
        weight = _REQUIREMENT_WEIGHT.get(item["requirement_type"], 1.0)
        numerator += _STATUS_POINTS[item["status"]] * weight
        denominator += weight
    return round(numerator / denominator, 4) if denominator else None


def derive_role_fit(cur, role_instance_id: str) -> dict:
    cur.execute("SELECT id FROM jobber.role_instance WHERE id = %s", (role_instance_id,))
    role = cur.fetchone()
    if role is None:
        raise RoleInstanceNotFoundError(role_instance_id)

    cur.execute(
        """
        SELECT rc.id, rc.requirement_type, rc.basis, rc.review_status, rc.evidence_span,
               c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.requirement_claim rc
        JOIN jobber.concept c ON c.id = rc.concept_id
        WHERE rc.role_instance_id = %s AND rc.superseded_by IS NULL
        ORDER BY rc.requirement_type, c.canonical_name
        """,
        (role_instance_id,),
    )
    requirement_rows = [dict(r) for r in cur.fetchall()]

    items = []
    counts = {"evidenced": 0, "partial": 0, "user_asserted": 0, "not_found": 0}
    blocking_gaps, unverified_required = [], []

    for rc in requirement_rows:
        concept_id = str(rc["concept_id"])
        concept = {"id": concept_id, "canonical_name": rc["canonical_name"], "type_code": rc["type_code"]}
        if rc["type_code"] == "capability":
            coverage = derive_capability_coverage(cur, concept_id)
            status = coverage["status"]
            detail = {"kind": "capability", "coverage": coverage}
        else:
            person = atomic_concept_evidence(cur, concept_id)
            status = person["status"]
            detail = {"kind": "concept", **person}

        counts[status] += 1
        items.append(
            {
                "requirement_claim_id": str(rc["id"]),
                "concept": concept,
                "requirement_type": rc["requirement_type"],
                "role_side": {
                    "basis": rc["basis"],
                    "review_status": rc["review_status"],
                    "evidence_span": rc["evidence_span"],
                },
                "status": status,
                "detail": detail,
            }
        )

        if rc["requirement_type"] == "required":
            if status == "not_found":
                blocking_gaps.append(concept)
            elif status in ("partial", "user_asserted"):
                unverified_required.append({**concept, "status": status})

    _, profile_vec = ensure_profile_embedding(cur)
    role_vec = get_embedding(cur, "role_instance", role_instance_id)
    embedding_similarity = cosine_similarity(profile_vec, role_vec) if profile_vec and role_vec else None

    return {
        "role_instance_id": str(role["id"]),
        "capabilities_required": len(items),
        "n_evidenced": counts["evidenced"],
        "n_partial": counts["partial"],
        "n_asserted": counts["user_asserted"],
        "n_not_found": counts["not_found"],
        "blocking_gaps": blocking_gaps,
        "unverified_required": unverified_required,
        "fit_score": _fit_score(items),
        "embedding_similarity": embedding_similarity,
        "trace": {"items": items},
    }


def derive_all_role_fit(cur) -> list[dict]:
    cur.execute("SELECT id FROM jobber.role_instance")
    ids = [str(r["id"]) for r in cur.fetchall()]
    return [derive_role_fit(cur, role_id) for role_id in ids]


def _persist_role_fit(cur, row: dict, vocabulary_version_id: str | None) -> None:
    cur.execute(
        """
        INSERT INTO jobber.d_role_fit (
            role_instance_id, capabilities_required, n_evidenced, n_partial, n_asserted, n_not_found,
            blocking_gaps, unverified_required, fit_score, embedding_similarity, trace,
            vocabulary_version_id, engine_version, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (role_instance_id) DO UPDATE SET
            capabilities_required = EXCLUDED.capabilities_required, n_evidenced = EXCLUDED.n_evidenced,
            n_partial = EXCLUDED.n_partial, n_asserted = EXCLUDED.n_asserted, n_not_found = EXCLUDED.n_not_found,
            blocking_gaps = EXCLUDED.blocking_gaps, unverified_required = EXCLUDED.unverified_required,
            fit_score = EXCLUDED.fit_score, embedding_similarity = EXCLUDED.embedding_similarity,
            trace = EXCLUDED.trace, vocabulary_version_id = EXCLUDED.vocabulary_version_id,
            engine_version = EXCLUDED.engine_version, computed_at = now()
        """,
        (
            row["role_instance_id"], row["capabilities_required"], row["n_evidenced"], row["n_partial"],
            row["n_asserted"], row["n_not_found"], to_json_param(row["blocking_gaps"]),
            to_json_param(row["unverified_required"]), row["fit_score"], row["embedding_similarity"],
            to_json_param(row["trace"]), vocabulary_version_id, ENGINE_VERSION,
        ),
    )


def rebuild_role_fit(cur) -> dict:
    vocabulary_version_id = get_or_create_current_vocabulary_version(cur)
    rows = derive_all_role_fit(cur)
    for row in rows:
        _persist_role_fit(cur, row, vocabulary_version_id)
    kept_ids = [row["role_instance_id"] for row in rows]
    if kept_ids:
        cur.execute("DELETE FROM jobber.d_role_fit WHERE role_instance_id != ALL(%s::uuid[])", (kept_ids,))
    else:
        cur.execute("DELETE FROM jobber.d_role_fit")
    return {"computed": len(rows), "removed_stale": cur.rowcount, "engine_version": ENGINE_VERSION}


def rebuild_phase3_derivations(cur) -> dict:
    """Safe to repeat (brief §20): recomputes fresh from source/mapping/
    catalogue rows, replaces stale rows, deletes derived rows for deleted/
    deactivated capabilities/roles, stamps one engine_version and the
    current vocabulary version. Re-running against unchanged source data
    produces semantically identical results (proven by
    test_capability_engine.py::test_rebuild_is_idempotent)."""
    return {
        "engine_version": ENGINE_VERSION,
        "capability_coverage": rebuild_capability_coverage(cur),
        "role_fit": rebuild_role_fit(cur),
    }
