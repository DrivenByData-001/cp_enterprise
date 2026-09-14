-- Rebuildable caches for this application's single personal profile.
CREATE TABLE jobber.target_analysis_revision (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    evidence bigint NOT NULL DEFAULT 0,
    path bigint NOT NULL DEFAULT 0
);
INSERT INTO jobber.target_analysis_revision (singleton) VALUES (true);

CREATE TABLE jobber.d_target_evidence (
    concept_id uuid PRIMARY KEY REFERENCES jobber.concept(id) ON DELETE CASCADE,
    revision text NOT NULL,
    status text NOT NULL
);
CREATE TABLE jobber.d_target_path (
    role_id uuid PRIMARY KEY REFERENCES jobber.role_instance(id) ON DELETE CASCADE,
    revision text NOT NULL,
    result jsonb NOT NULL,
    prepared_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION jobber.invalidate_target_analysis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE jobber.target_analysis_revision
    SET path = path + 1, evidence = evidence + CASE WHEN TG_ARGV[0] = 'evidence' THEN 1 ELSE 0 END;
    RETURN NULL;
END $$;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['concept', 'capability_detail', 'concept_edge',
        'concept_edge_rule', 'profile360_claim_mapping', 'profile360_capability_mapping',
        'person_capability_assertion'] LOOP
        EXECUTE format('CREATE TRIGGER invalidate_target_analysis AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON jobber.%I FOR EACH STATEMENT EXECUTE FUNCTION jobber.invalidate_target_analysis(''evidence'')', table_name);
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['role_instance', 'role_skill_observation',
        'requirement_claim', 'concept_alias', 'd_embedding'] LOOP
        EXECUTE format('CREATE TRIGGER invalidate_target_analysis AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON jobber.%I FOR EACH STATEMENT EXECUTE FUNCTION jobber.invalidate_target_analysis(''path'')', table_name);
    END LOOP;
END $$;
