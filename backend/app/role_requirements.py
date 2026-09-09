"""Canonical role requirement evidence loader (Phase 4 prompt §2).

The Phase 3 capability engine (`capability_engine.derive_role_fit`) originally
read only `jobber.requirement_claim` for a role's requirements. Production's
historical/current corpus overwhelmingly carries its role-side requirement
evidence in `jobber.role_skill_observation` instead (canonical_concept_id
populated through vocabulary curation) — currently 0 capability
requirement_claim rows exist in production against ~1,248 mapped capability
observations. Relying solely on requirement_claim would make every
historical role look requirement-free.

This module is the one place that decides which of the two evidence sources
is authoritative for a given role, so `capability_engine.py` (role fit) and
`economics_engine.py` (archetype demand) read requirement evidence
identically rather than duplicating this decision.

Semantics (prompt §2, "consistent with the existing Role Detail fallback
philosophy" — see `db.role_skills_with_fallback`, which solves the same
two-source problem for display purposes, though with the opposite
precedence: that function prefers role_skill_observation and falls back to
requirement_claim only when it is empty. Here the priority is reversed
because requirement_claim is the more evidence-rich, review-gated, span-
validated pipeline and should win whenever it has anything usable to say):

1. If a role has *usable* requirement_claim rows — non-superseded
   (`superseded_by IS NULL`) and non-rejected (`review_status != 'rejected'`)
   — those are authoritative. Rejected/superseded rows do not count towards
   "usable" and are never returned, so a role whose only claims were
   rejected falls through to the observation fallback rather than being
   treated as having zero requirements.
2. Otherwise, fall back to `role_skill_observation` rows whose
   `canonical_concept_id` resolves to an *active* canonical concept.
   Unmapped observations (`canonical_concept_id IS NULL`, or resolving to a
   non-active concept) are ignored — never surfaced as a requirement.
3. The two sources are never merged for one role. Exactly one is used.
4. `basis`/`review_status`/`evidence_span` are requirement_claim-only
   concepts — a role_skill_observation-sourced item carries `None` for all
   three rather than a fabricated "stated" value (prompt point 6: never
   upgrade a legacy observation to stated evidence merely because it
   exists).
5. `requirement_type`/`importance` are preserved where present on either
   source; a legacy observation with no stored requirement_type carries
   `None` rather than an invented default. `capability_engine.py`'s
   downstream weighting/blocking-gap logic already treats an unrecognised
   requirement_type as neither `required` nor contributing extra weight, so
   this is not a new gap.
"""

SOURCE_CLAIM = "claim"
SOURCE_OBSERVATION = "role_skill_observation"


def _load_requirement_claims(cur, role_instance_id: str) -> list[dict]:
    cur.execute(
        """
        SELECT rc.id, rc.requirement_type, rc.importance, rc.basis, rc.review_status, rc.evidence_span,
               c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.requirement_claim rc
        JOIN jobber.concept c ON c.id = rc.concept_id
        WHERE rc.role_instance_id = %s AND rc.superseded_by IS NULL AND rc.review_status != 'rejected'
        ORDER BY rc.requirement_type, c.canonical_name
        """,
        (role_instance_id,),
    )
    items = []
    for r in cur.fetchall():
        items.append(
            {
                "concept_id": str(r["concept_id"]),
                "canonical_name": r["canonical_name"],
                "type_code": r["type_code"],
                "requirement_type": r["requirement_type"],
                "importance": r["importance"],
                "source": SOURCE_CLAIM,
                "requirement_claim_id": str(r["id"]),
                "role_skill_observation_id": None,
                "basis": r["basis"],
                "review_status": r["review_status"],
                "evidence_span": r["evidence_span"],
            }
        )
    return items


def _load_mapped_observations(cur, role_instance_id: str) -> list[dict]:
    cur.execute(
        """
        SELECT rso.id, rso.requirement_type, rso.importance,
               c.id AS concept_id, c.canonical_name, c.type_code
        FROM jobber.role_skill_observation rso
        JOIN jobber.concept c ON c.id = rso.canonical_concept_id
        WHERE rso.role_instance_id = %s AND c.status = 'active'
        ORDER BY c.canonical_name
        """,
        (role_instance_id,),
    )
    items = []
    for r in cur.fetchall():
        items.append(
            {
                "concept_id": str(r["concept_id"]),
                "canonical_name": r["canonical_name"],
                "type_code": r["type_code"],
                "requirement_type": r["requirement_type"],
                "importance": r["importance"],
                "source": SOURCE_OBSERVATION,
                "requirement_claim_id": None,
                "role_skill_observation_id": str(r["id"]),
                "basis": None,
                "review_status": None,
                "evidence_span": None,
            }
        )
    return items


def load_role_requirements(cur, role_instance_id: str) -> list[dict]:
    """The canonical requirement-evidence loader for one role. Returns a
    list of dicts (see module docstring for the exact shape/semantics).
    Never raises for a role with no requirements of either kind — returns
    `[]`."""
    claims = _load_requirement_claims(cur, role_instance_id)
    if claims:
        return claims
    return _load_mapped_observations(cur, role_instance_id)
