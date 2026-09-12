-- Source-aware ingest cleanup, problem #5 (reviewable metadata enrichment):
-- registers 'role_metadata_enrich' as a new extraction_run task with no
-- controlled-vocabulary dependency, same reasoning 0007 already applied to
-- job_posting_extract (see that migration's own comment) — it proposes
-- plain metadata fields (title/organisation/location/...), never a
-- concept-linked claim, so it has nothing to report a vocabulary_version_id
-- for.
--
-- IMPORTANT: this constraint has been redefined by each of 0007, 0011, and
-- 0013 in turn, each time by dropping and re-adding it with one more task
-- appended to the allow-list — never by widening a stored pattern. The
-- allow-list below must therefore carry every task already exempted by
-- those earlier migrations (job_posting_extract, role_context_generate,
-- compensation_extract) plus the new one, or this would silently narrow the
-- constraint and break every one of those existing tasks.
ALTER TABLE jobber.extraction_run DROP CONSTRAINT IF EXISTS extraction_run_vocabulary_required_check;
ALTER TABLE jobber.extraction_run
    ADD CONSTRAINT extraction_run_vocabulary_required_check
    CHECK (vocabulary_version_id IS NOT NULL OR task IN (
        'job_posting_extract', 'role_context_generate', 'compensation_extract', 'role_metadata_enrich'
    ));
