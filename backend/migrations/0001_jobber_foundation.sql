-- Phase 2: jobber foundation.
--
-- Reconstructs, additively and idempotently, the tables the Phase 2 brief says
-- are already migrated into Supabase project `open-brain`, schema `jobber`
-- (document, role_instance, role_skill_observation, concept_type, concept,
-- concept_alias, concept_edge, concept_edge_rule, concept_proposal), plus this
-- app's own pre-existing Phase 0/1 tables (person, episode, episode_document,
-- profile_snapshots, vocabulary_version, d_embedding).
--
-- Every statement is IF NOT EXISTS / additive. Against a database where these
-- tables already exist with a compatible shape, this is a no-op. See
-- docs/14-phase2-postgres-architecture.md §3 for exactly what is reconstructed
-- vs. assumed, and why. This build has no credentials to verify the live
-- shape — see that doc before applying to the real open-brain project.

CREATE SCHEMA IF NOT EXISTS jobber;

CREATE EXTENSION IF NOT EXISTS vector;

-- === Layer 0: source (immutable) ===========================================

CREATE TABLE IF NOT EXISTS jobber.document (
    id              BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,        -- cv | linkedin_profile | job_posting | project_writeup | narrative | article | other
    title           TEXT,
    body            TEXT NOT NULL,        -- verbatim as ingested; never edited in place
    body_sha256     TEXT NOT NULL UNIQUE,
    source          TEXT,                 -- linkedin | company_site | user_paste | file | pdf
    url             TEXT,
    document_date   TEXT,                 -- ISO date the content refers to / was published (may be partial)
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by   BIGINT REFERENCES jobber.document(id),
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_document_kind ON jobber.document(kind);

-- provenance: not specified by name anywhere upstream — added here because §5/§14
-- of the Phase 2 brief make claim validity conditional on distinguishing a
-- genuine original capture from a legacy-reconstructed one. DEFAULT applies only
-- at ADD COLUMN time, backfilling every row that already exists in this database
-- when this migration first runs (the reconstruction: exactly the legacy-migrated
-- rows) — DROP DEFAULT afterwards forces every future INSERT to state it
-- explicitly rather than silently inheriting a default. See docs/14 §4.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'document' AND column_name = 'provenance'
    ) THEN
        ALTER TABLE jobber.document ADD COLUMN provenance TEXT NOT NULL DEFAULT 'legacy_extracted';
        ALTER TABLE jobber.document ALTER COLUMN provenance DROP DEFAULT;
        ALTER TABLE jobber.document ADD CONSTRAINT document_provenance_check
            CHECK (provenance IN ('original_capture', 'legacy_extracted', 'user_paste', 'unspecified'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS jobber.vocabulary_version (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    concept_count INTEGER NOT NULL,
    note          TEXT
);

-- === Layer 1: vocabulary (curated, slow-changing) ==========================

CREATE TABLE IF NOT EXISTS jobber.concept_type (
    code       TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    definition TEXT NOT NULL,
    is_atom    BOOLEAN NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobber.concept (
    id             BIGSERIAL PRIMARY KEY,
    type_code      TEXT NOT NULL REFERENCES jobber.concept_type(code),
    canonical_name TEXT NOT NULL,
    definition     TEXT,
    status         TEXT NOT NULL DEFAULT 'proposed',  -- proposed | active | deprecated | merged
    merged_into    BIGINT REFERENCES jobber.concept(id),
    origin         TEXT NOT NULL,                     -- curator | extraction_proposal
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at    TIMESTAMPTZ,
    UNIQUE (type_code, canonical_name)
);
CREATE INDEX IF NOT EXISTS idx_concept_type ON jobber.concept(type_code, status);

CREATE TABLE IF NOT EXISTS jobber.capability_detail (
    concept_id             BIGINT PRIMARY KEY REFERENCES jobber.concept(id) ON DELETE CASCADE,
    demonstration_standard TEXT NOT NULL,
    min_depth              TEXT NOT NULL DEFAULT 'owned',
    min_autonomy           TEXT,
    requires_all_core      BOOLEAN NOT NULL DEFAULT TRUE,
    economic_salience      TEXT,
    notes                  TEXT
);

CREATE TABLE IF NOT EXISTS jobber.role_archetype_detail (
    concept_id                  BIGINT PRIMARY KEY REFERENCES jobber.concept(id) ON DELETE CASCADE,
    seniority_band              TEXT,
    primary_function_concept_id BIGINT REFERENCES jobber.concept(id),
    typical_market               TEXT,
    notes                       TEXT
);

CREATE TABLE IF NOT EXISTS jobber.concept_alias (
    id         BIGSERIAL PRIMARY KEY,
    concept_id BIGINT NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    origin     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (alias, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_concept_alias_alias ON jobber.concept_alias(alias);

CREATE TABLE IF NOT EXISTS jobber.concept_xref (
    id         BIGSERIAL PRIMARY KEY,
    concept_id BIGINT NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    scheme     TEXT NOT NULL,
    code       TEXT NOT NULL,
    label      TEXT,
    UNIQUE (concept_id, scheme, code)
);

CREATE TABLE IF NOT EXISTS jobber.concept_edge (
    id              BIGSERIAL PRIMARY KEY,
    from_concept_id BIGINT NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    to_concept_id   BIGINT NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL,
    necessity       TEXT,
    weight          REAL,
    note            TEXT,
    origin          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'proposed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_concept_id, to_concept_id, relation)
);

CREATE TABLE IF NOT EXISTS jobber.concept_edge_rule (
    relation  TEXT NOT NULL,
    from_type TEXT NOT NULL REFERENCES jobber.concept_type(code),
    to_type   TEXT NOT NULL REFERENCES jobber.concept_type(code),
    PRIMARY KEY (relation, from_type, to_type)
);

CREATE TABLE IF NOT EXISTS jobber.concept_proposal (
    id                   BIGSERIAL PRIMARY KEY,
    surface_form         TEXT NOT NULL,
    suggested_type       TEXT REFERENCES jobber.concept_type(code),
    suggested_definition TEXT,
    nearest_concept_id   BIGINT REFERENCES jobber.concept(id),
    nearest_similarity   REAL,
    occurrence_count     INTEGER NOT NULL DEFAULT 1,
    document_id          BIGINT REFERENCES jobber.document(id),
    evidence_span        TEXT,
    extraction_run_id    BIGINT,  -- FK added in 0002 once extraction_run exists
    status               TEXT NOT NULL DEFAULT 'pending',  -- pending|accepted_new|accepted_alias|rejected|deferred
    resolved_concept_id  BIGINT REFERENCES jobber.concept(id),
    resolved_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_concept_proposal_surface ON jobber.concept_proposal(surface_form, status);

-- Derived/rebuildable embeddings (doc 11 §4.6) — pgvector-backed. 384 dims =
-- BAAI/bge-small-en-v1.5 (this app's only embedding model today). A future
-- model with a different dimension needs a new column/table, not a migration
-- of this one — consistent with "embeddings are a signal, never the sole home
-- of a fact": nothing else in the schema depends on this column's width.
CREATE TABLE IF NOT EXISTS jobber.d_embedding (
    owner_kind  TEXT NOT NULL,   -- concept | episode | role_instance | document | profile_snapshot
    owner_id    BIGINT NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      vector(384) NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_kind, owner_id, model)
);
CREATE INDEX IF NOT EXISTS idx_d_embedding_cosine ON jobber.d_embedding
    USING ivfflat (vector vector_cosine_ops) WITH (lists = 100);

-- === Layer 2: subjects ======================================================

CREATE TABLE IF NOT EXISTS jobber.person (
    id           BIGSERIAL PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobber.episode (
    id                BIGSERIAL PRIMARY KEY,
    person_id         BIGINT NOT NULL REFERENCES jobber.person(id),
    kind              TEXT NOT NULL,   -- employment | project | study | qualification | other
    title             TEXT NOT NULL,
    organisation      TEXT,
    start_date        TEXT,            -- ISO; may be YYYY or YYYY-MM (partial dates, so TEXT not DATE)
    end_date          TEXT,
    date_precision    TEXT NOT NULL DEFAULT 'month',
    parent_episode_id BIGINT REFERENCES jobber.episode(id),
    domain_hint       TEXT,
    context_note      TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_episode_person ON jobber.episode(person_id, start_date);

CREATE TABLE IF NOT EXISTS jobber.episode_document (
    episode_id  BIGINT NOT NULL REFERENCES jobber.episode(id) ON DELETE CASCADE,
    document_id BIGINT NOT NULL REFERENCES jobber.document(id),
    PRIMARY KEY (episode_id, document_id)
);

-- role_instance: designed in doc 11 §4.3 but never built pre-Phase-2 (flagged
-- "unscoped" in both the Phase 0 and Phase 1 build notes there). The Phase 2
-- brief states 23 rows already exist under this exact name — this is that
-- table, finally built, with `kind` covering the three existing node types
-- plus 'synthetic_reference' per the Phase 2 brief §4 (not created by any code
-- path yet — reserved so an explicitly-labelled reference role never has to
-- masquerade as an observed posting or a real target).
CREATE TABLE IF NOT EXISTS jobber.role_instance (
    id                   BIGSERIAL PRIMARY KEY,
    kind                 TEXT NOT NULL CHECK (kind IN ('posting', 'target_real', 'target_imagined', 'synthetic_reference')),
    document_id          BIGINT REFERENCES jobber.document(id),   -- nullable: absence IS the "no source document" signal
    archetype_concept_id BIGINT REFERENCES jobber.concept(id),
    title                TEXT NOT NULL,
    organisation         TEXT,
    location             TEXT,
    country              TEXT,
    remote_type          TEXT,
    employment_type      TEXT,
    seniority_level      TEXT,
    posting_date         TEXT,
    captured_at          TIMESTAMPTZ,
    url                  TEXT,
    summary              TEXT,
    career_track         TEXT,   -- legacy_role_analysis facet (doc 11 §10.2), kept live pending domain/function concept links
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_role_instance_kind ON jobber.role_instance(kind, archetype_concept_id);

-- role_skill_observation: this app's pre-existing job_role_skills, renamed to
-- match the Phase 2 brief's stated jobber table name. Explicitly legacy/flat —
-- not the canonical requirement_claim model (0002). Kept for compatibility
-- (§14 of the brief) and as raw input to vocabulary review, nothing else.
CREATE TABLE IF NOT EXISTS jobber.role_skill_observation (
    id                  BIGSERIAL PRIMARY KEY,
    role_instance_id    BIGINT NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    category            TEXT,
    importance          INTEGER,
    requirement_type    TEXT,
    resolved_concept_id BIGINT REFERENCES jobber.concept(id)
);
CREATE INDEX IF NOT EXISTS idx_role_skill_observation_role ON jobber.role_skill_observation(role_instance_id);
CREATE INDEX IF NOT EXISTS idx_role_skill_observation_name ON jobber.role_skill_observation(name);

-- Legacy v1 flat fields (career_track lives on role_instance directly, above,
-- since the Space/Dashboard views facet on it today; everything else here is
-- model-judgment scores with no provenance, doc 11 §10.2 "legacy_role_analysis" —
-- parked verbatim, wired into nothing new).
CREATE TABLE IF NOT EXISTS jobber.legacy_role_analysis (
    role_instance_id          BIGINT PRIMARY KEY REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    seniority_score            REAL,
    complexity_score           REAL,
    specialisation_score       REAL,
    transferability_score      REAL,
    market_demand_score        REAL,
    rarity_score               REAL,
    automation_risk_score      REAL,
    top_adjacent_roles         JSONB,
    salary_min                 REAL,
    salary_max                 REAL,
    salary_estimate_min        REAL,
    salary_estimate_max        REAL,
    currency                   TEXT,
    key_skills_summary         TEXT,
    description                TEXT,
    requirements               TEXT,
    responsibilities           TEXT,
    notes                      TEXT,
    extraction_status          TEXT,
    extraction_notes           TEXT,
    raw_json                   JSONB,
    -- target-role-only fields (kind = target_real | target_imagined)
    typical_tasks               JSONB,
    skill_decomposition         JSONB,
    technical_subjects          JSONB,
    grounding_note               TEXT,
    feasibility_note             TEXT,
    is_plausible                 BOOLEAN
);

CREATE TABLE IF NOT EXISTS jobber.profile_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    narrative_text   TEXT NOT NULL,
    embedding_model  TEXT,
    is_current       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_profile_snapshots_current ON jobber.profile_snapshots(is_current);

-- === Migration bookkeeping (this build's own runner, see app/db.py) ========

CREATE TABLE IF NOT EXISTS jobber.migration_history (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- === Seed vocabulary (doc 11 §2.3 concept types, §4.2.4 edge grammar) ======
-- Identical to this app's pre-existing SQLite seed (app/db.py) — same 10 types,
-- same 21 edge rules. INSERT ... ON CONFLICT DO NOTHING, so this is safe to run
-- against a database where the real migration already seeded these rows.

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
