-- Server-only proposal/review storage. Canonical career evidence stays in Profile360.
CREATE TABLE jobber.evidence_discovery_run (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 application_id uuid NOT NULL REFERENCES jobber.application(id) ON DELETE CASCADE,
 status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','complete','failed')),
 total_sources integer NOT NULL DEFAULT 0,
 total_batches integer NOT NULL DEFAULT 0,
 completed_batches integer NOT NULL DEFAULT 0,
 model text, metadata jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX evidence_one_running ON jobber.evidence_discovery_run(application_id) WHERE status='running';
CREATE TABLE jobber.evidence_finding (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id uuid NOT NULL REFERENCES jobber.evidence_discovery_run(id) ON DELETE CASCADE,
 application_id uuid NOT NULL REFERENCES jobber.application(id) ON DELETE CASCADE,
 claim_id uuid NOT NULL,
 concept_id uuid NOT NULL REFERENCES jobber.concept(id),
 source_snapshot jsonb NOT NULL, source_revision text NOT NULL, accepted_source_revision text,
 requirement_snapshot jsonb NOT NULL, requirement_revision text NOT NULL,
 proposal jsonb NOT NULL, reviewed_payload jsonb,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
 revision integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(application_id,claim_id,concept_id,source_revision,requirement_revision)
);
CREATE TABLE jobber.evidence_finding_review (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 finding_id uuid REFERENCES jobber.evidence_finding(id) ON DELETE SET NULL,
 action text NOT NULL CHECK(action IN ('approve','reject','edit')),
 before_state jsonb NOT NULL, reviewed_payload jsonb NOT NULL, mapping_result jsonb,
 reviewed_by text NOT NULL DEFAULT 'authenticated_operator', reviewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE jobber.evidence_assessment (
 mapping_id uuid PRIMARY KEY REFERENCES jobber.profile360_claim_mapping(id) ON DELETE CASCADE,
 source_revision text NOT NULL,
 depth text CHECK(depth IN ('exposed','applied','owned','set_standard')),
 autonomy text CHECK(autonomy IN ('assisted','independent','directed_others','accountable')),
 finding_id uuid REFERENCES jobber.evidence_finding(id) ON DELETE SET NULL,
 reviewed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE jobber.evidence_discovery_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobber.evidence_finding ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobber.evidence_finding_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobber.evidence_assessment ENABLE ROW LEVEL SECURITY;
CREATE INDEX evidence_finding_application ON jobber.evidence_finding(application_id,status);
