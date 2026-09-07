-- Vocabulary "Split cluster" (next-build brief §4). Additive only.
--
-- Problem: `cluster_key` (0009) is fully regenerated on every real bootstrap
-- run (`vocabulary_bootstrap.compute_cluster_keys` recomputes and overwrites
-- it for every pending proposal, unconditionally). That is exactly right for
-- the normal case — lexical clustering should keep re-grouping newly
-- captured surface forms — but it means a curator's decision to split an
-- over-broad cluster (e.g. "stakeholder engagement" wrongly grouped with
-- "stakeholder management" by the `_SYNONYM_GROUPS` seed list) would simply
-- be re-collapsed back together the next time the bootstrap runs. Human
-- curation must outrank automated lexical clustering (brief §4.3).
--
-- `cluster_key_locked`: set TRUE only by a curator split
-- (app/vocabulary_curation.py::split_cluster). Both
-- `compute_cluster_keys` (real bootstrap) and `analyze_cluster_keys_dryrun`
-- (--dry-run) now skip/override recomputation for a locked proposal, so a
-- split survives every future bootstrap run, dry-run or real, indefinitely —
-- there is no automatic path that ever clears this flag.
--
-- `cluster_split_from`/`cluster_split_at`: audit trail only (brief §4.3
-- "maintain auditability") — which cluster this proposal was split out of,
-- and when. Never read by any query; purely for a curator inspecting history.
ALTER TABLE jobber.concept_proposal
    ADD COLUMN IF NOT EXISTS cluster_key_locked BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS cluster_split_from TEXT,
    ADD COLUMN IF NOT EXISTS cluster_split_at TIMESTAMPTZ;
