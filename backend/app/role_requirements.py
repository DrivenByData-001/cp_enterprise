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
   `canonical_concept_id` resolves to an *active* canonical concept —
   **except** for a concept the curator has already spoken on via a
   rejected or superseded requirement_claim on this same role. Curator
   authority always outranks a legacy observation: a rejected claim for
   "Python" must prevent a legacy "Python" observation from resurrecting
   that same requirement, even though no *usable* claim exists to be
   authoritative in its place. An unrelated concept's observation (e.g.
   "SQL") is unaffected and still participates in fallback normally. This
   only ever applies within the fallback branch — reaching it already means
   no concept on this role has a usable claim (see point 1), so every
   requirement_claim row found here is, by construction, rejected and/or
   superseded.
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

# Shared by comparison, economics, filters and facet counts. Claims win at
# role level; curator rejection/supersession vetoes legacy resurrection.
REQUIREMENT_EVIDENCE_SQL = """
WITH usable_claims AS (
    SELECT rc.role_instance_id, rc.concept_id, rc.id AS requirement_claim_id,
           NULL::uuid AS role_skill_observation_id, rc.requirement_type,
           rc.importance, rc.basis, rc.review_status, rc.evidence_span,
           'claim'::text AS source
    FROM jobber.requirement_claim rc
    WHERE rc.superseded_by IS NULL AND rc.review_status != 'rejected'
), evidence AS (
    SELECT * FROM usable_claims
    UNION ALL
    SELECT rso.role_instance_id, rso.canonical_concept_id, NULL::uuid, rso.id,
           rso.requirement_type, rso.importance, NULL::text, NULL::text, NULL::text,
           'role_skill_observation'::text
    FROM jobber.role_skill_observation rso
    JOIN jobber.concept c ON c.id = rso.canonical_concept_id AND c.status = 'active'
    WHERE NOT EXISTS (SELECT 1 FROM usable_claims u WHERE u.role_instance_id = rso.role_instance_id)
      AND NOT EXISTS (
          SELECT 1 FROM jobber.requirement_claim veto
          WHERE veto.role_instance_id = rso.role_instance_id
            AND veto.concept_id = rso.canonical_concept_id
            AND (veto.review_status = 'rejected' OR veto.superseded_by IS NOT NULL)
      )
)
SELECT e.*, c.canonical_name, c.type_code, c.status AS concept_status
FROM evidence e JOIN jobber.concept c ON c.id = e.concept_id
"""


def load_role_requirements_bulk(cur, role_ids: list[str]) -> dict[str, list[dict]]:
    if not role_ids:
        return {}
    cur.execute("SELECT * FROM (" + REQUIREMENT_EVIDENCE_SQL + ") requirements "
                "WHERE role_instance_id = ANY(%s::uuid[]) ORDER BY requirement_type, canonical_name", (role_ids,))
    grouped = {str(key): [] for key in role_ids}
    for row in cur.fetchall():
        item = dict(row)
        role_id = str(item.pop("role_instance_id"))
        for key in ("concept_id", "requirement_claim_id", "role_skill_observation_id"):
            item[key] = str(item[key]) if item[key] is not None else None
        grouped[role_id].append(item)
    return grouped


def load_role_requirements(cur, role_instance_id: str) -> list[dict]:
    return load_role_requirements_bulk(cur, [role_instance_id]).get(str(role_instance_id), [])
