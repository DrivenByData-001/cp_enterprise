CREATE TABLE jobber.application_preparation (
    application_id UUID PRIMARY KEY REFERENCES jobber.application(id) ON DELETE CASCADE,
    deadline DATE,
    package_items JSONB NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 1,
    opportunity_fingerprint TEXT,
    requirements_fingerprint TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE jobber.application_preparation ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobber.application_resume ADD COLUMN stage TEXT NOT NULL DEFAULT 'evidence'
    CHECK (stage IN ('overview', 'opportunity', 'requirements', 'evidence'));
