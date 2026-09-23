-- One current requirement_claim per (role_instance_id, concept_id), many
-- supporting requirement_evidence occurrences (consolidated requirement
-- evidence + graph fix build).
--
-- Problem this fixes: jobber.requirement_claim (migration 0003) stores both
-- the role-level requirement AND exactly one source evidence_span on the
-- same row. A role can state the same canonical requirement several times in
-- different wording ("effective communicator" / "communicate with
-- colleagues", both -> Communication) — those are not separate analytical
-- requirements, they are multiple evidence occurrences for one requirement.
-- Migration 0020 already enforces "one current claim per (role, concept)" at
-- the database level and is correct to do so; this migration adds the
-- missing other half — a place for the *other* occurrences' evidence to live
-- once a rerun (or the original extraction) finds more than one.
--
-- requirement_claim's own evidence_span/evidence_offset_*/document_id/basis
-- columns are deliberately NOT removed here — kept temporarily for
-- compatibility with any code path not yet updated to read from
-- requirement_evidence (see extraction.py/role_instances.py/role_requirements.py
-- changes in this same build, which do read the new table, but the columns
-- themselves stay so a partially-rolled-out deploy never sees a missing
-- column).
CREATE TABLE IF NOT EXISTS jobber.requirement_evidence (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requirement_claim_id  UUID NOT NULL REFERENCES jobber.requirement_claim(id) ON DELETE CASCADE,
    document_id           UUID REFERENCES jobber.document(id),
    evidence_span         TEXT,
    evidence_offset_start INTEGER,
    evidence_offset_end   INTEGER,
    basis                 TEXT CHECK (basis IS NULL OR basis IN ('stated', 'implied', 'inferred', 'user_asserted')),
    extraction_run_id     UUID REFERENCES jobber.extraction_run(id),
    surface_form          TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_requirement_evidence_claim ON jobber.requirement_evidence(requirement_claim_id);
CREATE INDEX IF NOT EXISTS idx_requirement_evidence_document ON jobber.requirement_evidence(document_id);
CREATE INDEX IF NOT EXISTS idx_requirement_evidence_extraction_run ON jobber.requirement_evidence(extraction_run_id);

-- Dedup so a rerun over the same source never attaches the same occurrence
-- repeatedly. Two shapes of "same occurrence": a spanned one (a genuine
-- verbatim quote — document_id + evidence_span identifies it) and a
-- spanless one (inferred/user_asserted evidence has no quote to key on, so
-- the run that produced it is the next best identity). Both are partial
-- unique indexes rather than one plain unique constraint because NULLs never
-- compare equal to each other in a plain unique index, which would let
-- spanless rows (evidence_span IS NULL for every inferred/user_asserted
-- occurrence) accumulate duplicates freely.
CREATE UNIQUE INDEX IF NOT EXISTS idx_requirement_evidence_dedup_spanned
    ON jobber.requirement_evidence (requirement_claim_id, document_id, evidence_span)
    WHERE evidence_span IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_requirement_evidence_dedup_spanless
    ON jobber.requirement_evidence (requirement_claim_id, extraction_run_id)
    WHERE evidence_span IS NULL AND extraction_run_id IS NOT NULL;

-- Backfill every existing requirement_claim's own evidence into the new
-- table, including superseded history — following the supersession chain
-- to whichever row is the *current* survivor (superseded_by IS NULL) so a
-- role's now-consolidated requirement keeps every passage that was ever
-- captured for it, not just the evidence the surviving row itself carries.
-- Idempotent: re-running this migration (IF NOT EXISTS guards the table/
-- indexes above; ON CONFLICT DO NOTHING below covers the data) is always
-- safe.
--
-- A claim whose chain terminates on a row that no longer exists (should
-- never happen — superseded_by is FK-enforced — but the recursive walk
-- below simply stops at the last existing row in that case, same as any
-- other terminal current row) is not a concern this backfill needs to
-- special-case.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM jobber.requirement_claim) THEN
        WITH RECURSIVE chain AS (
            SELECT id AS origin_id, id AS current_id, superseded_by
            FROM jobber.requirement_claim
            UNION ALL
            SELECT c.origin_id, rc.id, rc.superseded_by
            FROM chain c
            JOIN jobber.requirement_claim rc ON rc.id = c.superseded_by
        ),
        survivors AS (
            SELECT origin_id, current_id
            FROM chain
            WHERE superseded_by IS NULL
        )
        INSERT INTO jobber.requirement_evidence
            (requirement_claim_id, document_id, evidence_span, evidence_offset_start,
             evidence_offset_end, basis, extraction_run_id, surface_form, created_at)
        SELECT
            s.current_id, rc.document_id, rc.evidence_span, rc.evidence_offset_start,
            rc.evidence_offset_end, rc.basis, rc.extraction_run_id, NULL, rc.created_at
        FROM jobber.requirement_claim rc
        JOIN survivors s ON s.origin_id = rc.id
        ON CONFLICT DO NOTHING;
    END IF;
END $$;
