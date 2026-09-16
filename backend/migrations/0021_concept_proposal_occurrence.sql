-- Requirement-review curation gate, code-review follow-up #3: per-role
-- attribution for unresolved (still-pending) concept_proposal rows.
--
-- jobber.concept_proposal is deduplicated globally by surface_form (doc 18
-- §3: "a term appearing in thirty postings is one decision") — correct for
-- vocabulary curation, but its own document_id/extraction_run_id columns
-- are only ever set once, on first insert, and then left alone
-- (`document_id = COALESCE(document_id, %s)` in extraction.py): a second
-- role whose extraction later produces the exact same unresolved surface
-- form bumps occurrence_count but never becomes attributable through those
-- columns — the proposal still "belongs" to whichever role happened to
-- extract it first.
--
-- role_requirements.load_requirement_review_summary_bulk's
-- unresolved_proposals count (an extracted requirement that never became a
-- requirement_claim at all, because it couldn't be mapped to any concept,
-- is just as excluded from analysis as an unreviewed claim) needs to know
-- about *every* role that has ever produced a still-pending proposal, not
-- only the first. This table is that per-(proposal, role) link, independent
-- of — and without changing — concept_proposal's own global dedup.
CREATE TABLE IF NOT EXISTS jobber.concept_proposal_occurrence (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_proposal_id  UUID NOT NULL REFERENCES jobber.concept_proposal(id) ON DELETE CASCADE,
    role_instance_id     UUID NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    document_id          UUID NOT NULL REFERENCES jobber.document(id),
    extraction_run_id    UUID REFERENCES jobber.extraction_run(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (concept_proposal_id, role_instance_id)
);
CREATE INDEX IF NOT EXISTS idx_concept_proposal_occurrence_role ON jobber.concept_proposal_occurrence(role_instance_id);
CREATE INDEX IF NOT EXISTS idx_concept_proposal_occurrence_proposal ON jobber.concept_proposal_occurrence(concept_proposal_id);

-- Best-effort backfill for proposals that already existed before this
-- table did: attribute each to whichever role(s) share its recorded
-- document_id — the same "first role" attribution the old COALESCE-based
-- columns already gave it, made explicit rather than left implicit. This
-- does not recover attribution for a *second* role that hit the same
-- surface form before this migration ran (that history was never
-- recorded anywhere); it only prevents this migration itself from
-- regressing an existing pending proposal to zero attribution.
INSERT INTO jobber.concept_proposal_occurrence (concept_proposal_id, role_instance_id, document_id, extraction_run_id)
SELECT cp.id, ri.id, cp.document_id, cp.extraction_run_id
FROM jobber.concept_proposal cp
JOIN jobber.role_instance ri ON ri.document_id = cp.document_id
WHERE cp.document_id IS NOT NULL
ON CONFLICT (concept_proposal_id, role_instance_id) DO NOTHING;
