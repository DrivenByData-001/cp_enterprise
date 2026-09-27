-- Phase 3: persistent Application workspace backbone (docs/34).
--
-- jobber.application turns an observed opportunity into a persistent,
-- user-owned workspace once the user says "I want to apply". `status` is a
-- user workflow state (never an AI judgment, never derived from structural
-- readiness) — see docs/34 for the full status lifecycle.
--
-- At most one *active* application per role (preparing/ready/submitted/
-- interviewing) is enforced here, at the database level, by a partial
-- unique index — not only in the API layer — so create/reopen
-- (routes/applications.py) is safe under concurrent requests via
-- `INSERT ... ON CONFLICT (role_instance_id) WHERE status IN (...)`, which
-- targets this exact index. Closed/withdrawn applications are historical
-- attempts, excluded from that uniqueness, so a role can accumulate several
-- closed/withdrawn attempts plus at most one live one.
CREATE TABLE IF NOT EXISTS jobber.application (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Restrictive (default NO ACTION) FK, deliberately not ON DELETE
    -- CASCADE: role deletion must never silently destroy application
    -- history. routes/roles.py::delete_role catches the resulting
    -- ForeignKeyViolation and returns a clear 409 instead of letting the
    -- role (and this history) disappear. This phase adds no
    -- application-deletion route at all, so the only way this FK is ever
    -- exercised is via an attempted role deletion.
    role_instance_id  UUID NOT NULL REFERENCES jobber.role_instance(id),
    status            TEXT NOT NULL DEFAULT 'preparing'
                          CHECK (status IN ('preparing', 'ready', 'submitted', 'interviewing', 'closed', 'withdrawn')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_application_role_instance ON jobber.application(role_instance_id);

-- The one-active-application-per-role invariant. Enforced here, not only in
-- application code, so it holds even under concurrent
-- POST /api/applications requests for the same role.
CREATE UNIQUE INDEX IF NOT EXISTS idx_application_one_active_per_role
    ON jobber.application(role_instance_id)
    WHERE status IN ('preparing', 'ready', 'submitted', 'interviewing');

-- Application-local notes/examples (build §2) — never automatically
-- promoted into Profile360 claims, Profile360 mappings,
-- person_capability_assertion, or requirement/capability evidence. See
-- docs/34 "Application-note boundary". concept_id is nullable (a general
-- note need not reference any concept) and deliberately has no ON DELETE
-- behaviour of its own beyond the default (a concept is never deleted, only
-- deprecated, in this codebase).
CREATE TABLE IF NOT EXISTS jobber.application_note (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  UUID NOT NULL REFERENCES jobber.application(id) ON DELETE CASCADE,
    concept_id      UUID REFERENCES jobber.concept(id),
    note_type       TEXT NOT NULL CHECK (note_type IN ('general', 'evidence_example')),
    note_text       TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_application_note_application ON jobber.application_note(application_id);
CREATE INDEX IF NOT EXISTS idx_application_note_concept ON jobber.application_note(concept_id);
