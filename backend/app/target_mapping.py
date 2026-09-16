"""Target requirements retain their wording and an explicit vocabulary resolution."""
from fastapi import HTTPException

from .concept_linking import exact_match_concept_id, normalize_name


def resolve_requirements(cur, skills):
    items = [dict(skill) for skill in skills]
    # Per-item candidate resolution (explicit concept_id, or an exact-match
    # lookup against the vocabulary) stays one call per item — that lookup
    # is name/alias text matching, not a batchable id fetch, and
    # concept_linking.exact_match_concept_id is deliberately the same
    # "cheap enough to call inline" helper every other extraction path uses.
    # The one check genuinely worth batching is the *candidate id ->
    # still-active concept* verification below: with N skills that was N
    # single-row SELECTs; a target's requirement list is edited and
    # re-previewed live as the user types, so this runs often.
    candidate_ids: list[str] = []
    for item in items:
        explicit = item.get("concept_id")
        cid = explicit or (None if item.get("mapping_reviewed") else
                           exact_match_concept_id(cur, normalize_name(item["name"])))
        candidate_ids.append(cid)

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
