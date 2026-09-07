-- Persisted, on-demand Day-in-the-Life / Role Context enrichment
-- (next-build brief §6/§7/§10). Additive only: no existing column dropped or
-- retyped, no existing row touched. Role-side market/context enrichment —
-- lives in jobber, never in profile360 (brief §6.1's "not person-side
-- evidence" boundary).
--
-- Version history is retained (rows are never deleted — consistent with
-- this schema's general "evidence/audit rows are never destroyed" posture,
-- e.g. concept_proposal's rejected/accepted rows), but exactly one row per
-- role may be `status = 'active'` at a time, enforced by the partial unique
-- index below rather than only by application-level care — a genuinely
-- concurrent double-generate cannot leave two "current" enrichments for one
-- role. `GET .../context` only ever reads the active row (brief §6.4:
-- "expose only the latest active one by default"); regenerate marks the
-- prior active row 'superseded' and inserts a new active one in the same
-- transaction (app/role_context.py).
CREATE TABLE IF NOT EXISTS jobber.role_context_enrichment (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_instance_id      UUID NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    generator_version     TEXT NOT NULL,
    model                 TEXT NOT NULL,
    source_document_id    UUID REFERENCES jobber.document(id),
    source_content_sha256 TEXT,
    day_in_life           JSONB NOT NULL DEFAULT '[]',
    typical_week          JSONB NOT NULL DEFAULT '[]',
    team_context          JSONB NOT NULL DEFAULT '{}',
    manager_context       JSONB NOT NULL DEFAULT '{}',
    stakeholder_context   JSONB NOT NULL DEFAULT '{}',
    career_progression    JSONB NOT NULL DEFAULT '[]',
    grounding_summary     JSONB NOT NULL DEFAULT '{}',
    caveats               TEXT,
    raw_output            JSONB,
    extraction_run_id     UUID REFERENCES jobber.extraction_run(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_role_context_enrichment_role ON jobber.role_context_enrichment(role_instance_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_role_context_enrichment_one_active
    ON jobber.role_context_enrichment(role_instance_id) WHERE status = 'active';

-- role_context_generate has no controlled-vocabulary dependency (it never
-- does concept-linking/matching), so it joins job_posting_extract as the
-- other task exempt from extraction_run's "vocabulary_version_id required"
-- rule (0007) — additive/loosening, same pattern that migration used.
ALTER TABLE jobber.extraction_run DROP CONSTRAINT IF EXISTS extraction_run_vocabulary_required_check;
ALTER TABLE jobber.extraction_run
    ADD CONSTRAINT extraction_run_vocabulary_required_check
    CHECK (vocabulary_version_id IS NOT NULL OR task IN ('job_posting_extract', 'role_context_generate'));
