-- Phase 2: extraction/task-run provenance + the canonical market-side claim
-- (requirement_claim). See docs/11-capability-model-design.md §4.1/§4.4 for the
-- design this generalises, and docs/14-phase2-postgres-architecture.md §6 for
-- why there is no person-side evidence_claim table here (that is profile360's
-- role, not jobber's).
--
-- extraction_run is redesigned relative to doc 11's original DDL: doc 11 assumed
-- every task has exactly one document to point at. Phase 2 adds task types whose
-- subject is a profile360 claim/capability (no jobber document at all), so
-- `document_id` changes from NOT NULL to a nullable column alongside three
-- sibling subject columns, discriminated by `subject_type` — never a fabricated
-- FK to satisfy a NOT NULL that doesn't fit the task (brief §9/§18).

CREATE TABLE IF NOT EXISTS jobber.extraction_run (
    id                    BIGSERIAL PRIMARY KEY,
    task                  TEXT NOT NULL,   -- job_posting_extract | requirement_extract | concept_link_adjudicate | profile360_claim_map | profile360_capability_map
    subject_type          TEXT NOT NULL CHECK (subject_type IN ('document', 'role_instance', 'profile360_claim', 'profile360_capability')),
    document_id           BIGINT REFERENCES jobber.document(id),
    role_instance_id      BIGINT REFERENCES jobber.role_instance(id),
    profile360_claim_id      UUID,  -- unconstrained cross-schema reference — see docs/14 §5
    profile360_capability_id UUID,
    model                 TEXT NOT NULL,
    prompt_name           TEXT NOT NULL,
    prompt_version        TEXT NOT NULL,
    vocabulary_version_id BIGINT NOT NULL REFERENCES jobber.vocabulary_version(id),
    started_at            TIMESTAMPTZ NOT NULL,
    finished_at           TIMESTAMPTZ,
    status                TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'failed')),
    error_type            TEXT,   -- AIConfigError | AIProviderError | AIResponseFormatError | AISchemaValidationError, when status='failed'
    error_message         TEXT,
    input_chars           INTEGER,
    output_chars          INTEGER,
    notes                 TEXT,
    CHECK (
        (subject_type = 'document' AND document_id IS NOT NULL) OR
        (subject_type = 'role_instance' AND role_instance_id IS NOT NULL) OR
        (subject_type = 'profile360_claim' AND profile360_claim_id IS NOT NULL) OR
        (subject_type = 'profile360_capability' AND profile360_capability_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_extraction_run_task ON jobber.extraction_run(task, status);
CREATE INDEX IF NOT EXISTS idx_extraction_run_role_instance ON jobber.extraction_run(role_instance_id);

-- concept_proposal.extraction_run_id was left FK-less in 0001 (extraction_run
-- didn't exist yet). Add the real constraint now.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_schema = 'jobber' AND table_name = 'concept_proposal'
          AND constraint_name = 'concept_proposal_extraction_run_id_fkey'
    ) THEN
        ALTER TABLE jobber.concept_proposal
            ADD CONSTRAINT concept_proposal_extraction_run_id_fkey
            FOREIGN KEY (extraction_run_id) REFERENCES jobber.extraction_run(id);
    END IF;
END $$;

-- requirement_claim: doc 11 §4.4, Postgres-ified. `basis` gains 'user_asserted'
-- relative to doc 11's role-side design — a role_instance with no document
-- (an imagined target, or a synthetic/reference role) can still have a
-- user-declared requirement, and that must be recorded as what it is rather
-- than dressed up as 'inferred'.
CREATE TABLE IF NOT EXISTS jobber.requirement_claim (
    id                    BIGSERIAL PRIMARY KEY,
    role_instance_id      BIGINT NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    concept_id            BIGINT NOT NULL REFERENCES jobber.concept(id),
    requirement_type      TEXT NOT NULL CHECK (requirement_type IN ('required', 'preferred', 'contextual')),
    importance            INTEGER CHECK (importance BETWEEN 1 AND 5),
    basis                 TEXT NOT NULL CHECK (basis IN ('stated', 'implied', 'inferred', 'user_asserted')),
    document_id           BIGINT REFERENCES jobber.document(id),
    evidence_span         TEXT,
    evidence_offset_start INTEGER,
    evidence_offset_end   INTEGER,
    extraction_run_id     BIGINT REFERENCES jobber.extraction_run(id),
    review_status         TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'rejected', 'corrected')),
    reviewed_at           TIMESTAMPTZ,
    superseded_by         BIGINT REFERENCES jobber.requirement_claim(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- doc 11 §5.2 invariants 1-2, carried over verbatim:
    CHECK (basis <> 'user_asserted' OR extraction_run_id IS NULL),
    CHECK (basis NOT IN ('stated', 'implied') OR (document_id IS NOT NULL AND evidence_span IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_requirement_claim_role ON jobber.requirement_claim(role_instance_id);
CREATE INDEX IF NOT EXISTS idx_requirement_claim_concept ON jobber.requirement_claim(concept_id);
CREATE INDEX IF NOT EXISTS idx_requirement_claim_review ON jobber.requirement_claim(review_status);
