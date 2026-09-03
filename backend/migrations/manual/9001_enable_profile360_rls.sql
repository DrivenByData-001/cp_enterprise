-- MANUAL, DELIBERATE migration. Not applied by the automatic migration runner
-- (backend/app/db.py only scans backend/migrations/*.sql, not migrations/manual/)
-- and not run by the test suite. Apply by hand, once, after reading
-- docs/15-security-and-rls.md in full:
--
--   psql "$DATABASE_URL" -f backend/migrations/manual/9001_enable_profile360_rls.sql
--
-- WHY THIS IS SEPARATE FROM EVERYTHING ELSE: the Phase 2 brief (§15) is explicit
-- that RLS must not be enabled on profile360 "without understanding the access
-- path, because enabling it without policies can break legitimate access." This
-- build has no visibility into what currently reads profile360 in production
-- (docs/10-career-nav-scoping.md describes a separate `open-brain-mcp` Edge
-- Function that predates this repository and this Phase 2 work entirely). If
-- that Edge Function — or anything else with legitimate access — authenticates
-- with the Supabase anon/authenticated key rather than the service-role key,
-- enabling RLS with zero permissive policies (as this migration does) WILL
-- break it. That is a real production judgment call this build cannot make
-- blind, so it is left as an explicit, reviewable, human-triggered step rather
-- than something that happens automatically the first time the app starts.
--
-- THE THREAT MODEL THIS CLOSES: today, per the brief, most profile360 tables
-- have RLS disabled — which means, on a Supabase project, that anyone holding
-- only the public anon key can read (and via PostgREST, potentially write) the
-- user's personal career evidence directly, bypassing every application-layer
-- control entirely. That is the "known issue" the brief names in §15.
--
-- THE ASSUMPTION THAT MAKES THIS SAFE: Supabase's `service_role` Postgres role
-- (and any custom role granted BYPASSRLS) ignores RLS entirely, by Postgres
-- design, regardless of what policies exist. If the FastAPI backend
-- (docs/15 §1) and the open-brain-mcp Edge Function both authenticate as
-- service_role or an equivalent BYPASSRLS role — the standard, recommended
-- pattern for a trusted server-side integration — then enabling RLS with *no*
-- policies for `anon`/`authenticated` breaks nothing for either of them and
-- closes the anon-read/write gap completely. VERIFY THIS ASSUMPTION (check
-- Supabase project logs / the Edge Function's own source for which key it
-- uses) before running this against production. If anything legitimate
-- authenticates as `anon`/`authenticated` against profile360 today, add an
-- explicit least-privilege policy for it in the same transaction as enabling
-- RLS, not after.
--
-- Idempotent and additive: enabling RLS on a table that already has it is a
-- no-op; this issues no DROP and adds no permissive policy, so re-running it
-- can only ever narrow access further, never widen it.

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'documents', 'episodes', 'claims', 'evidence', 'capabilities',
        'contradictions', 'open_questions', 'snapshots'
    ]
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'profile360' AND table_name = tbl
        ) THEN
            EXECUTE format('ALTER TABLE profile360.%I ENABLE ROW LEVEL SECURITY', tbl);
            EXECUTE format('ALTER TABLE profile360.%I FORCE ROW LEVEL SECURITY', tbl);
            RAISE NOTICE 'profile360.% : RLS enabled and forced, no policies added (default deny for anon/authenticated).', tbl;
        ELSE
            RAISE NOTICE 'profile360.% : table not found, skipped.', tbl;
        END IF;
    END LOOP;
END $$;

-- Deliberately no CREATE POLICY statements: default-deny is the entire point.
-- A trusted server-side role (service_role / BYPASSRLS) does not need a policy
-- to read or write; anything else now gets zero rows and zero writes, which is
-- the intended behaviour change.
