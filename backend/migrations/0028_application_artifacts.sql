-- Phase 4: grounded Positioning + Application Package generation (docs/35).
--
-- jobber.application_artifact is the one versioned table for every generated
-- (or user-edited) piece of application material: the Positioning brief, the
-- tailored CV, the Cover letter, and the Supporting statement. Same
-- active/draft/superseded lifecycle as jobber.concept_dossier (0012) and
-- jobber.role_context_enrichment (0011) — generation always creates a
-- 'draft', the user explicitly 'adopt's it to make it 'active', and history
-- is retained (a row is never deleted, only ever moved to 'superseded').
--
-- One table for all four artifact_type values, not four tables: the
-- lifecycle, audit metadata, and provenance shape are identical across all
-- four, and `content`/`raw_output` are JSONB precisely because each type's
-- structured shape differs (see app/application_generation.py /
-- app/application_artifacts.py) — see docs/35 for why this reuses the
-- concept_dossier precedent rather than fragmenting into per-type tables.
--
-- Audit metadata (model/prompt_name/prompt_version/guidance/raw_output)
-- lives directly on this table rather than jobber.extraction_run, for the
-- same reason migration 0012's header gives for concept_dossier:
-- extraction_run's subject_type/subject-FK CHECK constraints (0003) cover a
-- fixed, unnamed set of subject kinds (document/role_instance/
-- profile360_claim/profile360_capability) that does not include
-- "application" or "application_artifact", and altering two unnamed
-- constraints on a table every other AI feature in this app also writes to
-- is a materially riskier change than this feature warrants. Every other
-- invariant established by concept_dossier.py/role_context.py is preserved
-- exactly instead: GET never generates, the AI call happens outside any DB
-- transaction, and a failed call never touches the current active/draft
-- rows it didn't already supersede.
CREATE TABLE IF NOT EXISTS jobber.application_artifact (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES jobber.application(id) ON DELETE CASCADE,
    artifact_type       TEXT NOT NULL
                            CHECK (artifact_type IN ('positioning', 'cv', 'cover_letter', 'supporting_statement')),
    status              TEXT NOT NULL CHECK (status IN ('draft', 'active', 'superseded')),
    origin              TEXT NOT NULL CHECK (origin IN ('ai', 'user_edit')),
    generator_version   TEXT NOT NULL,
    model               TEXT,             -- null for a user_edit version
    prompt_name         TEXT,
    prompt_version      TEXT,
    guidance            TEXT,             -- optional user generation guidance for this version
    -- Deterministic SHA-256 over the generation inputs that materially
    -- affect this artifact (app/application_generation.py::
    -- compute_input_fingerprint) — never null, including for a user_edit
    -- version (it carries over the fingerprint of the generated version it
    -- was edited from, so "has the underlying evidence changed since"
    -- still means the same thing). Compared against the *current* fingerprint
    -- on every read to derive `stale` — never stored as a boolean itself,
    -- so staleness is always computed fresh rather than risking going stale
    -- itself.
    input_fingerprint   TEXT NOT NULL,
    -- [{ref, kind, label, category}, ...] — the stable source registry
    -- entries this version's content actually cites. Never a wholesale copy
    -- of Profile360/role data; see app/application_generation.py.
    source_manifest     JSONB NOT NULL DEFAULT '[]',
    -- The structured, typed artifact content (shape depends on
    -- artifact_type — see app/models.py's Application*Generation models and
    -- app/application_artifacts.py::_content_from_*). Every factual block
    -- inside carries its own source_refs.
    content             JSONB NOT NULL,
    -- Full raw AI response for this version — audit/debug only, same
    -- posture as concept_dossier.raw_output / role_context_enrichment.
    -- raw_output: never returned by a normal read endpoint. Always null for
    -- a user_edit version.
    raw_output          JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_application_artifact_application
    ON jobber.application_artifact(application_id, artifact_type);

-- At most one draft and at most one active version per (application,
-- artifact_type) — enforced here, not only in application code, exactly
-- like concept_dossier's idx_concept_dossier_one_active/one_draft (0012).
CREATE UNIQUE INDEX IF NOT EXISTS idx_application_artifact_one_active
    ON jobber.application_artifact(application_id, artifact_type) WHERE status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS idx_application_artifact_one_draft
    ON jobber.application_artifact(application_id, artifact_type) WHERE status = 'draft';
