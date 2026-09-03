-- Phase 3: capability coverage / role-fit derivation, plus the Phase 2 eval
-- debt (gold_document/gold_claim/eval_run) and the Phase 3 capability-
-- agreement gold set. See docs/16-phase3-capability-engine.md for the full
-- design and docs/11-capability-model-design.md §4.6/§9.2 for the design
-- this adapts.
--
-- Additive only, per brief §34: no existing table is dropped, no column
-- type is converted, no production identity is touched. `jobber.concept`,
-- `jobber.capability_detail`, `jobber.concept_edge` and
-- `jobber.role_instance` already exist (0001-0003) and are only extended
-- here (a new nullable column, two guarded CHECK constraints matching what
-- the app already enforces in code) or referenced by FK.
--
-- Deliberate deviation from doc 11 §4.6's `d_capability_coverage`/
-- `d_role_fit` DDL: no `person_id` column. `jobber.person` does not exist in
-- production and must not be reintroduced (brief §41, docs/14 §6/§9) — this
-- application operates against exactly one authoritative profile360
-- evidence set, so both derived tables below are keyed on the concept/role
-- alone. If multi-person support is ever needed, that is a future schema
-- decision, not something to pre-empt here.

-- capability_detail.requires_all_core=false needs an explicit, transparent
-- deterministic completeness rule rather than an invented percentage (brief
-- §12). The smallest additive extension that expresses "a curator can name
-- exactly how many core components are enough" is one nullable integer
-- column; NULL means the engine's documented default (>=1 core component)
-- applies. See capability_engine.py::_core_required.
ALTER TABLE jobber.capability_detail
    ADD COLUMN IF NOT EXISTS min_core_required INTEGER;

-- concept_edge.status/necessity have been free TEXT since 0002 (doc 11's
-- original DDL never constrained them beyond a code comment). Phase 3 is the
-- first thing that curates real component_of edges, so this is the first
-- safe moment to add the CHECK the app-layer validation
-- (routes/capabilities.py) already enforces — defense in depth, not a
-- behaviour change: concept_edge has carried zero rows through Phases 1-2
-- (doc 11 §11 Phase 1 build note 5), so there is nothing existing to
-- violate it. Guarded/idempotent like 0003's FK addition, and this fails
-- loudly rather than silently if that assumption is ever wrong.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_schema = 'jobber' AND table_name = 'concept_edge'
          AND constraint_name = 'concept_edge_status_check'
    ) THEN
        ALTER TABLE jobber.concept_edge
            ADD CONSTRAINT concept_edge_status_check
            CHECK (status IN ('proposed', 'accepted', 'rejected'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_schema = 'jobber' AND table_name = 'concept_edge'
          AND constraint_name = 'concept_edge_necessity_check'
    ) THEN
        ALTER TABLE jobber.concept_edge
            ADD CONSTRAINT concept_edge_necessity_check
            CHECK (necessity IS NULL OR necessity IN ('core', 'supporting', 'contextual'));
    END IF;

    -- Same reasoning applied to capability_detail.min_depth/min_autonomy —
    -- free TEXT since 0002, same ordinal vocabulary the engine now compares
    -- centrally (capability_engine.py). Existing rows only ever hold the
    -- column default ('owned') or NULL, so this cannot fail against them.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_schema = 'jobber' AND table_name = 'capability_detail'
          AND constraint_name = 'capability_detail_min_depth_check'
    ) THEN
        ALTER TABLE jobber.capability_detail
            ADD CONSTRAINT capability_detail_min_depth_check
            CHECK (min_depth IN ('exposed', 'applied', 'owned', 'set_standard'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_schema = 'jobber' AND table_name = 'capability_detail'
          AND constraint_name = 'capability_detail_min_autonomy_check'
    ) THEN
        ALTER TABLE jobber.capability_detail
            ADD CONSTRAINT capability_detail_min_autonomy_check
            CHECK (min_autonomy IS NULL OR min_autonomy IN ('assisted', 'independent', 'directed_others', 'accountable'));
    END IF;
END $$;

-- --- Derived layer (doc 11 §4.6, adapted per brief §41) --------------------

CREATE TABLE IF NOT EXISTS jobber.d_capability_coverage (
    capability_concept_id UUID PRIMARY KEY
        REFERENCES jobber.concept(id) ON DELETE CASCADE,

    status TEXT NOT NULL
        CHECK (status IN ('evidenced', 'partial', 'user_asserted', 'not_found')),

    coverage_score REAL,  -- internal ordering only — never shown to the user as a percentage (brief §14)

    core_components_total INTEGER NOT NULL DEFAULT 0,
    core_components_met   INTEGER NOT NULL DEFAULT 0,

    strongest_depth    TEXT,
    strongest_autonomy TEXT,

    directly_claimed BOOLEAN NOT NULL DEFAULT FALSE,

    last_demonstrated DATE,
    years_active      REAL,

    supporting_profile360_claim_ids UUID[] NOT NULL DEFAULT '{}',
    trace JSONB NOT NULL DEFAULT '{}',

    vocabulary_version_id UUID REFERENCES jobber.vocabulary_version(id),
    engine_version TEXT NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobber.d_role_fit (
    role_instance_id UUID PRIMARY KEY
        REFERENCES jobber.role_instance(id) ON DELETE CASCADE,

    capabilities_required INTEGER NOT NULL DEFAULT 0,
    n_evidenced  INTEGER NOT NULL DEFAULT 0,
    n_partial    INTEGER NOT NULL DEFAULT 0,
    n_asserted   INTEGER NOT NULL DEFAULT 0,
    n_not_found  INTEGER NOT NULL DEFAULT 0,

    -- JSONB arrays of {concept_id, canonical_name, requirement_type, status}
    -- rather than a bare UUID[] — "structurally interpretable" per brief §15,
    -- readable by the frontend with no extra join.
    blocking_gaps       JSONB NOT NULL DEFAULT '[]',
    unverified_required JSONB NOT NULL DEFAULT '[]',

    fit_score            REAL,  -- secondary signal only, see brief §18 and capability_engine.py::_fit_score
    embedding_similarity REAL,  -- one signal beside the structural result, never overrides it (brief §19)

    trace JSONB NOT NULL DEFAULT '{}',

    vocabulary_version_id UUID REFERENCES jobber.vocabulary_version(id),
    engine_version TEXT NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- Phase 2 evaluation debt (brief §24/§25) --------------------------------
--
-- UUID-compatible adaptation of doc 11 §9.2's gold_document/gold_claim/
-- eval_run (originally BIGSERIAL). gold_claim.concept_id/document_id point
-- at jobber's own document/concept tables — the only extraction pipeline
-- this codebase owns is role-side (job_posting documents ->
-- requirement_claim); see docs/16 §9 for why CV/LinkedIn/project-writeup
-- strata from doc 11's original 16-document plan have no equivalent
-- extraction pipeline here (that content and its extraction belong to
-- profile360, a separate tool this build does not own or measure).

CREATE TABLE IF NOT EXISTS jobber.gold_document (
    document_id UUID PRIMARY KEY REFERENCES jobber.document(id) ON DELETE CASCADE,
    split       TEXT NOT NULL CHECK (split IN ('dev', 'test')),
    stratum     TEXT NOT NULL,
    labelled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS jobber.gold_claim (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id       UUID NOT NULL REFERENCES jobber.gold_document(document_id) ON DELETE CASCADE,
    subject_hint      TEXT,
    concept_id        UUID NOT NULL REFERENCES jobber.concept(id),
    relation          TEXT NOT NULL,
    depth             TEXT,
    autonomy          TEXT,
    requirement_type  TEXT,
    evidence_span     TEXT NOT NULL,
    is_core           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gold_claim_document ON jobber.gold_claim(document_id);
CREATE INDEX IF NOT EXISTS idx_gold_claim_concept ON jobber.gold_claim(concept_id);

CREATE TABLE IF NOT EXISTS jobber.eval_run (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    split                 TEXT NOT NULL CHECK (split IN ('dev', 'test')),
    task                  TEXT NOT NULL,
    model                 TEXT,
    prompt_version        TEXT,
    vocabulary_version_id UUID REFERENCES jobber.vocabulary_version(id),
    precision_micro       REAL,
    recall_micro          REAL,
    f1_micro              REAL,
    span_validity         REAL,
    proposals_per_doc     REAL,
    modifier_accuracy     REAL,
    n_gold                INTEGER,
    run_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes                 TEXT
);

-- --- Phase 3 capability-agreement evaluation (brief §26) --------------------

CREATE TABLE IF NOT EXISTS jobber.capability_gold_judgment (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability_concept_id UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    expected_status       TEXT NOT NULL
        CHECK (expected_status IN ('evidenced', 'partial', 'user_asserted', 'not_found')),
    expected_missing_core_component_ids UUID[] NOT NULL DEFAULT '{}',
    expected_modifier_shortfall TEXT,
    notes                 TEXT,
    split                 TEXT NOT NULL DEFAULT 'dev' CHECK (split IN ('dev', 'test')),
    labelled_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_capability_gold_judgment_capability ON jobber.capability_gold_judgment(capability_concept_id);
