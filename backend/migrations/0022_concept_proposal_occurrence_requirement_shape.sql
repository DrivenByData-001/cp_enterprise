-- Requirement-review curation gate, code-review follow-up (round 3): a
-- resolved vocabulary proposal must not leave the requirement it represented
-- analytically lost.
--
-- When source-aware requirement extraction can't map a surface form to any
-- concept, it records a jobber.concept_proposal (+ concept_proposal_occurrence,
-- migration 0021) instead of a requirement_claim. Once a curator later
-- resolves that proposal in Vocabulary (accepts it as a new concept, or
-- merges it into an existing one), the proposal stops being "pending" — and
-- role_requirements.load_requirement_review_summary_bulk's unresolved_proposals
-- count (which only counts pending proposals) correctly stops flagging it.
-- But nothing before this migration ever created a requirement_claim for the
-- role(s) that produced it, so the requirement the extraction actually found
-- simply vanishes from analysis while the role's review now looks complete.
--
-- The fix (app/vocabulary_curation.py::resolve_surface_form_group, via
-- app/role_requirements.py::resolve_occurrences_for_concept) is to create an
-- unreviewed requirement_claim for each affected role the moment a proposal
-- resolves to a concept — still requiring human review (it was never a
-- human decision about *this role's* requirement, only about the
-- *vocabulary term*), but no longer silently absent from analysis once
-- accepted.
--
-- That needs occurrence-specific requirement data concept_proposal_occurrence
-- didn't carry: requirement_type is essential (a claim cannot exist without
-- one); basis/evidence_span preserve the original source provenance where
-- extraction had it (never fabricated — a legacy/inferred occurrence
-- legitimately carries NULL for both, same as any other inferred claim).
-- Nullable throughout: every occurrence row that already exists (this
-- migration's own backfill below, migration 0021's own backfill, and any
-- occurrence recorded before this column existed) has no way to supply this
-- retroactively, and app code must never invent it. Where a resolved
-- proposal's occurrence lacks requirement_type, there is nothing faithful to
-- build a claim from — role_requirements.py's review-summary query treats
-- that role as still needing requirement re-extraction (derived live, not a
-- stored flag: see that module) rather than fabricating a guess.
ALTER TABLE jobber.concept_proposal_occurrence
    ADD COLUMN IF NOT EXISTS requirement_type TEXT,
    ADD COLUMN IF NOT EXISTS basis             TEXT,
    ADD COLUMN IF NOT EXISTS evidence_span      TEXT;

-- One-time remediation for any proposal *already* resolved before this
-- migration ran (accepted_new/accepted_alias — a rejected/deferred proposal
-- has no concept to claim against, so is correctly excluded): create the
-- unreviewed claim now for exactly the cases where it safely can — an
-- occurrence with requirement_type data and no current claim already
-- covering that (role, concept) pair. Vacuous today (no occurrence row can
-- yet carry requirement_type — the columns above did not exist when any
-- extraction ran), and therefore a guaranteed no-op against any database
-- this ships against; kept so this migration is correct and safe to replay
-- against a database where it would not be vacuous, exactly like migration
-- 0021's own backfill. One claim per (role, resolved concept) even when
-- several surface forms/occurrences in the same resolved cluster point at
-- the same role (DISTINCT ON, earliest occurrence wins) — never two current
-- claims for the same pair, which migration 0020's unique index forbids.
INSERT INTO jobber.requirement_claim
    (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span, extraction_run_id, review_status)
SELECT DISTINCT ON (o.role_instance_id, cp.resolved_concept_id)
    o.role_instance_id, cp.resolved_concept_id, o.requirement_type, o.basis, o.document_id, o.evidence_span, o.extraction_run_id, 'unreviewed'
FROM jobber.concept_proposal_occurrence o
JOIN jobber.concept_proposal cp ON cp.id = o.concept_proposal_id
WHERE cp.status IN ('accepted_new', 'accepted_alias')
  AND cp.resolved_concept_id IS NOT NULL
  AND o.requirement_type IS NOT NULL
  -- requirement_claim's own CHECK constraint (migration 0003) requires a
  -- non-null evidence_span whenever basis is 'stated'/'implied' — skip
  -- (leave for live re-extraction detection, app/role_requirements.py)
  -- rather than let this INSERT fail outright on a combination that
  -- wouldn't satisfy it.
  AND (o.basis NOT IN ('stated', 'implied') OR o.evidence_span IS NOT NULL)
  AND NOT EXISTS (
      SELECT 1 FROM jobber.requirement_claim rc
      WHERE rc.role_instance_id = o.role_instance_id
        AND rc.concept_id = cp.resolved_concept_id
        AND rc.superseded_by IS NULL
  )
ORDER BY o.role_instance_id, cp.resolved_concept_id, o.created_at;
