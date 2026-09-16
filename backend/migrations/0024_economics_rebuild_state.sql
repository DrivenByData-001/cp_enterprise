-- Derivation freshness is not the same thing as cache invalidation.
--
-- Migration 0023 made every compensation/market/derived-economics mutation
-- bump `target_analysis_revision.economics`, so a Pathways cache entry is
-- correctly invalidated when compensation changes. But a cache miss only
-- recomposes Pathways *from* `d_archetype_comp`, `d_archetype_demand` and
-- `d_gap_value` — it does not rebuild them. Accepting a salary or changing
-- an archetype assignment therefore produced a fresh-looking answer computed
-- from yesterday's derived economics, presented as current. Profile/
-- capability changes could make `d_gap_value` stale the same way, since gap
-- value is a counterfactual over the current structural model.
--
-- This table records which source state the last economics rebuild actually
-- saw. `app/economics_freshness.py` compares it against the live counters at
-- read time; where they differ, the derived figure is suppressed with an
-- explicit "rebuild economics" reason rather than shown as current. Nothing
-- here rebuilds anything automatically — a rebuild walks every role's fit and
-- is far too expensive to run inside a request.
--
-- Singleton, same shape as `jobber.target_analysis_revision` (0019) and
-- `jobber.planning_assumption` (0023). A deployment that has never rebuilt
-- has the seeded row with NULL revisions, which reads as "never rebuilt" —
-- distinct from "rebuilt and now stale", and both distinct from fresh.
CREATE TABLE IF NOT EXISTS jobber.economics_rebuild_state (
    singleton          BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    evidence_revision  BIGINT,
    path_revision      BIGINT,
    economics_revision BIGINT,
    engine_version     TEXT,
    rebuilt_at         TIMESTAMPTZ
);
INSERT INTO jobber.economics_rebuild_state (singleton) VALUES (true) ON CONFLICT (singleton) DO NOTHING;

-- Deliberately NOT given an `invalidate_target_analysis` trigger: writing
-- this row is the *record* of a rebuild, and bumping the economics counter
-- from it would immediately invalidate the rebuild it just recorded.

-- --- Review-gate inputs must invalidate the Pathways cache ------------------
--
-- Migration 0019's `path` triggers cover role_instance, role_skill_observation,
-- requirement_claim, concept_alias and d_embedding. They do not cover
-- `concept_proposal` / `concept_proposal_occurrence` — which, since the
-- requirement-review curation gate, are part of the canonical review summary:
-- a role is "reviewed" only when it has no unreviewed claims, *no unresolved
-- vocabulary proposals*, and nothing needing re-extraction.
--
-- Without these triggers a pending proposal could appear (or be resolved)
-- while a cached Pathways result kept reporting the old review verdict —
-- exactly the stale-gate problem the rest of this migration addresses for
-- derived economics. Same statement-level, `path`-bumping shape as 0019's
-- own triggers, and guarded so re-running the migration is safe.
DO $$
DECLARE target_table text;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['concept_proposal', 'concept_proposal_occurrence'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'jobber' AND c.relname = target_table
              AND t.tgname = 'invalidate_target_analysis'
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER invalidate_target_analysis '
                'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON jobber.%I '
                'FOR EACH STATEMENT EXECUTE FUNCTION jobber.invalidate_target_analysis(''path'')',
                target_table);
        END IF;
    END LOOP;
END $$;
