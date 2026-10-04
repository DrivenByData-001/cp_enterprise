"""Profile360 mapping as a human-curation workflow (claims AND capabilities).

Profile360 evidence (read-only, authoritative) is mapped onto the canonical
jobber vocabulary (`jobber.concept`). This module holds the shared mechanics
for that workflow so claims and capabilities behave identically:

  candidates  - the top vocabulary concepts retrieval considered (persisted per
                run, with the similarity retrieval already computed — never an
                invented score) and which one the AI recommended;
  mappings    - many-to-many (item <-> concept). The AI only ever writes
                'unreviewed' rows; a human picking a concept writes
                'curator_asserted'/'accepted'; a rejected row never blocks a
                later mapping;
  proposals   - vocabulary missing from the catalogue becomes an ordinary
                jobber.concept_proposal (the *same* queue/review/accept
                semantics as vocabulary discovered in job postings), plus a
                provenance link (concept_proposal_profile360_source) back to the
                originating item. Accepting the proposal in Vocabulary creates
                the canonical jobber.concept and mapping rows back to the
                source (`resolve_sources_for_concept`, called by
                vocabulary_curation.resolve_surface_form_group).

Nothing here creates canonical vocabulary or accepts an AI mapping.
"""

from . import profile360_reader as p360
from .concept_linking import exact_match_concept_id, nearest_concepts, normalize_name

CANDIDATE_LIMIT = 10

KINDS: dict[str, dict] = {
    "claim": {
        "reader_table": "claims",
        "mapping_table": "profile360_claim_mapping",
        "source_col": "profile360_claim_id",
        "concept_col": "jobber_concept_id",
        "run_col": "profile360_claim_id",
        "get": p360.get_claim,
        "type_codes": None,  # a claim can map to a concept of any type
        "default_type": None,
    },
    "capability": {
        "reader_table": "capabilities",
        "mapping_table": "profile360_capability_mapping",
        "source_col": "profile360_capability_id",
        "concept_col": "jobber_capability_concept_id",
        "run_col": "profile360_capability_id",
        "get": p360.get_capability,
        "type_codes": ["capability"],  # capabilities only map to capability concepts
        "default_type": "capability",
    },
}

MAPPING_STATES = ("unmapped", "pending", "mapped", "boundary", "all")


class MappingError(ValueError):
    """A curator request that is invalid (bad concept/type/duplicate) — 4xx."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def cfg(kind: str) -> dict:
    if kind not in KINDS:
        raise MappingError("kind must be 'claim' or 'capability'")
    return KINDS[kind]


# --- candidates -------------------------------------------------------------

def retrieve_candidates(cur, text: str, type_codes: list[str] | None, limit: int = CANDIDATE_LIMIT) -> list[dict]:
    """Top-`limit` active concepts by the existing embedding retrieval
    (concept_linking.nearest_concepts), in rank order, each with its
    definition and the cosine similarity retrieval returned."""
    hits = nearest_concepts(cur, text, limit=limit, type_codes=type_codes)
    if not hits:
        return []
    cur.execute(
        "SELECT id, type_code, canonical_name, definition FROM jobber.concept WHERE id = ANY(%s::uuid[])",
        ([h[0] for h in hits],),
    )
    by_id = {str(r["id"]): r for r in cur.fetchall()}
    out = []
    for rank, (cid, sim) in enumerate(hits, start=1):
        row = by_id.get(str(cid))
        if row is None:
            continue
        out.append({
            "id": str(cid), "type_code": row["type_code"], "canonical_name": row["canonical_name"],
            "definition": row["definition"], "similarity": float(sim) if sim is not None else None, "rank": rank,
        })
    return out


def persist_candidates(cur, run_id: str, candidates: list[dict], selected_id: str | None) -> None:
    for c in candidates:
        cur.execute(
            "INSERT INTO jobber.profile360_mapping_candidate (extraction_run_id, concept_id, rank, similarity, ai_selected) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (extraction_run_id, concept_id) DO NOTHING",
            (run_id, c["id"], c["rank"], c["similarity"], c["id"] == selected_id),
        )


# --- mappings ---------------------------------------------------------------

def upsert_mapping(
    cur, kind: str, source_id: str, concept_id: str, *, basis: str, review_status: str,
    run_id: str | None = None, override_rejected: bool = False,
) -> dict:
    """Idempotent (source, concept) mapping write. An existing accepted/
    unreviewed row is never downgraded. A rejected row stays rejected unless
    `override_rejected` (a deliberate human choice), in which case it is
    re-opened with the supplied basis/status — a rejection never permanently
    consumes the pair."""
    c = cfg(kind)
    table, scol, ccol = c["mapping_table"], c["source_col"], c["concept_col"]
    cur.execute(
        f"""
        INSERT INTO jobber.{table} ({scol}, {ccol}, mapping_basis, review_status, reviewed_at, extraction_run_id)
        VALUES (%s, %s, %s, %s, CASE WHEN %s = 'accepted' THEN now() END, %s)
        ON CONFLICT ({scol}, {ccol}) DO UPDATE SET
            extraction_run_id = COALESCE(EXCLUDED.extraction_run_id, {table}.extraction_run_id),
            mapping_basis = CASE
                WHEN {table}.review_status = 'rejected' AND %s THEN EXCLUDED.mapping_basis
                WHEN {table}.review_status = 'unreviewed' AND EXCLUDED.review_status = 'accepted' THEN EXCLUDED.mapping_basis
                ELSE {table}.mapping_basis END,
            review_status = CASE
                WHEN {table}.review_status = 'rejected' AND %s THEN EXCLUDED.review_status
                WHEN {table}.review_status = 'unreviewed' AND EXCLUDED.review_status = 'accepted' THEN 'accepted'
                ELSE {table}.review_status END,
            reviewed_at = CASE
                WHEN {table}.review_status = 'rejected' AND %s THEN EXCLUDED.reviewed_at
                WHEN {table}.review_status = 'unreviewed' AND EXCLUDED.review_status = 'accepted' THEN now()
                ELSE {table}.reviewed_at END
        RETURNING id, mapping_basis, review_status
        """,
        (source_id, concept_id, basis, review_status, review_status, run_id,
         override_rejected, override_rejected, override_rejected),
    )
    return cur.fetchone()


def curator_map(cur, kind: str, source_id: str, concept_id: str) -> dict:
    """A human chose this concept: curator_asserted + accepted."""
    c = cfg(kind)
    if c["get"](cur, source_id) is None:
        raise MappingError(f"profile360 {kind} not found", 404)
    cur.execute("SELECT id, type_code, status FROM jobber.concept WHERE id = %s", (concept_id,))
    concept = cur.fetchone()
    if concept is None or concept["status"] != "active":
        raise MappingError("concept does not exist or is not active")
    if c["type_codes"] and concept["type_code"] not in c["type_codes"]:
        raise MappingError(f"a {kind} can only be mapped to a concept of type {c['type_codes']}")
    row = upsert_mapping(
        cur, kind, source_id, concept_id, basis="curator_asserted", review_status="accepted", override_rejected=True,
    )
    return {"mapping_id": row["id"], "concept_id": concept_id, "mapping_basis": row["mapping_basis"],
            "review_status": row["review_status"]}


# --- vocabulary proposals (reuses jobber.concept_proposal) ------------------

def record_proposal(
    cur, kind: str, source_id: str, source_text: str, *, canonical_name: str, type_code: str | None,
    definition: str | None, origin: str, run_id: str | None = None,
) -> dict:
    """Record vocabulary missing from the catalogue as an ordinary pending
    concept_proposal plus a Profile360 provenance link. Deduplicated globally
    by surface_form exactly like job-posting proposals. Never creates a
    concept."""
    c = cfg(kind)
    name = (canonical_name or "").strip()
    if not name:
        raise MappingError("canonical_name is required")
    type_code = type_code or c["default_type"]
    if c["type_codes"] and type_code not in c["type_codes"]:
        raise MappingError(f"a {kind} vocabulary proposal must have type {c['type_codes']}")
    if not type_code:
        raise MappingError("type_code is required")
    cur.execute("SELECT 1 FROM jobber.concept_type WHERE code = %s", (type_code,))
    if cur.fetchone() is None:
        raise MappingError(f"unknown type_code {type_code!r}")

    normalized = normalize_name(name)
    existing_id = exact_match_concept_id(cur, normalized)
    if existing_id:
        raise MappingError(
            "an existing concept already matches this name — map to it instead of proposing a duplicate", 409,
        )

    cur.execute("SELECT id FROM jobber.concept_proposal WHERE surface_form = %s AND status = 'pending'", (normalized,))
    existing = cur.fetchone()
    created_proposal = existing is None
    if existing:
        proposal_id = existing["id"]
        cur.execute(
            "UPDATE jobber.concept_proposal SET suggested_type = COALESCE(suggested_type, %s), "
            "suggested_definition = COALESCE(suggested_definition, %s) WHERE id = %s",
            (type_code, definition, proposal_id),
        )
    else:
        nearest = nearest_concepts(cur, name, limit=1, type_codes=c["type_codes"])
        nearest = nearest[0] if nearest else None
        cur.execute(
            """
            INSERT INTO jobber.concept_proposal
                (surface_form, suggested_type, suggested_definition, occurrence_count, nearest_concept_id,
                 nearest_similarity, extraction_run_id, status)
            VALUES (%s, %s, %s, 0, %s, %s, %s, 'pending') RETURNING id
            """,
            (normalized, type_code, definition, nearest[0] if nearest else None, nearest[1] if nearest else None, run_id),
        )
        proposal_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO jobber.concept_proposal_profile360_source
            (concept_proposal_id, source_kind, source_id, source_text, origin, extraction_run_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (concept_proposal_id, source_kind, source_id) DO NOTHING
        RETURNING id
        """,
        (proposal_id, kind, source_id, source_text, origin, run_id),
    )
    if cur.fetchone() is not None:
        cur.execute(
            "UPDATE jobber.concept_proposal SET occurrence_count = occurrence_count + 1 WHERE id = %s", (proposal_id,),
        )
    return {"proposal_id": str(proposal_id), "surface_form": normalized, "created_proposal": created_proposal}


def resolve_sources_for_concept(cur, proposal_ids: list[str], concept_id: str) -> int:
    """Called when concept_proposal rows resolve (accepted new / alias) to
    `concept_id`: map each originating Profile360 item back to it. Resolving
    the *vocabulary term* is not the human verdict on the *mapping*, so the
    mapping lands 'unreviewed' (basis follows the proposal's origin) and still
    goes through the normal accept/reject step — same convention as
    role_requirements.resolve_occurrences_for_concept. Returns rows touched."""
    if not proposal_ids:
        return 0
    cur.execute("SELECT type_code FROM jobber.concept WHERE id = %s", (concept_id,))
    concept = cur.fetchone()
    if concept is None:
        return 0
    cur.execute(
        "SELECT source_kind, source_id, origin, extraction_run_id FROM jobber.concept_proposal_profile360_source "
        "WHERE concept_proposal_id = ANY(%s::uuid[])",
        (proposal_ids,),
    )
    touched = 0
    for src in cur.fetchall():
        c = KINDS[src["source_kind"]]
        if c["type_codes"] and concept["type_code"] not in c["type_codes"]:
            continue  # e.g. a capability proposal merged into a non-capability concept
        try:
            if c["get"](cur, str(src["source_id"])) is None:
                continue
        except p360.Profile360UnavailableError:
            continue
        upsert_mapping(
            cur, src["source_kind"], str(src["source_id"]), concept_id,
            basis="ai_suggested" if src["origin"] == "ai" else "curator_asserted",
            review_status="unreviewed", run_id=src["extraction_run_id"],
        )
        touched += 1
    return touched


# --- vocabulary search ------------------------------------------------------

def search_vocabulary(cur, q: str, kind: str | None = None, limit: int = 25) -> list[dict]:
    """Search the full active canonical vocabulary (name, alias, definition)
    — independent of, and not restricted to, the AI's top candidates. Name
    matches rank above alias, above definition-only matches."""
    q = (q or "").strip()
    if not q:
        return []
    type_codes = cfg(kind)["type_codes"] if kind else None
    like = f"%{q}%"
    clauses = ["c.status = 'active'", "(c.canonical_name ILIKE %s OR c.definition ILIKE %s OR a.alias_hit)"]
    params: list = [like, like]
    sql_type = ""
    if type_codes:
        sql_type = " AND c.type_code = ANY(%s)"
        params.append(type_codes)
    cur.execute(
        f"""
        SELECT c.id, c.type_code, c.canonical_name, c.definition,
               CASE WHEN LOWER(c.canonical_name) = LOWER(%s) THEN 0
                    WHEN c.canonical_name ILIKE %s THEN 1
                    WHEN a.alias_hit THEN 2 ELSE 3 END AS match_rank
        FROM jobber.concept c
        CROSS JOIN LATERAL (
            SELECT EXISTS (SELECT 1 FROM jobber.concept_alias al WHERE al.concept_id = c.id AND al.alias ILIKE %s) AS alias_hit
        ) a
        WHERE {' AND '.join(clauses)}{sql_type}
        ORDER BY match_rank, c.canonical_name
        LIMIT %s
        """,
        [q, like, like, *params, limit],
    )
    return [{"id": str(r["id"]), "type_code": r["type_code"], "canonical_name": r["canonical_name"],
             "definition": r["definition"]} for r in cur.fetchall()]


# --- workbench (everything the UI needs for one item) -----------------------

def mapping_state(statuses: list[str]) -> str:
    if "accepted" in statuses:
        return "mapped"
    if "unreviewed" in statuses:
        return "pending"
    return "unmapped"


def mapping_states_for(cur, kind: str, source_ids: list[str]) -> dict[str, dict]:
    """Bulk {source_id: {state, counts}} for list views."""
    c = cfg(kind)
    out = {sid: {"state": "unmapped", "counts": {"accepted": 0, "unreviewed": 0, "rejected": 0}} for sid in source_ids}
    if not source_ids:
        return out
    cur.execute(
        f"SELECT {c['source_col']} AS sid, review_status, COUNT(*) AS n FROM jobber.{c['mapping_table']} "
        f"WHERE {c['source_col']} = ANY(%s::uuid[]) GROUP BY 1, 2",
        (source_ids,),
    )
    for r in cur.fetchall():
        out[str(r["sid"])]["counts"][r["review_status"]] = r["n"]
    for v in out.values():
        v["state"] = mapping_state([s for s, n in v["counts"].items() if n])
    return out


def get_workbench(cur, kind: str, source_id: str) -> dict:
    c = cfg(kind)
    row = c["get"](cur, source_id)
    if row is None:
        raise MappingError(f"profile360 {kind} not found", 404)

    cur.execute(
        f"""
        SELECT m.id, m.mapping_basis, m.review_status, m.reviewed_at, m.created_at, m.extraction_run_id,
               co.id AS concept_id, co.canonical_name, co.type_code, co.definition
        FROM jobber.{c['mapping_table']} m JOIN jobber.concept co ON co.id = m.{c['concept_col']}
        WHERE m.{c['source_col']} = %s ORDER BY m.created_at
        """,
        (source_id,),
    )
    mappings = [{**r, "id": str(r["id"]), "concept_id": str(r["concept_id"])} for r in cur.fetchall()]
    by_concept = {m["concept_id"]: m for m in mappings}

    cur.execute(
        f"SELECT id, task, status, started_at, notes, error_message FROM jobber.extraction_run "
        f"WHERE {c['run_col']} = %s ORDER BY started_at DESC, id LIMIT 1",
        (source_id,),
    )
    run = cur.fetchone()
    candidates: list[dict] = []
    ai_outcome = None
    if run:
        cur.execute(
            """
            SELECT k.rank, k.similarity, k.ai_selected, co.id, co.canonical_name, co.type_code, co.definition
            FROM jobber.profile360_mapping_candidate k JOIN jobber.concept co ON co.id = k.concept_id
            WHERE k.extraction_run_id = %s ORDER BY k.rank
            """,
            (run["id"],),
        )
        for r in cur.fetchall():
            cid = str(r["id"])
            existing = by_concept.get(cid)
            candidates.append({
                "concept_id": cid, "canonical_name": r["canonical_name"], "type_code": r["type_code"],
                "definition": r["definition"], "rank": r["rank"], "similarity": r["similarity"],
                "ai_selected": r["ai_selected"],
                "mapping_id": existing["id"] if existing else None,
                "mapping_status": existing["review_status"] if existing else None,
            })
        if run["status"] == "failed":
            ai_outcome = "failed"
        elif not candidates:
            ai_outcome = "no_candidates_available"
        elif any(k["ai_selected"] for k in candidates):
            ai_outcome = "recommended"
        else:
            ai_outcome = "declined_all_candidates"

    cur.execute(
        """
        SELECT cp.id, cp.surface_form, cp.suggested_type, cp.suggested_definition, cp.status, cp.nearest_similarity,
               cp.resolved_concept_id, s.origin, s.source_text, s.extraction_run_id, s.created_at,
               nc.id AS nearest_concept_id, nc.canonical_name AS nearest_canonical_name,
               rc.canonical_name AS resolved_canonical_name
        FROM jobber.concept_proposal_profile360_source s
        JOIN jobber.concept_proposal cp ON cp.id = s.concept_proposal_id
        LEFT JOIN jobber.concept nc ON nc.id = cp.nearest_concept_id
        LEFT JOIN jobber.concept rc ON rc.id = cp.resolved_concept_id
        WHERE s.source_kind = %s AND s.source_id = %s ORDER BY s.created_at DESC
        """,
        (kind, source_id),
    )
    proposals = [
        {**r, "id": str(r["id"]),
         "resolved_concept_id": str(r["resolved_concept_id"]) if r["resolved_concept_id"] else None,
         "nearest_concept_id": str(r["nearest_concept_id"]) if r["nearest_concept_id"] else None}
        for r in cur.fetchall()
    ]

    return {
        "kind": kind,
        "item": {**row, "_display": p360.display_text(row)},
        "mapping_state": mapping_state([m["review_status"] for m in mappings]),
        "mappings": mappings,
        "candidates": candidates,
        "ai_outcome": ai_outcome,
        "latest_run": ({"id": str(run["id"]), "task": run["task"], "status": run["status"],
                        "started_at": run["started_at"], "reasoning": run["notes"],
                        "error": run["error_message"]} if run else None),
        "proposals": proposals,
        "allowed_type_codes": c["type_codes"],
    }
