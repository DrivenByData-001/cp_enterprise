-- Authoritative many-to-many mapping from observed role-skill phrases to canonical concepts.
-- The legacy role_skill_observation.canonical_concept_id remains a compatibility
-- projection during migration, but downstream matching/analytics read this junction.

CREATE TABLE IF NOT EXISTS jobber.role_skill_observation_concept (
    role_skill_observation_id UUID NOT NULL
        REFERENCES jobber.role_skill_observation(id) ON DELETE CASCADE,
    concept_id UUID NOT NULL
        REFERENCES jobber.concept(id) ON DELETE CASCADE,
    mapping_basis TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_skill_observation_id, concept_id)
);

CREATE INDEX IF NOT EXISTS idx_role_skill_observation_concept_concept_id
    ON jobber.role_skill_observation_concept (concept_id);

-- Seed the junction from the historical single-valued mapping. This is
-- idempotent and preserves any richer M:N mappings already present.
INSERT INTO jobber.role_skill_observation_concept (
    role_skill_observation_id,
    concept_id,
    mapping_basis
)
SELECT
    rso.id,
    rso.canonical_concept_id,
    'legacy_single'
FROM jobber.role_skill_observation rso
JOIN jobber.concept c
  ON c.id = rso.canonical_concept_id
 AND c.status = 'active'
WHERE rso.canonical_concept_id IS NOT NULL
ON CONFLICT (role_skill_observation_id, concept_id) DO NOTHING;
