-- Phase 4: market economics / compensation / gap value (docs/23 prompt,
-- reconciled against doc 11 §4.5/§4.6/§11 and doc 12 §2). Additive only: no
-- existing column dropped or retyped, no existing row touched.
--
-- Role archetypes need NO schema here: concept(type_code='role_archetype'),
-- jobber.role_archetype_detail, and role_instance.archetype_concept_id all
-- already exist (0002) and are simply unused in production today (0 active
-- role_archetype concepts, 0 assigned roles) — this migration only adds what
-- is genuinely new: the market dimension, the compensation evidence layer,
-- and the three disposable/rebuildable Phase 4 derived tables.
--
-- Deliberate deviation from doc 11 §4.5: jobber.episode_compensation is NOT
-- built. Phase 2 established profile360 as the sole authoritative store for
-- person-side career evidence, and the current profile360 schema exposes no
-- structured compensation fields — adding a second, jobber-local home for
-- the user's own salary history would violate that boundary. See
-- docs/24-phase4-economics-and-vocabulary-overview.md for the full
-- deviation record. "Current benchmark" throughout this build means the best
-- compensation reference among currently structurally reachable archetypes,
-- never the user's actual salary.
--
-- Deviation from doc 11 §4.6's d_gap_value DDL (flagged in advance by doc 12
-- §2.3): market is part of this table's primary key from day one, not added
-- later. No person_id anywhere below, consistent with Phase 3's single-
-- profile architecture (0006's own deviation note) — jobber.person does not
-- exist in production and must not be reintroduced.

-- --- Market: the controlled comparison-context dimension (prompt §5) ------
--
-- Market is a property of an observation/aggregate, never of a capability
-- (doc 12 §2.6) — this table exists purely so every compensation fact and
-- every derived aggregate can name its market explicitly rather than
-- carrying a free-text string that drifts. Period is deliberately NOT here
-- (prompt §5): period belongs on compensation_observation/d_archetype_comp/
-- d_gap_value, not on the market row itself.
CREATE TABLE IF NOT EXISTS jobber.market (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code              TEXT NOT NULL UNIQUE,
    label             TEXT NOT NULL,
    country           TEXT,
    geography         TEXT,
    domain_concept_id UUID REFERENCES jobber.concept(id),
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated')),
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_market_status ON jobber.market(status);

-- --- Compensation observation: durable source-evidence layer (prompt §6) --
--
-- Every economic fact has a source (prompt §16). Supports both captured
-- posting compensation (basis='posting_stated'/'posting_estimated', §7's
-- backfill) and recruiter/market survey evidence (basis='survey', §8's
-- ingestion) — never combined into one row, never pooled as if the same
-- statistical object (§9). `curator_asserted` exists for a human directly
-- recording a benchmark with no posting/survey document behind it, the
-- economic-observation analogue of person_capability_assertion.
--
-- Amounts are nullable throughout: a source that states only a midpoint, or
-- only percentiles, or only a day rate, must not have the missing fields
-- fabricated (prompt §6 "Do not force values into fields the source did not
-- provide"). `amount_mid` may be a genuine source-stated midpoint OR a
-- deterministic range midpoint computed by the derived layer — the latter is
-- never written back here; this column, when populated on a row this app
-- itself wrote, is always taken verbatim from the source (survey extraction)
-- or left NULL (posting backfill, which computes its midpoint only in the
-- derived d_archetype_comp layer, never on the observation row itself).
CREATE TABLE IF NOT EXISTS jobber.compensation_observation (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key           TEXT NOT NULL UNIQUE,
    role_instance_id     UUID REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    archetype_concept_id UUID REFERENCES jobber.concept(id),
    raw_role_label       TEXT,
    market_id            UUID REFERENCES jobber.market(id),
    component            TEXT NOT NULL CHECK (component IN ('base', 'bonus_pct', 'total_package', 'day_rate')),
    pay_period           TEXT NOT NULL CHECK (pay_period IN ('annual', 'daily')),
    employment_basis     TEXT CHECK (employment_basis IS NULL OR employment_basis IN ('permanent', 'contract', 'unknown')),

    amount_min           NUMERIC,
    amount_mid           NUMERIC,
    amount_max           NUMERIC,
    currency             TEXT NOT NULL,
    reported_p25         NUMERIC,
    reported_p50         NUMERIC,
    reported_p75         NUMERIC,
    bonus_pct            NUMERIC,

    basis                TEXT NOT NULL CHECK (basis IN ('posting_stated', 'posting_estimated', 'survey', 'curator_asserted')),
    review_status        TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'accepted', 'rejected')),

    observed_at          DATE,
    period_start         DATE,
    period_end           DATE,

    document_id          UUID REFERENCES jobber.document(id),
    page_reference       TEXT,
    table_reference      TEXT,
    source_note          TEXT,
    reported_sample_size INTEGER,
    source_quality       TEXT,
    extraction_run_id     UUID REFERENCES jobber.extraction_run(id),

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at           TIMESTAMPTZ,

    -- A survey row may have neither a role_instance nor an archetype yet
    -- (prompt §8: "retain its raw_role_label and require curator assignment
    -- before it enters an archetype aggregate") — but it must be identifiable
    -- as *something*, so at least one of the three subject fields is required.
    CHECK (role_instance_id IS NOT NULL OR archetype_concept_id IS NOT NULL OR raw_role_label IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_compensation_observation_role ON jobber.compensation_observation(role_instance_id);
CREATE INDEX IF NOT EXISTS idx_compensation_observation_archetype ON jobber.compensation_observation(archetype_concept_id);
CREATE INDEX IF NOT EXISTS idx_compensation_observation_market ON jobber.compensation_observation(market_id);
CREATE INDEX IF NOT EXISTS idx_compensation_observation_review ON jobber.compensation_observation(review_status);
CREATE INDEX IF NOT EXISTS idx_compensation_observation_basis ON jobber.compensation_observation(basis);
CREATE INDEX IF NOT EXISTS idx_compensation_observation_bucket
    ON jobber.compensation_observation(archetype_concept_id, market_id, currency, component, pay_period, review_status);

-- --- Derived layer (prompt §15: disposable/rebuildable, engine_version + --
-- computed_at on every row, never the sole home of any fact) --------------

-- d_archetype_demand (prompt §4): empirically derived from assigned roles'
-- canonical requirement evidence (role_requirements.py) — never a curator-
-- typed intrinsic property, and never mixed into concept_edge's curated
-- component_of/demands ontology. Only capability-typed concepts participate.
CREATE TABLE IF NOT EXISTS jobber.d_archetype_demand (
    archetype_concept_id       UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    capability_concept_id      UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    roles_in_archetype         INTEGER NOT NULL DEFAULT 0,
    roles_demanding_capability INTEGER NOT NULL DEFAULT 0,
    demand_rate                REAL,
    required_count             INTEGER NOT NULL DEFAULT 0,
    preferred_count             INTEGER NOT NULL DEFAULT 0,
    contextual_count            INTEGER NOT NULL DEFAULT 0,
    source_role_ids              UUID[] NOT NULL DEFAULT '{}',
    trace                        JSONB NOT NULL DEFAULT '{}',
    vocabulary_version_id        UUID REFERENCES jobber.vocabulary_version(id),
    engine_version                TEXT NOT NULL,
    computed_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (archetype_concept_id, capability_concept_id)
);
CREATE INDEX IF NOT EXISTS idx_d_archetype_demand_capability ON jobber.d_archetype_demand(capability_concept_id);

-- d_archetype_comp (prompt §9/§10): posting and survey evidence are kept
-- statistically separate (never pooled into one fabricated "market median").
-- `reference_comp`/`reference_source`/`reference_basis_detail` are the
-- deterministic benchmark-selection rule's output (prompt §10) — always
-- present together with what was selected, or both null when no evidence
-- qualifies. Grain keeps economically incomparable observations apart:
-- archetype x market x period x currency x component x pay_period.
CREATE TABLE IF NOT EXISTS jobber.d_archetype_comp (
    archetype_concept_id  UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    market_id             UUID NOT NULL REFERENCES jobber.market(id) ON DELETE CASCADE,
    period_start          DATE NOT NULL,
    period_end            DATE NOT NULL,
    currency              TEXT NOT NULL,
    component             TEXT NOT NULL,
    pay_period            TEXT NOT NULL,

    n_observations        INTEGER NOT NULL DEFAULT 0,
    n_posting_stated       INTEGER NOT NULL DEFAULT 0,
    n_posting_estimated     INTEGER NOT NULL DEFAULT 0,
    n_survey_sources        INTEGER NOT NULL DEFAULT 0,

    -- Posting evidence: a deterministic "median advertised-range midpoint"
    -- (prompt §9), explicitly never labelled "market median salary".
    posting_p25            NUMERIC,
    posting_p50            NUMERIC,
    posting_p75            NUMERIC,

    -- Survey evidence: each qualifying accepted survey row's own reported
    -- figures, preserved as separate benchmark entries with publisher/source
    -- — never pooled with each other or with posting figures.
    survey_benchmarks       JSONB NOT NULL DEFAULT '[]',

    reference_comp          NUMERIC,
    reference_source        TEXT CHECK (reference_source IS NULL OR reference_source IN ('survey', 'posting')),
    reference_basis_detail   JSONB,

    trace                    JSONB NOT NULL DEFAULT '{}',
    engine_version            TEXT NOT NULL,
    computed_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (archetype_concept_id, market_id, period_start, period_end, currency, component, pay_period)
);
CREATE INDEX IF NOT EXISTS idx_d_archetype_comp_market ON jobber.d_archetype_comp(market_id, currency);

-- d_gap_value (prompt §11): the counterfactual "if this capability gap
-- became evidenced, what higher-value opportunities become structurally
-- reachable" answer. Contextual by construction — keyed by market/period/
-- currency from day one (doc 12 §2.3's advance amendment), never claims the
-- user has acquired the capability.
CREATE TABLE IF NOT EXISTS jobber.d_gap_value (
    capability_concept_id UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    market_id             UUID NOT NULL REFERENCES jobber.market(id) ON DELETE CASCADE,
    period_start          DATE NOT NULL,
    period_end            DATE NOT NULL,
    currency              TEXT NOT NULL,

    archetypes_unlocked   INTEGER NOT NULL DEFAULT 0,
    archetypes_improved    INTEGER NOT NULL DEFAULT 0,
    roles_unlocked          INTEGER NOT NULL DEFAULT 0,
    roles_improved           INTEGER NOT NULL DEFAULT 0,

    reference_comp_unlocked  NUMERIC,
    comp_delta_vs_best_current_reachable NUMERIC,
    n_comp_observations       INTEGER NOT NULL DEFAULT 0,
    evidence_quality           TEXT NOT NULL DEFAULT 'insufficient'
        CHECK (evidence_quality IN ('insufficient', 'thin', 'moderate', 'good')),
    rank                        INTEGER,

    trace                       JSONB NOT NULL DEFAULT '{}',
    engine_version                TEXT NOT NULL,
    computed_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (capability_concept_id, market_id, period_start, period_end, currency)
);
CREATE INDEX IF NOT EXISTS idx_d_gap_value_market ON jobber.d_gap_value(market_id, currency, rank);

-- `compensation_extract` (market survey extraction, prompt §8) has no
-- controlled-vocabulary dependency — it never does concept-linking/
-- matching, same as job_posting_extract and role_context_generate before
-- it (0007/0011). Additive/loosening, same named-constraint drop-then-
-- recreate pattern those migrations used.
ALTER TABLE jobber.extraction_run DROP CONSTRAINT IF EXISTS extraction_run_vocabulary_required_check;
ALTER TABLE jobber.extraction_run
    ADD CONSTRAINT extraction_run_vocabulary_required_check
    CHECK (vocabulary_version_id IS NOT NULL OR task IN ('job_posting_extract', 'role_context_generate', 'compensation_extract'));
