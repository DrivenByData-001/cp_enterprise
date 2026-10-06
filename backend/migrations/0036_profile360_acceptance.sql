-- Application-scoped human acceptance; canonical claims remain in Profile360.
-- Retain the audit when an application or vocabulary concept is removed.
CREATE TABLE jobber.profile360_acceptance (
    id UUID PRIMARY KEY,
    application_id UUID REFERENCES jobber.application(id) ON DELETE SET NULL,
    concept_id UUID REFERENCES jobber.concept(id) ON DELETE SET NULL,
    claim_id UUID NOT NULL,
    request_fingerprint TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('add', 'correct')),
    reviewed_payload JSONB NOT NULL,
    before_state JSONB,
    after_state JSONB NOT NULL,
    accepted_by TEXT NOT NULL DEFAULT 'authenticated_operator',
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX profile360_acceptance_application_idx
    ON jobber.profile360_acceptance (application_id, concept_id);
ALTER TABLE jobber.profile360_acceptance ENABLE ROW LEVEL SECURITY;
-- Access is through the existing authenticated server and its DB role only.
