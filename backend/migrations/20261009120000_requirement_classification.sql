-- Immutable, source-bound classifications; no vocabulary decisions are overwritten.
CREATE TABLE jobber.requirement_classification (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_instance_id UUID NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES jobber.document(id) ON DELETE CASCADE,
    extraction_run_id UUID NOT NULL UNIQUE REFERENCES jobber.extraction_run(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL,
    statements JSONB NOT NULL CHECK (jsonb_typeof(statements) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX requirement_classification_role ON jobber.requirement_classification(role_instance_id, created_at DESC);
CREATE INDEX requirement_classification_document ON jobber.requirement_classification(document_id);
ALTER TABLE jobber.requirement_classification ENABLE ROW LEVEL SECURITY;
