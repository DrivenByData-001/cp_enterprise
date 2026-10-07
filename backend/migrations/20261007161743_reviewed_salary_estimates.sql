-- Reviewed model estimates deliberately do not enter compensation_observation.
-- Salary review uses no controlled-vocabulary snapshot. Preserve the existing
-- vocabulary-dependent task guard, adding only this new document/role task.
ALTER TABLE jobber.extraction_run DROP CONSTRAINT extraction_run_vocabulary_required_check;
ALTER TABLE jobber.extraction_run ADD CONSTRAINT extraction_run_vocabulary_required_check
CHECK (vocabulary_version_id IS NOT NULL OR task IN (
    'job_posting_extract', 'role_context_generate', 'compensation_extract',
    'role_metadata_enrich', 'target_decompose', 'posting_compensation_extract',
    'role_archetype_classify', 'archetype_context_generate', 'role_salary_review'
));

CREATE TABLE jobber.role_salary_estimate (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_instance_id UUID NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    extraction_run_id UUID UNIQUE REFERENCES jobber.extraction_run(id) ON DELETE SET NULL,
    estimate JSONB NOT NULL,
    evidence JSONB NOT NULL,
    model TEXT NOT NULL,
    role_updated_at TIMESTAMPTZ NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX role_salary_estimate_role ON jobber.role_salary_estimate(role_instance_id, reviewed_at DESC);
ALTER TABLE jobber.role_salary_estimate ENABLE ROW LEVEL SECURITY;
CREATE TRIGGER invalidate_target_analysis_economics
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON jobber.role_salary_estimate
FOR EACH STATEMENT EXECUTE FUNCTION jobber.invalidate_target_analysis('economics');
