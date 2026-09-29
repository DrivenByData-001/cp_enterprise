-- Phase 9: Career Cockpit + Learning Loop (docs/40).
--
-- jobber.career_progress_checkpoint is an append-only historical record of
-- *derived* Target evidence state, captured only on explicit user action
-- (POST /api/cockpit/progress/checkpoints — see app/career_progress.py).
-- It is never written on a GET, and it is never itself evidence: recording
-- one changes no capability status, no Profile360 row, no requirement
-- review, no Career Direction/Target, and no Pathways state. It exists
-- solely so "what changed since I last looked" can be answered by diffing
-- one derived snapshot against a later one.
--
-- No update/delete endpoint is added anywhere in this build — history stays
-- append-only by omission, the same discipline jobber.career_direction_
-- discovery_run (migration 0030) already follows for its own audit rows.
CREATE TABLE IF NOT EXISTS jobber.career_progress_checkpoint (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    career_direction_id      UUID NOT NULL REFERENCES jobber.career_direction(id) ON DELETE CASCADE,
    -- ON DELETE SET NULL (not CASCADE, unlike career_direction_id above) so
    -- deleting an old Target narrows a checkpoint back to "no longer
    -- comparable to anything current" (see career_progress.py's comparable-
    -- checkpoint rule) rather than destroying the historical record of it —
    -- same reasoning as career_direction.target_role_instance_id (0030).
    target_role_instance_id  UUID REFERENCES jobber.role_instance(id) ON DELETE SET NULL,
    checkpoint_type          TEXT NOT NULL CHECK (checkpoint_type IN ('baseline', 'checkpoint')),
    label                    TEXT,
    -- Bumped only if the shape of `state` changes incompatibly — a
    -- checkpoint is comparable to a later one only when both this and the
    -- (career_direction_id, target_role_instance_id) pair match (build §6).
    state_schema_version     SMALLINT NOT NULL DEFAULT 1,
    -- The bounded derived snapshot itself (build §3) — requirement statuses,
    -- counts, review/mapping completeness, development-action counts. Never
    -- copied Profile360 claim text, CV/application content, full evidence
    -- traces, or market source text.
    state                    JSONB NOT NULL,
    -- Revision/fingerprint metadata explaining what current state this
    -- snapshot came from (build §3) — see career_progress.py, which reuses
    -- the existing jobber.target_analysis_revision evidence fingerprint
    -- rather than inventing a new one.
    source_revision          JSONB NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_career_progress_checkpoint_direction
    ON jobber.career_progress_checkpoint(career_direction_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_career_progress_checkpoint_direction_target
    ON jobber.career_progress_checkpoint(career_direction_id, target_role_instance_id, created_at DESC);
