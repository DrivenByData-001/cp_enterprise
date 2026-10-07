-- Reviewed model estimates deliberately do not enter compensation_observation.
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
