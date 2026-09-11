-- Phase 4 market-report ingestion refinement. Additive only.
ALTER TABLE jobber.compensation_observation
    ADD COLUMN IF NOT EXISTS reported_mean NUMERIC,
    ADD COLUMN IF NOT EXISTS geography_reported TEXT,
    ADD COLUMN IF NOT EXISTS domain_or_practice_area TEXT,
    ADD COLUMN IF NOT EXISTS seniority_band_reported TEXT,
    ADD COLUMN IF NOT EXISTS experience_band TEXT,
    ADD COLUMN IF NOT EXISTS pqe_band TEXT,
    ADD COLUMN IF NOT EXISTS source_kind TEXT;
