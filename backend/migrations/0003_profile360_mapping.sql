-- Phase 2: thin cross-schema mapping from profile360 (person-side evidence,
-- authoritative, not duplicated here) onto the jobber canonical vocabulary.
-- See docs/11-capability-model-design.md §1/§7 and
-- docs/14-phase2-postgres-architecture.md §5 for the reasoning and the
-- uncertainty this migration is deliberately defensive about: profile360's
-- primary-key type is assumed (uuid, the modern Supabase default) but never
-- verified from this build environment. The FK is attempted and gracefully
-- degrades to an unconstrained-but-typed column with a visible NOTICE if the
-- live table/column/type does not match — application code validates the
-- referenced row exists on every write either way (backend/app/profile360_reader.py).

CREATE TABLE IF NOT EXISTS jobber.profile360_claim_mapping (
    id                  BIGSERIAL PRIMARY KEY,
    profile360_claim_id UUID NOT NULL,
    jobber_concept_id   BIGINT NOT NULL REFERENCES jobber.concept(id),
    mapping_basis       TEXT NOT NULL CHECK (mapping_basis IN ('exact_match', 'ai_suggested', 'curator_asserted')),
    review_status       TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'rejected')),
    reviewed_at         TIMESTAMPTZ,
    extraction_run_id   BIGINT REFERENCES jobber.extraction_run(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile360_claim_id, jobber_concept_id)
);
CREATE INDEX IF NOT EXISTS idx_profile360_claim_mapping_concept ON jobber.profile360_claim_mapping(jobber_concept_id);
CREATE INDEX IF NOT EXISTS idx_profile360_claim_mapping_review ON jobber.profile360_claim_mapping(review_status);

CREATE TABLE IF NOT EXISTS jobber.profile360_capability_mapping (
    id                            BIGSERIAL PRIMARY KEY,
    profile360_capability_id      UUID NOT NULL,
    jobber_capability_concept_id  BIGINT NOT NULL REFERENCES jobber.concept(id),
    mapping_basis                 TEXT NOT NULL CHECK (mapping_basis IN ('exact_match', 'ai_suggested', 'curator_asserted')),
    review_status                 TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'rejected')),
    reviewed_at                   TIMESTAMPTZ,
    extraction_run_id             BIGINT REFERENCES jobber.extraction_run(id),
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile360_capability_id, jobber_capability_concept_id)
);
CREATE INDEX IF NOT EXISTS idx_profile360_capability_mapping_concept ON jobber.profile360_capability_mapping(jobber_capability_concept_id);
CREATE INDEX IF NOT EXISTS idx_profile360_capability_mapping_review ON jobber.profile360_capability_mapping(review_status);

-- Best-effort real FK to profile360, added only if the live shape matches the
-- uuid-PK assumption above. Never fails the migration.
DO $$
BEGIN
    BEGIN
        ALTER TABLE jobber.profile360_claim_mapping
            ADD CONSTRAINT profile360_claim_mapping_claim_fkey
            FOREIGN KEY (profile360_claim_id) REFERENCES profile360.claims(id);
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'profile360_claim_mapping: could not add FK to profile360.claims(id) (%). '
            'Falling back to an unconstrained uuid column; app-level existence checks still apply. '
            'See docs/14-phase2-postgres-architecture.md §5.', SQLERRM;
    END;
END $$;

DO $$
BEGIN
    BEGIN
        ALTER TABLE jobber.profile360_capability_mapping
            ADD CONSTRAINT profile360_capability_mapping_capability_fkey
            FOREIGN KEY (profile360_capability_id) REFERENCES profile360.capabilities(id);
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'profile360_capability_mapping: could not add FK to profile360.capabilities(id) (%). '
            'Falling back to an unconstrained uuid column; app-level existence checks still apply. '
            'See docs/14-phase2-postgres-architecture.md §5.', SQLERRM;
    END;
END $$;

-- The one deliberately minimal, evidence-free "the user says so" table on the
-- jobber side (docs/14 §6). Not a claim: no span, no episode, no modifiers —
-- exactly and only doc 11 §5.1's `user_asserted` basis ("no document supports
-- it"), scoped to the comparison UI's one-click "I have done this" action.
CREATE TABLE IF NOT EXISTS jobber.person_capability_assertion (
    id                BIGSERIAL PRIMARY KEY,
    jobber_concept_id BIGINT NOT NULL REFERENCES jobber.concept(id),
    asserted          BOOLEAN NOT NULL DEFAULT TRUE,
    note              TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (jobber_concept_id)
);
