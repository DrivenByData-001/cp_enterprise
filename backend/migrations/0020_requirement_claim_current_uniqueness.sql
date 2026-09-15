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
