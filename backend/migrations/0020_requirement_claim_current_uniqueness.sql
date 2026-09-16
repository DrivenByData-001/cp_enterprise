-- Requirement-review curation gate hardening (code-review follow-up): the
-- application layer (routes/role_instances.py's add/edit endpoints,
-- extraction.py's rerun-supersession) checks for an existing current claim
-- on the same (role, concept) before writing, but that check alone is not
-- race-safe and nothing previously stopped two current claims for the same
-- concept from coexisting on one role. Two current accepted rows for the
-- same concept would double-count in capability_engine.derive_role_fit
-- (which iterates every row the canonical loader returns) and violate the
-- rerun-dedup code's assumption that at most one current claim exists per
-- (role, concept). A partial unique index (only over current rows — history
-- rows with superseded_by set are exempt, since a corrected chain
-- legitimately reuses the same concept across time) makes that invariant a
-- database guarantee, not just an application-level convention.
--
-- Preflight (code-review follow-up #2): the *old* application code allowed
-- repeated extraction to create exactly this kind of duplicate — two
-- current unreviewed claims for the same (role, concept) — before this
-- build's conservative rerun-dedup handling existed. If any such duplicate
-- already exists wherever this migration runs, CREATE UNIQUE INDEX below
-- fails outright, and an ordinary index-creation error would be the first
-- anyone learns of it — mid-deploy, with no guidance on what to do next.
-- Check explicitly first and fail with a diagnostic that says what the
-- problem is and how to find it, rather than mutating anything: resolving
-- real duplicates (superseding all but one per pair, preserving history via
-- review_status='corrected'/superseded_by — never silently deleting a row)
-- needs a human to look at the actual data, which this migration cannot do
-- safely on its own.
DO $$
DECLARE
    dup_count int;
BEGIN
    SELECT COUNT(*) INTO dup_count FROM (
        SELECT role_instance_id, concept_id
        FROM jobber.requirement_claim
        WHERE superseded_by IS NULL
        GROUP BY role_instance_id, concept_id
        HAVING COUNT(*) > 1
    ) dupes;
    IF dup_count > 0 THEN
        RAISE EXCEPTION 'migration 0020 preflight failed: % (role_instance_id, concept_id) pair(s) '
            'have more than one current (superseded_by IS NULL) jobber.requirement_claim row. '
            'This must be resolved by a human before the unique index below can be created: for '
            'each pair, decide which row should remain current and supersede the rest (superseded_by '
            'pointing at the row that stays, review_status left as-is or set to ''corrected'' if a human '
            'is making that call now) — never silently delete a row. Find them with: '
            'SELECT role_instance_id, concept_id, COUNT(*), array_agg(id ORDER BY created_at) '
            'FROM jobber.requirement_claim WHERE superseded_by IS NULL '
            'GROUP BY role_instance_id, concept_id HAVING COUNT(*) > 1;',
            dup_count;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_requirement_claim_one_current_per_concept
    ON jobber.requirement_claim (role_instance_id, concept_id)
    WHERE superseded_by IS NULL;

-- Non-destructive supersession (routes/role_instances.py::_supersede_with_new_claim,
-- extraction.py's rerun path) must free the old row's (role, concept) slot
-- — clearing superseded_by there — *before* the new row reclaims it, or the
-- partial index above rejects the new row while both are briefly current.
-- But superseded_by is a self-referencing foreign key, so the old row's
-- update needs the new row's id to already exist — the exact opposite
-- order. Deferring the FK check to end-of-transaction resolves this: the
-- old row's superseded_by can point at a not-yet-existing new row for the
-- moment in between, as long as that row exists by commit. (The partial
-- unique index itself cannot be deferred — Postgres does not support
-- deferrable partial indexes — so it is the FK, not the uniqueness check,
-- that has to give.)
ALTER TABLE jobber.requirement_claim
    DROP CONSTRAINT requirement_claim_superseded_by_fkey,
    ADD CONSTRAINT requirement_claim_superseded_by_fkey
        FOREIGN KEY (superseded_by) REFERENCES jobber.requirement_claim(id)
        DEFERRABLE INITIALLY DEFERRED;
