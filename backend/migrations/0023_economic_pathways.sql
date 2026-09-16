-- Career Planner: economic pathways, compensation context and archetype
-- context. Additive only — no existing column is dropped or retyped, no
-- existing row is touched, and every new table/column is nullable or
-- defaulted so an existing deployment keeps working with zero backfill.
--
-- Five things are genuinely new here; everything else this build needs
-- already exists and is reused unchanged (profile360.compensation_observation
-- from 0015, jobber.compensation_observation / d_archetype_comp /
-- d_archetype_demand / d_gap_value / market from 0013-0014,
-- role_instance.archetype_concept_id + role_archetype_detail from 0002,
-- role_context_enrichment from 0011, the target-analysis revision/cache
-- machinery from 0019).

-- --- 1. Exact source span on a posting compensation observation ----------
--
-- Posting compensation review (build §2) is only allowed to create a stated
-- economic fact when the AI proposal quoted the immutable source document
-- verbatim — the same rule requirement_claim.evidence_span already enforces
-- for capability evidence (0003). The column is nullable because every
-- existing row legitimately has no span: the 0013 backfill is a mechanical
-- projection of already-structured salary columns (no quote exists), and
-- survey extraction cites page/table references instead.
ALTER TABLE jobber.compensation_observation
    ADD COLUMN IF NOT EXISTS evidence_span TEXT;

-- --- 2. Explicit planning assumptions (build §1) --------------------------
--
-- A contract day rate is never silently annualised. When the user wants a
-- planning comparison against an annual salary they must state the
-- assumption (billable days per year) themselves, and the resulting figure
-- is always labelled a planning equivalent, never a salary.
--
-- This lives in `jobber`, not `profile360`, and that is deliberate: it is
-- not a person-side *source fact* (profile360 owns those, and this app only
-- ever reads them) but a parameter of this application's own analysis. No
-- compensation amount is stored here — only the assumption used to derive a
-- comparison at read time.
--
-- Singleton, matching this app's single-profile architecture (same shape as
-- jobber.target_analysis_revision, 0019). A deployment with no row, or a row
-- with a NULL day count, simply has no assumption — day rates then stay
-- daily and no annual comparison is offered.
CREATE TABLE IF NOT EXISTS jobber.planning_assumption (
    singleton                       BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    contract_billable_days_per_year NUMERIC
        CHECK (contract_billable_days_per_year IS NULL
               OR (contract_billable_days_per_year > 0 AND contract_billable_days_per_year <= 366)),
    note                            TEXT,
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO jobber.planning_assumption (singleton) VALUES (true) ON CONFLICT (singleton) DO NOTHING;

-- --- 3. Archetype Day-in-the-Life / context enrichment (build §10) --------
--
-- Deliberately the same table shape, lifecycle and one-active invariant as
-- jobber.role_context_enrichment (0011) — an archetype's context is the same
-- kind of object as a role's, just grounded in the archetype's assigned
-- postings and derived demand rather than one advert. History is retained
-- (rows are superseded, never deleted) and exactly one row per archetype may
-- be active, enforced by the partial unique index rather than only by
-- application care.
CREATE TABLE IF NOT EXISTS jobber.archetype_context_enrichment (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    archetype_concept_id  UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    generator_version     TEXT NOT NULL,
    model                 TEXT NOT NULL,
    source_fingerprint    TEXT,
    grounding_provenance  JSONB NOT NULL DEFAULT '{}',
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

CREATE INDEX IF NOT EXISTS idx_archetype_context_enrichment_archetype
    ON jobber.archetype_context_enrichment(archetype_concept_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_archetype_context_enrichment_one_active
    ON jobber.archetype_context_enrichment(archetype_concept_id) WHERE status = 'active';

-- --- 4. New extraction_run tasks ------------------------------------------
--
-- Three new AI tasks, none of which does concept-linking/matching, so none
-- has a vocabulary_version_id to report — same reasoning 0007/0011/0013/0017
-- already applied to job_posting_extract, role_context_generate,
-- compensation_extract and role_metadata_enrich.
--
-- IMPORTANT (as 0017 warns): this constraint is redefined by drop-and-re-add
-- each time, so the allow-list below must carry every previously exempted
-- task as well as the new ones, or existing tasks silently break. The full
-- inherited set at this point is 0007's job_posting_extract, 0011's
-- role_context_generate, 0013's compensation_extract, 0017's
-- role_metadata_enrich and 0018's target_decompose.
--
-- `archetype_context_generate` has subject_type='role_instance' problems:
-- extraction_run's subject-FK CHECK (0003) has no 'role_archetype' kind, and
-- 0012 already established the precedent that widening those two *unnamed*
-- constraints is riskier than the feature warrants. So archetype context
-- generation records its run against the archetype's own *document-free*
-- shape by reusing subject_type='role_instance' with the archetype's
-- highest-evidence assigned role — see app/archetype_context.py, which
-- explains the linkage and stores the full grounding provenance on the
-- enrichment row itself rather than relying on the run's subject.
ALTER TABLE jobber.extraction_run DROP CONSTRAINT IF EXISTS extraction_run_vocabulary_required_check;
ALTER TABLE jobber.extraction_run
    ADD CONSTRAINT extraction_run_vocabulary_required_check
    CHECK (vocabulary_version_id IS NOT NULL OR task IN (
        'job_posting_extract', 'role_context_generate', 'compensation_extract',
        'role_metadata_enrich', 'target_decompose',
        'posting_compensation_extract', 'role_archetype_classify', 'archetype_context_generate'
    ));

-- --- 5. Pathways cache + an economics revision counter ---------------------
--
-- 0019 gave this app two rebuildable revision counters (`evidence`, `path`)
-- advanced by statement-level triggers. Pathways depends on both of those
-- *and* on compensation/market/derived-economics state, which nothing
-- currently invalidates. Rather than overloading `path` (which would make
-- every accepted compensation observation needlessly rebuild every target
-- path), a third counter is added and the shared trigger function is
-- extended with a third argument.
--
-- The replacement function preserves the existing 'evidence'/'path'
-- behaviour exactly — both still bump `path`, and 'evidence' still also
-- bumps `evidence` — so every trigger 0019 installed keeps its current
-- meaning without being recreated.
ALTER TABLE jobber.target_analysis_revision ADD COLUMN IF NOT EXISTS economics BIGINT NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION jobber.invalidate_target_analysis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE jobber.target_analysis_revision
    SET path      = path + CASE WHEN TG_ARGV[0] = 'economics' THEN 0 ELSE 1 END,
        evidence  = evidence + CASE WHEN TG_ARGV[0] = 'evidence' THEN 1 ELSE 0 END,
        economics = economics + CASE WHEN TG_ARGV[0] = 'economics' THEN 1 ELSE 0 END;
    RETURN NULL;
END $$;

DO $$
DECLARE target_table text;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['compensation_observation', 'market', 'planning_assumption',
        'd_archetype_comp', 'd_gap_value', 'd_archetype_demand'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'jobber' AND c.relname = target_table
              AND t.tgname = 'invalidate_target_analysis_economics'
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER invalidate_target_analysis_economics '
                'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON jobber.%I '
                'FOR EACH STATEMENT EXECUTE FUNCTION jobber.invalidate_target_analysis(''economics'')',
                target_table);
        END IF;
    END LOOP;
END $$;

-- Pathways composition is expensive (requirement evidence for every
-- candidate posting, archetype grouping, compensation resolution) and is
-- opened interactively, so it gets the same rebuildable-cache treatment
-- d_target_path already has. `context_key` carries the selected market/
-- currency, since the same target has a different economic answer per
-- market. `revision` folds in the evidence/path/economics counters *and* a
-- profile360 personal-compensation fingerprint (app/pathways.py) — an
-- externally-owned schema this app never triggers on.
CREATE TABLE IF NOT EXISTS jobber.d_pathways (
    target_role_id uuid NOT NULL REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    context_key    text NOT NULL,
    revision       text NOT NULL,
    result         jsonb NOT NULL,
    prepared_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (target_role_id, context_key)
);

-- --- 6. Human planning fields on development actions (build §9) -----------
--
-- Conservative transition cost uses only what the system actually knows.
-- These two columns let a *human* record their own plan; nothing derives,
-- estimates or defaults them, and a development action never becomes
-- capability evidence — it has no path into requirement_claim,
-- person_capability_assertion or any profile360 table.
ALTER TABLE jobber.development_action
    ADD COLUMN IF NOT EXISTS planned_start_date DATE,
    ADD COLUMN IF NOT EXISTS estimated_effort_hours NUMERIC
        CHECK (estimated_effort_hours IS NULL OR estimated_effort_hours > 0);
