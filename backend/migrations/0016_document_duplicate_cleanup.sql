-- Source-aware ingest duplicate-detection cleanup (handoff:
-- source_aware_ingest_cleanup). Adds a conservative, whitespace-normalised
-- content fingerprint alongside the existing `content_sha256` so a PDF
-- re-extraction of the same underlying posting (different raw bytes, same
-- text once whitespace is collapsed) can be flagged as a *possible*
-- duplicate even when the raw hash differs. Deliberately NOT unique — same
-- reasoning as `content_sha256` itself (see db.py's create_document
-- docstring / docs/14 §4): this is an informational dedup signal only, and
-- must never block or collapse a genuine second capture.
ALTER TABLE jobber.document ADD COLUMN IF NOT EXISTS content_normalized_sha256 TEXT;
CREATE INDEX IF NOT EXISTS idx_document_content_normalized_sha256 ON jobber.document(content_normalized_sha256);

-- Backfill existing rows so a *new* upload can be compared against
-- historical captures, not just other new ones. Uses the same normalisation
-- rule as app/db.py's normalize_document_text (ASCII whitespace runs -> one
-- space, then trim) expressed in SQL, and the same hash algorithm as
-- content_sha256 (sha256, via the core Postgres 14+ builtin — no pgcrypto
-- dependency) so a row backfilled here and a row hashed by the application
-- always agree.
UPDATE jobber.document
SET content_normalized_sha256 = encode(
    sha256(convert_to(btrim(regexp_replace(content_text, '[ \t\r\n\f\v]+', ' ', 'g')), 'UTF8')),
    'hex'
)
WHERE content_text IS NOT NULL AND content_normalized_sha256 IS NULL;
