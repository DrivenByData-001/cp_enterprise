-- Phase 2/3 additive: role_skill_observation.observation_basis CHECK constraint
-- expansion. The existing 327 migrated production rows all have
-- observation_basis='legacy_extraction', but Phase 2+ application writes use
-- 'app_capture' (db.py line 338), and Phase 3 can generate
-- 'phase2_requirement_claim' and 'manual' as well (see docs/14-phase2-postgres-
-- architecture.md §3, docs/16-phase3-capability-engine.md).
--
-- This migration adds the complete set of supported observation_basis values
-- to the CHECK constraint. The production database (as of 2026-09-03) already
-- has this constraint too restrictive (likely allowing only 'legacy_extraction');
-- this migration widens it to match what the application actually writes.
-- Existing rows are never touched; the constraint only affects new/updated rows.
--
-- Idempotent pattern (DO block with information_schema check) matches 0006 —
-- see that file for the design rationale of this idiom.

DO $$
BEGIN
    -- If the constraint already exists, drop it so we can recreate it with the
    -- expanded value set. The IF EXISTS on the ALTER makes this idempotent:
    -- if this migration has already run, the second execution does nothing.
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_schema = 'jobber' AND table_name = 'role_skill_observation'
          AND constraint_name = 'role_skill_observation_observation_basis_check'
    ) THEN
        ALTER TABLE jobber.role_skill_observation
            DROP CONSTRAINT role_skill_observation_observation_basis_check;
    END IF;

    -- Add the expanded CHECK constraint with all currently-supported values.
    -- Order: historical/migrated first, then application-generated in logical grouping.
    ALTER TABLE jobber.role_skill_observation
        ADD CONSTRAINT role_skill_observation_observation_basis_check
        CHECK (observation_basis IN ('legacy_extraction', 'app_capture', 'phase2_requirement_claim', 'manual'));
END $$;
