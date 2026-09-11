-- Personal compensation history belongs in profile360, not jobber.
-- Additive only: preserve source facts without forcing PAYE and contracting
-- into a single annual-salary representation.
--
-- The live open-brain profile360 schema was re-inspected on 2026-09-11:
-- episodes.id and documents.id are UUID primary keys. jobber already has a
-- separate market-side compensation_observation table; nothing here copies
-- person-side compensation into jobber.

DO $$
DECLARE
    missing TEXT := '';
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'profile360' AND table_name = 'episodes'
          AND column_name = 'id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  profile360.episodes.id must be uuid';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'profile360' AND table_name = 'documents'
          AND column_name = 'id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  profile360.documents.id must be uuid';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION E'Profile360 compensation migration preflight failed:%', missing;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS profile360.compensation_observation (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key       TEXT NOT NULL UNIQUE,
    episode_id       UUID REFERENCES profile360.episodes(id) ON DELETE SET NULL,

    source_kind      TEXT NOT NULL CHECK (
        source_kind IN ('payslip', 'contract', 'invoice', 'user_asserted', 'other')
    ),
    employment_basis TEXT CHECK (
        employment_basis IS NULL OR employment_basis IN ('paye', 'contract', 'other', 'unknown')
    ),
    component        TEXT NOT NULL CHECK (
        component IN (
            'annual_base', 'periodic_base', 'gross_pay', 'day_rate',
            'bonus', 'allowance', 'employer_pension', 'other'
        )
    ),

    period_start     DATE,
    period_end       DATE,
    pay_date         DATE,
    amount           NUMERIC NOT NULL,
    currency         TEXT,
    unit             TEXT CHECK (
        unit IS NULL OR unit IN ('annual', 'monthly', 'weekly', 'daily', 'period', 'one_off')
    ),
    quantity         NUMERIC,

    notes            TEXT,
    uncertainty      TEXT,
    review_status    TEXT NOT NULL DEFAULT 'unreviewed' CHECK (
        review_status IN ('unreviewed', 'accepted', 'rejected')
    ),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at      TIMESTAMPTZ,

    CHECK (period_start IS NULL OR period_end IS NULL OR period_end >= period_start),
    CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CHECK (quantity IS NULL OR quantity > 0)
);

CREATE INDEX IF NOT EXISTS idx_profile360_comp_obs_episode
    ON profile360.compensation_observation(episode_id);
CREATE INDEX IF NOT EXISTS idx_profile360_comp_obs_period
    ON profile360.compensation_observation(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_profile360_comp_obs_component
    ON profile360.compensation_observation(component);
CREATE INDEX IF NOT EXISTS idx_profile360_comp_obs_review
    ON profile360.compensation_observation(review_status);

-- One fact may be supported by several source documents. Keeping that as a
-- separate relation avoids duplicating the compensation fact once per payslip,
-- contract, or invoice and preserves clean many-to-many provenance.
CREATE TABLE IF NOT EXISTS profile360.compensation_evidence (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id UUID NOT NULL REFERENCES profile360.compensation_observation(id) ON DELETE CASCADE,
    document_id    UUID NOT NULL REFERENCES profile360.documents(id) ON DELETE CASCADE,
    locator        TEXT,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (observation_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_profile360_comp_evidence_document
    ON profile360.compensation_evidence(document_id);

-- No derived annual-equivalent table is created here. Any future annualisation
-- of day rates or repeated periodic pay must remain explicitly derived and
-- traceable rather than being stored as if it were a source-stated fact.
