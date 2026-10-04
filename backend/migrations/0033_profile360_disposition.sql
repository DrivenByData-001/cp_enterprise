-- First-class disposition for Profile360 items that are intentionally not canonical mappings.
-- Kept in jobber so profile360 remains authoritative and read-only.

CREATE TABLE IF NOT EXISTS jobber.profile360_disposition (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('claim', 'capability')),
    profile360_id uuid NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('mappable', 'boundary', 'needs_review')),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, profile360_id)
);

CREATE INDEX IF NOT EXISTS profile360_disposition_kind_state_idx
    ON jobber.profile360_disposition (kind, disposition);
