-- Phase 2: evidence-backed career preference layer. Structurally separate from
-- capability: no FK, view, or query in this migration or the app code joins
-- preference_observation into a capability/requirement/comparison table. That
-- separation is deliberate and load-bearing (Phase 2 brief §10, definition of
-- done #9-10) — personality/psychometric material can reach this table via
-- `basis`, and nothing built on top of `requirement_claim` or the comparison
-- view can see it.

CREATE TABLE IF NOT EXISTS jobber.preference_dimension (
    code       TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    definition TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);

INSERT INTO jobber.preference_dimension (code, label, definition, sort_order) VALUES
    ('autonomy', 'Autonomy', 'Preference for independent latitude over close direction', 1),
    ('intellectual_complexity', 'Intellectual complexity', 'Preference for conceptually demanding vs. routine work', 2),
    ('technical_engagement', 'Technical engagement', 'Preference for hands-on technical work vs. managing/coordinating it', 3),
    ('people_leadership', 'People leadership', 'Preference for leading/developing others vs. individual contribution', 4),
    ('novelty_learning', 'Novelty / learning', 'Preference for new problems and continual learning vs. mastery of the familiar', 5),
    ('creation_system_improvement', 'Creation / system improvement', 'Preference for building or improving systems vs. operating existing ones', 6),
    ('procedural_structure', 'Procedural structure', 'Preference for defined process vs. open-ended latitude', 7),
    ('ambiguity_tolerance', 'Ambiguity tolerance', 'Comfort operating without a well-defined problem or answer', 8),
    ('bureaucracy_tolerance', 'Bureaucracy tolerance', 'Comfort with organisational process/governance overhead', 9),
    ('compensation', 'Compensation', 'Weight placed on pay relative to other factors', 10),
    ('lifestyle_environment', 'Lifestyle / working environment', 'Preference for a given pace, location, or working pattern', 11)
ON CONFLICT (code) DO NOTHING;

-- basis is ranked exactly per the Phase 2 brief §10 source hierarchy, strongest
-- first. `validated_psychometric` and `typology_hypothesis` are the only two
-- routes personality material can take, and both are the lowest-ranked bases —
-- structurally incapable of being mistaken for observed behaviour or explicit
-- user statement.
CREATE TABLE IF NOT EXISTS jobber.preference_observation (
    id                    BIGSERIAL PRIMARY KEY,
    dimension_code        TEXT NOT NULL REFERENCES jobber.preference_dimension(code),
    direction             TEXT NOT NULL CHECK (direction IN ('toward', 'away', 'neutral')),
    strength              SMALLINT NOT NULL CHECK (strength BETWEEN 1 AND 3),  -- ordinal, never a fabricated float (doc 11 §5.1 precedent)
    basis                 TEXT NOT NULL CHECK (basis IN (
                               'observed_behavior', 'user_stated', 'repeated_episode_evidence',
                               'validated_psychometric', 'typology_hypothesis'
                           )),
    source_label          TEXT,     -- free-text pointer to the concrete source, e.g. 'MBTI: INTP', 'episode 23'
    profile360_claim_id   UUID,     -- optional grounding in a profile360 claim — unconstrained, see docs/14 §5
    episode_id            BIGINT REFERENCES jobber.episode(id),
    confidence            TEXT NOT NULL DEFAULT 'low' CHECK (confidence IN ('low', 'medium', 'high')),
    occurred_at           TEXT,     -- date/recency of the underlying evidence, if known (partial dates allowed)
    recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    note                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_preference_observation_dimension ON jobber.preference_observation(dimension_code);
CREATE INDEX IF NOT EXISTS idx_preference_observation_basis ON jobber.preference_observation(basis);
