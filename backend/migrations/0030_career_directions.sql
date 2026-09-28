-- Phase 6: Career Direction / property-first target discovery (docs/37).
--
-- Three different things, kept as three different tables (build brief §1):
--
--   Preferences (jobber.preference_dimension/preference_observation, 0005)
--     — evidence about what the user tends to value or enjoy. Unchanged by
--     this migration; nothing here writes into preference_observation.
--   Career Direction (this migration) — a user-owned statement of the future
--     career state being explored or chosen: desired properties/trade-offs,
--     practical constraints, and (optionally) a link to a concrete Target
--     and/or a market archetype.
--   Target (jobber.role_instance, instance_type='user_defined_target',
--     0002) — one concrete role hypothesis used for requirements/Pathways
--     analysis. Unchanged and not duplicated: a Career Direction may exist
--     with no Target at all, and links to one via target_role_instance_id
--     rather than growing a second target-shaped table.
--
-- jobber.career_direction_discovery_run is this feature's audit/proposal
-- table, deliberately separate from jobber.extraction_run for the same
-- reason migrations 0012 (concept_dossier) and 0028 (application_artifact)
-- give: extraction_run's subject_type/subject-FK CHECK constraints (0003)
-- are unnamed and cover a fixed set of subject kinds that does not include
-- "career direction discovery", and altering them on a table every other AI
-- feature in this app also writes to is a materially riskier change than
-- this feature warrants. Unlike concept_dossier/application_artifact (which
-- fold their own audit metadata directly onto the versioned content row),
-- this run table is kept separate from jobber.career_direction itself
-- because one discovery run proposes *several* candidate hypotheses
-- (build §12/§13), only some (or none) of which are ever adopted — the run
-- is a many-candidates-to-zero-or-more-directions proposal record, not a
-- version in a single row's own lifecycle.
--
-- Created before jobber.career_direction so the latter's
-- source_discovery_run_id can be a real FK from the start.
CREATE TABLE IF NOT EXISTS jobber.career_direction_discovery_run (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status            TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    model             TEXT,             -- null when the run failed before a model was ever resolved
    prompt_name       TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    guidance          TEXT,             -- optional free-text user guidance for this generation
    -- Deterministic fingerprint over the assembled discovery context (builder
    -- criteria + preference summary + bounded evidence) — audit/debugging
    -- only, same posture as application_artifact.input_fingerprint, but never
    -- consulted to suppress a "stale" figure here (there is no persisted
    -- proposal that needs staleness detection; a discovery run is a
    -- point-in-time record, never silently re-used).
    input_fingerprint TEXT NOT NULL,
    -- The unsaved property-first builder state this run was generated from
    -- (dimensions/constraints/guidance) — never a copy of preference_observation
    -- rows or profile360 content.
    criteria          JSONB NOT NULL,
    -- [{ref, kind, label}, ...] — the stable source registry entries this
    -- run's candidates were allowed to cite (build §14). Never a wholesale
    -- copy of Profile360/the corpus.
    source_manifest   JSONB NOT NULL DEFAULT '[]',
    -- The validated CareerDirectionDiscoveryResult (candidates, or an
    -- insufficient-evidence verdict) — what GET .../discovery-runs/{id}
    -- returns.
    output            JSONB,
    -- Full raw AI response — audit/debug only, same posture as
    -- concept_dossier.raw_output/application_artifact.raw_output: never
    -- returned by a normal read endpoint.
    raw_output        JSONB,
    error_type        TEXT,   -- AIConfigError | AIProviderError | AIResponseFormatError | AISchemaValidationError, when status='failed'
    error_message     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_career_direction_discovery_run_created ON jobber.career_direction_discovery_run(created_at DESC);

-- jobber.career_direction: the user-owned statement of a future career state.
-- `target_role_instance_id` may reference only a non-posting Target — the
-- app layer validates instance_type != 'observed_posting' before writing it
-- (a CHECK constraint here cannot see role_instance's own column). ON DELETE
-- SET NULL so deleting a Target narrows this direction back to "not yet
-- linked to a concrete Target" rather than destroying the direction itself
-- (build §3/§25 — a selected direction remains valid with no Target linked).
-- `target_archetype_concept_id` is validated app-side to be an active
-- role_archetype concept, same discipline archetype_classification.py
-- already applies to role_instance.archetype_concept_id.
CREATE TABLE IF NOT EXISTS jobber.career_direction (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                         TEXT NOT NULL,
    summary                      TEXT NOT NULL DEFAULT '',
    state                        TEXT NOT NULL DEFAULT 'exploring' CHECK (state IN ('exploring', 'selected', 'archived')),
    origin                       TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('user', 'ai_adopted')),
    target_role_instance_id      UUID REFERENCES jobber.role_instance(id) ON DELETE SET NULL,
    target_archetype_concept_id  UUID REFERENCES jobber.concept(id),
    -- Typed at the API layer (Pydantic CareerDirectionConstraints) — see
    -- build §5. Kept JSONB here for the same reason application_artifact.content
    -- and concept_dossier's list-shaped columns are JSONB: the shape is
    -- validated in application code, not by Postgres CHECK constraints.
    constraints                  JSONB NOT NULL DEFAULT '{}',
    source_discovery_run_id      UUID REFERENCES jobber.career_direction_discovery_run(id),
    -- The specific candidate (by its server-assigned id — see
    -- career_directions.py::_validate_and_shape) adopted from
    -- source_discovery_run_id's `output.candidates`, when this direction was
    -- AI-adopted. Not a real FK (candidate ids live inside a JSONB blob, not
    -- their own table) — Direction detail's "why this direction exists"
    -- resolves it by reading the run's own output. Always NULL for a manual
    -- (origin='user') direction.
    source_candidate_id          TEXT,
    selected_at                  TIMESTAMPTZ,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_career_direction_state ON jobber.career_direction(state);
CREATE INDEX IF NOT EXISTS idx_career_direction_target ON jobber.career_direction(target_role_instance_id);

-- At most one Career Direction may have state='selected' (build §3), enforced
-- at the database level — not only in application code — same partial-unique-
-- index technique as idx_concept_dossier_one_active (0012), here filtered on
-- a fixed value of `state` itself (every row satisfying the WHERE clause has
-- the same state, so uniqueness of that column among them means at most one
-- such row can exist).
CREATE UNIQUE INDEX IF NOT EXISTS idx_career_direction_one_selected
    ON jobber.career_direction(state) WHERE state = 'selected';

-- jobber.career_direction_dimension: explicit user choices for *this*
-- direction — structurally separate from jobber.preference_observation
-- (build §4/§9/§31). "Preferences inform the discussion but do not override
-- explicit builder input" (build §10) depends on these never being written
-- into preference_observation, and vice versa; no FK, view or query anywhere
-- in this build joins the two into one row or one score.
CREATE TABLE IF NOT EXISTS jobber.career_direction_dimension (
    career_direction_id UUID NOT NULL REFERENCES jobber.career_direction(id) ON DELETE CASCADE,
    dimension_code      TEXT NOT NULL REFERENCES jobber.preference_dimension(code),
    desired_direction   TEXT NOT NULL CHECK (desired_direction IN ('toward', 'away', 'neutral')),
    importance          SMALLINT NOT NULL CHECK (importance BETWEEN 1 AND 3),
    note                TEXT,
    PRIMARY KEY (career_direction_id, dimension_code)
);
