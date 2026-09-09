"""Accepted-vocabulary maintenance: curator editing of an already-accepted
canonical concept's metadata (canonical name, type, definition, active/
deprecated status) and aliases (cp_round_of_changes.md §B). Distinct from
`vocabulary_curation.py`, which resolves *pending* `concept_proposal` rows
into brand-new concepts — every function here only ever edits a concept that
already exists.

"Accepted means curated/protected from automation, NOT immutable" (§B):
nothing here is reachable from any scheduled job or bootstrap pass — every
function is called only from a route a curator hits deliberately.
`app/vocabulary_bootstrap.py` never `UPDATE`s `jobber.concept` for an
existing row (it only ever `INSERT`s new proposals/concepts), so a curator's
edits here can never be silently overwritten by an automated run.

Type changes (§B: "Do not blindly UPDATE type_code where doing so would
break capability invariants") are the one non-trivial part of this module.
A type change is only ever applied after `_edge_violations` confirms every
`jobber.concept_edge` row touching this concept would still satisfy
`jobber.concept_edge_rule` under the *new* type — otherwise the whole change
is rejected with a 409 naming the conflicting edge(s), rather than silently
leaving an edge (or a `capability_detail` row) that no longer makes sense.
A transition *into* 'capability' requires the caller to supply
`capability_detail` fields (at least `demonstration_standard`, that table's
own NOT NULL column) in the same call; a transition *out of* 'capability'
removes the row's now-meaningless `capability_detail` sidecar. Concepts
themselves are never hard-deleted anywhere in this module — deprecation
(`status='deprecated'`) is the only "remove from active use" path, mirroring
the same convention `jobber.concept.status` already uses for capabilities
(CapabilityUpdate).

Two integrity-only follow-ups (still §B, not a new feature):

- **Active-vocabulary surface-form integrity.** A new alias, or a renamed
  canonical name, must not already denote another *active* concept (as
  either its canonical_name or one of its own aliases) — `_active_surface_
  form_conflict` rejects that with a 409 naming the conflicting concept.
  This is deliberately application-level, not a DB-wide UNIQUE constraint:
  `jobber.concept`'s status/lifecycle means a 'proposed'/'deprecated'/
  'merged'/'rejected' concept's canonical_name or alias must never block an
  active one from using the same surface form — a UNIQUE index spanning all
  statuses would get that wrong.
- **Concept embedding invalidation.** Renaming a concept or editing its
  definition changes what it *means* semantically, so any existing
  `jobber.d_embedding` row for it (owner_kind='concept') is deleted in the
  same transaction as the metadata update — never regenerated synchronously
  here. `concept_linking.ensure_concept_embeddings` already treats a
  missing current-model vector as "needs backfilling" and recomputes it
  lazily off the concept's (now-current) canonical_name/definition next
  time it runs, so this needs no new regeneration path. A type-only or
  status-only edit never touches embeddings — neither one is embedded text."""

from datetime import datetime, timezone

import psycopg
from fastapi import HTTPException

from .concept_linking import normalize_name

# Concepts editable through this module. 'proposed' concepts don't occur in
# practice (every creation path — ConceptCreate, resolve_surface_form_group —
# inserts 'active' directly), and 'merged'/'rejected' concepts are the
# capability-merge/proposal-rejection flows' own business, not this generic
# editor's — so both are rejected here with a clear 409 rather than silently
# reanimating a concept another flow already resolved.
_EDITABLE_STATUSES = ("active", "deprecated")


def _active_surface_form_conflict(cur, normalized_value: str, *, exclude_concept_id: str) -> dict | None:
    """Does `normalized_value` (already run through `concept_linking.
    normalize_name` — the same case-fold + collapse-whitespace semantics
    `exact_match_concept_id` uses, not a new incompatible definition of
    "the same surface form") already denote another *active* concept's
    canonical_name or one of its aliases? Only 'active' concepts are ever a
    conflict *source* — a deprecated concept's old name/aliases must never
    block a new active one from reusing that surface form. Returns
    {id, canonical_name} of the conflicting concept (enough for a curator to
    go resolve it) or None if clear."""
    cur.execute(
        "SELECT id, canonical_name FROM jobber.concept "
        "WHERE status = 'active' AND id != %s AND LOWER(canonical_name) = %s",
        (exclude_concept_id, normalized_value),
    )
    row = cur.fetchone()
    if row:
        return {"id": str(row["id"]), "canonical_name": row["canonical_name"]}
    cur.execute(
        """
        SELECT c.id, c.canonical_name FROM jobber.concept_alias a
        JOIN jobber.concept c ON c.id = a.concept_id
        WHERE c.status = 'active' AND c.id != %s AND LOWER(a.alias) = %s
        """,
        (exclude_concept_id, normalized_value),
    )
    row = cur.fetchone()
    return {"id": str(row["id"]), "canonical_name": row["canonical_name"]} if row else None


def _conflict_error(surface_form: str, conflict: dict) -> HTTPException:
    return HTTPException(
        409,
        f"{surface_form!r} already denotes the active concept {conflict['canonical_name']!r} "
        f"(id {conflict['id']}) — choose a different surface form or resolve the ambiguity on that concept first",
    )


def _edge_violations(cur, concept_id: str, new_type_code: str) -> list[str]:
    """Every `jobber.concept_edge` row touching `concept_id` that would no
    longer satisfy `jobber.concept_edge_rule` if this concept's type became
    `new_type_code` — the safety gate for §B's "Do not blindly UPDATE
    type_code where doing so would break capability invariants." An empty
    list means the type change is safe to apply. Checks every edge
    regardless of status (proposed or accepted) — a proposed edge that would
    become nonsensical is still worth blocking on, not just accepted ones."""
    cur.execute(
        """
        SELECT ce.id, ce.relation, ce.from_concept_id, ce.to_concept_id,
               fc.type_code AS from_type, fc.canonical_name AS from_name,
               tc.type_code AS to_type, tc.canonical_name AS to_name
        FROM jobber.concept_edge ce
        JOIN jobber.concept fc ON fc.id = ce.from_concept_id
        JOIN jobber.concept tc ON tc.id = ce.to_concept_id
        WHERE ce.from_concept_id = %s OR ce.to_concept_id = %s
        """,
        (concept_id, concept_id),
    )
    edges = cur.fetchall()
    if not edges:
        return []

    violations = []
    for edge in edges:
        from_type = new_type_code if str(edge["from_concept_id"]) == str(concept_id) else edge["from_type"]
        to_type = new_type_code if str(edge["to_concept_id"]) == str(concept_id) else edge["to_type"]
        cur.execute(
            "SELECT 1 FROM jobber.concept_edge_rule WHERE relation = %s AND from_type = %s AND to_type = %s",
            (edge["relation"], from_type, to_type),
        )
        if not cur.fetchone():
            violations.append(
                f"'{edge['relation']}' edge between {edge['from_name']!r} and {edge['to_name']!r} "
                f"would no longer be a valid relation ({from_type} -> {to_type})"
            )
    return violations


def _serialize(cur, concept_id: str) -> dict:
    cur.execute("SELECT * FROM jobber.concept WHERE id = %s", (concept_id,))
    concept = dict(cur.fetchone())
    concept["id"] = str(concept["id"])
    cur.execute("SELECT id, alias, origin FROM jobber.concept_alias WHERE concept_id = %s ORDER BY alias", (concept_id,))
    concept["aliases"] = [{**dict(a), "id": str(a["id"])} for a in cur.fetchall()]
    return concept


def update_concept_metadata(cur, concept_id: str, patch: dict, *, now: datetime | None = None) -> dict:
    """Applies `patch` (already `exclude_unset`-filtered by the route layer,
    so a key's mere presence means "the caller wants this changed") to one
    concept. Raises HTTPException(404) if the concept doesn't exist, 409 if
    it isn't in an editable status or the requested type change is unsafe,
    400 on a canonical-name collision."""
    now = now or datetime.now(timezone.utc)
    cur.execute("SELECT * FROM jobber.concept WHERE id = %s", (concept_id,))
    concept = cur.fetchone()
    if not concept:
        raise HTTPException(404, "concept not found")
    if concept["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(409, f"concept has status {concept['status']!r} and cannot be edited here")

    old_canonical_name = concept["canonical_name"]
    old_definition = concept["definition"]

    concept_fields: dict = {}
    if "canonical_name" in patch and patch["canonical_name"] is not None:
        new_name = patch["canonical_name"]
        conflict = _active_surface_form_conflict(cur, normalize_name(new_name), exclude_concept_id=concept_id)
        if conflict:
            raise _conflict_error(new_name, conflict)
        concept_fields["canonical_name"] = new_name
    if "definition" in patch:
        concept_fields["definition"] = patch["definition"]
    if "status" in patch and patch["status"] is not None:
        concept_fields["status"] = patch["status"]

    old_type = concept["type_code"]
    new_type = patch.get("type_code")
    capability_detail = patch.get("capability_detail")

    if new_type is not None and new_type != old_type:
        cur.execute("SELECT code FROM jobber.concept_type WHERE code = %s", (new_type,))
        if not cur.fetchone():
            raise HTTPException(400, f"unknown type_code {new_type!r}")

        violations = _edge_violations(cur, concept_id, new_type)
        if violations:
            raise HTTPException(
                409,
                {
                    "message": "type change would break existing concept relationships",
                    "conflicts": violations,
                },
            )

        if new_type == "capability" and (not capability_detail or not capability_detail.get("demonstration_standard")):
            raise HTTPException(
                409,
                "changing type to 'capability' requires capability_detail.demonstration_standard in the same request",
            )

        if old_type == "capability" and new_type != "capability":
            cur.execute(
                "SELECT 1 FROM jobber.concept_edge WHERE to_concept_id = %s AND relation IN ('component_of', 'demands')",
                (concept_id,),
            )
            if cur.fetchone():
                raise HTTPException(
                    409,
                    "concept is a capability with existing component/demand edges — remove them "
                    "(via the Capabilities page) before changing its type",
                )

        concept_fields["type_code"] = new_type

    if concept_fields:
        set_clause = ", ".join(f"{k} = %s" for k in concept_fields)
        try:
            cur.execute(
                f"UPDATE jobber.concept SET {set_clause}, reviewed_at = %s WHERE id = %s",
                [*concept_fields.values(), now, concept_id],
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(400, "a concept with this type and canonical name already exists")

        # A concept's embedded text is canonical_name (+ definition) —
        # concept_linking.ensure_concept_embeddings. Either one actually
        # changing value invalidates any existing vector; a type/status-only
        # edit (or a no-op resubmission of the same name/definition) does
        # not. Deleted here, never regenerated synchronously — the lazy
        # backfill in ensure_concept_embeddings recomputes it next time it
        # runs, off the now-current text.
        semantically_changed = (
            "canonical_name" in concept_fields and concept_fields["canonical_name"] != old_canonical_name
        ) or ("definition" in concept_fields and concept_fields["definition"] != old_definition)
        if semantically_changed:
            cur.execute("DELETE FROM jobber.d_embedding WHERE owner_kind = 'concept' AND owner_id = %s", (concept_id,))

        if "type_code" in concept_fields:
            if new_type == "capability":
                cur.execute(
                    """
                    INSERT INTO jobber.capability_detail
                        (concept_id, demonstration_standard, min_depth, min_autonomy, requires_all_core,
                         min_core_required, economic_salience, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (concept_id) DO UPDATE SET
                        demonstration_standard = EXCLUDED.demonstration_standard,
                        min_depth = EXCLUDED.min_depth,
                        min_autonomy = EXCLUDED.min_autonomy,
                        requires_all_core = EXCLUDED.requires_all_core,
                        min_core_required = EXCLUDED.min_core_required,
                        economic_salience = EXCLUDED.economic_salience,
                        notes = EXCLUDED.notes
                    """,
                    (
                        concept_id,
                        capability_detail["demonstration_standard"],
                        capability_detail.get("min_depth") or "owned",
                        capability_detail.get("min_autonomy"),
                        capability_detail.get("requires_all_core", True),
                        capability_detail.get("min_core_required"),
                        capability_detail.get("economic_salience"),
                        capability_detail.get("notes"),
                    ),
                )
            elif old_type == "capability":
                cur.execute("DELETE FROM jobber.capability_detail WHERE concept_id = %s", (concept_id,))

    return _serialize(cur, concept_id)


def add_alias(cur, concept_id: str, alias: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cur.execute("SELECT id FROM jobber.concept WHERE id = %s", (concept_id,))
    if not cur.fetchone():
        raise HTTPException(404, "concept not found")
    alias = alias.strip()
    if not alias:
        raise HTTPException(400, "alias must not be empty")

    conflict = _active_surface_form_conflict(cur, normalize_name(alias), exclude_concept_id=concept_id)
    if conflict:
        raise _conflict_error(alias, conflict)

    try:
        cur.execute(
            "INSERT INTO jobber.concept_alias (concept_id, alias, origin, created_at) "
            "VALUES (%s, %s, 'curator', %s) RETURNING id",
            (concept_id, alias, now),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(400, "this alias already exists on this concept")
    alias_id = str(cur.fetchone()["id"])
    return {"id": alias_id, "alias": alias, "status": "created"}


def remove_alias(cur, concept_id: str, alias_id: str) -> None:
    cur.execute("DELETE FROM jobber.concept_alias WHERE id = %s AND concept_id = %s", (alias_id, concept_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "alias not found on this concept")
