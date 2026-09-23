-- MANUAL, ONE-TIME production cleanup. Not applied by the automatic
-- migration runner (backend/app/db.py only scans backend/migrations/*.sql,
-- not migrations/manual/) and not run by the test suite. Apply by hand,
-- once, against production BEFORE migration 0020 (already merged) is asked
-- to run there:
--
--   psql "$DATABASE_URL" -f backend/migrations/manual/9002_resolve_duplicate_current_requirement_claims.sql
--
-- WHY THIS EXISTS: migration 0020's own preflight (see its header) fails
-- startup outright if any (role_instance_id, concept_id) pair has more than
-- one *current* (superseded_by IS NULL) jobber.requirement_claim row — a
-- state the pre-0020 application code could produce (repeated extraction
-- before this build's conservative rerun-dedup existed). Production has been
-- running a release/backport specifically because normal `main` startup is
-- blocked on this. This script resolves the block.
--
-- WHAT IT DOES: for every duplicate-current (role_instance_id, concept_id)
-- group, keeps exactly one row current and supersedes the rest via
-- superseded_by — never DELETEs a row, never rewrites review_status. The
-- survivor is chosen by: a human-accepted decision always outranks an
-- unreviewed AI proposal, which outranks a rejected one (a role/concept a
-- human explicitly accepted must not lose to a stale unreviewed duplicate);
-- ties broken by the most recently created row. Every non-surviving row's
-- own evidence remains fully intact on that row afterwards — nothing here
-- deletes it — so migration 0026's backfill (which walks the resulting
-- superseded_by chain to the surviving row) picks it all up as
-- jobber.requirement_evidence once that migration runs.
--
-- Idempotent: a group with only one current row (already resolved, or never
-- duplicated) is left untouched; re-running this script after it has already
-- fixed every duplicate is a no-op.
DO $$
DECLARE
    grp RECORD;
    winner_id UUID;
    resolved_groups INT := 0;
    superseded_rows INT := 0;
BEGIN
    FOR grp IN
        SELECT role_instance_id, concept_id
        FROM jobber.requirement_claim
        WHERE superseded_by IS NULL
        GROUP BY role_instance_id, concept_id
        HAVING COUNT(*) > 1
    LOOP
        SELECT id INTO winner_id
        FROM jobber.requirement_claim
        WHERE role_instance_id = grp.role_instance_id
          AND concept_id = grp.concept_id
          AND superseded_by IS NULL
        ORDER BY
            CASE review_status
                WHEN 'accepted' THEN 0
                WHEN 'corrected' THEN 1
                WHEN 'unreviewed' THEN 2
                WHEN 'rejected' THEN 3
                ELSE 4
            END,
            created_at DESC,
            id DESC
        LIMIT 1;

        UPDATE jobber.requirement_claim
        SET superseded_by = winner_id
        WHERE role_instance_id = grp.role_instance_id
          AND concept_id = grp.concept_id
          AND superseded_by IS NULL
          AND id != winner_id;

        GET DIAGNOSTICS superseded_rows = ROW_COUNT;
        resolved_groups := resolved_groups + 1;
        RAISE NOTICE '(role_instance_id=%, concept_id=%): kept % current, superseded % duplicate row(s)',
            grp.role_instance_id, grp.concept_id, winner_id, superseded_rows;
    END LOOP;

    RAISE NOTICE 'resolved % duplicate-current (role_instance_id, concept_id) group(s)', resolved_groups;
END $$;

-- Verify zero duplicate-current groups remain before running migration 0020
-- (this is the exact query migration 0020's own preflight runs):
--   SELECT role_instance_id, concept_id, COUNT(*), array_agg(id ORDER BY created_at)
--   FROM jobber.requirement_claim WHERE superseded_by IS NULL
--   GROUP BY role_instance_id, concept_id HAVING COUNT(*) > 1;
-- The query above must return zero rows.
