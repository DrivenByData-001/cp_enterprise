-- Preserve source posting dates; the read model falls back to original role created_at.
ALTER TABLE jobber.role_instance ADD COLUMN IF NOT EXISTS application_closing_date date;
-- Null means use suggested recurring skills; a saved profile contains 3–8 concept UUIDs.
ALTER TABLE jobber.role_archetype_detail ADD COLUMN IF NOT EXISTS target_skill_ids jsonb;
ALTER TABLE jobber.role_archetype_detail ADD CONSTRAINT archetype_target_skills_array
    CHECK (target_skill_ids IS NULL OR (jsonb_typeof(target_skill_ids) = 'array'
        AND jsonb_array_length(target_skill_ids) BETWEEN 3 AND 8));
