"""Transparent, evidence-led candidate ranking; never predicts hiring or ability."""

import logging
from time import perf_counter
from fastapi.encoders import jsonable_encoder

from .capability_engine import atomic_concept_evidence, derive_capability_coverage
from .db import to_json_param
from .embeddings import cosine_similarity, get_embeddings
from .role_requirements import load_role_requirements_bulk, load_requirement_review_summary_bulk
from .target_mapping import target_mapping_summary
from .target_cache import revisions, cached_statuses, save_status

logger = logging.getLogger(__name__)


def measured(result, started, cache_hit, evaluated):
    result["metrics"] = {"cache_hit": cache_hit, "candidates": result["candidates_assessed"],
                         "distinct_concepts": result["distinct_concepts"], "concepts_evaluated": evaluated,
                         "elapsed_ms": round((perf_counter() - started) * 1000, 2)}
    logger.info("target_path_analysis %s", result["metrics"])
    return result


def assess_candidate(requirements, target_requirements, status_by_concept, pending=0, target_pending=0):
    """`requirements`/`target_requirements` come from the canonical
    `role_requirements` loader, which only ever returns *usable* evidence
    (accepted claims + fallback observations) — an unreviewed claim never
    appears in these lists at all, so it cannot be detected by inspecting
    row content. `pending`/`target_pending` (current unreviewed-claim counts
    from `role_requirements.load_requirement_review_summary_bulk`) are how
    the caller tells this function "review is incomplete" instead —
    preserved as an explicit gate below so a partially-reviewed role's
    requirement set is never silently treated as final."""
    # Repeated claims do not give a role extra weight. A required occurrence wins.
    unique = {}
    for row in requirements:
        key = row["concept_id"]
        if key not in unique or row["requirement_type"] == "required":
            unique[key] = row
    reviewed = list(unique.values())
    target = {r["concept_id"]: r for r in target_requirements}
    target_gaps = {k for k in target if status_by_concept.get(k) != "evidenced"}
    required = [r for r in reviewed if r["requirement_type"] == "required"]
    missing = [r["canonical_name"] for r in required if status_by_concept.get(r["concept_id"]) == "not_found"]
    unverified = [r["canonical_name"] for r in required if status_by_concept.get(r["concept_id"]) in ("partial", "user_asserted")]
    evidenced = sum(status_by_concept.get(r["concept_id"]) == "evidenced" for r in reviewed)
    shared = [r["canonical_name"] for r in reviewed if r["concept_id"] in target_gaps]
    coverage = evidenced / len(reviewed) if reviewed else None
    progress = len(shared) / len(target_gaps) if target_gaps else None
    target_required_gaps = sum(r["requirement_type"] == "required" and status_by_concept.get(k) != "evidenced"
                               for k, r in target.items())
    target_coverage = sum(status_by_concept.get(k) == "evidenced" for k in target) / len(target) if target else None
    candidate_required_gaps = len(missing) + len(unverified)
    easier_than_target = (candidate_required_gaps < target_required_gaps or
                          (candidate_required_gaps <= target_required_gaps and coverage is not None
                           and target_coverage is not None and coverage > target_coverage))
    if not reviewed or not required or not target or pending or target_pending:
        state = "insufficient_evidence"
        reason = "Review candidate and target requirements, including which are required, before assessing this step."
    elif not target_gaps:
        state = "target_evidenced"
        reason = "Your evidence already covers the mapped target requirements; no development step is inferred."
    elif not shared:
        state = "no_target_progress"
        reason = "This role does not share any of the target requirements still needing evidence."
    elif not evidenced:
        state = "needs_evidence"
        reason = "No accepted evidence currently supports this role's requirements; reachability is not established."
    elif not easier_than_target:
        state = "not_an_intermediate_step"
        reason = "The evidence does not show a more reachable intermediate step than the target itself."
    else:
        state = "potential_step"
        reason = f"Evidence supports {evidenced}/{len(reviewed)} requirements; this role involves {len(shared)} target evidence gap(s)."
    # Harmonic mean rewards balance: neither target relevance nor coverage alone suffices.
    score = 2 * coverage * progress / (coverage + progress) if coverage and progress else 0.0
    return {
        "assessment": state, "explanation": reason, "ranking_score": round(score, 4),
        "evidenced_requirements": evidenced, "requirements_total": len(reviewed),
        "evidence_coverage": coverage, "target_gap_coverage": progress,
        "missing_required": missing, "unverified_required": unverified,
        "target_gaps_addressed": shared, "pending_requirements": pending,
        "target_pending_requirements": target_pending,
        "target_required_evidence_gaps": target_required_gaps,
        "legacy_requirements": sum(r["source"] == "role_skill_observation" for r in reviewed),
    }


def assess_all_candidates(cur, target_id, target_vec, profile_vec, evidence_revision):
    """Every observed posting assessed against one target, with the shared
    bulk loads done once: requirement evidence and review summaries for the
    target plus every candidate in two queries, embeddings in one, and each
    distinct concept's evidence status evaluated at most once per request
    (cached across requests by revision in `jobber.d_target_evidence`).

    Extracted from `path_to_target` so Pathways (app/pathways.py) can group
    the *full* ranked list by reviewed archetype without either duplicating
    this loading or re-deriving fit per candidate — `path_to_target` still
    slices its own top 5 out of the same list. Returns the ranked candidates
    plus the shared artefacts a caller needs to interpret them (target
    requirement mapping, per-concept evidence statuses, the target's own
    pending-review count) rather than only the ranking, so nothing
    downstream has to re-query for them."""
    cur.execute("SELECT id, title, organisation, career_track, posting_date, archetype_concept_id "
                "FROM jobber.role_instance "
                "WHERE instance_type = 'observed_posting' AND id != %s", (target_id,))
    candidates = cur.fetchall()
    ids = [str(c["id"]) for c in candidates]
    requirements = load_role_requirements_bulk(cur, [target_id, *ids])
    review_summary = load_requirement_review_summary_bulk(cur, [target_id, *ids])
    vectors = get_embeddings(cur, "role_instance", ids)
    # Evaluate each distinct concept once per request, using the same engine as Comparison.
    concepts = {r["concept_id"] for rows in requirements.values() for r in rows}
    statuses = cached_statuses(cur, evidence_revision, list(concepts))
    evaluated = 0
    for rows in requirements.values():
        for row in rows:
            key = row["concept_id"]
            if key not in statuses:
                if row["concept_status"] != "active":
                    statuses[key] = "not_found"
                    continue
                evidence = (derive_capability_coverage(cur, key) if row["type_code"] == "capability"
                            else atomic_concept_evidence(cur, key))
                statuses[key] = evidence["status"]
                save_status(cur, evidence_revision, key, evidence["status"])
                evaluated += 1
    mapping = target_mapping_summary(cur, target_id, requirements.get(target_id, []))
    target_pending = review_summary.get(target_id, {}).get("unreviewed", 0)
    ranked = []
    for c in candidates:
        key = str(c["id"])
        vector = vectors.get(key, [])
        candidate_pending = review_summary.get(key, {}).get("unreviewed", 0)
        assessment = assess_candidate(requirements.get(key, []), requirements.get(target_id, []), statuses,
                                       pending=candidate_pending, target_pending=target_pending)
        if not mapping["complete"]:
            assessment.update(assessment="incomplete_target_mapping", ranking_score=0,
                              explanation="Target requirements are unmapped or excluded. Resolve these before interpreting readiness or intermediate steps.")
        candidate = {**dict(c), "id": key, **assessment,
                     "similarity_to_target": cosine_similarity(target_vec, vector),
                     "similarity_to_profile": cosine_similarity(profile_vec, vector) if profile_vec else None}
        candidate["archetype_concept_id"] = str(c["archetype_concept_id"]) if c["archetype_concept_id"] else None
        ranked.append(candidate)
    ranked.sort(key=lambda r: (
        r["assessment"] != "potential_step", -r["ranking_score"],
        len(r["missing_required"]), len(r["unverified_required"]),
        -(r["similarity_to_profile"] or 0), -(r["similarity_to_target"] or 0), r["id"],
    ))
    return {
        "ranked": ranked, "target_mapping": mapping, "statuses": statuses,
        "target_requirements": requirements.get(target_id, []),
        "target_pending_requirements": target_pending, "distinct_concepts": len(concepts),
        "concepts_evaluated": evaluated,
    }


def path_to_target(cur, target_id, target_vec, profile_vec):
    started = perf_counter()
    evidence_revision, path_revision = revisions(cur, target_vec, profile_vec)
    cur.execute("SELECT result FROM jobber.d_target_path WHERE role_id = %s AND revision = %s", (target_id, path_revision))
    cached = cur.fetchone()
    if cached:
        return measured(dict(cached["result"]), started, True, 0)
    assessed = assess_all_candidates(cur, target_id, target_vec, profile_vec, evidence_revision)
    ranked, evaluated = assessed["ranked"], assessed["concepts_evaluated"]
    result = {
        "profile_to_target_similarity": cosine_similarity(profile_vec, target_vec) if profile_vec else None,
        "stepping_stones": ranked[:5], "candidates_assessed": len(ranked),
        "target_mapping": assessed["target_mapping"], "distinct_concepts": assessed["distinct_concepts"],
        "method": "Evidence coverage balanced with coverage of target evidence gaps; similarity breaks ties. "
                  "A role involving a gap is an opportunity to develop it, not proof you will acquire it. "
                  "Historical postings describe role patterns, not confirmed vacancies.",
    }
    result = jsonable_encoder(result)
    cur.execute("""INSERT INTO jobber.d_target_path (role_id, revision, result) VALUES (%s, %s, %s)
                   ON CONFLICT (role_id) DO UPDATE SET revision = EXCLUDED.revision,
                   result = EXCLUDED.result, prepared_at = now()""", (target_id, path_revision, to_json_param(result)))
    return measured(result, started, False, evaluated)
