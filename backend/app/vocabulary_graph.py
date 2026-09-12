"""Vocabulary Map graph projection (docs/25-vocabulary-map.md).

A **read-only visual/navigation projection** over the existing vocabulary
model — never a second vocabulary store. This module introduces no
persistence of its own; every node/edge is derived, on each request, from
`jobber.concept_proposal` / `jobber.role_skill_observation` / `jobber.concept`
/ `jobber.concept_alias` / `jobber.concept_edge`, the same tables
`vocabulary_curation.py` and `routes/concepts.py` already read.

Architecture, deliberately reuse-first:

- **Pending vocabulary** is built entirely on top of
  `vocabulary_curation.list_clusters` — the exact same server-side filtered/
  sorted/paginated cluster queue the Review tab already uses. This module
  adds no parallel cluster-aggregation query; it only reshapes that
  function's already-computed, already-bounded page of clusters into graph
  nodes/edges (cluster -> its own surface forms, plus its already-stored
  `nearest_concept_id`/`nearest_similarity` advisory edge). A human split
  lock (`cluster_key_locked`) is transparently honoured because
  `list_clusters` reads whatever `cluster_key` is currently persisted — this
  module never recomputes clustering.
- **Accepted vocabulary** is one small set of direct, bulk, `LIMIT`-bounded
  queries over `concept`/`concept_alias` — grouped and capped the same way
  (see `_accepted_subgraph`), with no per-node query.
- **Similarity** reuses `concept_linking.nearest_concepts` (the existing
  embed-then-pgvector-kNN cascade) — this module never builds an all-pairs
  similarity matrix and never persists a similarity score anywhere. Base
  (non-focus) graphs only ever read the `nearest_concept_id`/
  `nearest_similarity` already stored on `concept_proposal` by Pass B — zero
  embedding calls. Similarity-focus mode does embed the *query* text (the
  transient search string itself — the same always-on primitive Pass B/
  concept curation already use inline), but calls `nearest_concepts` with
  `backfill=False`, so it never computes or persists a *concept's* embedding
  as a side effect of a GET request — a concept with no embedding yet is
  simply not offered as a neighbour until the existing backfill script
  populates one. Every similarity edge is advisory only (§26 of the brief
  this module implements) and bounded to a small top-N per request.
- **No mutation anywhere in this module.** Every function here only ever
  executes `SELECT`s.

Node kinds: `group` (presentation-only — a priority band or concept type,
never a persisted vocabulary item), `pending_cluster`, `concept`,
`surface_form`. Edge relations: `contains` (group -> member, presentation
only), `member_of` (surface form -> pending cluster), `maps_to` (a surface
form whose accepted-concept mapping traces back to a resolved job-posting
proposal — `concept_alias.origin = 'extraction_proposal'`), `alias_of` (a
curator-typed alias — `concept_alias.origin = 'curator'`, never claimed to
have been observed verbatim in a posting), `similar_to` (advisory embedding
similarity — dashed in the UI, never implies equivalence), `ontology` (a
real, curator-accepted `concept_edge` row, `status = 'accepted'` only).

Every list this module returns is bounded (`DEFAULT_NODE_LIMIT`/
`MAX_NODE_LIMIT`, `SURFACE_FORMS_PER_CLUSTER_LIMIT`,
`ALIASES_PER_CONCEPT_LIMIT`, `DEFAULT_SIMILARITY_LIMIT`/
`MAX_SIMILARITY_LIMIT`) — see docs/25 §"Performance" for the full rationale.
"""

from datetime import datetime, timezone

from fastapi import HTTPException

from . import vocabulary_curation as curation
from .concept_linking import nearest_concepts, normalize_name

# --- bounds (docs/25 "Performance") -----------------------------------------

DEFAULT_NODE_LIMIT = 300
MAX_NODE_LIMIT = 500
DEFAULT_SIMILARITY_LIMIT = 8
MAX_SIMILARITY_LIMIT = 20
SURFACE_FORMS_PER_CLUSTER_LIMIT = 25
ALIASES_PER_CONCEPT_LIMIT = 20
DETAIL_ROLE_LIMIT = 25

_STATUSES = ("pending", "accepted", "combined")
_GROUP_BY = ("priority", "type", "none")

NODE_KINDS = ("group", "pending_cluster", "concept", "surface_form")
EDGE_RELATIONS = ("contains", "member_of", "maps_to", "alias_of", "similar_to", "ontology")


# --- small shared builders ---------------------------------------------------

def _group_node(node_id: str, label: str, count: int) -> dict:
    return {"id": node_id, "kind": "group", "label": label, "count": count}


def _edge(source: str, target: str, relation: str, **extra) -> dict:
    e = {"source": source, "target": target, "relation": relation}
    e.update({k: v for k, v in extra.items() if v is not None})
    return e


def _concept_type_labels(cur) -> dict[str, str]:
    cur.execute("SELECT code, label FROM jobber.concept_type")
    return {row["code"]: row["label"] for row in cur.fetchall()}


def _bulk_concepts(cur, concept_ids: set[str]) -> dict[str, dict]:
    ids = [cid for cid in concept_ids if cid]
    if not ids:
        return {}
    cur.execute("SELECT id, canonical_name, type_code, status FROM jobber.concept WHERE id = ANY(%s::uuid[])", (ids,))
    return {str(r["id"]): r for r in cur.fetchall()}


def _concept_node(concept_id: str, row: dict, *, alias_count: int | None = None) -> dict:
    node = {
        "id": f"concept:{concept_id}",
        "kind": "concept",
        "label": row["canonical_name"],
        "type_code": row["type_code"],
        "status": row["status"],
        "target": {"type": "concept", "id": concept_id},
    }
    if alias_count is not None:
        node["alias_count"] = alias_count
    return node


def _ontology_edges(cur, concept_ids: list[str]) -> list[dict]:
    """Real, curator-accepted `concept_edge` rows between concepts already
    present in the rendered node set only — both endpoints must already be
    in `concept_ids`, so this never pulls a node outside the current budget.
    One bulk query, never per-node."""
    if len(concept_ids) < 2:
        return []
    cur.execute(
        "SELECT from_concept_id, to_concept_id, relation FROM jobber.concept_edge "
        "WHERE status = 'accepted' AND from_concept_id = ANY(%s::uuid[]) AND to_concept_id = ANY(%s::uuid[])",
        (concept_ids, concept_ids),
    )
    return [
        _edge(f"concept:{r['from_concept_id']}", f"concept:{r['to_concept_id']}", "ontology", ontology_relation=r["relation"])
        for r in cur.fetchall()
    ]


# --- pending subgraph (reuses vocabulary_curation.list_clusters) -----------

def _pending_subgraph(
    cur, *, group_by: str, band, type_code, q, min_role_count, min_observation_count,
    country, seniority, observed_from, observed_to, node_budget: int, include_similarity: bool,
    current_year: int | None, type_labels: dict[str, str],
) -> tuple[list[dict], list[dict], bool, int]:
    # `node_budget` is always >= 0 (build_graph never passes a negative
    # value) — a budget of exactly 0 flows through the normal path below
    # (list_clusters(limit=0) still returns a correct `total`), rather than
    # a hardcoded early "truncated" that would be wrong when nothing
    # actually matched.

    # The one call that does all filtering/sorting/pagination — identical
    # logic the Review tab's queue uses (docs/19 §4). `limit=node_budget` is
    # a safe upper bound: a cluster contributes >=1 node, so no more than
    # node_budget clusters could ever fit regardless.
    result = curation.list_clusters(
        cur, status="pending", q=q, min_role_count=min_role_count, min_observation_count=min_observation_count,
        observed_from=observed_from, observed_to=observed_to, country=country, seniority=seniority,
        type_code=type_code, band=band, sort="priority", limit=node_budget, offset=0, current_year=current_year,
    )
    clusters = result["items"]
    total_matching = result["total"]

    nearest_ids = {c["nearest_concept_id"] for c in clusters if c.get("nearest_concept_id")}
    nearest_by_id = _bulk_concepts(cur, nearest_ids) if include_similarity else {}

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_concept_ids: set[str] = set()
    group_nodes: dict[str, dict] = {}
    node_count = 0
    truncated_by_budget = False

    for c in clusters:
        if node_count >= node_budget:
            truncated_by_budget = True
            break

        gid = None
        group_label = None
        if group_by != "none":
            if group_by == "priority":
                band_key = c["priority_band"] or "sparse"
                gid = f"group:priority:{band_key}"
                group_label = band_key.title()
            else:
                type_key = c.get("suggested_type") or "unclassified"
                gid = f"group:type:{type_key}"
                group_label = type_labels.get(type_key, type_key.title() if type_key == "unclassified" else type_key)

        # A brand-new group node costs one extra unit of the same node
        # budget the cluster itself does — accounted for *before* either is
        # added, so `returned_nodes` (which includes group/root nodes) can
        # never exceed `limit` (docs/25 §9).
        needs_new_group = gid is not None and gid not in group_nodes
        cost = 1 + (1 if needs_new_group else 0)
        if node_count + cost > node_budget:
            truncated_by_budget = True
            break

        if needs_new_group:
            group_nodes[gid] = _group_node(gid, group_label, 0)
            node_count += 1
        if gid is not None:
            group_nodes[gid]["count"] += 1

        cluster_id = f"cluster:{c['cluster_key']}"
        nodes.append({
            "id": cluster_id,
            "kind": "pending_cluster",
            "label": c["suggested_canonical_label"],
            "cluster_key": c["cluster_key"],
            "priority_band": c["priority_band"],
            "priority_score": c["priority_score"],
            "surface_count": len(c["surface_forms"]),
            "observation_count": c["observation_count"],
            "role_count": c["role_count"],
            "country_count": len(c.get("countries") or []),
            "flags": c["flags"],
            "target": {"type": "cluster_review", "id": c["cluster_key"]},
        })
        node_count += 1
        if gid is not None:
            edges.append(_edge(gid, cluster_id, "contains"))

        for sf in sorted(c["surface_forms"])[:SURFACE_FORMS_PER_CLUSTER_LIMIT]:
            if node_count >= node_budget:
                truncated_by_budget = True
                break
            sf_id = f"surface:{c['cluster_key']}:{sf}"
            nodes.append({
                "id": sf_id, "kind": "surface_form", "label": sf, "status": "pending",
                "target": {"type": "surface_form", "value": sf},
            })
            node_count += 1
            edges.append(_edge(sf_id, cluster_id, "member_of"))

        nearest_id = c.get("nearest_concept_id")
        if include_similarity and nearest_id and nearest_id in nearest_by_id and node_count < node_budget:
            concept_node_id = f"concept:{nearest_id}"
            if nearest_id not in seen_concept_ids:
                nodes.append(_concept_node(nearest_id, nearest_by_id[nearest_id]))
                seen_concept_ids.add(nearest_id)
                node_count += 1
            similarity = c.get("nearest_similarity")
            edges.append(_edge(cluster_id, concept_node_id, "similar_to", similarity=round(similarity, 4) if similarity is not None else None))

    nodes.extend(group_nodes.values())
    truncated = truncated_by_budget or (total_matching > len(clusters))
    return nodes, edges, truncated, total_matching


# --- accepted subgraph (direct, bounded, bulk queries) ----------------------

def _proposal_occurrence_lookup(cur, concept_ids: list[str]) -> dict[tuple[str, str], int]:
    """Real corpus evidence for `extraction_proposal`-origin aliases only —
    a curator-typed alias (`origin='curator'`) has no claim to have been
    observed verbatim (brief §4), so it is never looked up here."""
    if not concept_ids:
        return {}
    cur.execute(
        "SELECT resolved_concept_id, surface_form, occurrence_count FROM jobber.concept_proposal "
        "WHERE resolved_concept_id = ANY(%s::uuid[]) AND status IN ('accepted_new', 'accepted_alias')",
        (concept_ids,),
    )
    return {(str(r["resolved_concept_id"]), r["surface_form"]): r["occurrence_count"] for r in cur.fetchall()}


def _accepted_subgraph(
    cur, *, group_by: str, type_code, q, node_budget: int, include_ontology: bool, type_labels: dict[str, str],
) -> tuple[list[dict], list[dict], bool, int]:
    # `node_budget` is always >= 0 — a budget of exactly 0 flows through the
    # normal path (the SQL LIMIT 0 still returns a correct `total_matching`
    # count from the separate COUNT query below), same reasoning as
    # _pending_subgraph.
    if group_by not in ("type", "none"):
        group_by = "type"  # "priority" is meaningless for accepted concepts

    clauses = ["status = 'active'"]
    params: list = []
    if type_code:
        clauses.append("type_code = %s")
        params.append(type_code)
    if q:
        needle = f"%{q}%"
        clauses.append(
            "(canonical_name ILIKE %s OR EXISTS "
            "(SELECT 1 FROM jobber.concept_alias ca WHERE ca.concept_id = concept.id AND ca.alias ILIKE %s))"
        )
        params.extend([needle, needle])
    where_sql = " AND ".join(clauses)

    cur.execute(f"SELECT COUNT(*) AS n FROM jobber.concept WHERE {where_sql}", params)
    total_matching = cur.fetchone()["n"]

    cur.execute(
        f"SELECT id, canonical_name, type_code, status FROM jobber.concept WHERE {where_sql} "
        f"ORDER BY canonical_name LIMIT %s",
        [*params, node_budget],
    )
    concept_rows = cur.fetchall()
    concept_ids = [str(r["id"]) for r in concept_rows]

    aliases_by_concept: dict[str, list[dict]] = {}
    if concept_ids:
        cur.execute(
            "SELECT concept_id, alias, origin FROM jobber.concept_alias WHERE concept_id = ANY(%s::uuid[]) ORDER BY alias",
            (concept_ids,),
        )
        for row in cur.fetchall():
            aliases_by_concept.setdefault(str(row["concept_id"]), []).append(row)

    occurrence_by_alias = _proposal_occurrence_lookup(cur, concept_ids)

    nodes: list[dict] = []
    edges: list[dict] = []
    group_nodes: dict[str, dict] = {}
    node_count = 0
    truncated_by_budget = False

    for row in concept_rows:
        if node_count >= node_budget:
            truncated_by_budget = True
            break

        gid = f"group:type:{row['type_code']}" if group_by == "type" else None

        # Same group-cost accounting as _pending_subgraph — a brand-new
        # group node costs one extra unit of the shared node budget.
        needs_new_group = gid is not None and gid not in group_nodes
        cost = 1 + (1 if needs_new_group else 0)
        if node_count + cost > node_budget:
            truncated_by_budget = True
            break

        if needs_new_group:
            group_nodes[gid] = _group_node(gid, type_labels.get(row["type_code"], row["type_code"]), 0)
            node_count += 1
        if gid is not None:
            group_nodes[gid]["count"] += 1

        concept_id = str(row["id"])
        aliases = aliases_by_concept.get(concept_id, [])
        nodes.append(_concept_node(concept_id, row, alias_count=len(aliases)))
        node_count += 1
        if gid is not None:
            edges.append(_edge(gid, f"concept:{concept_id}", "contains"))

        for alias_row in aliases[:ALIASES_PER_CONCEPT_LIMIT]:
            if node_count >= node_budget:
                truncated_by_budget = True
                break
            alias_text = alias_row["alias"]
            alias_id = f"surface:{concept_id}:{alias_text}"
            nodes.append({
                "id": alias_id,
                "kind": "surface_form",
                "label": alias_text,
                "status": "accepted",
                "observation_count": occurrence_by_alias.get((concept_id, alias_text)),
                "target": {"type": "concept", "id": concept_id},
            })
            node_count += 1
            relation = "maps_to" if alias_row["origin"] == "extraction_proposal" else "alias_of"
            edges.append(_edge(alias_id, f"concept:{concept_id}", relation))

    nodes.extend(group_nodes.values())

    if include_ontology and concept_ids:
        edges.extend(_ontology_edges(cur, concept_ids))

    truncated = truncated_by_budget or (total_matching > len(concept_rows))
    return nodes, edges, truncated, total_matching


# --- top-level graph builder --------------------------------------------------

def build_graph(
    cur,
    *,
    status: str = "pending",
    group_by: str | None = None,
    band: str | None = None,
    type_code: str | None = None,
    q: str | None = None,
    min_role_count: int | None = None,
    min_observation_count: int | None = None,
    country: str | None = None,
    seniority: str | None = None,
    observed_from: str | None = None,
    observed_to: str | None = None,
    limit: int = DEFAULT_NODE_LIMIT,
    include_similarity: bool = True,
    include_ontology: bool = True,
    current_year: int | None = None,
) -> dict:
    """The bounded, server-filtered Vocabulary Map graph (docs/25). Never
    writes anything. `limit` is a strict total *node* budget — it bounds
    `returned_nodes` including group nodes and the synthetic root, not just
    clusters/concepts/surface forms — shared across whichever branch(es)
    `status` selects. See module docstring."""
    if status not in _STATUSES:
        raise HTTPException(400, f"status must be one of {_STATUSES}")
    if group_by is not None and group_by not in _GROUP_BY:
        raise HTTPException(400, f"group_by must be one of {_GROUP_BY}")
    limit = max(1, min(limit, MAX_NODE_LIMIT))
    type_labels = _concept_type_labels(cur)

    nodes: list[dict] = []
    edges: list[dict] = []
    truncated = False
    total_matching = 0

    # One slot is always reserved for the synthetic root added below; each
    # subgraph call accounts for its own group nodes' cost against the
    # remainder, so `len(nodes)` after both branches is guaranteed <=
    # `item_budget`, and `item_budget + 1 (root) == limit`.
    item_budget = max(0, limit - 1)

    if status in ("pending", "combined"):
        budget = item_budget if status == "pending" else item_budget // 2
        p_nodes, p_edges, p_trunc, p_total = _pending_subgraph(
            cur, group_by=group_by or "priority", band=band, type_code=type_code, q=q,
            min_role_count=min_role_count, min_observation_count=min_observation_count,
            country=country, seniority=seniority, observed_from=observed_from, observed_to=observed_to,
            node_budget=budget, include_similarity=include_similarity, current_year=current_year,
            type_labels=type_labels,
        )
        nodes += p_nodes
        edges += p_edges
        truncated = truncated or p_trunc
        total_matching += p_total

    if status in ("accepted", "combined"):
        budget = item_budget if status == "accepted" else max(0, item_budget - len(nodes))
        a_nodes, a_edges, a_trunc, a_total = _accepted_subgraph(
            cur, group_by=group_by or "type", type_code=type_code, q=q, node_budget=budget,
            include_ontology=include_ontology, type_labels=type_labels,
        )
        nodes += a_nodes
        edges += a_edges
        truncated = truncated or a_trunc
        total_matching += a_total

    root_label = {"pending": "Pending", "accepted": "Accepted", "combined": "Vocabulary"}[status]
    root_id = "root"
    nodes.append(_group_node(root_id, root_label, len(nodes)))

    # Wire every top-level cluster/concept/group (i.e. one never targeted by
    # a "contains" edge already, whether because group_by=none or because it
    # *is* a group node) straight to the synthetic root — never a persisted
    # vocabulary node, purely the innermost ring of the radial layout (brief §5).
    containable = {"group", "pending_cluster", "concept"}
    contained_targets = {e["target"] for e in edges if e["relation"] == "contains"}
    for n in nodes:
        if n["id"] != root_id and n["kind"] in containable and n["id"] not in contained_targets:
            edges.append(_edge(root_id, n["id"], "contains"))

    return {
        "meta": {
            # total_nodes counts matching pending clusters + accepted
            # concepts only (not their child surface-form/group nodes) — an
            # approximate "how much more is out there" signal, not an exact
            # unbounded node count. See docs/25 "Known limitations".
            "total_nodes": total_matching,
            "returned_nodes": len(nodes),
            "returned_edges": len(edges),
            "truncated": truncated,
            "status": status,
            "group_by": group_by or ("type" if status == "accepted" else "priority"),
            "filters": {
                "band": band, "type_code": type_code, "q": q,
                "min_role_count": min_role_count, "min_observation_count": min_observation_count,
                "country": country, "seniority": seniority,
                "observed_from": observed_from, "observed_to": observed_to,
            },
        },
        "nodes": nodes,
        "edges": edges,
    }


# --- similarity focus (bounded local neighbourhood, brief §6.4/§20) --------

def _focus_response(center_id: str, nodes: list[dict], edges: list[dict], similarity_limit: int) -> dict:
    return {
        "meta": {
            "total_nodes": len(nodes),
            "returned_nodes": len(nodes),
            "returned_edges": len(edges),
            "truncated": False,
            "mode": "similarity_focus",
            "focus": center_id,
            "similarity_limit": similarity_limit,
        },
        "nodes": nodes,
        "edges": edges,
    }


def _safe_nearest_concepts(cur, text: str, *, limit: int) -> list[tuple[str, float]]:
    """`nearest_concepts` needs a live embedding model to embed the query
    text — normally instant (a small local ONNX model, not a network AI
    call), but the very first use in a process may need to fetch model
    weights, which can fail in a network-restricted environment. Similarity
    here is advisory-only decoration on a read endpoint, never something a
    request should hard-fail on: if the embedding subsystem is unavailable,
    similarity focus degrades to "no neighbours found" rather than a 500."""
    try:
        return nearest_concepts(cur, text, limit=limit, backfill=False)
    except Exception:
        return []


def _focus_on_cluster(cur, cluster_key: str, similarity_limit: int) -> dict:
    evidence = curation.build_pending_cluster_index(cur)
    ev = evidence.get(cluster_key)
    if ev is None:
        raise HTTPException(404, "unknown or already-resolved cluster_key")
    summary = curation.cluster_summary(ev, current_year=datetime.now(timezone.utc).year)

    center_id = f"cluster:{cluster_key}"
    nodes = [{
        "id": center_id, "kind": "pending_cluster", "label": summary["suggested_canonical_label"],
        "cluster_key": cluster_key, "priority_band": summary["priority_band"], "priority_score": summary["priority_score"],
        "surface_count": len(summary["surface_forms"]), "observation_count": summary["observation_count"],
        "role_count": summary["role_count"], "target": {"type": "cluster_review", "id": cluster_key}, "focus": True,
    }]
    edges: list[dict] = []

    # No raw-term-to-raw-term (or cluster-to-cluster) similarity is
    # persisted anywhere in this codebase (only concepts carry a
    # `d_embedding` row) — so a pending cluster's neighbourhood is its
    # nearest *accepted concepts* by embedding similarity, computed fresh via
    # the existing bounded kNN cascade (concept_linking.nearest_concepts),
    # never a fabricated cluster-to-cluster score. Documented limitation,
    # docs/25 §"Similarity semantics".
    neighbours = _safe_nearest_concepts(cur, summary["suggested_canonical_label"], limit=similarity_limit)
    neighbour_rows = _bulk_concepts(cur, {cid for cid, _ in neighbours})
    for concept_id, similarity in neighbours:
        row = neighbour_rows.get(concept_id)
        if not row:
            continue
        nid = f"concept:{concept_id}"
        nodes.append(_concept_node(concept_id, row))
        edges.append(_edge(center_id, nid, "similar_to", similarity=round(similarity, 4)))

    return _focus_response(center_id, nodes, edges, similarity_limit)


def _focus_on_concept(cur, concept_id: str, similarity_limit: int, include_ontology: bool) -> dict:
    cur.execute("SELECT id, canonical_name, type_code, status, definition FROM jobber.concept WHERE id = %s", (concept_id,))
    concept = cur.fetchone()
    if not concept:
        raise HTTPException(404, "unknown concept")

    center_id = f"concept:{concept_id}"
    nodes = [{
        "id": center_id, "kind": "concept", "label": concept["canonical_name"], "type_code": concept["type_code"],
        "status": concept["status"], "target": {"type": "concept", "id": concept_id}, "focus": True,
    }]
    edges: list[dict] = []

    query_text = concept["canonical_name"]
    if concept["definition"]:
        query_text = f'{query_text}: {concept["definition"]}'
    # A concept's own embedding is always its own nearest match — over-fetch
    # by one and drop self rather than special-casing the SQL.
    neighbours = [
        (cid, sim) for cid, sim in _safe_nearest_concepts(cur, query_text, limit=similarity_limit + 1) if cid != str(concept_id)
    ][:similarity_limit]

    neighbour_rows = _bulk_concepts(cur, {cid for cid, _ in neighbours})
    neighbour_ids = []
    for cid, similarity in neighbours:
        row = neighbour_rows.get(cid)
        if not row:
            continue
        nodes.append(_concept_node(cid, row))
        edges.append(_edge(center_id, f"concept:{cid}", "similar_to", similarity=round(similarity, 4)))
        neighbour_ids.append(cid)

    if include_ontology:
        edges.extend(_ontology_edges(cur, [str(concept_id), *neighbour_ids]))

    return _focus_response(center_id, nodes, edges, similarity_limit)


def _focus_on_surface_form(cur, value: str, similarity_limit: int) -> dict:
    normalized = normalize_name(value)
    if not normalized:
        raise HTTPException(400, "surface form value must not be empty")

    center_id = f"surface_form:{normalized}"
    nodes = [{
        "id": center_id, "kind": "surface_form", "label": normalized, "focus": True,
        "target": {"type": "surface_form", "value": normalized},
    }]
    edges: list[dict] = []
    neighbours = _safe_nearest_concepts(cur, normalized, limit=similarity_limit)
    neighbour_rows = _bulk_concepts(cur, {cid for cid, _ in neighbours})
    for concept_id, similarity in neighbours:
        row = neighbour_rows.get(concept_id)
        if not row:
            continue
        nodes.append(_concept_node(concept_id, row))
        edges.append(_edge(center_id, f"concept:{concept_id}", "similar_to", similarity=round(similarity, 4)))

    return _focus_response(center_id, nodes, edges, similarity_limit)


def build_similarity_focus(cur, *, focus: str, similarity_limit: int = DEFAULT_SIMILARITY_LIMIT, include_ontology: bool = True) -> dict:
    """A selected node's bounded local semantic neighbourhood (brief §6.4) —
    never a global all-node similarity map, never a mutation. `focus` is one
    of `cluster:<cluster_key>`, `concept:<concept_id>`, or
    `surface_form:<literal text>` (a deliberately separate, simple grammar
    from the graph's own node `id`s — a surface-form node's graph id is
    owner-scoped for uniqueness within one response, but similarity lookup
    only ever needs the literal text)."""
    similarity_limit = max(1, min(similarity_limit, MAX_SIMILARITY_LIMIT))
    if focus.startswith("cluster:"):
        return _focus_on_cluster(cur, focus[len("cluster:"):], similarity_limit)
    if focus.startswith("concept:"):
        return _focus_on_concept(cur, focus[len("concept:"):], similarity_limit, include_ontology)
    if focus.startswith("surface_form:"):
        return _focus_on_surface_form(cur, focus[len("surface_form:"):], similarity_limit)
    raise HTTPException(400, "focus must start with 'cluster:', 'concept:', or 'surface_form:'")


# --- surface-form detail read (brief §13/§19) -------------------------------

def _surface_form_detail_from_concept_only(cur, normalized: str) -> dict | None:
    """A surface form with no `concept_proposal` row at all — the only way
    that can still resolve to something is a concept whose canonical name or
    a curator-typed alias happens to equal this text (never automatically
    claimed to have been observed verbatim in a posting)."""
    cur.execute("SELECT id, canonical_name, type_code FROM jobber.concept WHERE status = 'active' AND LOWER(canonical_name) = %s", (normalized,))
    row = cur.fetchone()
    alias_origin = None
    if not row:
        cur.execute(
            "SELECT c.id, c.canonical_name, c.type_code, ca.origin FROM jobber.concept_alias ca "
            "JOIN jobber.concept c ON c.id = ca.concept_id WHERE c.status = 'active' AND LOWER(ca.alias) = %s",
            (normalized,),
        )
        row = cur.fetchone()
        alias_origin = row["origin"] if row else None
    if not row:
        return None
    return {
        "surface_form": normalized,
        "normalised_surface_form": normalized,
        "status": "accepted",
        "cluster_key": None,
        "cluster_key_locked": False,
        "suggested_type": None,
        "observation_count": None,
        "role_count": None,
        "years": [], "countries": [], "seniority_levels": [], "career_tracks": [], "example_roles": [],
        "nearest_concept": None,
        "resolved_concept": {"id": str(row["id"]), "canonical_name": row["canonical_name"], "type_code": row["type_code"]},
        "resolved_at": None,
        "alias_origin": alias_origin,
    }


def get_surface_form_detail(cur, value: str) -> dict | None:
    """Single-surface-form evidence (brief §13/§19) — pure read, no
    mutation. Returns None (the route 404s) when `value` matches nothing in
    persisted vocabulary state at all."""
    normalized = normalize_name(value)
    if not normalized:
        return None

    cur.execute(
        "SELECT surface_form, cluster_key, cluster_key_locked, suggested_type, nearest_concept_id, nearest_similarity, "
        "status, resolved_concept_id, resolved_at, occurrence_count FROM jobber.concept_proposal WHERE surface_form = %s",
        (normalized,),
    )
    proposal = cur.fetchone()
    if proposal is None:
        return _surface_form_detail_from_concept_only(cur, normalized)

    if proposal["status"] != "pending":
        resolved_concept = None
        if proposal["resolved_concept_id"]:
            cur.execute("SELECT id, canonical_name, type_code FROM jobber.concept WHERE id = %s", (proposal["resolved_concept_id"],))
            rc = cur.fetchone()
            if rc:
                resolved_concept = {"id": str(rc["id"]), "canonical_name": rc["canonical_name"], "type_code": rc["type_code"]}
        return {
            "surface_form": normalized,
            "normalised_surface_form": normalized,
            "status": proposal["status"],
            "cluster_key": proposal["cluster_key"] or normalized,
            "cluster_key_locked": bool(proposal["cluster_key_locked"]),
            "suggested_type": proposal["suggested_type"],
            # Audit-history evidence (docs/19 §1's documented boundary) — the
            # stored count as of resolution, not a live re-aggregation.
            "observation_count": proposal["occurrence_count"],
            "role_count": None,
            "years": [], "countries": [], "seniority_levels": [], "career_tracks": [], "example_roles": [],
            "nearest_concept": None,
            "resolved_concept": resolved_concept,
            "resolved_at": proposal["resolved_at"].isoformat() if proposal["resolved_at"] else None,
        }

    cluster_key = proposal["cluster_key"] or normalized
    cur.execute(
        """
        SELECT rso.role_instance_id, rso.surface_form, ri.posting_date, ri.country, ri.seniority_level, ri.career_track, ri.title
        FROM jobber.role_skill_observation rso
        JOIN jobber.role_instance ri ON ri.id = rso.role_instance_id
        WHERE rso.canonical_concept_id IS NULL
        ORDER BY ri.posting_date DESC NULLS LAST, rso.role_instance_id
        """
    )
    observation_count = 0
    role_ids: set[str] = set()
    years: set[int] = set()
    countries: set[str] = set()
    seniority_levels: set[str] = set()
    career_tracks: set[str] = set()
    example_roles: list[dict] = []
    for row in cur.fetchall():
        if normalize_name(row["surface_form"]) != normalized:
            continue
        observation_count += 1
        role_id = str(row["role_instance_id"])
        role_ids.add(role_id)
        if row["posting_date"] is not None:
            years.add(row["posting_date"].year)
        if row["country"]:
            countries.add(row["country"])
        if row["seniority_level"]:
            seniority_levels.add(row["seniority_level"])
        if row["career_track"]:
            career_tracks.add(row["career_track"])
        if len(example_roles) < DETAIL_ROLE_LIMIT and role_id not in {r["id"] for r in example_roles}:
            example_roles.append({"id": role_id, "title": row["title"]})

    nearest_concept = None
    if proposal["nearest_concept_id"]:
        cur.execute("SELECT canonical_name FROM jobber.concept WHERE id = %s", (proposal["nearest_concept_id"],))
        nc = cur.fetchone()
        if nc:
            nearest_concept = {
                "id": str(proposal["nearest_concept_id"]),
                "canonical_name": nc["canonical_name"],
                "similarity": proposal["nearest_similarity"],
            }

    return {
        "surface_form": normalized,
        "normalised_surface_form": normalized,
        "status": "pending",
        "cluster_key": cluster_key,
        "cluster_key_locked": bool(proposal["cluster_key_locked"]),
        "suggested_type": proposal["suggested_type"],
        "observation_count": observation_count,
        "role_count": len(role_ids),
        "years": sorted(years),
        "countries": sorted(countries),
        "seniority_levels": sorted(seniority_levels),
        "career_tracks": sorted(career_tracks),
        "example_roles": example_roles,
        "nearest_concept": nearest_concept,
        "resolved_concept": None,
        "resolved_at": None,
    }
