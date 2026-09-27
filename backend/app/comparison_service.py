"""Reusable role-comparison assembly (Phase 3 application-workspace build
§4/§14). Extracted from `routes/comparison.py`'s `compare_role` handler
verbatim so `/api/comparison/role/{id}` and `/api/applications/{id}/evidence`
(routes/applications.py) derive their structural picture from the exact same
comparison semantics — never two independently-maintained assemblers. The
existing Comparison API's response contract is unchanged: `compare_role` now
just calls `build_role_comparison` and returns its result directly.

Status logic itself still lives only in `capability_engine` (brief §11); this
module is presentation, same division of responsibility as before.
"""

from fastapi import HTTPException

from . import capability_engine
from .db import instance_type_to_app_kind
from .role_requirements import load_requirement_review_summary, load_role_requirements
from .target_mapping import target_mapping_summary


def _requirement_documents(cur, requirement_claim_ids: list[str]) -> dict[str, dict | None]:
    """Role-side document detail per requirement_claim — kept as a small,
    separate query rather than folded into capability_engine.derive_role_fit,
    since that engine is deliberately jobber.document-agnostic (role-fit
    status never depends on which document a requirement came from)."""
    if not requirement_claim_ids:
        return {}
    cur.execute(
        """
        SELECT rc.id AS requirement_claim_id, d.id AS document_id, d.title, d.provenance_quality, d.url
        FROM jobber.requirement_claim rc
        LEFT JOIN jobber.document d ON d.id = rc.document_id
        WHERE rc.id = ANY(%s::uuid[])
        """,
        (requirement_claim_ids,),
    )
    out: dict[str, dict | None] = {}
    for r in cur.fetchall():
        out[str(r["requirement_claim_id"])] = (
            {"id": str(r["document_id"]), "title": r["title"], "provenance": r["provenance_quality"], "url": r["url"]}
            if r["document_id"]
            else None
        )
    return out


def build_role_comparison(cur, role_instance_id: str) -> dict:
    """The structural comparison/evidence-readiness picture for one role:
    per-requirement status, blocking/unverified gaps, and counts — everything
    both the Comparison page and the Application evidence pack need. Takes an
    already-open cursor (rather than opening its own `db_cursor()`) so a
    caller that needs to do more in the same transaction — the Application
    evidence pack also loads application-local notes — never pays for a
    second connection checkout. Raises HTTPException(404) if the role
    doesn't exist, exactly as the pre-extraction inline version did."""
    cur.execute(
        "SELECT id, title, instance_type, target_basis FROM jobber.role_instance WHERE id = %s",
        (role_instance_id,),
    )
    role = cur.fetchone()
    if not role:
        raise HTTPException(404, "role_instance not found")
    role = {
        "id": str(role["id"]),
        "title": role["title"],
        "kind": instance_type_to_app_kind(role["instance_type"], role["target_basis"]),
    }
    mapping = (target_mapping_summary(cur, role_instance_id, load_role_requirements(cur, role_instance_id))
               if role["kind"] != "posting" else None)

    try:
        fit = capability_engine.derive_role_fit(cur, role_instance_id)
    except capability_engine.RoleInstanceNotFoundError:
        raise HTTPException(404, "role_instance not found")

    review_summary = load_requirement_review_summary(cur, role_instance_id)

    trace_items = fit["trace"]["items"]
    claim_ids = [item["requirement_claim_id"] for item in trace_items if item["requirement_claim_id"]]
    docs_by_claim = _requirement_documents(cur, claim_ids)

    # Keep personal notes retractable even when stronger mapped evidence wins
    # the status. Reading this separately never changes the engine's judgment.
    concept_ids = list({item["concept"]["id"] for item in trace_items})
    cur.execute("SELECT id, jobber_concept_id, note, created_at, promoted_to_profile360_at "
                "FROM jobber.person_capability_assertion WHERE jobber_concept_id = ANY(%s::uuid[])", (concept_ids,))
    assertions = {}
    for row in cur.fetchall():
        assertion = dict(row)
        key = str(assertion.pop("jobber_concept_id"))
        assertion["id"] = str(assertion["id"])
        assertions[key] = assertion

    items = []
    for item in trace_items:
        role_side = {
            "requirement_claim_id": item["requirement_claim_id"],
            "role_skill_observation_id": item.get("role_skill_observation_id"),
            "requirement_source": item.get("requirement_source"),
            "requirement_type": item["requirement_type"],
            "basis": item["role_side"]["basis"],
            "review_status": item["role_side"]["review_status"],
            "evidence_span": item["role_side"]["evidence_span"],
            "document": docs_by_claim.get(item["requirement_claim_id"]) if item["requirement_claim_id"] else None,
        }
        detail = item["detail"]
        if detail["kind"] == "concept":
            person_side = {
                "mappings": detail["mappings"],
                "assertion": detail["assertion"],
                "component_of": detail.get("component_of", []),
                "coverage": None,
            }
        else:
            person_side = {"mappings": [], "assertion": None, "component_of": [], "coverage": detail["coverage"]}
        person_side["assertion"] = assertions.get(item["concept"]["id"])
        items.append({"concept": item["concept"], "status": item["status"], "role_side": role_side, "person_side": person_side})

    counts = {
        "evidenced": fit["n_evidenced"],
        "partial": fit["n_partial"],
        "user_asserted": fit["n_asserted"],
        "not_found": fit["n_not_found"],
    }

    return {
        "role": role,
        "items": items,
        "counts": counts,
        "target_mapping": mapping,
        # Structural summary (brief §17/§18) — shown before, and separate
        # from, fit_score in the UI.
        "blocking_gaps": fit["blocking_gaps"],
        "unverified_required": fit["unverified_required"],
        "fit_score": None if mapping is not None and not mapping["complete"] else fit["fit_score"],
        "embedding_similarity": fit["embedding_similarity"],
        "engine_version": capability_engine.ENGINE_VERSION,
        # Requirement-review curation gate: this comparison already excludes
        # unreviewed claims from every count/status above, but that
        # exclusion is itself invisible unless a consumer is told review is
        # incomplete — never let a partially-reviewed role's comparison look
        # final. See role_requirements.py.
        "review_summary": review_summary,
    }
