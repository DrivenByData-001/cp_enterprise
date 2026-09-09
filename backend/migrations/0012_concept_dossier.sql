-- Concept Dossier (cp_round_of_changes.md §C/§D/§E/§F): a first-class,
-- persisted, AI-assisted explanatory dossier for an accepted canonical
-- concept — the thing that turns a terse label like "ORSA" or "Prophet"
-- into something a curator can actually read and understand. Lives in
-- jobber, never profile360 (this describes the shared vocabulary, not the
-- user's own evidence — app/concept_dossier.py never reads profile360).
-- Additive only: no existing column dropped or retyped, no existing row
-- touched.
--
-- Lifecycle: active | draft | superseded (§C's recommended lifecycle,
-- taken verbatim). At most one active and at most one draft per concept,
-- enforced by the two partial unique indexes below — same pattern as
-- role_context_enrichment's one-active index (0011) — rather than only
-- application-level care. History is retained: a row is never deleted,
-- only ever moved to status='superseded', whether by curator manual edit (a
-- new active row supersedes the old one), AI regeneration (supersedes any
-- prior draft, never touches active), or draft adoption/discard. See
-- app/concept_dossier.py for the full state machine.
--
-- Audit metadata (model/prompt_version/guidance/raw_output) lives directly
-- on this table rather than in jobber.extraction_run. extraction_run's
-- subject_type/subject-FK CHECK constraints (0003) only cover
-- document/role_instance/profile360_claim/profile360_capability today, both
-- unnamed at creation (relying on Postgres's auto-generated constraint
-- names to safely DROP/ADD them, as 0011 does for the separate *named*
-- extraction_run_vocabulary_required_check, is not viable here) — extending
-- them for a fifth subject kind would mean altering two unnamed constraints
-- on a table every other AI feature in this app also writes to, which is a
-- materially riskier change than this feature warrants. Every other
-- invariant role_context.py established is preserved exactly instead: GET
-- never generates, the AI call happens outside any DB transaction, and a
-- failed call never touches the current active row.
CREATE TABLE IF NOT EXISTS jobber.concept_dossier (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id                UUID NOT NULL REFERENCES jobber.concept(id) ON DELETE CASCADE,
    status                    TEXT NOT NULL CHECK (status IN ('active', 'draft', 'superseded')),
    origin                    TEXT NOT NULL CHECK (origin IN ('ai', 'curator')),
    generated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    generator_version         TEXT NOT NULL,
    model                     TEXT,             -- null for a curator manual-edit version
    prompt_version            TEXT,
    guidance                  TEXT,             -- the optional "Guide the AI" text used for this version (§D)
    plain_definition          TEXT NOT NULL DEFAULT '',
    classification_rationale  TEXT NOT NULL DEFAULT '',
    practical_meaning         TEXT NOT NULL DEFAULT '',
    underlying_elements       JSONB NOT NULL DEFAULT '[]',   -- list[str]
    stronger_expressions      JSONB NOT NULL DEFAULT '[]',   -- list[str]
    weaker_expressions        JSONB NOT NULL DEFAULT '[]',   -- list[str]
    boundaries_and_overlaps   TEXT NOT NULL DEFAULT '',
    related_concepts          JSONB NOT NULL DEFAULT '[]',   -- [{concept_id, canonical_name, type_code, relationship, explanation}], §F — never formal concept_edge rows
    caveats                   TEXT,
    raw_output                JSONB,            -- full AI response, audit only — never returned by the normal read API
    superseded_at             TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_concept_dossier_concept ON jobber.concept_dossier(concept_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_concept_dossier_one_active
    ON jobber.concept_dossier(concept_id) WHERE status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS idx_concept_dossier_one_draft
    ON jobber.concept_dossier(concept_id) WHERE status = 'draft';
