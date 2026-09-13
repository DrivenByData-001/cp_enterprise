"""Transparent, evidence-led candidate ranking; never predicts hiring or ability."""

from .capability_engine import atomic_concept_evidence, derive_capability_coverage
from .embeddings import cosine_similarity, get_embeddings
from .role_requirements import load_role_requirements_bulk


def assess_candidate(requirements, target_requirements, status_by_concept):
    # Repeated claims do not give a role extra weight. A required occurrence wins.
    unique = {}
    for row in requirements:
        key = row["concept_id"]
        if key not in unique or row["requirement_type"] == "required":
            unique[key] = row
    reviewed = [r for r in unique.values() if r["review_status"] != "unreviewed"]
    target = {r["concept_id"]: r for r in target_requirements if r["review_status"] != "unreviewed"}
    target_gaps = {k for k in target if status_by_concept.get(k) != "evidenced"}
    required = [r for r in reviewed if r["requirement_type"] == "required"]
    missing = [r["canonical_name"] for r in required if status_by_concept.get(r["concept_id"]) == "not_found"]
    unverified = [r["canonical_name"] for r in required if status_by_concept.get(r["concept_id"]) in ("partial", "user_asserted")]
    evidenced = sum(status_by_concept.get(r["concept_id"]) == "evidenced" for r in reviewed)
    shared = [r["canonical_name"] for r in reviewed if r["concept_id"] in target_gaps]
    coverage = evidenced / len(reviewed) if reviewed else None
    progress = len(shared) / len(target_gaps) if target_gaps else None
    pending = sum(r["review_status"] == "unreviewed" for r in requirements)
    target_pending = sum(r["review_status"] == "unreviewed" for r in target_requirements)
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


def path_to_target(cur, target_id, target_vec, profile_vec):
    cur.execute("SELECT id, title, organisation, career_track, posting_date FROM jobber.role_instance "
                "WHERE instance_type = 'observed_posting' AND id != %s", (target_id,))
    candidates = cur.fetchall()
    ids = [str(c["id"]) for c in candidates]
    requirements = load_role_requirements_bulk(cur, [target_id, *ids])
    vectors = get_embeddings(cur, "role_instance", ids)
    # Evaluate each distinct concept once per request, using the same engine as Comparison.
    statuses = {}
    for rows in requirements.values():
        for row in rows:
            key = row["concept_id"]
            if key not in statuses:
                evidence = (derive_capability_coverage(cur, key) if row["type_code"] == "capability"
                            else atomic_concept_evidence(cur, key))
                statuses[key] = evidence["status"]
    ranked = []
    for c in candidates:
        key = str(c["id"])
        vector = vectors.get(key, [])
        assessment = assess_candidate(requirements.get(key, []), requirements.get(target_id, []), statuses)
        ranked.append({**dict(c), "id": key, **assessment,
                       "similarity_to_target": cosine_similarity(target_vec, vector),
                       "similarity_to_profile": cosine_similarity(profile_vec, vector) if profile_vec else None})
    ranked.sort(key=lambda r: (
        r["assessment"] != "potential_step", -r["ranking_score"],
        len(r["missing_required"]), len(r["unverified_required"]),
        -(r["similarity_to_profile"] or 0), -(r["similarity_to_target"] or 0), r["id"],
    ))
    return {
        "profile_to_target_similarity": cosine_similarity(profile_vec, target_vec) if profile_vec else None,
        "stepping_stones": ranked[:5], "candidates_assessed": len(ranked),
        "method": "Evidence coverage balanced with coverage of target evidence gaps; similarity breaks ties. "
                  "A role involving a gap is an opportunity to develop it, not proof you will acquire it. "
                  "Historical postings describe role patterns, not confirmed vacancies.",
    }
