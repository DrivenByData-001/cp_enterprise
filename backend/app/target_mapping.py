"""Target requirements retain their wording and an explicit vocabulary resolution."""
from fastapi import HTTPException

from .concept_linking import bulk_exact_match_concept_ids, normalize_name


def resolve_requirements(cur, skills):
    items = [dict(skill) for skill in skills]
    # Two batched lookups, each in a bounded number of queries regardless of
    # how many skills there are — a target's requirement list is edited and
    # re-previewed live as the user types, so this runs often, and N skills
    # must not mean O(N) round trips:
    #
    # 1. Auto-match candidates (no explicit concept_id, not already marked
    #    reviewed — an item explicitly marked reviewed with nothing chosen
    #    means "treat as unmapped", not "go try to auto-match it") resolved
    #    together via bulk_exact_match_concept_ids, which was previously one
    #    exact_match_concept_id() call — up to two single-row SELECTs — per
    #    such item.
    names_to_match = [
        normalize_name(item["name"]) for item in items
        if not item.get("concept_id") and not item.get("mapping_reviewed")
    ]
    matched_by_name = bulk_exact_match_concept_ids(cur, names_to_match)

    candidate_ids: list[str | None] = []
    for item in items:
        explicit = item.get("concept_id")
        if explicit:
            cid = explicit
        elif item.get("mapping_reviewed"):
            cid = None
        else:
            cid = matched_by_name.get(normalize_name(item["name"]))
        candidate_ids.append(cid)

    # 2. Every resulting candidate id (explicit or auto-matched) verified as
    #    a currently-active concept in one WHERE id = ANY(...) query.
    concepts_by_id: dict[str, dict] = {}
    unique_ids = list({cid for cid in candidate_ids if cid})
    if unique_ids:
        cur.execute(
            "SELECT id, canonical_name FROM jobber.concept WHERE id = ANY(%s::uuid[]) AND status = 'active'",
            (unique_ids,),
        )
        concepts_by_id = {str(row["id"]): row for row in cur.fetchall()}

    result = []
    for item, cid in zip(items, candidate_ids):
        explicit = item.get("concept_id")
        concept = concepts_by_id.get(cid) if cid else None
        if explicit and (not item.get("mapping_reviewed") or not concept):
            raise HTTPException(422, "Select an active vocabulary concept and confirm the requirement mapping.")
        result.append({**item, "concept_id": str(concept["id"]) if concept else None,
                       "canonical_name": concept["canonical_name"] if concept else None,
                       "mapping_status": "mapped" if concept else "unmapped"})
    return result


def target_mapping_summary(cur, target_id, requirements):
    # Include every stored observation, even when the authoritative claim loader
    # excludes it. A mapped-but-excluded observation must not disappear either.
    cur.execute("""SELECT o.id, o.surface_form AS name, c.id AS concept_id, c.canonical_name,
                          c.status AS concept_status
                   FROM jobber.role_skill_observation o
                   LEFT JOIN jobber.concept c ON c.id = o.canonical_concept_id
                   WHERE o.role_instance_id = %s ORDER BY o.created_at, o.id""", (target_id,))
    included = {r["concept_id"] for r in requirements}
    items = []
    for row in cur.fetchall():
        cid = str(row["concept_id"]) if row["concept_id"] else None
        mapped = cid is not None and row["concept_status"] == "active"
        status = "mapped" if mapped and cid in included else "excluded" if mapped else "unmapped"
        items.append({"name": row["name"], "concept_id": cid if mapped else None,
                      "canonical_name": row["canonical_name"] if mapped else None, "mapping_status": status})
    observed = {i["concept_id"] for i in items}
    for row in requirements:
        if row["concept_id"] not in observed:
            items.append({"name": row["canonical_name"], "concept_id": row["concept_id"],
                          "canonical_name": row["canonical_name"],
                          "mapping_status": "mapped" if row["concept_status"] == "active" else "unmapped"})
    unresolved = sum(i["mapping_status"] != "mapped" for i in items)
    return {"items": items, "total": len(items), "mapped": len(items) - unresolved,
            "unresolved": unresolved, "complete": bool(items) and unresolved == 0}
