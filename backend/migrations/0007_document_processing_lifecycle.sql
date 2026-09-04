-- CP Ent Phase 3B: raw jobber.document -> job_posting_extract -> jobber.role_instance.
-- See docs/17-document-processing-pipeline.md for the full design.
--
-- Additive only: no existing column is dropped or retyped, no existing row is
-- touched. jobber.extraction_run already exists (0003) and is only extended
-- here (two new nullable columns, a widened status CHECK, and
-- vocabulary_version_id relaxed to nullable for the one task that has no
-- controlled-vocabulary dependency).

-- §3 — a job_posting_extract attempt now goes through a real running -> ok/
-- partial/failed lifecycle (§6/§12): the row is inserted with status='running'
-- and committed *before* the AI provider is ever called, then updated in
-- place once the attempt resolves. result_role_instance_id is the run's own
-- *output* linkage, deliberately separate from the existing role_instance_id
-- subject column (which means "this run's *subject* was this role_instance" —
-- true for role-side tasks like requirement_extract, never true for
-- job_posting_extract, whose subject is always a document). output_payload is
-- the exact validated JobPostingImport JSON — the durable equivalent of the
-- old analysed-JSON-file step, so a role can always be traced back to exactly
-- what the model returned, not just what got written into role_instance.
ALTER TABLE jobber.extraction_run
    ADD COLUMN IF NOT EXISTS result_role_instance_id UUID REFERENCES jobber.role_instance(id),
    ADD COLUMN IF NOT EXISTS output_payload JSONB;

CREATE INDEX IF NOT EXISTS idx_extraction_run_result_role ON jobber.extraction_run(result_role_instance_id);

-- Document-scoped lookups (duplicate-processing check §14, state derivation
-- §5, batch eligibility §17) all filter on (document_id, task) and want the
-- most recent attempt — this index serves all three directly.
CREATE INDEX IF NOT EXISTS idx_extraction_run_document_task ON jobber.extraction_run(document_id, task, started_at DESC);

-- 'running' means the AI task has been started but has not completed (§3) —
-- a real interruptible in-flight state, not merely "ok vs failed" after the
-- fact. Existing rows are all 'ok'/'partial'/'failed' already, so widening
-- this CHECK cannot violate anything present.
ALTER TABLE jobber.extraction_run DROP CONSTRAINT IF EXISTS extraction_run_status_check;
ALTER TABLE jobber.extraction_run
    ADD CONSTRAINT extraction_run_status_check CHECK (status IN ('running', 'ok', 'partial', 'failed'));

-- §4 — vocabulary_version_id was NOT NULL for every task, which was correct
-- for the four closed-vocabulary tasks (requirement_extract,
-- concept_link_adjudicate, profile360_claim_map, profile360_capability_map)
-- but wrong for job_posting_extract: it extracts a JobPostingImport, not a
-- controlled-concept mapping, and has no vocabulary dependency to report.
-- Relaxing the column to nullable and adding a task-scoped CHECK (rather than
-- just dropping NOT NULL outright) keeps the "existing vocabulary-dependent
-- tasks must continue supplying a real vocabulary version" guarantee at the
-- database layer, not merely by convention in extraction.py.
ALTER TABLE jobber.extraction_run ALTER COLUMN vocabulary_version_id DROP NOT NULL;
ALTER TABLE jobber.extraction_run DROP CONSTRAINT IF EXISTS extraction_run_vocabulary_required_check;
ALTER TABLE jobber.extraction_run
    ADD CONSTRAINT extraction_run_vocabulary_required_check
    CHECK (vocabulary_version_id IS NOT NULL OR task = 'job_posting_extract');
