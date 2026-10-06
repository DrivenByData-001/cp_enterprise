CREATE TABLE jobber.application_evidence_decision (
    application_id UUID NOT NULL REFERENCES jobber.application(id) ON DELETE CASCADE,
    concept_id UUID NOT NULL REFERENCES jobber.concept(id),
    disposition TEXT NOT NULL CHECK (disposition IN ('covered', 'partial', 'investigate', 'gap')),
    selected_refs JSONB NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL DEFAULT '',
    input_fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (application_id, concept_id)
);

CREATE TABLE jobber.application_resume (
    application_id UUID PRIMARY KEY REFERENCES jobber.application(id) ON DELETE CASCADE,
    concept_id UUID REFERENCES jobber.concept(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
