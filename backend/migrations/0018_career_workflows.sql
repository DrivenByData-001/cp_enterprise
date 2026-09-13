-- Local development actions never contribute to capability evidence.
CREATE TABLE IF NOT EXISTS jobber.development_action (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_instance_id UUID NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    concept_id UUID NOT NULL REFERENCES jobber.concept(id),
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 500),
    note TEXT NOT NULL DEFAULT '',
    due_date DATE,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_development_action_role ON jobber.development_action(role_instance_id);
CREATE INDEX IF NOT EXISTS idx_requirement_claim_role_concept ON jobber.requirement_claim(role_instance_id, concept_id);

ALTER TABLE jobber.extraction_run DROP CONSTRAINT IF EXISTS extraction_run_vocabulary_required_check;
ALTER TABLE jobber.extraction_run ADD CONSTRAINT extraction_run_vocabulary_required_check
CHECK (vocabulary_version_id IS NOT NULL OR task IN (
    'job_posting_extract', 'role_context_generate', 'compensation_extract',
    'role_metadata_enrich', 'target_decompose'
));
