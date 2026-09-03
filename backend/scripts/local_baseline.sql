-- LOCAL DEV / TEST ONLY. Never apply this against the real Supabase
-- `open-brain` project — it already has these tables, with real migrated
-- data (23 documents, 23 role instances, 327 skill observations).
--
-- This file exists because backend/migrations/0001_live_schema_preflight.sql
-- deliberately does NOT create these tables (see that file's header): Phase 2
-- migrations only ever ASSERT this baseline exists and extend it additively,
-- because getting this wrong against production would be a schema-corrupting
-- mistake, not a no-op. Anyone standing up a fully local Postgres instead of
-- pointing DATABASE_URL at the shared Supabase project (see README) needs an
-- equivalent baseline to develop against — this is that stand-in, built to
-- the exact shapes confirmed by live inspection of `open-brain.jobber` on
-- 2026-09-03 (see docs/14-phase2-postgres-architecture.md §3).
--
-- Idempotent (IF NOT EXISTS throughout) and struct-only: it seeds the two
-- lookup tables (concept_type, concept_edge_rule) with the same rows
-- production has, but inserts no document/role_instance/role_skill_observation
-- rows — there is no way to fabricate the real captured corpus locally, and
-- pretending to would be worse than an empty starting point.
--
-- Also stubs a minimal `profile360` schema: since the Phase 2 production-
-- schema reconciliation pass, backend/migrations/0004_profile360_mapping.sql
-- and 0005_preferences.sql declare real (non-defensive) foreign keys to
-- profile360.claims/capabilities/episodes, so migrations now genuinely
-- require *something* at those tables to apply at all — even for a
-- jobber-only local setup that will never touch the profile360-mapping
-- features. `claims`/`capabilities`/`manual_import_queue` match the columns
-- confirmed by live inspection (docs/14 §5/§6); `episodes`/`snapshots` do not
-- have a confirmed shape beyond `id` being uuid, so they get the minimum
-- needed to be a valid FK target plus one guessed text field so
-- profile360_reader.display_text has something to show — never trust these
-- two as a shape reference for anything beyond that.
--
-- Usage: applied by backend/tests/conftest.py before run_migrations(), and by
-- hand for local dev against a from-scratch Postgres:
--   psql "$DATABASE_URL" -f backend/scripts/local_baseline.sql

CREATE SCHEMA IF NOT EXISTS jobber;
CREATE SCHEMA IF NOT EXISTS profile360;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- belt-and-braces for gen_random_uuid() on older Postgres; core since PG13

CREATE TABLE IF NOT EXISTS profile360.claims (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_key      TEXT,
    episode_id     UUID,
    claim_text     TEXT NOT NULL,
    evidence_class TEXT NOT NULL DEFAULT 'stated',
    depth          TEXT,
    recency        TEXT,
    confidence     TEXT,
    uncertainty    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profile360.capabilities (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability_key     TEXT,
    name               TEXT NOT NULL,
    description        TEXT,
    synthesis_status   TEXT,
    current_assessment TEXT,
    uncertainty        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unconfirmed shape beyond `id` — see header note above.
CREATE TABLE IF NOT EXISTS profile360.episodes (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profile360.snapshots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    narrative_text TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Confirmed shape (docs/14 §6) — identity is source_key, not `id`; there is
-- no id column at all. app/profile360_promotion.py is the only writer.
CREATE TABLE IF NOT EXISTS profile360.manual_import_queue (
    source_key       TEXT PRIMARY KEY,
    imported_at      TIMESTAMPTZ DEFAULT now(),
    source_label     TEXT NOT NULL,
    payload          JSONB NOT NULL,
    processed        BOOLEAN DEFAULT false,
    processed_at     TIMESTAMPTZ,
    processing_notes TEXT
);

CREATE TABLE IF NOT EXISTS jobber.concept_type (
    code       TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    definition TEXT NOT NULL,
    is_atom    BOOLEAN NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobber.concept (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_concept_id INTEGER UNIQUE,
    type_code         TEXT NOT NULL REFERENCES jobber.concept_type(code),
    canonical_name    TEXT NOT NULL,
    definition        TEXT,
    status            TEXT NOT NULL DEFAULT 'proposed',
    merged_into       UUID REFERENCES jobber.concept(id),
    origin            TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at       TIMESTAMPTZ,
    UNIQUE (type_code, canonical_name)
);
CREATE INDEX IF NOT EXISTS idx_concept_type_local ON jobber.concept(type_code, status);

CREATE TABLE IF NOT EXISTS jobber.concept_alias (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_alias_id INTEGER UNIQUE,
    concept_id      UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    alias           TEXT NOT NULL,
    origin          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (alias, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_concept_alias_alias_local ON jobber.concept_alias(alias);

CREATE TABLE IF NOT EXISTS jobber.concept_edge_rule (
    relation  TEXT NOT NULL,
    from_type TEXT NOT NULL REFERENCES jobber.concept_type(code),
    to_type   TEXT NOT NULL REFERENCES jobber.concept_type(code),
    PRIMARY KEY (relation, from_type, to_type)
);

CREATE TABLE IF NOT EXISTS jobber.document (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key         TEXT UNIQUE NOT NULL,
    kind               TEXT NOT NULL,
    title              TEXT,
    source             TEXT,
    url                TEXT,
    source_date        DATE,
    captured_at        TIMESTAMPTZ,
    content_text       TEXT,
    content_sha256     TEXT,
    content_kind       TEXT NOT NULL DEFAULT 'source',
    provenance_quality TEXT NOT NULL DEFAULT 'original'
        CHECK (provenance_quality IN ('original', 'legacy_extracted', 'reconstructed', 'unknown')),
    source_payload     JSONB NOT NULL DEFAULT '{}',
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_document_kind_local ON jobber.document(kind);

CREATE TABLE IF NOT EXISTS jobber.role_instance (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_job_role_id     INTEGER UNIQUE,
    document_id            UUID REFERENCES jobber.document(id),
    instance_type          TEXT NOT NULL
        CHECK (instance_type IN ('observed_posting', 'user_defined_target', 'synthetic_reference')),
    title                  TEXT,
    organisation           TEXT,
    location               TEXT,
    country                TEXT,
    remote_type            TEXT,
    employment_type        TEXT,
    seniority_level        TEXT,
    posting_date           DATE,
    salary_min             NUMERIC,
    salary_max             NUMERIC,
    salary_estimate_min    NUMERIC,
    salary_estimate_max    NUMERIC,
    currency               TEXT,
    description            TEXT,
    requirements           TEXT,
    responsibilities       TEXT,
    summary                TEXT,
    career_track           TEXT,
    legacy_scores          JSONB,
    legacy_analysis        JSONB,
    extraction_status      TEXT,
    extraction_notes       TEXT,
    status                 TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_role_instance_type_local ON jobber.role_instance(instance_type);

CREATE TABLE IF NOT EXISTS jobber.role_skill_observation (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_skill_id      INTEGER UNIQUE,
    role_instance_id     UUID NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    surface_form         TEXT NOT NULL,
    category             TEXT,
    importance           INTEGER,
    requirement_type     TEXT,
    observation_basis    TEXT,
    canonical_concept_id UUID REFERENCES jobber.concept(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rso_role_local ON jobber.role_skill_observation(role_instance_id);
CREATE INDEX IF NOT EXISTS idx_rso_surface_local ON jobber.role_skill_observation(surface_form);

-- Shape unconfirmed beyond "present" — nothing in this codebase reads or
-- writes it, so it is deliberately not modelled here. See docs/14 §3.

INSERT INTO jobber.concept_type (code, label, definition, is_atom, sort_order) VALUES
    ('knowledge', 'Knowledge', 'A body of theory one can know', TRUE, 1),
    ('method', 'Method', 'A named technique one can apply', TRUE, 2),
    ('tool', 'Tool', 'A named artefact one operates', TRUE, 3),
    ('function', 'Function', 'A business activity', TRUE, 4),
    ('domain', 'Domain', 'A sector or market context', TRUE, 5),
    ('product', 'Product', 'A thing sold or managed', TRUE, 6),
    ('regulation', 'Regulation', 'A named regulatory or reporting regime', TRUE, 7),
    ('credential', 'Credential', 'An externally-issued, verifiable qualification', TRUE, 8),
    ('capability', 'Capability', 'Something a person can do, at economic scale', FALSE, 9),
    ('role_archetype', 'Role archetype', 'A recurring role shape across many postings', FALSE, 10)
ON CONFLICT (code) DO NOTHING;

INSERT INTO jobber.concept_edge_rule (relation, from_type, to_type) VALUES
    ('component_of', 'knowledge', 'capability'),
    ('component_of', 'method', 'capability'),
    ('component_of', 'tool', 'capability'),
    ('component_of', 'function', 'capability'),
    ('component_of', 'domain', 'capability'),
    ('component_of', 'product', 'capability'),
    ('component_of', 'regulation', 'capability'),
    ('component_of', 'credential', 'capability'),
    ('demands', 'role_archetype', 'capability'),
    ('broader_than', 'knowledge', 'knowledge'),
    ('broader_than', 'method', 'method'),
    ('broader_than', 'tool', 'tool'),
    ('broader_than', 'function', 'function'),
    ('broader_than', 'domain', 'domain'),
    ('broader_than', 'product', 'product'),
    ('broader_than', 'regulation', 'regulation'),
    ('broader_than', 'credential', 'credential'),
    ('governs', 'regulation', 'function'),
    ('applies_in', 'method', 'domain'),
    ('applies_in', 'method', 'product'),
    ('senior_to', 'role_archetype', 'role_archetype')
ON CONFLICT (relation, from_type, to_type) DO NOTHING;
