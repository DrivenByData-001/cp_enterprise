-- Phase 5: Application lifecycle events + Interview Prep artifact type
-- (docs/36).
--
-- Two independent changes:
--
-- 1. jobber.application_event — a persisted, user-recorded timeline
--    (submission, interviews, offers, closure/outcome). This is history, not
--    a judgment: nothing here is derived by AI, and nothing here drives
--    jobber.application.status (migration 0027) — that stays exactly what it
--    always was, a status the user sets explicitly through the existing
--    status control. See routes/applications.py and docs/36 "Status vs.
--    event semantics" for the full boundary. A user may correct or delete a
--    wrongly-recorded event (routes/applications.py), which only ever
--    touches this table — never the Application itself, never any
--    application_artifact.
--
-- 2. jobber.application_artifact.artifact_type widens to accept
--    'interview_prep' — Interview Prep is a fifth artifact type reusing the
--    exact generate -> draft -> adopt -> active lifecycle Phase 4 already
--    built (migration 0028) rather than a parallel document-generation
--    subsystem. No new version-history table: app/application_artifacts.py's
--    existing draft/active/superseded machinery, and its one-active/
--    one-draft-per-(application_id, artifact_type) partial unique indexes,
--    apply to 'interview_prep' automatically once this CHECK accepts it.
CREATE TABLE IF NOT EXISTS jobber.application_event (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- CASCADE (unlike application's own restrictive FK to role_instance,
    -- migration 0027): an event has no meaning once its Application is gone,
    -- and Application deletion isn't offered by this or any prior phase
    -- (build §20) — this FK is never actually exercised as a delete path
    -- today, only kept correct for if/when one exists.
    application_id  UUID NOT NULL REFERENCES jobber.application(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'submitted', 'interview_scheduled', 'interview_completed',
                        'offer_received', 'offer_accepted', 'offer_declined',
                        'rejected', 'role_closed', 'withdrawn', 'closed', 'other'
                    )),
    event_at        TIMESTAMPTZ NOT NULL,
    label           TEXT,
    notes           TEXT,
    details         JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every list this phase needs (the workspace timeline, the "next scheduled
-- interview" lookup, the Applications-index aggregate) orders or filters by
-- (application_id, event_at DESC) — one index covers all three.
CREATE INDEX IF NOT EXISTS idx_application_event_application_event_at
    ON jobber.application_event(application_id, event_at DESC);

ALTER TABLE jobber.application_artifact DROP CONSTRAINT IF EXISTS application_artifact_artifact_type_check;
ALTER TABLE jobber.application_artifact
    ADD CONSTRAINT application_artifact_artifact_type_check
    CHECK (artifact_type IN ('positioning', 'cv', 'cover_letter', 'supporting_statement', 'interview_prep'));
