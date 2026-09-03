-- Phase 2: thin cross-schema mapping from profile360 (person-side evidence,
-- authoritative, not duplicated here) onto the jobber canonical vocabulary.
-- See docs/11-capability-model-design.md §1/§7 and
-- docs/14-phase2-postgres-architecture.md §5/§6.
--
-- profile360.claims.id and profile360.capabilities.id were confirmed UUID by
-- live inspection of the open-brain project (2026-09-03) — these are real,
-- direct foreign keys, not a defensive best-effort attempt.

CREATE TABLE IF NOT EXISTS jobber.profile360_claim_mapping (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile360_claim_id UUID NOT NULL REFERENCES profile360.claims(id),
    jobber_concept_id   UUID NOT NULL REFERENCES jobber.concept(id),
    mapping_basis       TEXT NOT NULL CHECK (mapping_basis IN ('exact_match', 'ai_suggested', 'curator_asserted')),
    review_status       TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'rejected')),
    reviewed_at         TIMESTAMPTZ,
    extraction_run_id   UUID REFERENCES jobber.extraction_run(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile360_claim_id, jobber_concept_id)
);
CREATE INDEX IF NOT EXISTS idx_profile360_claim_mapping_concept ON jobber.profile360_claim_mapping(jobber_concept_id);
CREATE INDEX IF NOT EXISTS idx_profile360_claim_mapping_review ON jobber.profile360_claim_mapping(review_status);

CREATE TABLE IF NOT EXISTS jobber.profile360_capability_mapping (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile360_capability_id      UUID NOT NULL REFERENCES profile360.capabilities(id),
    jobber_capability_concept_id  UUID NOT NULL REFERENCES jobber.concept(id),
    mapping_basis                 TEXT NOT NULL CHECK (mapping_basis IN ('exact_match', 'ai_suggested', 'curator_asserted')),
    review_status                 TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'rejected')),
    reviewed_at                   TIMESTAMPTZ,
    extraction_run_id             UUID REFERENCES jobber.extraction_run(id),
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile360_capability_id, jobber_capability_concept_id)
);
CREATE INDEX IF NOT EXISTS idx_profile360_capability_mapping_concept ON jobber.profile360_capability_mapping(jobber_capability_concept_id);
CREATE INDEX IF NOT EXISTS idx_profile360_capability_mapping_review ON jobber.profile360_capability_mapping(review_status);

-- The one deliberately minimal, evidence-free "the user says so" table on
-- the jobber side (docs/14 §6, reviewed again in §11 of the reconciliation
-- pass). NOT a claim: no span, no episode, no modifiers. It is explicitly a
-- TEMPORARY NAVIGATION OVERRIDE, never evidence, and must never outrank
-- profile360 or silently become capability evidence — comparison.py treats
-- it as strictly weaker than any profile360 mapping (see the status
-- ordering there: evidenced > partial > user_asserted > not_found).
-- `promoted_to_profile360_at` tracks the one-way, best-effort promotion path
-- into profile360.manual_import_queue (backend/app/profile360_promotion.py)
-- — this row is not deleted on promotion so the UI can still show "you
-- asserted this, and it has been queued for profile360 to confirm".
CREATE TABLE IF NOT EXISTS jobber.person_capability_assertion (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jobber_concept_id        UUID NOT NULL REFERENCES jobber.concept(id),
    asserted                 BOOLEAN NOT NULL DEFAULT TRUE,
    note                     TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_to_profile360_at TIMESTAMPTZ,
    UNIQUE (jobber_concept_id)
);
