// Every entity id in this app is a UUID (string) — the live jobber/profile360
// schema confirmed by inspection uses UUID primary keys throughout (see
// docs/14-phase2-postgres-architecture.md §3/§5). Only genuinely numeric
// values (coordinates, scores, counts, strengths) stay `number`.

export type RoleSkill = {
  name: string
  category: string | null
  importance: number | null
  requirement_type: string | null
  resolved_concept_id: string | null
}

export type NodeType = 'posting' | 'target_real' | 'target_imagined'

export type SkillDecompositionItem = {
  skill: string
  examples: string[]
}

export type TechnicalSubjectItem = {
  subject: string
  why: string | null
  resources: string[]
}

export type SteppingStone = {
  id: string
  title: string
  organisation: string | null
  career_track: string | null
  similarity_to_target: number | null
  similarity_to_profile: number | null
  posting_date: string | null
  assessment: string
  explanation: string
  evidenced_requirements: number
  requirements_total: number
  missing_required: string[]
  unverified_required: string[]
  target_gaps_addressed: string[]
  legacy_requirements: number
}

export type TargetPath = {
  target_mapping?: { total: number; mapped: number; unresolved: number; complete: boolean; items: TargetRequirementMapping[] }
  profile_to_target_similarity: number | null
  stepping_stones: SteppingStone[]
  candidates_assessed: number
  method: string
}

export type TargetDraft = {
  metadata: { source?: string | null; notes_for_user?: string | null }
  target: {
    title: string; organisation?: string | null; is_imagined: boolean
    description?: string | null; summary?: string | null; career_track?: string | null
    seniority_level?: string | null; typical_tasks: string[]
    skill_decomposition: SkillDecompositionItem[]; technical_subjects: TechnicalSubjectItem[]
    grounding_note?: string | null; feasibility_note?: string | null; is_plausible?: boolean | null
  }
  skills: { name: string; category?: string | null; requirement_type?: string | null; importance?: number | null; concept_id?: string | null; mapping_reviewed?: boolean }[]
}

export type TargetRequirementMapping = { name: string; concept_id: string | null; canonical_name: string | null; mapping_status: 'mapped' | 'unmapped' | 'excluded' }

export type DevelopmentAction = {
  id: string; concept_id: string; role_instance_id: string; title: string; note: string
  due_date: string | null; status: 'open' | 'done'
  // Human planning fields (build §9). Recorded by the user, never derived,
  // and never evidence of a capability — completing an action changes no
  // capability status anywhere.
  planned_start_date?: string | null
  estimated_effort_hours?: number | null
}

// The authoritative ok/partial signal (docs/18 §5) for a role produced by
// the document-processing pipeline — the *run's* own verdict, which can
// diverge from the role's self-reported `extraction_status` column (a
// deterministic guardrail can force a run to 'partial' even when the model
// itself claimed 'ok'; see backend/app/document_processing.py::
// role_extraction_quality). null for a role never processed through that
// pipeline (legacy/bulk import, hand-entered) — callers fall back to
// `Role.extraction_status` in that case, the only signal such a role has.
export type ExtractionQuality = { status: 'ok' | 'partial' | 'failed'; finished_at: string | null; notes: string | null }

export type Role = {
  id: string
  node_type: NodeType
  title: string
  organisation: string | null
  location: string | null
  country: string | null
  remote_type: string | null
  employment_type: string | null
  posting_date: string | null
  captured_at: string | null
  career_track: string | null
  seniority_level: string | null
  salary_min: number | null
  salary_max: number | null
  currency: string | null
  summary: string | null
  description: string | null
  requirements: string | null
  responsibilities: string | null
  key_skills_summary: string | null
  top_adjacent_roles: string[] | null
  extraction_status: string | null
  extraction_notes: string | null
  extraction_quality?: ExtractionQuality | null
  similarity: number | null
  // Reviewed requirements only (claim-sourced — role_requirements.
  // load_role_requirements's claim items) — never a stale/unreviewed legacy
  // signal. `legacy_skills` below is every role_skill_observation not
  // already covered by one of these and not curator-rejected; shown
  // separately so a human never mistakes unreviewed legacy extraction for a
  // reviewed decision.
  skills?: RoleSkill[]
  legacy_skills?: RoleSkill[]
  // 2026 Role Detail regression fallback (docs/21): populated only when
  // description/requirements/responsibilities are all empty AND a linked
  // document has real captured text — the source-aware ingest pipeline
  // (role-instances/ingest + extract-requirements) never fills those flat
  // columns, so without this the advert text would be invisible even though
  // it was captured. Null whenever the flat fields already carry it.
  source_document_text?: string | null
  url: string | null
  raw_json?: unknown
  typical_tasks?: string[] | null
  skill_decomposition?: SkillDecompositionItem[] | null
  technical_subjects?: TechnicalSubjectItem[] | null
  grounding_note?: string | null
  feasibility_note?: string | null
  is_plausible?: boolean | null
  path?: TargetPath
  // Requirement-review curation gate (role_requirements.py): current
  // requirement_claim counts by status for this role, so Role Detail can
  // show a "Requirements review pending" indicator without a second
  // round-trip. Always present for a role that went through build_role_view.
  requirement_review?: RequirementReviewSummary
  // Reviewed archetype assignment (build §5/§14). Carried on the role view
  // itself so Role Detail never needs a second round-trip just to know
  // whether the role is classified. Compensation is deliberately NOT here:
  // it has its own endpoint, so a list view reusing this projection never
  // pays for benchmark resolution it will not show.
  archetype?: RoleArchetypeSummary
}

// --- Day-in-the-Life / Role Context enrichment ------------------------------
//
// Role-side market/context enrichment (docs/21) — never based on the user's
// own profile360 evidence. `basis` distinguishes claims directly supported
// by the source posting ('advert_grounded') from reasonable occupational
// inference ('inferred') at the granularity of each individual item.

export type RoleContextBasis = 'advert_grounded' | 'inferred'
export type RoleContextConfidence = 'high' | 'medium' | 'low'

export type DayInLifeItem = {
  time_or_phase: string
  activity: string
  detail: string | null
  basis: RoleContextBasis
  confidence: RoleContextConfidence
}

export type TypicalWeekItem = {
  day_or_theme: string
  activity: string
  detail: string | null
  basis: RoleContextBasis
  confidence: RoleContextConfidence
}

export type TeamSizeEstimate = { min: number | null; max: number | null; basis: RoleContextBasis; confidence: RoleContextConfidence }
export type GroundedNote = { text: string; basis: RoleContextBasis; confidence: RoleContextConfidence }
export type TeamContext = { expected_team_size: TeamSizeEstimate | null; team_work: GroundedNote[] }

export type ManagerContext = {
  likely_manager_title: string | null
  title_basis: RoleContextBasis | null
  dynamic: string | null
  dynamic_basis: RoleContextBasis | null
  dynamic_confidence: RoleContextConfidence
}

export type CareerStep = { step: string; basis: RoleContextBasis; confidence: RoleContextConfidence }
export type GroundingSummary = { advert_grounded_points: string[]; inferred_points: string[] }

export type RoleContextEnrichment = {
  id: string
  role_instance_id: string
  status: 'active' | 'superseded'
  generated_at: string
  generator_version: string
  model: string
  source_document_id: string | null
  source_content_sha256: string | null
  day_in_life: DayInLifeItem[]
  typical_week: TypicalWeekItem[]
  team_context: TeamContext
  manager_context: ManagerContext
  stakeholder_context: { stakeholders: GroundedNote[] }
  career_progression: CareerStep[]
  grounding_summary: GroundingSummary
  caveats: string | null
  created_at: string
  updated_at: string
}

export type RoleContextResponse = { role_instance_id: string; enrichment: RoleContextEnrichment | null }
export type RoleContextGenerateResult = { created: boolean; enrichment: RoleContextEnrichment }

export type YearRange = { min: number; max: number } | null

export type RoleListResponse = {
  items: Role[]
  total: number
  limit: number
  offset: number
  period: 'recent' | 'all' | 'year' | 'range' | 'unknown_date'
  year_range: YearRange
}

// profile360 rows have no fixed shape known to this app (docs/14 §5/§9) — the
// backend returns whatever columns exist plus a best-effort `_display`
// string. Used for both the current-profile snapshot and episode browsing.
export type Profile360Row = { [key: string]: unknown; id: string; _display: string }

export type Profile = Profile360Row | null

export type SpacePoint = {
  id: string
  title: string
  organisation: string | null
  career_track: string | null
  node_type: NodeType
  is_plausible: boolean | null
  posting_date: string | null
  x: number
  y: number
  z: number
}

export type SpaceResponse = {
  points: SpacePoint[]
  profile: { x: number; y: number; z: number } | null
  note?: string
  role_count: number
  embedded_role_count: number
  embedding_model: string
  year_range: YearRange
}

export type SpaceFilter = { year?: number; date_from?: string; date_to?: string }

export type RebuildEmbeddingsSummary = {
  model: string
  roles_scanned: number
  embeddings_created: number
  embeddings_updated: number
  skipped: number
}

export type ConceptType = {
  code: string
  label: string
  definition: string
  is_atom: boolean
  sort_order: number
}

export type Concept = {
  id: string
  type_code: string
  canonical_name: string
  definition: string | null
  status: string
  merged_into: string | null
  origin: string
  created_at: string
  reviewed_at: string | null
  aliases?: { id: string; alias: string; origin: string }[]
}

export type ConceptInput = {
  type_code: string
  canonical_name: string
  definition?: string | null
  status?: string
}

export type Facet = {
  id: string
  canonical_name: string
  role_count: number
}

// --- Accepted-vocabulary maintenance (Concept Details drawer) --------------

export type CapabilityDetailInput = {
  demonstration_standard: string
  min_depth?: string
  min_autonomy?: string | null
  requires_all_core?: boolean
  min_core_required?: number | null
  economic_salience?: string | null
  notes?: string | null
}

export type ConceptMetadataUpdateInput = {
  canonical_name?: string
  type_code?: string
  definition?: string | null
  status?: 'active' | 'deprecated'
  // Only consulted when type_code is changing *into* 'capability'.
  capability_detail?: CapabilityDetailInput
}

// --- Concept Dossier ---------------------------------------------------------
//
// Persisted, AI-assisted explanatory dossier for one accepted canonical
// concept. `related_concepts` is an explanatory annotation only — never a
// formal concept_edge; every concept_id here is restricted server-side to
// concepts actually offered as grounded candidates.

export type ConceptDossierStatus = 'active' | 'draft' | 'superseded'
export type ConceptDossierOrigin = 'ai' | 'curator'

export type RelatedConceptSuggestion = {
  concept_id: string
  canonical_name: string
  type_code: string
  relationship: string
  explanation: string
}

export type ConceptDossier = {
  id: string
  concept_id: string
  status: ConceptDossierStatus
  origin: ConceptDossierOrigin
  generated_at: string
  generator_version: string
  model: string | null
  prompt_version: string | null
  guidance: string | null
  plain_definition: string
  classification_rationale: string
  practical_meaning: string
  underlying_elements: string[]
  stronger_expressions: string[]
  weaker_expressions: string[]
  boundaries_and_overlaps: string
  related_concepts: RelatedConceptSuggestion[]
  caveats: string | null
  created_at: string
  updated_at: string
}

export type ConceptDossierResponse = { concept_id: string; active: ConceptDossier | null; draft: ConceptDossier | null }
export type ConceptDossierGenerateResult = { created: boolean; dossier: ConceptDossier }
export type ConceptDossierActionResult = { dossier: ConceptDossier }

export type ConceptDossierManualEditInput = {
  plain_definition?: string
  classification_rationale?: string
  practical_meaning?: string
  underlying_elements?: string[]
  stronger_expressions?: string[]
  weaker_expressions?: string[]
  boundaries_and_overlaps?: string
  caveats?: string | null
}

export type ProposalGroup = {
  cluster_key: string
  surface_form: string // the first/representative exact form — kept for back-compat
  surface_forms: string[] // every exact surface form this cluster covers (docs/18 §3) — usually length 1
  proposal_ids: string[]
  suggested_type: string | null
  nearest_concept_id: string | null
  nearest_similarity: number | null
  occurrence_count: number
}

export type ProposalAction = 'accept_new' | 'accept_alias' | 'reject' | 'defer'

export type ProposalResolveInput = {
  surface_form: string
  action: ProposalAction
  type_code?: string
  canonical_name?: string
  definition?: string
  concept_id?: string
}

export type ClusterProposalResolveInput = {
  cluster_key: string
  action: ProposalAction
  type_code?: string
  canonical_name?: string
  definition?: string
  concept_id?: string
}

export type ProposalStats = {
  pending_groups: number
  total_documents: number
  proposals_per_document: number | null
}

// --- Vocabulary curation (Vocabulary Proposal Prioritisation and Curation
// UX) — the prioritised, evidence-rich cluster review queue. Distinct from
// the legacy ProposalGroup/resolveProposalCluster pair above, which remains
// unchanged for backward compatibility. -------------------------------------

export type PriorityBand = 'high' | 'medium' | 'low' | 'sparse'

export type ClusterExampleRole = { id: string; title: string | null }

// Fields beyond cluster_key/status/surface_forms/suggested_canonical_label
// are only populated for `status: 'pending'` rows — an accepted/rejected
// row is audit history (what happened, onto what concept), not a re-scored
// evidence card. See vocabulary_curation.py's `_resolved_cluster_rows`.
export type VocabClusterSummary = {
  cluster_key: string
  status: string
  suggested_canonical_label: string
  surface_forms: string[]
  proposal_ids: string[] | null
  suggested_type: string | null
  nearest_concept_id: string | null
  nearest_similarity: number | null
  role_count: number | null
  observation_count: number | null
  distinct_years?: number[]
  first_observed: string | null
  last_observed: string | null
  countries?: string[]
  seniority_levels?: string[]
  career_tracks?: string[]
  example_roles?: ClusterExampleRole[]
  priority_score: number | null
  priority_band: PriorityBand | null
  flags: string[]
  resolved_concept_id?: string | null
  resolved_canonical_name?: string | null
  resolved_at?: string | null
}

export type VocabClusterListResponse = {
  items: VocabClusterSummary[]
  total: number
  limit: number
  offset: number
  status: string
  sort: string
}

export type VocabClusterFilters = {
  status?: 'pending' | 'accepted' | 'rejected' | 'all'
  q?: string
  min_role_count?: number
  min_observation_count?: number
  observed_from?: string
  observed_to?: string
  country?: string
  seniority?: string
  type_code?: string
  band?: PriorityBand
  sort?: 'priority' | 'occurrence' | 'role_count' | 'recent' | 'alphabetical'
  limit?: number
  offset?: number
}

export type VocabProgress = {
  total_clusters: number
  pending_clusters: number
  accepted_clusters: number
  rejected_clusters: number
  other_status_clusters: number
  high_priority_pending_clusters: number
  accepted_concepts: number
  observations_mapped: number
  observations_unresolved: number
  // The brief §10 signal: distinguishes "canonical vocabulary is not yet
  // curated" from "no match found" everywhere in the app that depends on
  // accepted concepts.
  canonical_vocabulary_curated: boolean
}

export type VocabMethodology = {
  text: string
  bands: string[]
  weights: Record<string, number>
  recency_half_life_years: number
  band_thresholds: Record<string, number>
}

export type ClusterActionResult = {
  cluster_key: string
  status: string
  resolved_concept_id: string | null
  surface_forms?: string[]
  aliases_created?: number
  idempotent_replay: boolean
}

export type BatchAcceptItemInput = { cluster_key: string; canonical_name?: string; type_code?: string; definition?: string }

export type BatchPreviewResult = {
  action: 'accept' | 'reject'
  clusters_selected: number
  clusters_ready: number
  clusters_not_pending: string[]
  resulting_concepts: number
  aliases_estimate: number
  observations_affected: number
}

export type BatchExecuteResult = {
  action: 'accept' | 'reject'
  clusters_processed: number
  results: ClusterActionResult[]
}

// --- Vocabulary "Split cluster" ---------------------------------------------

export type ClusterSplitGroupInput = { surface_forms: string[] }

export type ClusterSplitPreviewGroup = {
  new_cluster_key: string
  suggested_canonical_label: string
  surface_forms: string[]
  role_count: number
  observation_count: number
}

export type ClusterSplitPreviewResult = {
  cluster_key: string
  resulting_groups: ClusterSplitPreviewGroup[]
}

export type ClusterSplitResultGroup = { new_cluster_key: string; surface_forms: string[]; proposals_updated: number }

export type ClusterSplitResult = {
  cluster_key: string
  groups_created: number
  resulting_clusters: ClusterSplitResultGroup[]
}

// --- Vocabulary Map (docs/25-vocabulary-map.md) -----------------------------
//
// A read-only visual/navigation projection over the exact same vocabulary
// state the Review tab curates — never a second vocabulary model. Node kinds
// are a discriminated union on `kind`; `target` says what clicking a node
// should open (cluster review, Concept Details, or Word Details).

export type VocabTarget =
  | { type: 'cluster_review'; id: string }
  | { type: 'concept'; id: string }
  | { type: 'surface_form'; value: string }

export type VocabGraphGroupNode = {
  id: string
  kind: 'group'
  label: string
  count: number
}

export type VocabGraphPendingClusterNode = {
  id: string
  kind: 'pending_cluster'
  label: string
  cluster_key: string
  priority_band: PriorityBand | null
  priority_score: number | null
  surface_count: number
  observation_count: number | null
  role_count: number | null
  country_count: number
  flags: string[]
  target: { type: 'cluster_review'; id: string }
  focus?: boolean
}

export type VocabGraphConceptNode = {
  id: string
  kind: 'concept'
  label: string
  type_code: string
  status: string
  alias_count?: number
  target: { type: 'concept'; id: string }
  focus?: boolean
}

export type VocabGraphSurfaceFormNode = {
  id: string
  kind: 'surface_form'
  label: string
  status: 'pending' | 'accepted'
  observation_count?: number | null
  target: { type: 'surface_form'; value: string } | { type: 'concept'; id: string }
  focus?: boolean
}

export type VocabularyGraphNode = VocabGraphGroupNode | VocabGraphPendingClusterNode | VocabGraphConceptNode | VocabGraphSurfaceFormNode
export type VocabularyGraphNodeKind = VocabularyGraphNode['kind']
export type VocabularyGraphRelation = 'contains' | 'member_of' | 'maps_to' | 'alias_of' | 'similar_to' | 'ontology'

export type VocabularyGraphEdge = {
  source: string
  target: string
  relation: VocabularyGraphRelation
  similarity?: number
  ontology_relation?: string
}

export type VocabularyGraphMeta = {
  total_nodes: number
  returned_nodes: number
  returned_edges: number
  truncated: boolean
  status?: 'pending' | 'accepted' | 'combined'
  group_by?: 'priority' | 'type' | 'none'
  mode?: 'similarity_focus'
  focus?: string
  similarity_limit?: number
  filters?: Record<string, unknown>
}

export type VocabularyGraphResponse = {
  meta: VocabularyGraphMeta
  nodes: VocabularyGraphNode[]
  edges: VocabularyGraphEdge[]
}

export type VocabularyGraphFilters = {
  status?: 'pending' | 'accepted' | 'combined'
  group_by?: 'priority' | 'type' | 'none'
  band?: PriorityBand
  type_code?: string
  q?: string
  min_role_count?: number
  min_observation_count?: number
  country?: string
  seniority?: string
  observed_from?: string
  observed_to?: string
  limit?: number
  include_similarity?: boolean
  include_ontology?: boolean
}

export type SurfaceFormDetail = {
  surface_form: string
  normalised_surface_form: string
  status: string
  cluster_key: string | null
  cluster_key_locked: boolean
  suggested_type: string | null
  observation_count: number | null
  role_count: number | null
  years: number[]
  countries: string[]
  seniority_levels: string[]
  career_tracks: string[]
  example_roles: ClusterExampleRole[]
  nearest_concept: { id: string; canonical_name: string; similarity: number | null } | null
  resolved_concept: { id: string; canonical_name: string; type_code: string } | null
  resolved_at?: string | null
  alias_origin?: string | null
}

// One supporting source occurrence for a requirement_claim (migration
// 0026, jobber.requirement_evidence) — "one current requirement per
// (role_instance_id, concept_id), many supporting evidence occurrences".
export type RequirementEvidence = {
  id: string
  document_id: string | null
  document_title: string | null
  document_provenance: string | null
  evidence_span: string | null
  evidence_offset_start: number | null
  evidence_offset_end: number | null
  basis: 'stated' | 'implied' | 'inferred' | 'user_asserted' | null
  surface_form: string | null
  extraction_run_id: string | null
  created_at: string
}

export type RequirementClaim = {
  id: string
  requirement_type: 'required' | 'preferred' | 'contextual'
  importance: number | null
  basis: 'stated' | 'implied' | 'inferred' | 'user_asserted'
  evidence_span: string | null
  review_status: 'unreviewed' | 'accepted' | 'rejected' | 'corrected'
  created_at: string
  extraction_run_id: string | null
  superseded_by: string | null
  concept_id: string
  canonical_name: string
  type_code: string
  document_id: string | null
  document_title: string | null
  document_provenance: string | null
  // All supporting source occurrences for this one requirement — every
  // distinct passage that stated it, not only the one quoted in
  // evidence_span above. Always present on API responses (possibly empty
  // for a manually-added requirement with no linked document, or history
  // rows fetched via ?history=true whose evidence moved to the current
  // survivor).
  evidence: RequirementEvidence[]
}

export type RequirementReviewSummary = {
  accepted: number
  unreviewed: number
  rejected: number
  // Pending jobber.concept_proposal rows for this role's own source
  // document — a surface form extraction couldn't resolve to any concept
  // becomes one of these, never a requirement_claim, so it's tracked
  // separately from the claim-status counts above.
  unresolved_proposals: number
  // Whether a requirement_extract run has ever been recorded for this role
  // — distinguishes "never extracted" from "reviewed and complete" even
  // though both currently have zero current claims.
  extraction_attempted: boolean
  // Count of distinct concepts a now-accepted/merged vocabulary proposal
  // this role contributed to never turned into a requirement_claim for (not
  // enough occurrence data to build one faithfully) and that still has no
  // current claim from any other route. Drops to 0 once one does — most
  // commonly by running requirement extraction again for this role.
  needs_reextraction: number
  complete: boolean
}

export type RequirementClaimList = {
  items: RequirementClaim[]
  review_summary: RequirementReviewSummary
}

export type RequirementClaimEditInput = {
  concept_id?: string
  requirement_type?: 'required' | 'preferred' | 'contextual'
  basis?: 'stated' | 'implied' | 'inferred' | 'user_asserted'
  importance?: number | null
  evidence_span?: string | null
}

export type RequirementClaimCreateInput = {
  concept_id: string
  requirement_type: 'required' | 'preferred' | 'contextual'
  basis?: 'stated' | 'implied'
  importance?: number | null
  evidence_span: string
}

export type ExtractionSummary = {
  status: 'ok' | 'partial' | 'failed'
  extraction_run_id: string
  adjudication_run_id?: string | null
  claims_created?: number
  claims_superseded?: number
  claims_deduplicated?: number
  evidence_created?: number
  evidence_deduplicated?: number
  proposals_created?: number
  proposals_updated?: number
  rejected_span_count?: number
  error?: string
}

// --- Duplicate detection (source-aware ingest cleanup) ----------------------
//
// `exact_duplicate` (identical raw content) always takes precedence over
// `possible_duplicate` (identical only after conservative whitespace
// normalisation — e.g. a PDF re-extraction of the same posting). Either may
// be null. Neither ever blocks a capture — the UI is expected to warn and
// offer 'Open existing' / 'Capture anyway'.
export type DuplicateRoleSummary = {
  document_id: string
  role_instance_id: string | null
  title: string | null
  organisation: string | null
  posting_date: string | null
}

export type DuplicateCheckResult = {
  exact_duplicate: DuplicateRoleSummary | null
  possible_duplicate: DuplicateRoleSummary | null
}

export type IngestResult = {
  id: string
  document_id: string
  duplicate_of_document_id: string | null
  duplicate: DuplicateCheckResult
  status: string
}

// --- Source-aware metadata (manual Edit + reviewable AI enrichment) --------

export type RoleMetadataInput = {
  title?: string | null
  organisation?: string | null
  location?: string | null
  country?: string | null
  remote_type?: string | null
  employment_type?: string | null
  seniority_level?: string | null
  posting_date?: string | null
}

export type RoleMetadataProposal = RoleMetadataInput

export type MetadataProposalResult = {
  status: 'ok' | 'failed'
  extraction_run_id: string
  error: string | null
  proposal: RoleMetadataProposal | null
}

export type MappingReviewStatus = 'unreviewed' | 'accepted' | 'rejected'

export type Profile360Mapping = {
  id: string
  profile360_id: string
  mapping_basis: 'exact_match' | 'ai_suggested' | 'curator_asserted'
  review_status: MappingReviewStatus
  reviewed_at: string | null
  created_at: string
  extraction_run_id: string | null
  concept_id: string
  canonical_name: string
  type_code: string
  _display: string | null
}

export type MappingAttemptResult = {
  status: 'ok' | 'failed'
  extraction_run_id: string
  mapped?: boolean
  mapping_id?: string
  concept_id?: string
  error?: string
  // Present whenever status === 'ok': how many canonical-vocabulary
  // candidates the mapping attempt actually had to consider. Until the
  // vocabulary bootstrap (docs/18 §3/§6) is reviewed and accepted, this is
  // routinely 0 — 'no_candidates_available' — which is a statement about
  // the vocabulary, not about the person's evidence. 'declined_all_candidates'
  // means real candidates existed and none were confident enough.
  candidates_considered?: number
  reason?: 'no_candidates_available' | 'declined_all_candidates'
}

export type ComparisonStatus = 'evidenced' | 'partial' | 'user_asserted' | 'not_found'

// --- Phase 3: capability catalogue / coverage -------------------------------

export type DepthLevel = 'exposed' | 'applied' | 'owned' | 'set_standard'
export type AutonomyLevel = 'assisted' | 'independent' | 'directed_others' | 'accountable'
export type Necessity = 'core' | 'supporting' | 'contextual'
export type EconomicSalience = 'low' | 'medium' | 'high'

export type ComponentEdge = {
  edge_id: string
  necessity: Necessity
  concept_id: string
  canonical_name: string
  type_code: string
}

export type CapabilityComponents = { core: ComponentEdge[]; supporting: ComponentEdge[]; contextual: ComponentEdge[] }

export type CapabilitySummary = {
  id: string
  canonical_name: string
  definition: string | null
  status: string
  origin: string
  created_at: string
  reviewed_at: string | null
  demonstration_standard: string
  min_depth: DepthLevel
  min_autonomy: AutonomyLevel | null
  requires_all_core: boolean
  min_core_required: number | null
  economic_salience: EconomicSalience | null
  notes: string | null
  core_component_count: number
  supporting_component_count: number
  contextual_component_count: number
  proposed_component_count: number
}

export type CoverageEpisodeSummary = {
  episode_id: string
  core_met: string[]
  core_missing: string[]
  supporting_met: string[]
  supporting_missing: string[]
  contextual_met: string[]
  contextual_missing: string[]
}

export type CapabilityCoverageTrace = {
  capability: { id: string; canonical_name: string }
  requirement: { min_depth: DepthLevel; min_autonomy: AutonomyLevel | null; requires_all_core: boolean; min_core_required: number | null }
  direct_evidence: {
    source_kind: 'claim' | 'capability'
    mapping_id: string
    review_status: string
    mapping_basis: string
    display: string | null
    depth: DepthLevel | null
    autonomy: AutonomyLevel | null
    episode_id: string | null
    meets_depth: boolean
    meets_autonomy: boolean
  }[]
  compositional:
    | {
        core_total: number
        supporting_total: number
        contextual_total: number
        best_episode: CoverageEpisodeSummary | null
        episodes_considered: number
        core_complete: boolean
        core_required: number
        meaningful: boolean
      }
    | { core_total: 0; supporting_total: 0; contextual_total: 0; note: string }
  assertion: { id: string; note: string | null; created_at: string; promoted_to_profile360_at: string | null } | null
  status_reason: { code: string; message: string }
}

export type CapabilityCoverage = {
  capability_concept_id: string
  canonical_name?: string
  status: ComparisonStatus
  coverage_score: number
  core_components_total: number
  core_components_met: number
  strongest_depth: DepthLevel | null
  strongest_autonomy: AutonomyLevel | null
  directly_claimed: boolean
  last_demonstrated: string | null
  years_active: number | null
  supporting_profile360_claim_ids: string[]
  trace: CapabilityCoverageTrace
}

export type Capability = CapabilitySummary & {
  components: CapabilityComponents
  // 'proposed' component_of edges only (docs/18 §10, bootstrap or any future
  // proposer) — never merged with `components` above, and never read by the
  // coverage engine; a separate review affordance in the curation UI.
  components_proposed: CapabilityComponents
  coverage: CapabilityCoverage | null
}

export type CapabilityInput = {
  canonical_name: string
  definition?: string | null
  demonstration_standard: string
  min_depth?: DepthLevel
  min_autonomy?: AutonomyLevel | null
  requires_all_core?: boolean
  min_core_required?: number | null
  economic_salience?: EconomicSalience | null
  notes?: string | null
  status?: string
}

export type CapabilitySpecificationInput = Omit<CapabilityInput, 'canonical_name' | 'definition' | 'status'>
export type UnconfiguredCapability = Pick<CapabilitySummary, 'id' | 'canonical_name' | 'definition' | 'status' | 'origin' | 'created_at' | 'reviewed_at'>

export type RebuildSummary = {
  engine_version: string
  capability_coverage: { computed: number; removed_stale: number; engine_version: string }
  role_fit: { computed: number; removed_stale: number; engine_version: string }
}

export type EvalMetric = { measured: boolean; value: number | null; n: number; note?: string; [key: string]: unknown }
export type EvalReport = {
  span_validity: EvalMetric
  concept_linking_f1: EvalMetric
  modifier_accuracy: EvalMetric
  proposals_per_document: EvalMetric
  capability_agreement: EvalMetric
}

export type ComparisonItem = {
  concept: { id: string; canonical_name: string; type_code: string }
  status: ComparisonStatus
  role_side: {
    requirement_claim_id: string | null
    role_skill_observation_id?: string | null
    requirement_type: string
    basis: string
    review_status: string
    evidence_span: string | null
    document: { id: string; title: string | null; provenance: string; url: string | null } | null
  }
  person_side: {
    mappings: { id: string; profile360_id: string; review_status: string; mapping_basis: string; mapping_kind: string; display: string | null }[]
    assertion: { id: string; note: string | null; created_at: string; promoted_to_profile360_at: string | null } | null
    component_of: { id: string; canonical_name: string; necessity: Necessity }[]
    coverage: CapabilityCoverage | null
  }
}

export type GapConcept = { id: string; canonical_name: string; type_code: string }

export type ComparisonResult = {
  target_mapping?: TargetPath['target_mapping']
  role: { id: string; title: string; kind: string }
  items: ComparisonItem[]
  counts: Record<ComparisonStatus, number>
  blocking_gaps: GapConcept[]
  unverified_required: (GapConcept & { status: ComparisonStatus })[]
  fit_score: number | null
  embedding_similarity: number | null
  engine_version: string
  review_summary: RequirementReviewSummary
}

export type PreferenceDimension = { code: string; label: string; definition: string; sort_order: number }

export type PreferenceObservation = {
  id: string
  dimension_code: string
  direction: 'toward' | 'away' | 'neutral'
  strength: number
  basis: 'observed_behavior' | 'user_stated' | 'repeated_episode_evidence' | 'validated_psychometric' | 'typology_hypothesis'
  source_label: string | null
  profile360_episode_id: string | null
  profile360_claim_id: string | null
  confidence: 'low' | 'medium' | 'high'
  occurred_at: string | null
  recorded_at: string
  note: string | null
}

export type PreferenceObservationInput = {
  dimension_code: string
  direction: 'toward' | 'away' | 'neutral'
  strength: number
  basis: PreferenceObservation['basis']
  source_label?: string | null
  profile360_episode_id?: string | null
  profile360_claim_id?: string | null
  confidence?: 'low' | 'medium' | 'high'
  occurred_at?: string | null
  note?: string | null
}

// --- Trends (docs/18 §7/§8/§9) — descriptive statistics over the captured
// role corpus, never a labour-market forecast; every result carries its own
// sample size. -------------------------------------------------------------

export type TrendFilterInput = {
  year_from?: number
  year_to?: number
  country?: string
  seniority_level?: string
  career_track?: string
}

export type Bucket = { value: string | number | null; role_count: number }

export type CorpusOverview = {
  sample_size: number
  by_year: Bucket[]
  by_country: Bucket[]
  by_region: Bucket[]
  by_seniority: Bucket[]
  by_career_track: Bucket[]
}

export type RequirementFrequencyItem = {
  concept_id: string | null
  label: string
  type_code: string | null
  is_canonical: boolean
  role_count: number
  proportion: number | null
  by_requirement_type: { required: number; preferred: number; inferred: number }
}

export type TopRequirements = {
  sample_size: number
  insufficient_sample: boolean
  min_sample_size: number
  items: RequirementFrequencyItem[]
}

export type RequirementKey = { concept_id: string } | { surface_form: string }

export type TrendPeriodPoint = { period: number; role_count: number; total_roles: number; proportion: number | null; sample_size: number }

export type TrendLabel = 'emerging' | 'increasing' | 'persistent' | 'declining' | 'sparse_insufficient_evidence'

export type TrendClassification = {
  label: TrendLabel
  rationale: string
  usable_periods: number
  total_periods: number
  early_mean_proportion?: number
  late_mean_proportion?: number
}

export type RequirementTrend = { granularity: 'year' | '5year'; series: TrendPeriodPoint[]; classification: TrendClassification }

export type CooccurrenceItem = { concept_id: string; canonical_name: string; type_code: string; co_count: number; proportion_of_roles: number }
export type Cooccurrence = { sample_size: number; items: CooccurrenceItem[] }

export type DimensionCompareItem = {
  value: string | number | null
  role_count: number
  sample_size: number
  proportion: number | null
  insufficient_sample: boolean
}
export type DimensionCompare = { dimension: string; items: DimensionCompareItem[] }

export type TrendMethodology = {
  text: string
  sparse_min_sample: number
  emerging_early_max_proportion: number
  change_relative_threshold: number
}

function trendQuery(filters: TrendFilterInput, extra: Record<string, string | number | undefined> = {}): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries({ ...filters, ...extra })) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  }
  const s = qs.toString()
  return s ? `?${s}` : ''
}

function requirementKeyParams(key: RequirementKey): Record<string, string> {
  return 'concept_id' in key ? { concept_id: key.concept_id } : { surface_form: key.surface_form }
}

// --- Auth (docs/20-render-deployment.md) -----------------------------------
//
// Session state lives in an HttpOnly cookie the browser sends automatically
// on same-origin requests — there is no token for this module to hold. The
// one thing every other API call needs from here is `onUnauthorized`: when
// any /api call comes back 401 (session missing/expired), `req` below
// notifies whoever is showing the app's authenticated UI so it can drop
// back to the login screen, instead of every call site having to notice
// this itself.
let unauthorizedHandler: (() => void) | null = null

function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export type LoginResult = { ok: true } | { ok: false; error: string }

async function login(password: string): Promise<LoginResult> {
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    if (res.ok) return { ok: true }
    if (res.status === 401) return { ok: false, error: 'Incorrect password.' }
    if (res.status === 429) return { ok: false, error: 'Too many attempts. Please wait a minute and try again.' }
    return { ok: false, error: 'Sign-in failed. Please try again.' }
  } catch {
    return { ok: false, error: 'Unable to reach the server. Please try again.' }
  }
}

async function logout(): Promise<void> {
  try {
    await fetch('/api/auth/logout', { method: 'POST' })
  } catch {
    // Best-effort: even if this fails, the caller drops back to the login
    // screen regardless (see AuthGate.tsx) — there is no client-side state
    // to invalidate beyond the (HttpOnly, unreadable-by-JS) cookie itself.
  }
}

async function authStatus(): Promise<boolean> {
  try {
    const res = await fetch('/api/auth/status')
    if (!res.ok) return false
    const data = (await res.json()) as { authenticated?: boolean }
    return !!data.authenticated
  } catch {
    return false
  }
}

// --- Phase 4: role archetypes -----------------------------------------------

export interface RoleSummary {
  id: string
  title: string | null
  organisation: string | null
  country: string | null
  seniority_level: string | null
  instance_type?: string
}

export interface TitleGroup {
  normalized_title: string
  sample_title: string | null
  role_count: number
  roles: RoleSummary[]
}

export interface Archetype {
  id: string
  canonical_name: string
  status: string
  created_at: string
  reviewed_at: string | null
  seniority_band: string | null
  primary_function_concept_id: string | null
  typical_market: string | null
  notes: string | null
  role_count: number
  accepted_compensation_observation_count: number
}

export interface ArchetypeDetail extends Archetype {
  roles: RoleSummary[]
}

export interface ArchetypeCreateInput {
  canonical_name: string
  seniority_band?: string | null
  primary_function_concept_id?: string | null
  typical_market?: string | null
  notes?: string | null
  role_instance_ids?: string[]
}

export interface ArchetypeUpdateInput {
  canonical_name?: string
  seniority_band?: string | null
  primary_function_concept_id?: string | null
  typical_market?: string | null
  notes?: string | null
  status?: 'active' | 'deprecated'
}

// --- Phase 4: market + compensation observations ----------------------------

export interface Market {
  id: string
  code: string
  label: string
  country: string | null
  geography: string | null
  domain_concept_id: string | null
  status: string
  notes: string | null
  created_at: string
}

export interface CompensationObservation {
  id: string
  role_instance_id: string | null
  archetype_concept_id: string | null
  raw_role_label: string | null
  market_id: string | null
  component: string
  pay_period: string
  employment_basis: string | null
  amount_min: number | null
  amount_mid: number | null
  amount_max: number | null
  currency: string
  reported_p25: number | null
  reported_p50: number | null
  reported_p75: number | null
  reported_mean: number | null
  bonus_pct: number | null
  basis: 'posting_stated' | 'posting_estimated' | 'survey' | 'curator_asserted'
  review_status: 'unreviewed' | 'accepted' | 'rejected'
  observed_at: string | null
  period_end: string | null
  document_id: string | null
  reported_sample_size: number | null
  source_quality: string | null
  source_kind: string | null
  geography_reported: string | null
  domain_or_practice_area: string | null
  seniority_band_reported: string | null
  experience_band: string | null
  pqe_band: string | null
  publisher?: string | null
  document_title?: string | null
  report_date?: string | null
  source_note: string | null
  page_reference?: string | null
  table_reference?: string | null
  created_at: string
  reviewed_at: string | null
}

export interface CompensationObservationCreateInput {
  role_instance_id?: string | null
  archetype_concept_id?: string | null
  raw_role_label?: string | null
  market_id: string
  component: string
  pay_period: string
  employment_basis?: string | null
  amount_min?: number | null
  amount_mid?: number | null
  amount_max?: number | null
  currency: string
  reported_p25?: number | null
  reported_p50?: number | null
  reported_p75?: number | null
  bonus_pct?: number | null
  observed_at?: string | null
  reported_sample_size?: number | null
  source_note?: string | null
}

export interface BackfillSummary {
  roles_considered: number
  observations_created: number
  observations_already_present: number
  skipped_no_currency: number
  unassigned_market: number
}

// --- Phase 4: derived economics tables --------------------------------------

export interface ArchetypeDemandRow {
  archetype_concept_id: string
  capability_concept_id: string
  canonical_name: string
  roles_in_archetype: number
  roles_demanding_capability: number
  demand_rate: number | null
  required_count: number
  preferred_count: number
  contextual_count: number
  source_role_ids: string[]
  trace: Record<string, unknown>
  engine_version: string
  computed_at: string
}

export interface ArchetypeCompRow {
  archetype_concept_id: string
  archetype_name: string
  market_id: string
  market_label: string
  period_start: string
  period_end: string
  currency: string
  component: string
  pay_period: string
  n_observations: number
  n_posting_stated: number
  n_posting_estimated: number
  n_survey_sources: number
  posting_p25: number | null
  posting_p50: number | null
  posting_p75: number | null
  survey_benchmarks: Array<{
    observation_id: string
    reported_p25: number | null
    reported_p50: number | null
    reported_p75: number | null
    amount_mid: number | null
    reported_sample_size: number | null
    source_note: string | null
  }>
  reference_comp: number | null
  reference_source: 'survey' | 'posting' | null
  reference_basis_detail: Record<string, unknown> | null
  trace: Record<string, unknown>
  engine_version: string
  computed_at: string
}

export interface GapValueContext {
  market_id: string
  market_label: string
  currency: string
}

export interface GapValueRow {
  capability_concept_id: string
  canonical_name: string
  market_id: string
  period_start: string
  period_end: string
  currency: string
  archetypes_unlocked: number
  archetypes_improved: number
  roles_unlocked: number
  roles_improved: number
  reference_comp_unlocked: number | null
  comp_delta_vs_best_current_reachable: number | null
  n_comp_observations: number
  evidence_quality: 'insufficient' | 'thin' | 'moderate' | 'good'
  rank: number | null
  trace: {
    capability_name?: string
    best_current_reachable?: number | null
    unlocked_archetype_ids?: string[]
    improved_archetype_ids?: string[]
    roles?: Array<{ role_instance_id: string; effect: 'unlocked' | 'improved' }>
    [key: string]: unknown
  }
  engine_version: string
  computed_at: string
}

export interface EconomicsRebuildState {
  evidence_revision: number | null
  path_revision: number | null
  economics_revision: number | null
  engine_version: string | null
  rebuilt_at: string | null
}

export interface Phase4RebuildSummary {
  // Which source state this rebuild saw. A later read compares it against the
  // live counters and withholds derived figures that no longer reflect them.
  rebuild_state?: EconomicsRebuildState
  engine_version: string
  archetype_demand: { computed: number; removed_stale: number; engine_version: string }
  archetype_comp: { computed: number; removed_stale: number; engine_version: string }
  gap_value: { computed: number; removed_stale: number; engine_version: string; buckets: number }
}

export interface Phase4Readiness {
  capability_agreement: { measured: boolean; value: number | null; n: number; note?: string }
  total_active_archetypes: number
  archetypes_with_assigned_roles: number
  accepted_compensation_observations: number
  accepted_posting_stated_observations: number
  accepted_survey_observations: number
  compensation_sample_sufficiency: 'sufficient' | 'insufficient'
  engine_version: string
}

// --- Phase 4: market survey documents ---------------------------------------

export interface MarketSurveyDocument {
  id: string
  title: string | null
  source: string | null
  url: string | null
  source_date: string | null
  captured_at: string | null
  source_payload: { publisher?: string; report_title?: string; report_date?: string; methodology_notes?: string }
  created_at: string
}

export interface MarketSurveyDocumentDetail extends MarketSurveyDocument {
  content_text: string
  observations: CompensationObservation[]
  extraction_runs: Array<{ id: string; status: string; started_at: string; finished_at: string | null; error_message: string | null }>
}

export interface MarketDataIngestInput {
  text: string
  publisher?: string
  report_title?: string
  report_date?: string
  source_url?: string
  methodology_notes?: string
}

export interface ExtractionResult {
  document_id: string
  extraction_run_id: string
  status: string
  observations_created?: number
  items_skipped_incomplete?: number
  error?: string
}

// --- Market analytics (docs/25) ---------------------------------------------
//
// A read-only analytical layer over *every* accepted compensation
// observation, whether or not it carries an archetype assignment — a
// separate consumer of the same evidence `economics_engine`'s archetype
// benchmarks/Gap Value read from (see backend/app/market_analytics.py).
// `reported_p50`/`reported_mean`/range fields are always kept as the source
// actually reported them; a `derived_range_midpoint` is always separately
// labelled and never substitutes for a missing median.

export interface MarketAnalyticsFacetValue {
  value: string | number
  label: string
  count: number
}

export interface MarketAnalyticsFacets {
  markets: MarketAnalyticsFacetValue[]
  currencies: MarketAnalyticsFacetValue[]
  components: MarketAnalyticsFacetValue[]
  pay_periods: MarketAnalyticsFacetValue[]
  practice_groups: MarketAnalyticsFacetValue[]
  providers: MarketAnalyticsFacetValue[]
  source_kinds: MarketAnalyticsFacetValue[]
  years: MarketAnalyticsFacetValue[]
}

export interface MarketAnalyticsCoverage {
  accepted_observation_count: number
  distinct_source_document_count: number
  distinct_provider_count: number
  earliest_period: string | null
  latest_period: string | null
  with_reported_median_count: number
  with_reported_mean_count: number
  with_range_count: number
  with_sample_size_count: number
  archetype_linked_count: number
  not_archetype_linked_count: number
}

// One row of PQE/experience progression evidence — never aggregated or
// interpolated across bands; `band` carries the raw reported band label for
// whichever axis this series represents.
export interface MarketAnalyticsPoint {
  observation_id: string
  provider: string | null
  document_id: string | null
  document_title: string | null
  report_date: string | null
  market_id: string | null
  market_label: string | null
  geography_reported: string | null
  raw_role_label: string | null
  practice_reported: string | null
  practice_group: string
  band: string
  component: string
  pay_period: string
  currency: string
  reported_p50: number | null
  reported_mean: number | null
  amount_min: number | null
  amount_max: number | null
  reported_sample_size: number | null
  source_kind: string | null
  source_quality: string | null
  archetype_concept_id: string | null
}

export interface MarketComparisonContext {
  document_id: string
  provider: string | null
  document_title: string | null
  report_date: string | null
  component?: string
  pay_period: string
  currency: string
  band_type: 'pqe' | 'experience' | null
  band: string | null
  practice_group?: string
  practice_reported?: string | null
}

export interface MarketPracticeComparisonItem {
  observation_id: string
  practice_reported: string | null
  practice_group: string
  reported_p50: number | null
  reported_mean: number | null
  amount_min: number | null
  amount_max: number | null
  reported_sample_size: number | null
  source_kind: string | null
  source_quality: string | null
}

// A same-document/component/pay-period/currency/band context in which two
// or more distinct practice areas can be honestly compared — never a
// cross-provider average.
export interface MarketPracticeComparison {
  context: MarketComparisonContext
  practices: MarketPracticeComparisonItem[]
}

export interface MarketComponentComparisonItem {
  observation_id: string
  component: string
  reported_p50: number | null
  reported_mean: number | null
  amount_min: number | null
  amount_max: number | null
  reported_sample_size: number | null
  source_kind: string | null
  source_quality: string | null
}

// Base vs total-package evidence for the same document/practice/band — never
// day-rate vs annual.
export interface MarketComponentComparison {
  context: MarketComparisonContext
  components: MarketComponentComparisonItem[]
}

export interface MarketRoleRange {
  observation_id: string
  raw_role_label: string | null
  provider: string | null
  document_id: string | null
  document_title: string | null
  report_date: string | null
  market_id: string | null
  market_label: string | null
  geography_reported: string | null
  practice_reported: string | null
  practice_group: string
  seniority_band_reported: string | null
  experience_band: string | null
  pqe_band: string | null
  amount_min: number | null
  amount_max: number | null
  // Computed only for sorting/positioning — never a reported statistic.
  derived_range_midpoint: number | null
  reported_p50: number | null
  reported_mean: number | null
  component: string
  pay_period: string
  currency: string
  reported_sample_size: number | null
  source_kind: string | null
  source_quality: string | null
  archetype_concept_id: string | null
}

export interface MarketTrendContext {
  provider: string | null
  market_id: string | null
  geography_reported: string | null
  currency: string
  component: string
  pay_period: string
  practice_group: string
  band_type: 'pqe' | 'experience'
  band: string
  statistic: 'median' | 'mean' | 'range'
}

export interface MarketTrendPoint {
  period: string
  value?: number | null
  amount_min?: number | null
  amount_max?: number | null
  observation_id: string
  reported_sample_size: number | null
}

// Emitted only when >=2 exactly comparable periods exist — see
// backend/app/market_analytics.py::build_trends. Absence is expected with a
// sparse corpus, never filled in with a misleading single-point line.
export interface MarketTrendSeries {
  context: MarketTrendContext
  points: MarketTrendPoint[]
}

export interface MarketEvidenceRow {
  observation_id: string
  provider: string | null
  document_id: string | null
  document_title: string | null
  report_date: string | null
  market_id: string | null
  market_label: string | null
  geography_reported: string | null
  practice_reported: string | null
  practice_group: string
  raw_role_label: string | null
  seniority_band_reported: string | null
  experience_band: string | null
  pqe_band: string | null
  component: string
  pay_period: string
  currency: string
  reported_p50: number | null
  reported_mean: number | null
  amount_min: number | null
  amount_max: number | null
  reported_sample_size: number | null
  source_kind: string | null
  source_quality: string | null
  basis: string
  archetype_concept_id: string | null
}

export interface MarketAnalyticsEvidencePage {
  total: number
  limit: number
  offset: number
  items: MarketEvidenceRow[]
}

export interface MarketAnalyticsSummary {
  coverage: MarketAnalyticsCoverage
  facets: MarketAnalyticsFacets
  pqe_series: MarketAnalyticsPoint[]
  experience_series: MarketAnalyticsPoint[]
  practice_comparisons: MarketPracticeComparison[]
  component_comparisons: MarketComponentComparison[]
  role_ranges: MarketRoleRange[]
  trends: MarketTrendSeries[]
  evidence_rows: MarketAnalyticsEvidencePage
}

export interface MarketAnalyticsFiltersInput {
  market_id?: string
  currency?: string
  component?: string
  pay_period?: string
  practice_group?: string
  provider?: string
  source_kind?: string
  employment_basis?: string
  period_from?: string
  period_to?: string
  evidence_limit?: number
  evidence_offset?: number
}

// --- Phase 4: accepted vocabulary overview ----------------------------------

export interface AcceptedVocabularyOverview {
  total_active_concepts: number
  by_type: Array<{ type_code: string; count: number }>
  missing_definition: { count: number; concept_ids: string[] }
  no_active_dossier: { count: number; concept_ids: string[] }
  dossier_no_related_concepts: { count: number; concept_ids: string[] }
  alias_count: number
  recent_concepts: Array<{ id: string; canonical_name: string; type_code: string; reviewed_at: string | null; created_at: string | null }>
}

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (res.status === 401) {
    unauthorizedHandler?.()
  }
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json()
}

// --- Economic pathways, compensation and archetype context ------------------
//
// Every compensation figure in this app carries a `basis` saying where it
// came from, and the four bases are never merged or relabelled into each
// other. `insufficient_evidence` is a real, expected answer — not an error
// and not a reason to show a zero.

export type CompensationBasis =
  | 'advert_stated'
  | 'market_estimate'
  | 'legacy_estimate'
  | 'insufficient_evidence'

export type EvidenceQuality = 'insufficient' | 'thin' | 'moderate' | 'good'

export type CompensationSupplementaryFigure = {
  observation_id: string
  component: string
  pay_period: string
  label: string
  amount_min: number | null
  amount_max: number | null
  bonus_pct: number | null
  currency: string | null
  evidence_span: string | null
}

export type ResolvedCompensation = {
  basis: CompensationBasis
  basis_label: string
  // What kind of figure the headline actually is — "base salary", "day rate",
  // "total package". A package is never presented as a base salary, and a
  // bonus is never the headline at all (it appears in `supplementary`).
  component_label: string | null
  supplementary: CompensationSupplementaryFigure[]
  currency: string | null
  amount_min: number | null
  amount_reference: number | null
  amount_max: number | null
  component: string | null
  pay_period: string | null
  employment_basis: string | null
  market: { id: string; label: string; code?: string } | null
  period: { start: string; end: string } | null
  as_of: string | null
  archetype: { id: string; name: string } | null
  evidence: {
    n_observations: number
    n_posting_stated: number
    n_posting_estimated?: number
    n_survey_sources: number
  }
  reference_source: string | null
  evidence_quality: EvidenceQuality
  reason: string
  trace: Record<string, unknown>
}

export type PlanningEquivalent = {
  amount: number
  currency: string
  unit: 'annual'
  basis: 'planning_equivalent'
  assumption: { billable_days_per_year: number; day_rate: number; note: string | null }
  label: string
  caveat: string
}

export type EarningsBaseline = {
  observation_id: string
  episode_id: string | null
  // Employment-episode context. An open-ended salary attached to an episode
  // that has ended is historical, however open-ended the row looks — so the
  // UI can explain why a figure reads as past rather than current.
  episode_title: string | null
  episode_organisation: string | null
  episode_end_date: string | null
  episode_status: string | null
  evidence_status_reason: string | null
  source_kind: string
  employment_basis: string | null
  employment_basis_equivalent: string | null
  component: string
  amount: number
  currency: string | null
  unit: string | null
  quantity: number | null
  effective_from: string | null
  period_start: string | null
  period_end: string | null
  pay_date: string | null
  evidence_period: string | null
  evidence_status: 'current' | 'historical'
  notes: string | null
  uncertainty: string | null
  planning_equivalent: PlanningEquivalent | null
  label: string
  other_evidence_in_group: {
    observation_id: string
    component: string
    amount: number
    unit: string | null
    evidence_status: string
    evidence_period: string | null
  }[]
  group: { currency: string | null; employment_basis: string | null }
}

export type PlanningAssumption = {
  contract_billable_days_per_year: number | null
  note: string | null
  updated_at: string | null
}

export type PersonalEarningsState = {
  status: 'current' | 'historical' | 'unavailable' | 'profile360_unavailable'
  as_of: string
  baselines: EarningsBaseline[]
  other_components: Omit<EarningsBaseline, 'planning_equivalent' | 'label' | 'other_evidence_in_group' | 'group'>[]
  currencies: string[]
  planning_assumption: PlanningAssumption
  notes: string[]
  fingerprint: string
}

export type PersonalComparison = {
  comparable: boolean
  reason: string | null
  baseline: {
    observation_id: string
    component: string
    amount: number
    source_amount: number
    currency: string | null
    unit: string | null
    employment_basis: string | null
    evidence_status: string
    evidence_period: string | null
    label: string
    planning_equivalent: PlanningEquivalent | null
  } | null
  uses_planning_equivalent: boolean
  difference_min: number | null
  difference_reference: number | null
  difference_max: number | null
  opportunity?: {
    basis: CompensationBasis
    basis_label: string
    currency: string | null
    amount_min: number | null
    amount_reference: number | null
    amount_max: number | null
  }
  limitations?: string[]
}

export type RoleCompensationObservation = {
  id: string
  basis: string
  review_status: string
  component: string
  pay_period: string
  employment_basis: string | null
  currency: string
  amount_min: number | null
  amount_mid: number | null
  amount_max: number | null
  bonus_pct: number | null
  evidence_span: string | null
  observed_at: string | null
  source_note: string | null
  market_id: string | null
  market_label: string | null
  created_at: string
  reviewed_at: string | null
}

export type RoleCompensationResponse = {
  role_instance_id: string
  compensation: ResolvedCompensation
  personal_comparison: PersonalComparison
  personal_earnings: PersonalEarningsState
  observations: RoleCompensationObservation[]
}

export type CompensationProposalItem = {
  amount_min: number | null
  amount_max: number | null
  // A bonus is a percentage of pay, never a cash amount with a currency.
  bonus_pct: number | null
  currency: string | null
  component: string | null
  pay_period: string | null
  employment_basis: string | null
  evidence_span: string | null
  note: string | null
  // The server's own verdict on this item, computed with the same rules
  // acceptance re-applies — so the review screen never offers an Accept the
  // server will then refuse.
  acceptable: boolean
  problems: string[]
}

export type CompensationProposalResult = {
  status: 'ok'
  extraction_run_id: string
  error: string | null
  error_type: string | null
  document_id: string
  provenance_quality: string
  proposal: {
    items: CompensationProposalItem[]
    no_compensation_stated: boolean
    notes: string | null
  }
}

export type CompensationAcceptInput = {
  amount_min: number | null
  amount_max: number | null
  bonus_pct?: number | null
  // Optional for a bonus percentage, which borrows the currency of the pay it
  // applies to from the role's own evidence.
  currency?: string | null
  component: string
  pay_period: string
  employment_basis?: string | null
  evidence_span: string
  note?: string | null
}

export type CompensationAcceptResult = {
  // A correction creates a *new* accepted observation rather than rewriting
  // the old one, so this is not necessarily the id that was corrected — the
  // prior figure survives, retired and annotated, as history.
  id: string
  created?: boolean
  status: string
  review_status?: string
  market_unassigned_reason?: string | null
  corrected_from_observation_id?: string
  // Accepting a corrected figure retires the one it corrects, so a role never
  // carries two accepted stated figures for the same component.
  superseded_observation_ids: string[]
}

export type EconomicsFreshness = {
  state: 'fresh' | 'stale' | 'never_rebuilt'
  fresh: boolean
  stale_inputs: string[]
  reason: string | null
  last_rebuilt_at: string | null
  engine_version: string | null
}

export type RoleArchetypeSummary = {
  assigned: boolean
  archetype_concept_id: string | null
  archetype_name: string | null
  archetype_status?: string
  seniority_band: string | null
  typical_market: string | null
  catalogue_size: number
  state: 'unclassified' | 'assigned' | 'assigned_to_deprecated_archetype'
}

export type ArchetypeCatalogueEntry = {
  id: string
  canonical_name: string
  seniority_band: string | null
  typical_market: string | null
  notes: string | null
}

export type ArchetypeProposalResult = {
  status: 'ok'
  extraction_run_id: string
  error: string | null
  error_type: string | null
  proposal: {
    matched: boolean
    archetype_concept_id: string | null
    archetype_name: string | null
    raw_suggestion: string | null
    confidence: string | null
    rationale: string | null
    alternatives: { id: string; canonical_name: string }[]
    catalogue_size: number
    note: string | null
  }
}

export type ArchetypeContextEnrichment = {
  id: string
  archetype_concept_id: string
  status: 'active' | 'superseded'
  generated_at: string
  generator_version: string
  model: string
  source_fingerprint: string | null
  grounding_provenance: {
    posting_ids: string[]
    postings_used_in_prompt: number
    postings_total: number
    requirement_counts: Record<string, number>
    demand_capability_ids: string[]
    role_context_ids: string[]
    reads_profile360: false
  }
  day_in_life: DayInLifeItem[]
  typical_week: TypicalWeekItem[]
  team_context: TeamContext
  manager_context: ManagerContext
  stakeholder_context: { stakeholders: GroundedNote[] }
  career_progression: CareerStep[]
  grounding_summary: GroundingSummary
  caveats: string | null
  created_at: string
  updated_at: string
}

export type ArchetypeContextResponse = {
  archetype_concept_id: string
  archetype_name: string
  enrichment: ArchetypeContextEnrichment | null
  synthesis_note: string
}

export type ArchetypeContextGenerateResult = { created: boolean; enrichment: ArchetypeContextEnrichment }

export type MarketContext = {
  market_id: string
  market_label: string
  market_code: string
  currency: string
  accepted_observations: number
}

export type PathwayTransition = {
  blocking_required_gaps: number
  blocking_required_gap_names: string[]
  unverified_required_gaps: number
  unverified_required_gap_names: string[]
  evidence_coverage: number | null
  evidenced_requirements: number | null
  requirements_total: number | null
  target_gaps_addressed: string[]
  development_actions: {
    open: number
    done: number
    earliest_planned_start: string | null
    estimated_effort_hours: number | null
    actions_with_estimated_effort: number
  }
  not_estimated: string[]
}

export type DirectRoute = {
  kind: 'direct'
  role_instance_id: string
  title: string
  organisation: string | null
  archetype: { id: string; name: string } | null
  state:
    | 'structurally_evidenced'
    | 'blocking_gaps'
    | 'unverified_gaps'
    | 'review_incomplete'
    | 'mapping_incomplete'
    | 'insufficient_evidence'
    | 'no_required_requirements'
  state_reason: string
  fit: {
    evidenced_requirements: number
    requirements_total: number
    evidence_coverage: number | null
    blocking_required_gaps: string[]
    unverified_required_gaps: string[]
    review_complete: boolean
    // The canonical review gate is complete only when unreviewed claims,
    // unresolved vocabulary proposals and concepts needing re-extraction are
    // all zero. `review_blockers` names whichever are outstanding.
    review_blockers: ReviewBlocker[]
    unreviewed_requirement_claims: number
    unresolved_vocabulary_proposals: number
    concepts_needing_reextraction: number
    mapping_complete: boolean
    unmapped_requirements: number
  }
  compensation: ResolvedCompensation
  personal_comparison: PersonalComparison
  transition: PathwayTransition
}

export type ReviewBlocker = { kind: string; label: string; count: number }

export type SupportingPosting = {
  id: string
  title: string
  organisation: string | null
  posting_date: string | null
  career_track: string | null
  assessment: string
  explanation: string
  evidenced_requirements: number
  requirements_total: number
  evidence_coverage: number | null
  target_gaps_addressed: string[]
  missing_required: string[]
  unverified_required: string[]
  pending_requirements: number
  review_complete: boolean
  review_blockers: ReviewBlocker[]
  similarity_to_target: number | null
  similarity_to_profile: number | null
}

export type IntermediateArchetypeRoute = {
  kind: 'intermediate_archetype'
  archetype_concept_id: string
  archetype_name: string
  seniority_band: string | null
  typical_market: string | null
  state: 'useful_intermediate' | 'route_without_compensation' | 'no_target_progress'
  state_reason: string
  supporting_posting_ids: string[]
  supporting_posting_count: number
  supporting_postings: SupportingPosting[]
  fit: {
    best_evidence_coverage: number | null
    blocking_required_gaps: string[]
    unverified_required_gaps: string[]
    unreviewed_requirement_claims: number
    review_complete: boolean
    review_blockers: ReviewBlocker[]
  }
  target_gaps_addressed: string[]
  compensation: ResolvedCompensation
  personal_comparison: PersonalComparison
  transition: PathwayTransition
  day_in_the_life_available: boolean
  evidence_quality: EvidenceQuality
}

export type GapValueItem = {
  concept_id: string
  canonical_name: string
  type_code: string
  evidence_status: string | null
  target_relevance: {
    required_by_target: boolean
    requirement_type: string | null
    addresses_target_gaps: number
    current_evidence_status: string | null
    intermediate_archetypes_involving_it: { archetype_concept_id: string; archetype_name: string }[]
  }
  market_option_value:
    | { available: false; reason: string; withheld_as_stale?: boolean }
    | {
        available: true
        archetypes_unlocked: number
        archetypes_improved: number
        roles_unlocked: number
        roles_improved: number
        highest_qualifying_reference_compensation: number | null
        currency: string
        delta_vs_best_currently_reachable: number | null
        n_compensation_observations: number
        rank: number | null
        as_of: string | null
        scope_note: string
      }
  your_context: PersonalComparison | { comparable: false; reason: string }
  evidence_quality: EvidenceQuality
}

export type PathwaysGates = {
  target_requirements_reviewed: boolean
  target_mapping_complete: boolean
  target_has_requirements: boolean
  target_archetype_assigned: boolean
  compensation_context_available: boolean
  target_compensation_available: boolean
  personal_earnings_available: boolean
  // Scoped to the candidates that actually support a route shown here, not
  // every posting in the corpus.
  candidate_review_complete: boolean
  derived_economics_fresh: boolean
}

export type PathwaysResult = {
  target: {
    id: string
    title: string
    organisation: string | null
    instance_type: string
    archetype: { id: string; name: string; status: string } | null
  }
  market_context: {
    market_id: string | null
    market_label: string | null
    market_code: string | null
    currency: string | null
    selected_by: 'explicit' | 'target_archetype_benchmark' | 'most_evidenced_context' | 'none_available'
  }
  available_market_contexts: MarketContext[]
  personal_earnings: PersonalEarningsState
  direct_route: DirectRoute
  intermediate_archetypes: IntermediateArchetypeRoute[]
  unclassified_supporting_postings: {
    id: string
    title: string
    organisation: string | null
    target_gaps_addressed: string[]
  }[]
  gap_value: GapValueItem[]
  economics_freshness: EconomicsFreshness
  gates: PathwaysGates
  incomplete: string[]
  review_blockers: { target: ReviewBlocker[]; supporting_candidates: ReviewBlocker[] }
  candidates_assessed: number
  distinct_concepts: number
  method: {
    route_depth: string
    route_depth_limitation: string
    intermediate_basis: string
    ranking: string
    compensation: string
    not_a_prediction: string
  }
  metrics: {
    cache_hit: boolean
    candidates: number
    archetypes_assessed: number
    distinct_concepts: number
    concepts_evaluated: number
    elapsed_ms: number
  }
}

export const api = {
  previewTarget: (payload: { title: string; organisation?: string | null; is_imagined: boolean; description: string; supporting_material: string }) =>
    req<{ status: 'ok' | 'failed'; proposal: TargetDraft | null; error: string | null; extraction_run_id: string }>('/targets/preview', { method: 'POST', body: JSON.stringify(payload) }),
  listDevelopmentActions: (roleId: string) => req<DevelopmentAction[]>(`/comparison/role/${roleId}/actions`),
  createDevelopmentAction: (
    roleId: string,
    payload: {
      concept_id: string; title: string; note: string; due_date: string | null
      planned_start_date?: string | null; estimated_effort_hours?: number | null
    },
  ) =>
    req<DevelopmentAction>(`/comparison/role/${roleId}/actions`, { method: 'POST', body: JSON.stringify(payload) }),
  updateDevelopmentAction: (roleId: string, actionId: string, status: 'open' | 'done') =>
    req<DevelopmentAction>(`/comparison/role/${roleId}/actions/${actionId}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  deleteDevelopmentAction: (roleId: string, actionId: string) =>
    req<{ status: string }>(`/comparison/role/${roleId}/actions/${actionId}`, { method: 'DELETE' }),
  login,
  logout,
  authStatus,
  setUnauthorizedHandler,
  listRoles: (
    params: {
      career_track?: string
      concept_id?: string
      min_similarity?: number
      sort?: string
      period?: 'recent' | 'all' | 'unknown_date'
      year?: number
      date_from?: string
      date_to?: string
      limit?: number
      offset?: number
    } = {},
  ) => {
    const qs = new URLSearchParams()
    if (params.career_track) qs.set('career_track', params.career_track)
    if (params.concept_id !== undefined) qs.set('concept_id', params.concept_id)
    if (params.min_similarity !== undefined) qs.set('min_similarity', String(params.min_similarity))
    if (params.sort) qs.set('sort', params.sort)
    if (params.period) qs.set('period', params.period)
    if (params.year !== undefined) qs.set('year', String(params.year))
    if (params.date_from) qs.set('date_from', params.date_from)
    if (params.date_to) qs.set('date_to', params.date_to)
    if (params.limit !== undefined) qs.set('limit', String(params.limit))
    if (params.offset !== undefined) qs.set('offset', String(params.offset))
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<RoleListResponse>(`/roles${suffix}`)
  },
  getRole: (id: string) => req<Role>(`/roles/${id}`),

  // --- Economic pathways, compensation and archetype review ----------------
  //
  // Every call below is an explicit user action. The GETs never trigger AI
  // generation; the two `propose` POSTs are the only ones that call a model,
  // and neither of them writes anything — they return a proposal for review.
  getRoleCompensation: (id: string, params: { market_id?: string; currency?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.market_id) qs.set('market_id', params.market_id)
    if (params.currency) qs.set('currency', params.currency)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<RoleCompensationResponse>(`/role-instances/${id}/compensation${suffix}`)
  },
  proposeRoleCompensation: (id: string) =>
    req<CompensationProposalResult>(`/role-instances/${id}/compensation/propose`, { method: 'POST' }),
  acceptRoleCompensation: (id: string, payload: CompensationAcceptInput) =>
    req<CompensationAcceptResult>(`/role-instances/${id}/compensation/accept`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  // Correcting, retiring or restoring an *accepted* posting-stated
  // observation goes through these role-aware endpoints, not the generic
  // market-data review/PATCH pair. The generic ones flip a status or set
  // columns without re-reading the source document, which would let a
  // re-accept recreate two accepted base salaries and let a correction
  // detach a stated fact from the quote justifying it. These enforce the
  // same validation and supersession as initial acceptance.
  correctRoleCompensation: (roleId: string, observationId: string, payload: Partial<CompensationAcceptInput>) =>
    req<CompensationAcceptResult>(`/role-instances/${roleId}/compensation/${observationId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  rejectRoleCompensation: (roleId: string, observationId: string) =>
    req<CompensationAcceptResult>(`/role-instances/${roleId}/compensation/${observationId}/reject`, {
      method: 'POST',
    }),
  reacceptRoleCompensation: (roleId: string, observationId: string) =>
    req<CompensationAcceptResult>(`/role-instances/${roleId}/compensation/${observationId}/reaccept`, {
      method: 'POST',
    }),
  getRoleArchetype: (id: string) => req<RoleArchetypeSummary>(`/role-instances/${id}/archetype`),
  getArchetypeCatalogue: () => req<ArchetypeCatalogueEntry[]>('/role-instances/archetype-catalogue'),
  proposeRoleArchetype: (id: string) =>
    req<ArchetypeProposalResult>(`/role-instances/${id}/archetype/propose`, { method: 'POST' }),
  setRoleArchetype: (id: string, archetype_concept_id: string | null) =>
    req<{ role_instance_id: string; archetype_concept_id: string | null; archetype_name: string | null; status: string }>(
      `/role-instances/${id}/archetype`,
      { method: 'PUT', body: JSON.stringify({ archetype_concept_id }) },
    ),
  getArchetypeContext: (id: string) => req<ArchetypeContextResponse>(`/archetypes/${id}/context`),
  generateArchetypeContext: (id: string) =>
    req<ArchetypeContextGenerateResult>(`/archetypes/${id}/context/generate`, { method: 'POST' }),
  regenerateArchetypeContext: (id: string) =>
    req<ArchetypeContextGenerateResult>(`/archetypes/${id}/context/regenerate`, { method: 'POST' }),
  getArchetypeCompensation: (id: string, params: { market_id?: string; currency?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.market_id) qs.set('market_id', params.market_id)
    if (params.currency) qs.set('currency', params.currency)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<ResolvedCompensation>(`/archetypes/${id}/compensation${suffix}`)
  },
  getPathways: (targetId: string, params: { market_id?: string; currency?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.market_id) qs.set('market_id', params.market_id)
    if (params.currency) qs.set('currency', params.currency)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<PathwaysResult>(`/pathways/${targetId}${suffix}`)
  },
  getPathwaysMarketContexts: () => req<MarketContext[]>('/pathways/market-contexts'),
  getPersonalEarnings: () => req<PersonalEarningsState>('/pathways/personal-earnings'),
  getPlanningAssumptions: () => req<PlanningAssumption>('/pathways/planning-assumptions'),
  savePlanningAssumptions: (payload: { contract_billable_days_per_year: number | null; note?: string | null }) =>
    req<PlanningAssumption>('/pathways/planning-assumptions', { method: 'PUT', body: JSON.stringify(payload) }),
  getRoleContext: (id: string) => req<RoleContextResponse>(`/roles/${id}/context`),
  generateRoleContext: (id: string) => req<RoleContextGenerateResult>(`/roles/${id}/context/generate`, { method: 'POST' }),
  regenerateRoleContext: (id: string) => req<RoleContextGenerateResult>(`/roles/${id}/context/regenerate`, { method: 'POST' }),
  updateRole: (id: string, payload: unknown) =>
    req<{ id: string; status: string }>(`/roles/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteRole: (id: string) => req<{ status: string }>(`/roles/${id}`, { method: 'DELETE' }),
  importPosting: (payload: unknown) =>
    req<{ id: string; status: string }>('/import', { method: 'POST', body: JSON.stringify(payload) }),
  importPostingNative: (payload: { text: string; source_url?: string | null; known_posting_date?: string | null }) =>
    req<{
      id: string
      status: string
      extraction: unknown
      run: { task: string; model: string; prompt_name: string; prompt_version: string; status: string }
    }>('/import/native', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  importBulk: (files: File[]) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    return fetch('/api/import/bulk', { method: 'POST', body: form }).then((r) => r.json())
  },
  // profile360 is authoritative and read-only from here (docs/14 §9) — there
  // is no updateProfile anymore; the narrative is authored in profile360's
  // own tool.
  getProfile: () => req<Profile>('/profile'),
  getProfileHistory: () => req<Profile360Row[]>('/profile/history'),
  getSpace: (filter: SpaceFilter = {}) => {
    const qs = new URLSearchParams()
    if (filter.year !== undefined) qs.set('year', String(filter.year))
    if (filter.date_from) qs.set('date_from', filter.date_from)
    if (filter.date_to) qs.set('date_to', filter.date_to)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<SpaceResponse>(`/space${suffix}`)
  },
  rebuildRoleEmbeddings: (force = false) =>
    req<RebuildEmbeddingsSummary>(`/space/rebuild-role-embeddings${force ? '?force=true' : ''}`, { method: 'POST' }),
  listTargets: () => req<Role[]>('/targets'),
  importTarget: (payload: unknown) =>
    req<{ id: string; status: string }>('/targets', { method: 'POST', body: JSON.stringify(payload) }),
  updateTarget: (id: string, payload: unknown) =>
    req<{ id: string; status: string }>(`/targets/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  // Episodes are also read-only from profile360 now — no create/update/
  // delete/timeline (docs/14 §9); see Episodes.tsx.
  listEpisodes: () => req<Profile360Row[]>('/episodes'),
  listConceptTypes: () => req<ConceptType[]>('/concepts/types'),
  listConcepts: (params: { type_code?: string; status?: string; q?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.type_code) qs.set('type_code', params.type_code)
    if (params.status) qs.set('status', params.status)
    if (params.q) qs.set('q', params.q)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<Concept[]>(`/concepts${suffix}`)
  },
  resolveTargetRequirements: (skills: TargetDraft['skills']) =>
    req<TargetRequirementMapping[]>('/targets/resolve-requirements', { method: 'POST', body: JSON.stringify(skills) }),
  createConcept: (payload: ConceptInput) =>
    req<{ id: string; status: string }>('/concepts', { method: 'POST', body: JSON.stringify(payload) }),
  getConcept: (id: string) => req<Concept>(`/concepts/${id}`),
  getFacets: (type_code: string) => req<Facet[]>(`/concepts/facets?type_code=${encodeURIComponent(type_code)}`),

  // --- Accepted-vocabulary maintenance (Concept Details drawer) ------------
  updateConcept: (id: string, payload: ConceptMetadataUpdateInput) =>
    req<Concept>(`/concepts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  addConceptAlias: (id: string, alias: string) =>
    req<{ id: string; alias: string; status: string }>(`/concepts/${id}/aliases`, {
      method: 'POST',
      body: JSON.stringify({ alias }),
    }),
  removeConceptAlias: (id: string, aliasId: string) =>
    req<{ status: string }>(`/concepts/${id}/aliases/${aliasId}`, { method: 'DELETE' }),

  // --- Concept Dossier -------------------------------------------------------
  getConceptDossier: (id: string) => req<ConceptDossierResponse>(`/concepts/${id}/dossier`),
  getConceptDossierHistory: (id: string) => req<ConceptDossier[]>(`/concepts/${id}/dossier/history`),
  generateConceptDossier: (id: string, guidance?: string) =>
    req<ConceptDossierGenerateResult>(`/concepts/${id}/dossier/generate`, {
      method: 'POST',
      body: JSON.stringify({ guidance: guidance || undefined }),
    }),
  regenerateConceptDossier: (id: string, guidance?: string) =>
    req<ConceptDossierGenerateResult>(`/concepts/${id}/dossier/regenerate`, {
      method: 'POST',
      body: JSON.stringify({ guidance: guidance || undefined }),
    }),
  adoptConceptDossierDraft: (id: string) =>
    req<ConceptDossierActionResult>(`/concepts/${id}/dossier/adopt`, { method: 'POST' }),
  discardConceptDossierDraft: (id: string) =>
    req<{ status: string }>(`/concepts/${id}/dossier/discard`, { method: 'POST' }),
  saveConceptDossierEdit: (id: string, payload: ConceptDossierManualEditInput) =>
    req<ConceptDossierActionResult>(`/concepts/${id}/dossier`, { method: 'PUT', body: JSON.stringify(payload) }),
  listProposals: (status = 'pending') =>
    req<ProposalGroup[]>(`/concepts/proposals?status=${encodeURIComponent(status)}`),
  getProposalStats: () => req<ProposalStats>('/concepts/proposals/stats'),
  resolveProposal: (payload: ProposalResolveInput) =>
    req<{ surface_form: string; status: string; resolved_concept_id: string | null }>(
      '/concepts/proposals/resolve',
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  resolveProposalCluster: (payload: ClusterProposalResolveInput) =>
    req<{ cluster_key: string; surface_forms: string[]; status: string; resolved_concept_id: string | null }>(
      '/concepts/proposals/resolve-cluster',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  // --- Vocabulary curation (prioritised cluster review queue) ---------------
  listVocabClusters: (filters: VocabClusterFilters = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<VocabClusterListResponse>(`/vocabulary/clusters${suffix}`)
  },
  getVocabClusterDetail: (clusterKey: string) => req<VocabClusterSummary>(`/vocabulary/clusters/${encodeURIComponent(clusterKey)}`),
  getVocabProgress: () => req<VocabProgress>('/vocabulary/progress'),
  getVocabMethodology: () => req<VocabMethodology>('/vocabulary/methodology'),
  acceptVocabCluster: (payload: { cluster_key: string; type_code: string; canonical_name: string; definition?: string }) =>
    req<ClusterActionResult>('/vocabulary/clusters/accept', { method: 'POST', body: JSON.stringify(payload) }),
  rejectVocabCluster: (payload: { cluster_key: string }) =>
    req<ClusterActionResult>('/vocabulary/clusters/reject', { method: 'POST', body: JSON.stringify(payload) }),
  mergeVocabCluster: (payload: { cluster_key: string; concept_id: string }) =>
    req<ClusterActionResult>('/vocabulary/clusters/merge', { method: 'POST', body: JSON.stringify(payload) }),
  previewVocabBatch: (payload: { action: 'accept' | 'reject'; items: BatchAcceptItemInput[] }) =>
    req<BatchPreviewResult>('/vocabulary/clusters/batch/preview', { method: 'POST', body: JSON.stringify(payload) }),
  executeVocabBatch: (payload: { action: 'accept' | 'reject'; items: BatchAcceptItemInput[] }) =>
    req<BatchExecuteResult>('/vocabulary/clusters/batch', { method: 'POST', body: JSON.stringify(payload) }),
  previewVocabSplit: (payload: { cluster_key: string; groups: ClusterSplitGroupInput[] }) =>
    req<ClusterSplitPreviewResult>('/vocabulary/clusters/split/preview', { method: 'POST', body: JSON.stringify(payload) }),
  splitVocabCluster: (payload: { cluster_key: string; groups: ClusterSplitGroupInput[] }) =>
    req<ClusterSplitResult>('/vocabulary/clusters/split', { method: 'POST', body: JSON.stringify(payload) }),

  // --- Vocabulary Map (read-only graph projection) --------------------------
  getVocabularyGraph: (filters: VocabularyGraphFilters = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<VocabularyGraphResponse>(`/vocabulary/graph${suffix}`)
  },
  getVocabularySimilarityFocus: (focus: string, opts: { similarity_limit?: number; include_ontology?: boolean } = {}) => {
    const qs = new URLSearchParams({ focus })
    if (opts.similarity_limit !== undefined) qs.set('similarity_limit', String(opts.similarity_limit))
    if (opts.include_ontology !== undefined) qs.set('include_ontology', String(opts.include_ontology))
    return req<VocabularyGraphResponse>(`/vocabulary/graph?${qs}`)
  },
  getSurfaceFormDetail: (value: string) => req<SurfaceFormDetail>(`/vocabulary/surface-form?value=${encodeURIComponent(value)}`),

  // --- Phase 2: source-aware ingestion + requirement claims -----------------
  ingestText: (payload: {
    text: string
    kind?: string
    title?: string | null
    organisation?: string | null
    location?: string | null
    country?: string | null
    posting_date?: string | null
    source_url?: string | null
    source?: string | null
  }) => req<IngestResult>('/role-instances/ingest', { method: 'POST', body: JSON.stringify(payload) }),
  ingestPdf: (
    file: File,
    params: {
      kind?: string
      title?: string | null
      organisation?: string | null
      location?: string | null
      country?: string | null
      posting_date?: string | null
      source_url?: string | null
    } = {},
  ) => {
    const form = new FormData()
    form.append('file', file)
    const qs = new URLSearchParams()
    if (params.kind) qs.set('kind', params.kind)
    if (params.title) qs.set('title', params.title)
    if (params.organisation) qs.set('organisation', params.organisation)
    if (params.location) qs.set('location', params.location)
    if (params.country) qs.set('country', params.country)
    if (params.posting_date) qs.set('posting_date', params.posting_date)
    if (params.source_url) qs.set('source_url', params.source_url)
    const suffix = qs.toString() ? `?${qs}` : ''
    return fetch(`/api/role-instances/ingest/pdf${suffix}`, { method: 'POST', body: form }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`)
      return r.json() as Promise<IngestResult>
    })
  },
  // Text-only PDF extraction (no persistence) — the first half of the PDF
  // duplicate-check preflight: extract here, then checkDuplicate with the
  // returned text, before ever calling ingestPdf/ingestText for real.
  extractPdfText: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch('/api/role-instances/pdf/extract-text', { method: 'POST', body: form }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`)
      return (await r.json()) as { text: string }
    })
  },
  checkDuplicate: (text: string, kind?: string) =>
    req<DuplicateCheckResult>('/role-instances/duplicate-check', { method: 'POST', body: JSON.stringify({ text, kind }) }),
  extractRequirements: (roleId: string) =>
    req<ExtractionSummary>(`/role-instances/${roleId}/extract-requirements`, { method: 'POST' }),
  listRequirements: (roleId: string, opts: { history?: boolean } = {}) =>
    req<RequirementClaimList>(`/role-instances/${roleId}/requirements${opts.history ? '?history=true' : ''}`),
  acceptRequirement: (roleId: string, claimId: string) =>
    req<RequirementClaim>(`/role-instances/${roleId}/requirements/${claimId}/accept`, { method: 'POST' }),
  rejectRequirement: (roleId: string, claimId: string) =>
    req<RequirementClaim>(`/role-instances/${roleId}/requirements/${claimId}/reject`, { method: 'POST' }),
  reopenRequirement: (roleId: string, claimId: string) =>
    req<RequirementClaim>(`/role-instances/${roleId}/requirements/${claimId}/reopen`, { method: 'POST' }),
  editRequirement: (roleId: string, claimId: string, payload: RequirementClaimEditInput) =>
    req<RequirementClaim>(`/role-instances/${roleId}/requirements/${claimId}/edit`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  addRequirement: (roleId: string, payload: RequirementClaimCreateInput) =>
    req<RequirementClaim>(`/role-instances/${roleId}/requirements`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // --- Source-aware metadata: manual Edit + reviewable AI enrichment --------
  proposeRoleMetadata: (roleId: string) =>
    req<MetadataProposalResult>(`/role-instances/${roleId}/metadata/propose`, { method: 'POST' }),
  updateRoleMetadata: (roleId: string, payload: RoleMetadataInput) =>
    req<Role>(`/role-instances/${roleId}/metadata`, { method: 'PATCH', body: JSON.stringify(payload) }),

  // --- Phase 2: profile360 mapping review -----------------------------------
  listProfile360Claims: (limit = 50, offset = 0) =>
    req<Profile360Row[]>(`/profile360/claims?limit=${limit}&offset=${offset}`),
  listProfile360Capabilities: (limit = 50, offset = 0) =>
    req<Profile360Row[]>(`/profile360/capabilities?limit=${limit}&offset=${offset}`),
  mapProfile360Claim: (claimId: string) =>
    req<MappingAttemptResult>(`/profile360/claims/${encodeURIComponent(claimId)}/map`, { method: 'POST' }),
  mapProfile360Capability: (capabilityId: string) =>
    req<MappingAttemptResult>(`/profile360/capabilities/${encodeURIComponent(capabilityId)}/map`, { method: 'POST' }),
  listProfile360Mappings: (kind: 'claim' | 'capability', reviewStatus?: MappingReviewStatus) => {
    const qs = new URLSearchParams({ kind })
    if (reviewStatus) qs.set('review_status', reviewStatus)
    return req<Profile360Mapping[]>(`/profile360/mappings?${qs}`)
  },
  reviewProfile360Mapping: (mappingId: string, kind: 'claim' | 'capability', action: 'accept' | 'reject') =>
    req<{ id: string; review_status: string }>(`/profile360/mappings/${mappingId}/review`, {
      method: 'POST',
      body: JSON.stringify({ kind, action }),
    }),

  // --- Phase 2: comparison ---------------------------------------------------
  compareRole: (roleId: string) => req<ComparisonResult>(`/comparison/role/${roleId}`),
  assertCapability: (concept_id: string, note?: string) =>
    req<{ id: string; status: string }>('/comparison/assert', { method: 'POST', body: JSON.stringify({ concept_id, note }) }),
  retractAssertion: (conceptId: string) =>
    req<{ status: string }>(`/comparison/assert/${conceptId}`, { method: 'DELETE' }),
  promoteAssertion: (conceptId: string) =>
    req<{ status: string; profile360_manual_import_source_key: string }>(`/comparison/assert/${conceptId}/promote`, { method: 'POST' }),

  // --- Phase 2: preferences ---------------------------------------------------
  listPreferenceDimensions: () => req<PreferenceDimension[]>('/preferences/dimensions'),
  listPreferenceObservations: (dimensionCode?: string) =>
    req<PreferenceObservation[]>(`/preferences${dimensionCode ? `?dimension_code=${encodeURIComponent(dimensionCode)}` : ''}`),
  createPreferenceObservation: (payload: PreferenceObservationInput) =>
    req<{ id: string; status: string }>('/preferences', { method: 'POST', body: JSON.stringify(payload) }),

  // --- Phase 3: capability catalogue + coverage -------------------------------
  listCapabilities: (params: { status?: string; q?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.status) qs.set('status', params.status)
    if (params.q) qs.set('q', params.q)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<CapabilitySummary[]>(`/capabilities${suffix}`)
  },
  listUnconfiguredCapabilities: (q?: string) =>
    req<UnconfiguredCapability[]>(`/capabilities/unconfigured${q ? `?${new URLSearchParams({ q })}` : ''}`),
  configureCapability: (id: string, payload: CapabilitySpecificationInput) =>
    req<{ id: string; status: string }>(`/capabilities/${id}/configure`, { method: 'POST', body: JSON.stringify(payload) }),
  listCapabilityCoverage: () => req<CapabilityCoverage[]>('/capabilities/coverage'),
  createCapability: (payload: CapabilityInput) =>
    req<{ id: string; status: string }>('/capabilities', { method: 'POST', body: JSON.stringify(payload) }),
  getCapability: (id: string) => req<Capability>(`/capabilities/${id}`),
  updateCapability: (id: string, payload: Partial<CapabilityInput>) =>
    req<{ id: string; status: string }>(`/capabilities/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  getCapabilityCoverage: (id: string) => req<CapabilityCoverage>(`/capabilities/${id}/coverage`),
  addComponent: (capabilityId: string, payload: { concept_id: string; necessity: Necessity }) =>
    req<{ id: string; status: string }>(`/capabilities/${capabilityId}/components`, { method: 'POST', body: JSON.stringify(payload) }),
  updateComponent: (capabilityId: string, edgeId: string, necessity: Necessity) =>
    req<{ id: string; status: string }>(`/capabilities/${capabilityId}/components/${edgeId}`, {
      method: 'PUT',
      body: JSON.stringify({ necessity }),
    }),
  removeComponent: (capabilityId: string, edgeId: string) =>
    req<{ status: string }>(`/capabilities/${capabilityId}/components/${edgeId}`, { method: 'DELETE' }),
  reviewComponent: (capabilityId: string, edgeId: string, action: 'accept' | 'reject') =>
    req<{ id: string; status: string }>(`/capabilities/${capabilityId}/components/${edgeId}/review`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  mergeCapability: (capabilityId: string, mergeIntoId: string) =>
    req<{ id: string; status: string; merged_into: string }>(`/capabilities/${capabilityId}/merge`, {
      method: 'POST',
      body: JSON.stringify({ merge_into_id: mergeIntoId }),
    }),
  rebuildCapabilities: () => req<RebuildSummary>('/capabilities/rebuild', { method: 'POST' }),

  mapClaimToCapability: (claimId: string) =>
    req<MappingAttemptResult>(`/profile360/claims/${encodeURIComponent(claimId)}/map-capability`, { method: 'POST' }),
  runPassC: (limit = 25) =>
    req<{ status: string; attempted: number; mapped: number; failed: number }>(`/profile360/pass-c/run?limit=${limit}`, {
      method: 'POST',
    }),

  getEvalReport: (split?: 'dev' | 'test') => req<EvalReport>(`/eval/report${split ? `?split=${split}` : ''}`),

  // --- Trends (docs/18 §7/§8/§9) ---------------------------------------------
  getTrendOverview: (filters: TrendFilterInput = {}) => req<CorpusOverview>(`/trends/overview${trendQuery(filters)}`),
  getTopRequirements: (filters: TrendFilterInput = {}, opts: { min_sample_size?: number; limit?: number } = {}) =>
    req<TopRequirements>(`/trends/top-requirements${trendQuery(filters, opts)}`),
  getRequirementTrend: (key: RequirementKey, filters: TrendFilterInput = {}, granularity: 'year' | '5year' = 'year') =>
    req<RequirementTrend>(`/trends/requirement-trend${trendQuery(filters, { ...requirementKeyParams(key), granularity })}`),
  getCooccurrence: (key: RequirementKey, filters: TrendFilterInput = {}) =>
    req<Cooccurrence>(`/trends/cooccurrence${trendQuery(filters, requirementKeyParams(key))}`),
  compareDimension: (
    key: RequirementKey,
    dimension: 'country' | 'seniority_level' | 'career_track',
    filters: TrendFilterInput = {},
  ) => req<DimensionCompare>(`/trends/compare${trendQuery(filters, { ...requirementKeyParams(key), dimension })}`),
  getTrendMethodology: () => req<TrendMethodology>('/trends/methodology'),

  // --- Phase 4: role archetypes -----------------------------------------------
  listArchetypeTitleGroups: () => req<TitleGroup[]>('/archetypes/title-groups'),
  listArchetypes: (status = 'active') => req<Archetype[]>(`/archetypes?status=${encodeURIComponent(status)}`),
  getArchetype: (id: string) => req<ArchetypeDetail>(`/archetypes/${id}`),
  createArchetype: (payload: ArchetypeCreateInput) =>
    req<{ id: string; status: string; assigned_count: number }>('/archetypes', { method: 'POST', body: JSON.stringify(payload) }),
  updateArchetype: (id: string, payload: ArchetypeUpdateInput) =>
    req<{ id: string; status: string }>(`/archetypes/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  assignRolesToArchetype: (id: string, roleInstanceIds: string[]) =>
    req<{ id: string; status: string; assigned_count: number }>(`/archetypes/${id}/assign`, {
      method: 'POST',
      body: JSON.stringify({ role_instance_ids: roleInstanceIds }),
    }),

  // --- Phase 4: markets + compensation observations ---------------------------
  listMarkets: (status = 'active') => req<Market[]>(`/economics/markets?status=${encodeURIComponent(status)}`),
  createMarket: (payload: { code: string; label: string; country?: string; geography?: string; domain_concept_id?: string; notes?: string }) =>
    req<{ id: string; status: string }>('/economics/markets', { method: 'POST', body: JSON.stringify(payload) }),
  updateMarket: (id: string, payload: { label?: string; country?: string; geography?: string; status?: 'active' | 'deprecated'; notes?: string }) =>
    req<{ id: string; status: string }>(`/economics/markets/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  listCompensationObservations: (
    params: { basis?: string; review_status?: string; archetype_concept_id?: string; market_id?: string } = {},
  ) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<CompensationObservation[]>(`/economics/compensation-observations${suffix}`)
  },
  createCompensationObservation: (payload: CompensationObservationCreateInput) =>
    req<{ id: string; status: string }>('/economics/compensation-observations', { method: 'POST', body: JSON.stringify(payload) }),
  backfillCompensation: () => req<BackfillSummary>('/economics/compensation-observations/backfill', { method: 'POST' }),

  // --- Phase 4: derived economics tables --------------------------------------
  getArchetypeDemand: (archetypeId: string) => req<ArchetypeDemandRow[]>(`/economics/archetype-demand/${archetypeId}`),
  listArchetypeComp: (params: { archetype_concept_id?: string; market_id?: string; currency?: string } = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<ArchetypeCompRow[]>(`/economics/archetype-comp${suffix}`)
  },
  listGapValueContexts: () => req<GapValueContext[]>('/economics/gap-value/contexts'),
  listGapValue: (marketId: string, currency: string) =>
    req<GapValueRow[]>(`/economics/gap-value?market_id=${encodeURIComponent(marketId)}&currency=${encodeURIComponent(currency)}`),
  getGapValue: (capabilityId: string, marketId: string, currency: string) =>
    req<GapValueRow>(
      `/economics/gap-value/${capabilityId}?market_id=${encodeURIComponent(marketId)}&currency=${encodeURIComponent(currency)}`,
    ),
  rebuildEconomics: () => req<Phase4RebuildSummary>('/economics/rebuild', { method: 'POST' }),
  getPhase4Readiness: () => req<Phase4Readiness>('/economics/readiness'),

  // --- Phase 4: market survey documents (Market Data) -------------------------
  listMarketDataDocuments: () => req<MarketSurveyDocument[]>('/market-data/documents'),
  getMarketDataDocument: (id: string) => req<MarketSurveyDocumentDetail>(`/market-data/documents/${id}`),
  ingestMarketDataText: (payload: MarketDataIngestInput) =>
    req<{ id: string; duplicate_of_document_id: string | null; status: string }>('/market-data/documents/ingest', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  ingestMarketDataPdf: (file: File, meta: Omit<MarketDataIngestInput, 'text'> = {}) => {
    const form = new FormData()
    form.append('file', file)
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(meta)) if (v) qs.set(k, v)
    const suffix = qs.toString() ? `?${qs}` : ''
    return fetch(`/api/market-data/documents/ingest/pdf${suffix}`, { method: 'POST', body: form }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`)
      return r.json() as Promise<{ id: string; duplicate_of_document_id: string | null; status: string }>
    })
  },
  extractMarketData: (documentId: string) => req<ExtractionResult>(`/market-data/documents/${documentId}/extract`, { method: 'POST' }),
  getMarketAnalyticsSummary: (filters: MarketAnalyticsFiltersInput = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<MarketAnalyticsSummary>(`/market-data/analytics/summary${suffix}`)
  },
  listDraftCompensationObservations: (reviewStatus: 'unreviewed' | 'accepted' | 'rejected' = 'unreviewed') =>
    req<CompensationObservation[]>(`/market-data/compensation-observations?review_status=${reviewStatus}`),
  reviewCompensationObservation: (id: string, action: 'accept' | 'reject') =>
    req<{ id: string; review_status: string }>(`/market-data/compensation-observations/${id}/review`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  correctCompensationObservation: (
    id: string,
    payload: Partial<{
      archetype_concept_id: string
      market_id: string
      raw_role_label: string
      component: string
      pay_period: string
      employment_basis: string
      amount_min: number
      amount_mid: number
      amount_max: number
      currency: string
      reported_p25: number
      reported_p50: number
      reported_p75: number
      bonus_pct: number
      reported_sample_size: number
      source_note: string
      observed_at: string
      period_end: string
    }>,
  ) => req<{ id: string; status: string }>(`/market-data/compensation-observations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  // --- Phase 4: accepted vocabulary overview -----------------------------------
  getAcceptedVocabularyOverview: () => req<AcceptedVocabularyOverview>('/vocabulary/accepted-overview'),
}
