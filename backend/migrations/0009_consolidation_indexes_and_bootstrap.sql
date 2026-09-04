-- CP Ent consolidation pass (post-historical-ingestion). Additive only: no
-- column dropped/retyped, no existing row touched. See
-- docs/18-consolidation-and-analytical-foundation.md for the full design.
--
-- Two independent pieces:
--
-- 1. `concept_proposal.cluster_key` — supports deterministic-clustering-aware
--    proposal review (app/vocabulary_bootstrap.py, docs/18 §3): several exact
--    surface forms (e.g. "solvency ii" and "sii") that a curator should
--    review as one decision get the same cluster_key so routes/concepts.py
--    can group them into one review card, without changing what
--    `concept_proposal.surface_form` means anywhere it is already used (the
--    exact-match auto-link in resolve_proposal/db.upsert_role_instance keeps
--    working unchanged). NULL for every proposal predating this migration,
--    and for any proposal the ordinary Pass B path creates going forward
--    without running the bootstrap clustering pass — routes/concepts.py
--    treats NULL as "its own single-member cluster" (COALESCE(cluster_key,
--    surface_form)), so this is purely additive to existing behaviour.
--
-- 2. A handful of indexes the new corpus-trend-analytics queries
--    (app/trends.py) and the new Space/Dashboard temporal filters
--    (routes/space.py, routes/roles.py) filter/sort on repeatedly. The corpus
--    is currently ~307 roles (trivial either way), but these are cheap,
--    standard, and correct to have regardless of corpus size.

ALTER TABLE jobber.concept_proposal
    ADD COLUMN IF NOT EXISTS cluster_key TEXT;

CREATE INDEX IF NOT EXISTS idx_concept_proposal_cluster ON jobber.concept_proposal(cluster_key, status);

CREATE INDEX IF NOT EXISTS idx_role_instance_posting_date ON jobber.role_instance(posting_date);
CREATE INDEX IF NOT EXISTS idx_role_instance_country ON jobber.role_instance(country);
CREATE INDEX IF NOT EXISTS idx_role_instance_seniority ON jobber.role_instance(seniority_level);

-- Candidate-capability bootstrap proposals (docs/18 §3) reuse
-- jobber.concept.status='proposed' (already the column default, already
-- unconstrained by any CHECK) and jobber.concept_edge.status='proposed'
-- (already a legal value per 0006's concept_edge_status_check) — no schema
-- change needed for either. This index serves the curation UI's "list
-- proposed component edges for capability X" query
-- (app/capability_engine.py::load_proposed_components), the proposed-edge
-- counterpart of the existing accepted-only engine read path.
CREATE INDEX IF NOT EXISTS idx_concept_edge_to_status ON jobber.concept_edge(to_concept_id, status);
