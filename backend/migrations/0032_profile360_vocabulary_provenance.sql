-- Profile360 mapping as a human-curation workflow.
--
-- 1. profile360_mapping_candidate: the candidate vocabulary concepts a
--    Profile360 mapping run actually retrieved/considered (rank + the
--    similarity the retrieval already computed) and which one the AI chose.
--    Lets the UI show "what the AI considered" without re-running retrieval
--    or inventing scores, and survives page reloads.
--
-- 2. concept_proposal_profile360_source: provenance for a jobber.concept_proposal
--    that originated from a Profile360 claim/capability instead of a job
--    posting. concept_proposal_occurrence (0021/0022) cannot represent this:
--    it is keyed by role_instance_id + document_id (both NOT NULL) and carries
--    requirement shape. The proposal itself is the *same* concept_proposal row
--    used for postings (same dedupe, same Vocabulary review, same accept/alias/
--    reject semantics, same jobber.concept result) — this table is only the
--    side link back to the originating Profile360 item, not a parallel
--    vocabulary system. Source id has no FK (cross-schema, profile360 is
--    read-only/authoritative); the source text is snapshotted for provenance.

CREATE TABLE IF NOT EXISTS jobber.profile360_mapping_candidate (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_run_id UUID NOT NULL REFERENCES jobber.extraction_run(id) ON DELETE CASCADE,
    concept_id        UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    rank              INTEGER NOT NULL,
    similarity        REAL,
    ai_selected       BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (extraction_run_id, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_p360_mapping_candidate_run ON jobber.profile360_mapping_candidate(extraction_run_id);

CREATE TABLE IF NOT EXISTS jobber.concept_proposal_profile360_source (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_proposal_id UUID NOT NULL REFERENCES jobber.concept_proposal(id) ON DELETE CASCADE,
    source_kind         TEXT NOT NULL CHECK (source_kind IN ('claim', 'capability')),
    source_id           UUID NOT NULL,
    source_text         TEXT,
    origin              TEXT NOT NULL CHECK (origin IN ('ai', 'curator')),
    extraction_run_id   UUID REFERENCES jobber.extraction_run(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (concept_proposal_id, source_kind, source_id)
);
CREATE INDEX IF NOT EXISTS idx_cp_p360_source_item ON jobber.concept_proposal_profile360_source(source_kind, source_id);
