-- Phase 2 reconciliation (2026-09-03): the live `open-brain` Supabase project
-- was inspected directly, and its `jobber` schema differs from an earlier
-- version of this migration set in ways that matter at the type level (core
-- entity ids are UUID, not BIGSERIAL) — see
-- docs/14-phase2-postgres-architecture.md §3 for the full comparison.
--
-- This migration therefore does NOT create document / role_instance /
-- role_skill_observation / concept_type / concept / concept_alias /
-- concept_edge_rule — they already exist in production with real migrated
-- data (23 documents, 23 role instances, 327 skill observations) and must
-- never be reshaped to match this codebase's expectations. Instead it
-- ASSERTS the load-bearing facts every later migration and every runtime
-- query depends on, and fails loudly and immediately if they don't hold,
-- rather than let a later CREATE TABLE ... REFERENCES fail confusingly with
-- a type-mismatch error three tables deep.
--
-- Local dev/test against a from-scratch Postgres (no existing production
-- data) needs an equivalent baseline first —
-- see backend/scripts/local_baseline.sql, applied by
-- backend/tests/conftest.py before migrations run.

CREATE SCHEMA IF NOT EXISTS jobber;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS jobber.migration_history (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
DECLARE
    missing TEXT := '';
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'document' AND column_name = 'id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  jobber.document.id must be uuid';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'role_instance' AND column_name = 'id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  jobber.role_instance.id must be uuid';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'role_instance' AND column_name = 'document_id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  jobber.role_instance.document_id must be uuid';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'concept' AND column_name = 'id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  jobber.concept.id must be uuid';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'concept_alias' AND column_name = 'id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  jobber.concept_alias.id must be uuid';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'role_skill_observation' AND column_name = 'role_instance_id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  jobber.role_skill_observation.role_instance_id must be uuid';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'jobber' AND table_name = 'role_skill_observation' AND column_name = 'canonical_concept_id' AND data_type = 'uuid'
    ) THEN
        missing := missing || E'\n  jobber.role_skill_observation.canonical_concept_id must be uuid';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION E'Phase 2 preflight failed — the live jobber schema does not match what these migrations require:%\nSee docs/14-phase2-postgres-architecture.md §3, and backend/scripts/local_baseline.sql if you are bootstrapping a fresh local database rather than pointing at the real Supabase project.', missing;
    END IF;
END $$;

-- profile360 checked the same way, but only when the schema is present at
-- all — a jobber-only local setup with no profile360 (e.g. a from-scratch
-- Postgres that only ran local_baseline.sql) can still run everything except
-- the profile360-mapping features, and should not be blocked from doing so.
DO $$
DECLARE
    missing TEXT := '';
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'profile360') THEN
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'profile360' AND table_name = 'claims')
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'profile360' AND table_name = 'claims' AND column_name = 'id' AND data_type = 'uuid'
           ) THEN
            missing := missing || E'\n  profile360.claims.id must be uuid';
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'profile360' AND table_name = 'capabilities')
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'profile360' AND table_name = 'capabilities' AND column_name = 'id' AND data_type = 'uuid'
           ) THEN
            missing := missing || E'\n  profile360.capabilities.id must be uuid';
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'profile360' AND table_name = 'episodes')
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'profile360' AND table_name = 'episodes' AND column_name = 'id' AND data_type = 'uuid'
           ) THEN
            missing := missing || E'\n  profile360.episodes.id must be uuid';
        END IF;

        IF missing <> '' THEN
            RAISE EXCEPTION E'Phase 2 preflight failed — the live profile360 schema does not match what these migrations require:%\nSee docs/14-phase2-postgres-architecture.md §5.', missing;
        END IF;
    END IF;
END $$;
