-- Phase 2 additions to the existing (live, UUID-keyed) jobber vocabulary.
-- Every FK to concept/document below is UUID, matching the confirmed
-- production types asserted in 0001. All ids here are first-class new Phase 2
-- entities, so they get real UUID surrogate keys of their own
-- (gen_random_uuid()) — there is no legacy integer id to preserve for
-- anything created in this migration, unlike the pre-existing tables.

-- Two small additive columns on the existing (live) role_instance table.
-- Neither existed in production at review time; both are genuinely new
-- Phase 2 needs, not a reconstruction of something already there.
ALTER TABLE jobber.role_instance
    ADD COLUMN IF NOT EXISTS archetype_concept_id UUID REFERENCES jobber.concept(id);

-- Production's instance_type ('observed_posting' | 'user_defined_target' |
-- 'synthetic_reference') collapses the app's former real-vs-imagined target
-- distinction into one value. target_basis restores it, scoped to targets
-- only, without introducing a second, competing type system alongside
-- instance_type.
ALTER TABLE jobber.role_instance
    ADD COLUMN IF NOT EXISTS target_basis TEXT
    CHECK (target_basis IS NULL OR target_basis IN ('real_role', 'imagined'));

CREATE TABLE IF NOT EXISTS jobber.capability_detail (
    concept_id             UUID PRIMARY KEY REFERENCES jobber.concept(id) ON DELETE CASCADE,
    demonstration_standard TEXT NOT NULL,
    min_depth              TEXT NOT NULL DEFAULT 'owned',
    min_autonomy           TEXT,
    requires_all_core      BOOLEAN NOT NULL DEFAULT TRUE,
    economic_salience      TEXT,
    notes                  TEXT
);

CREATE TABLE IF NOT EXISTS jobber.role_archetype_detail (
    concept_id                  UUID PRIMARY KEY REFERENCES jobber.concept(id) ON DELETE CASCADE,
    seniority_band              TEXT,
    primary_function_concept_id UUID REFERENCES jobber.concept(id),
    typical_market               TEXT,
    notes                       TEXT
);

CREATE TABLE IF NOT EXISTS jobber.concept_xref (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    scheme     TEXT NOT NULL,
    code       TEXT NOT NULL,
    label      TEXT,
    UNIQUE (concept_id, scheme, code)
);

CREATE TABLE IF NOT EXISTS jobber.concept_edge (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_concept_id UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    to_concept_id   UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL,
    necessity       TEXT,
    weight          REAL,
    note            TEXT,
    origin          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'proposed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_concept_id, to_concept_id, relation)
);

CREATE TABLE IF NOT EXISTS jobber.concept_proposal (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    surface_form         TEXT NOT NULL,
    suggested_type       TEXT REFERENCES jobber.concept_type(code),
    suggested_definition TEXT,
    nearest_concept_id   UUID REFERENCES jobber.concept(id),
    nearest_similarity   REAL,
    occurrence_count     INTEGER NOT NULL DEFAULT 1,
    document_id          UUID REFERENCES jobber.document(id),
    evidence_span        TEXT,
    extraction_run_id    UUID,  -- FK added in 0003 once extraction_run exists
    status               TEXT NOT NULL DEFAULT 'pending',  -- pending|accepted_new|accepted_alias|rejected|deferred
    resolved_concept_id  UUID REFERENCES jobber.concept(id),
    resolved_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_concept_proposal_surface ON jobber.concept_proposal(surface_form, status);

CREATE TABLE IF NOT EXISTS jobber.vocabulary_version (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    concept_count INTEGER NOT NULL,
    note          TEXT
);

-- Derived/rebuildable embeddings (doc 11 §4.6) — pgvector-backed, 384 dims
-- (BAAI/bge-small-en-v1.5, this app's only embedding model). owner_id is
-- UUID because every owner_kind this app actually uses (concept,
-- role_instance, document, profile360_snapshot) has a UUID primary key in
-- production — see docs/14 §8.
CREATE TABLE IF NOT EXISTS jobber.d_embedding (
    owner_kind  TEXT NOT NULL,   -- concept | role_instance | document | profile360_snapshot
    owner_id    UUID NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      vector(384) NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_kind, owner_id, model)
);
CREATE INDEX IF NOT EXISTS idx_d_embedding_cosine ON jobber.d_embedding
    USING ivfflat (vector vector_cosine_ops) WITH (lists = 100);
