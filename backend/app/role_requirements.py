"""Canonical role requirement evidence loader (Phase 4 prompt §2, tightened
by the requirement-review curation gate build).

The Phase 3 capability engine (`capability_engine.derive_role_fit`) originally
read only `jobber.requirement_claim` for a role's requirements. Production's
historical/current corpus overwhelmingly carries its role-side requirement
evidence in `jobber.role_skill_observation` instead (canonical_concept_id
populated through vocabulary curation) — currently 0 capability
requirement_claim rows exist in production against ~1,248 mapped capability
observations. Relying solely on requirement_claim would make every
historical role look requirement-free.

This module is the one place that decides which of the two evidence sources
is authoritative for a given role, so `capability_engine.py` (role fit),
`economics_engine.py` (archetype demand), `routes/comparison.py`,
`routes/concepts.py` (facets) and `stepping_stones.py` (target-path
analysis) read requirement evidence identically rather than duplicating this
decision.

Semantics (prompt §2, "consistent with the existing Role Detail fallback
philosophy" — see `db.role_skills_display`, which solves the same two-source
problem for *display* purposes by reusing this loader's own output directly:
its claim-sourced items become Role Detail's "reviewed requirements", and
every role_skill_observation not already covered by one of those (and not
vetoed) becomes its separately-labelled "legacy skills" — never a stale
observation outranking, or silently standing in for, a human-reviewed claim
for the same concept):

1. If a role has *usable* requirement_claim rows — current (`superseded_by
   IS NULL`) and **accepted** (`review_status = 'accepted'`) — those are
   authoritative. An unreviewed claim is a visible *proposal* only: it must
   never become analytically authoritative merely because the user has not
   rejected it yet (the requirement-review curation gate's core rule).
   Rejected/corrected/superseded rows never count towards "usable" either,
   so a role whose only claims are unreviewed and/or rejected falls through
   to the observation fallback rather than being treated as having zero
   requirements. Use `load_requirement_review_summary`/`_bulk` alongside
   this loader when a consumer needs to know whether unreviewed claims are
   being silently excluded (i.e. whether review is "complete") — this
   loader alone cannot express that; it only ever returns usable evidence.
2. Otherwise, fall back to `role_skill_observation` rows whose
   `canonical_concept_id` resolves to an *active* canonical concept —
   **except** for a concept the curator has explicitly rejected or corrected
   via a requirement_claim on this same role. Curator authority always
   outranks a legacy observation: a rejected (or human-corrected-away) claim
   for "Python" must prevent a legacy "Python" observation from resurrecting
   that same requirement, even though no *usable* claim exists to be
   authoritative in its place. An unrelated concept's observation (e.g.
   "SQL") is unaffected and still participates in fallback normally. This
   only ever applies within the fallback branch — reaching it already means
   no concept on this role has a usable claim (see point 1).
   Unmapped observations (`canonical_concept_id IS NULL`, or resolving to a
   non-active concept) are ignored — never surfaced as a requirement.
   The veto is keyed on `review_status IN ('rejected', 'corrected')` rather
   than merely `superseded_by IS NOT NULL`: re-extraction (see
   `extraction.py`'s conservative rerun-supersession handling) can chain an
   *unreviewed* proposal's `superseded_by` to a fresher unreviewed proposal
   with no human ever having looked at either — that is proposal churn, not
   a curator decision, and per point 1's core rule it must not gain veto
   power over the fallback it would otherwise never have blocked. Only a
   claim a human actually rejected, or actually corrected (which — see
   `routes/role_instances.py`'s edit/accept endpoint — always both sets
   `superseded_by` *and* flips `review_status` to `'corrected'` in the same
   transaction), represents real curator authority.
3. The two sources are never merged for one role. Exactly one is used.
4. `basis`/`review_status`/`evidence_span` are requirement_claim-only
   concepts — a role_skill_observation-sourced item carries `None` for all
   three rather than a fabricated "stated" value (never upgrade a legacy
   observation to stated evidence merely because it exists).
5. `requirement_type`/`importance` are preserved where present on either
   source; a legacy observation with no stored requirement_type carries
   `None` rather than an invented default. `capability_engine.py`'s
   downstream weighting/blocking-gap logic already treats an unrecognised
   requirement_type as neither `required` nor contributing extra weight, so
   this is not a new gap.
"""

SOURCE_CLAIM = "claim"
SOURCE_OBSERVATION = "role_skill_observation"

# Shared by comparison, economics, filters and facet counts. Accepted claims
# win at role level; curator rejection/correction vetoes legacy resurrection.
# review_status is hardcoded to 'accepted' (not parameterised) precisely so
# an unreviewed or rejected claim can never slip back into "usable" here —
# see module docstring point 1.
REQUIREMENT_EVIDENCE_SQL = """
WITH usable_claims AS (
    SELECT rc.role_instance_id, rc.concept_id, rc.id AS requirement_claim_id,
           NULL::uuid AS role_skill_observation_id, rc.requirement_type,
           rc.importance, rc.basis, rc.review_status, rc.evidence_span,
           'claim'::text AS source
    FROM jobber.requirement_claim rc
    WHERE rc.superseded_by IS NULL AND rc.review_status = 'accepted'
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
            AND veto.review_status IN ('rejected', 'corrected')
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


def vetoed_concept_ids(cur, role_instance_id: str) -> set[str]:
    """Concepts a curator has actively rejected or corrected away for this
    role *and never re-accepted since* — real curator authority that must
    prevent a legacy `role_skill_observation` for the same concept from
    reappearing wherever it might otherwise resurface, not only in this
    module's own fallback branch. `db.role_skills_display` uses this too:
    its `legacy_skills` list would otherwise keep showing a concept as a
    legacy skill even after a curator explicitly rejected it as a
    requirement.

    The "never re-accepted since" half matters and is not redundant: unlike
    this module's own SQL-embedded veto (which only ever runs once a role
    has *zero* current accepted claims at all, so a concept with a live
    accepted claim can never reach it), `role_skills_display` calls this
    standalone helper regardless of what else the role has accepted.
    A claim corrected from "Python, required" to "Python, preferred" (same
    concept, still current and accepted — routes/role_instances.py's edit
    endpoint marks the *old* row 'corrected' even when the concept itself
    didn't change) must not veto Python: there is a live accepted claim for
    it right now, so excluding it would silently drop a legitimate,
    currently-accepted requirement from display, not correct a stale one.
    A claim actually rejected, or corrected *away to a different concept*
    with nothing current left behind for the original one, still vetoes —
    excluded here by requiring a matching *current* accepted row for the
    same concept_id, which only the "graded, not remapped" case has."""
    cur.execute(
        """
        SELECT DISTINCT veto.concept_id
        FROM jobber.requirement_claim veto
        WHERE veto.role_instance_id = %s
          AND veto.review_status IN ('rejected', 'corrected')
          AND NOT EXISTS (
              SELECT 1 FROM jobber.requirement_claim current
              WHERE current.role_instance_id = veto.role_instance_id
                AND current.concept_id = veto.concept_id
                AND current.superseded_by IS NULL
                AND current.review_status = 'accepted'
          )
        """,
        (role_instance_id,),
    )
    return {str(r["concept_id"]) for r in cur.fetchall()}


# --- Review-summary helper --------------------------------------------------
#
# The authoritative loader above deliberately *excludes* unreviewed/rejected
# claims — that's the whole point of the curation gate. But excluding them
# from the authoritative set must not make pending review invisible: a
# consumer (the Requirements review UI, Role Detail's pending-review
# indicator, Comparison's "review incomplete" banner, target/stepping-stone
# analysis) needs to know *separately* whether a role's requirement review is
# still in progress, so it never presents a partially-reviewed requirement
# set as final.
#
# "Complete" must also account for extraction's *other* output: a surface
# form extraction could not resolve to any concept becomes a
# jobber.concept_proposal (never a requirement_claim at all — see
# extraction.py), deduplicated globally by surface form for vocabulary
# curation (one decision per term). concept_proposal's own document_id only
# ever remembers the *first* role/document that produced a given unresolved
# term — a later role hitting the same still-pending term bumps its
# occurrence_count but is otherwise invisible through that column.
# jobber.concept_proposal_occurrence (migration 0021) is the per-(proposal,
# role) link that makes every contributing role attributable, independent of
# concept_proposal's own dedup. A role can have every one of its
# requirement_claim rows accepted while still carrying an unresolved
# proposal it contributed to — that requirement is just as excluded from
# analysis as an unreviewed claim would be (it never became a claim to begin
# with), so `complete` must not be true while any remain.
#
# A proposal being *resolved* (accepted into the vocabulary, or merged into
# an existing concept) doesn't finish the story either — see "Closing the
# vocabulary-acceptance gap" below. `needs_reextraction` covers the residual
# case that fix can't always close on its own: an occurrence predating
# migration 0022 (or one migration 0021's own backfill created) has no
# requirement_type to build a faithful claim from, so nothing is fabricated
# and this role's review stays reported incomplete until requirement
# extraction actually runs again for it.

_REVIEW_STATUSES = ("accepted", "unreviewed", "rejected")


def load_requirement_review_summary_bulk(cur, role_ids: list[str]) -> dict[str, dict]:
    """Per role_instance_id: counts of *current* (superseded_by IS NULL)
    requirement_claim rows by review_status; `unresolved_proposals` (pending
    jobber.concept_proposal rows tied to the role's own source document —
    extraction output that never became a claim at all); `extraction_attempted`
    (whether a requirement_extract run has ever been recorded for this role,
    so a consumer can distinguish "never extracted" from "reviewed and
    genuinely complete" even though both currently have zero current claims);
    `needs_reextraction` (count of distinct concepts this role produced a
    now-resolved-proposal occurrence for that resolve_occurrences_for_concept
    could not turn into a claim — see "Closing the vocabulary-acceptance gap"
    below — and no current claim has appeared for since, by any other
    route); and `complete` (True iff there are zero current unreviewed
    claims, zero unresolved proposals, AND zero pending re-extraction need —
    vacuously true for a role with none of the three, e.g. one relying
    entirely on the legacy observation fallback). Superseded/corrected
    history is deliberately excluded from every claim count here — it is
    neither pending nor a current decision."""
    summary = {
        str(rid): {status: 0 for status in _REVIEW_STATUSES}
        | {"unresolved_proposals": 0, "extraction_attempted": False, "needs_reextraction": 0}
        for rid in role_ids
    }
    if not role_ids:
        return summary
    cur.execute(
        """
        SELECT role_instance_id, review_status, COUNT(*) AS n
        FROM jobber.requirement_claim
        WHERE superseded_by IS NULL AND role_instance_id = ANY(%s::uuid[])
        GROUP BY role_instance_id, review_status
        """,
        (role_ids,),
    )
    for row in cur.fetchall():
        role_id = str(row["role_instance_id"])
        if row["review_status"] in summary[role_id]:
            summary[role_id][row["review_status"]] += row["n"]

    cur.execute(
        """
        SELECT o.role_instance_id, COUNT(DISTINCT o.concept_proposal_id) AS n
        FROM jobber.concept_proposal_occurrence o
        JOIN jobber.concept_proposal cp ON cp.id = o.concept_proposal_id AND cp.status = 'pending'
        WHERE o.role_instance_id = ANY(%s::uuid[])
        GROUP BY o.role_instance_id
        """,
        (role_ids,),
    )
    for row in cur.fetchall():
        summary[str(row["role_instance_id"])]["unresolved_proposals"] = row["n"]

    cur.execute(
        """
        SELECT DISTINCT role_instance_id FROM jobber.extraction_run
        WHERE task = 'requirement_extract' AND role_instance_id = ANY(%s::uuid[])
        """,
        (role_ids,),
    )
    for row in cur.fetchall():
        summary[str(row["role_instance_id"])]["extraction_attempted"] = True

    cur.execute(_ROLES_NEEDING_REEXTRACTION_SQL, (list(_RESOLVED_PROPOSAL_STATUSES), role_ids))
    for row in cur.fetchall():
        summary[str(row["role_instance_id"])]["needs_reextraction"] = row["n"]

    for entry in summary.values():
        entry["complete"] = (
            entry["unreviewed"] == 0 and entry["unresolved_proposals"] == 0 and entry["needs_reextraction"] == 0
        )
    return summary


def load_requirement_review_summary(cur, role_instance_id: str) -> dict:
    return load_requirement_review_summary_bulk(cur, [role_instance_id])[str(role_instance_id)]


# --- Closing the vocabulary-acceptance gap ----------------------------------
#
# A concept_proposal being pending is not the only way a role's extracted
# requirement can be missing from analysis. Once a curator *resolves* a
# proposal in Vocabulary (accepts it as a new concept, or merges it into an
# existing one — vocabulary_curation.resolve_surface_form_group, statuses
# 'accepted_new'/'accepted_alias'), unresolved_proposals above correctly
# stops counting it — but unless something creates a requirement_claim for
# every role that produced it, the requirement itself never enters analysis
# at all: resolving the *vocabulary term* is not the same decision as
# accepting it as *this role's* requirement, and nothing did that automatically
# before this fix. `resolve_occurrences_for_concept` is that "something" —
# called by resolve_surface_form_group the moment a proposal resolves — and
# `_ROLES_NEEDING_REEXTRACTION_SQL` below is how a role whose occurrence
# lacked enough data to act on (see migration 0022) stays reported incomplete
# rather than silently dropping the gap. See docs/29 §12.

_RESOLVED_PROPOSAL_STATUSES = ("accepted_new", "accepted_alias")


def resolve_occurrences_for_concept(cur, proposal_ids: list[str], resolved_concept_id: str) -> dict:
    """Called immediately after concept_proposal rows (`proposal_ids`) resolve
    to `resolved_concept_id`. For every role that ever produced one of these
    proposals (concept_proposal_occurrence, migration 0021), exactly one of
    three things happens, never inventing evidence that was never captured:

    1. A current claim already exists for (role, resolved_concept_id) — from
       any source (a prior manual add, a prior extraction, a prior
       resolution). Nothing to do; already covered.
    2. No current claim exists, but the occurrence carries requirement_type
       (migration 0022 — only possible for an occurrence extraction.py wrote
       after that migration; never fabricated retroactively) — a fresh
       *unreviewed* requirement_claim is created, preserving whatever
       basis/evidence_span/document/extraction_run provenance the original
       extraction captured. Still requires human review: accepting a
       vocabulary term is a curation decision about the *concept*, not a
       verdict on whether it is actually a requirement for this particular
       role.
    3. No current claim exists and the occurrence has no requirement_type
       (an occurrence from before migration 0022, or from migration 0021's
       own backfill, neither of which could carry it) — nothing faithful can
       be built, so nothing is created. This role is picked up by
       `_ROLES_NEEDING_REEXTRACTION_SQL` below (derived live, not a stored
       flag — self-healing the moment a fresh extraction run, or a manual
       add, gives that concept a current claim) until requirement extraction
       runs again for it.

    One claim per role even when several occurrences of this same resolved
    concept exist for it (e.g. two clustered surface forms both extracted
    from the same posting) — migration 0020's unique index allows at most
    one current claim per (role, concept) regardless."""
    if not proposal_ids:
        return {"claims_created": 0, "already_covered": 0, "needs_reextraction": 0}
    cur.execute(
        """
        SELECT DISTINCT ON (o.role_instance_id)
               o.role_instance_id, o.document_id, o.extraction_run_id,
               o.requirement_type, o.basis, o.evidence_span
        FROM jobber.concept_proposal_occurrence o
        WHERE o.concept_proposal_id = ANY(%s::uuid[])
        ORDER BY o.role_instance_id, o.created_at
        """,
        (proposal_ids,),
    )
    occurrences = cur.fetchall()
    claims_created = already_covered = needs_reextraction = 0
    for occ in occurrences:
        role_id = str(occ["role_instance_id"])
        cur.execute(
            "SELECT 1 FROM jobber.requirement_claim WHERE role_instance_id = %s AND concept_id = %s AND superseded_by IS NULL",
            (role_id, resolved_concept_id),
        )
        if cur.fetchone():
            already_covered += 1
            continue
        # requirement_claim's own CHECK constraint (migration 0003) requires
        # a non-null evidence_span whenever basis is 'stated'/'implied' — a
        # real extraction run never produces that combination without one
        # (span_validation.validate_span already rejected the item
        # otherwise), but an occurrence is still just data: treat a
        # combination that wouldn't satisfy the constraint the same as
        # missing requirement_type, rather than letting a raw
        # CheckViolation surface from what should be a graceful "not enough
        # to build a faithful claim" case.
        if not occ["requirement_type"] or (occ["basis"] in ("stated", "implied") and not occ["evidence_span"]):
            needs_reextraction += 1
            continue
        cur.execute(
            """
            INSERT INTO jobber.requirement_claim
                (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span, extraction_run_id, review_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'unreviewed')
            """,
            (role_id, resolved_concept_id, occ["requirement_type"], occ["basis"], occ["document_id"], occ["evidence_span"], occ["extraction_run_id"]),
        )
        claims_created += 1
    return {"claims_created": claims_created, "already_covered": already_covered, "needs_reextraction": needs_reextraction}


# Live-derived, not a stored flag (see resolve_occurrences_for_concept's own
# docstring, case 3): a role whose resolved-proposal occurrence still has no
# current claim for that concept. Self-healing — the moment any current claim
# appears for that (role, concept) pair, from any source, this stops matching.
_ROLES_NEEDING_REEXTRACTION_SQL = """
    SELECT o.role_instance_id, COUNT(DISTINCT cp.resolved_concept_id) AS n
    FROM jobber.concept_proposal_occurrence o
    JOIN jobber.concept_proposal cp ON cp.id = o.concept_proposal_id
    WHERE cp.status = ANY(%s) AND cp.resolved_concept_id IS NOT NULL
      AND o.role_instance_id = ANY(%s::uuid[])
      AND NOT EXISTS (
          SELECT 1 FROM jobber.requirement_claim rc
          WHERE rc.role_instance_id = o.role_instance_id
            AND rc.concept_id = cp.resolved_concept_id
            AND rc.superseded_by IS NULL
      )
    GROUP BY o.role_instance_id
"""
