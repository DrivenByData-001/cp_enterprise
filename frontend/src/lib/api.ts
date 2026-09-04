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
  similarity_to_target: number
}

export type TargetPath = {
  profile_to_target_similarity: number | null
  stepping_stones: SteppingStone[]
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
  remote_type: string | null
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
  skills?: RoleSkill[]
  url: string | null
  raw_json?: unknown
  typical_tasks?: string[] | null
  skill_decomposition?: SkillDecompositionItem[] | null
  technical_subjects?: TechnicalSubjectItem[] | null
  grounding_note?: string | null
  feasibility_note?: string | null
  is_plausible?: boolean | null
  path?: TargetPath
}

export type YearRange = { min: number; max: number } | null

export type RoleListResponse = {
  items: Role[]
  total: number
  limit: number
  offset: number
  period: 'recent' | 'all' | 'year' | 'range'
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

export type RequirementClaim = {
  id: string
  requirement_type: 'required' | 'preferred' | 'contextual'
  importance: number | null
  basis: 'stated' | 'implied' | 'inferred' | 'user_asserted'
  evidence_span: string | null
  review_status: 'unreviewed' | 'accepted' | 'rejected' | 'corrected'
  created_at: string
  extraction_run_id: string | null
  concept_id: string
  canonical_name: string
  type_code: string
  document_id: string | null
  document_title: string | null
  document_provenance: string | null
}

export type ExtractionSummary = {
  status: 'ok' | 'partial' | 'failed'
  extraction_run_id: string
  adjudication_run_id?: string | null
  claims_created?: number
  proposals_created?: number
  proposals_updated?: number
  rejected_span_count?: number
  error?: string
}

export type IngestResult = { id: string; document_id: string; duplicate_of_document_id: string | null; status: string }

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
    requirement_claim_id: string
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
  role: { id: string; title: string; kind: string }
  items: ComparisonItem[]
  counts: Record<ComparisonStatus, number>
  blocking_gaps: GapConcept[]
  unverified_required: (GapConcept & { status: ComparisonStatus })[]
  fit_score: number | null
  embedding_similarity: number | null
  engine_version: string
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

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json()
}

export const api = {
  listRoles: (
    params: {
      career_track?: string
      concept_id?: string
      min_similarity?: number
      sort?: string
      period?: 'recent' | 'all'
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
  createConcept: (payload: ConceptInput) =>
    req<{ id: string; status: string }>('/concepts', { method: 'POST', body: JSON.stringify(payload) }),
  getFacets: (type_code: string) => req<Facet[]>(`/concepts/facets?type_code=${encodeURIComponent(type_code)}`),
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

  // --- Phase 2: source-aware ingestion + requirement claims -----------------
  ingestText: (payload: { text: string; kind?: string; title?: string | null; organisation?: string | null; source_url?: string | null }) =>
    req<IngestResult>('/role-instances/ingest', { method: 'POST', body: JSON.stringify(payload) }),
  ingestPdf: (file: File, params: { kind?: string; title?: string | null; organisation?: string | null; source_url?: string | null } = {}) => {
    const form = new FormData()
    form.append('file', file)
    const qs = new URLSearchParams()
    if (params.kind) qs.set('kind', params.kind)
    if (params.title) qs.set('title', params.title)
    if (params.organisation) qs.set('organisation', params.organisation)
    if (params.source_url) qs.set('source_url', params.source_url)
    const suffix = qs.toString() ? `?${qs}` : ''
    return fetch(`/api/role-instances/ingest/pdf${suffix}`, { method: 'POST', body: form }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`)
      return r.json() as Promise<IngestResult>
    })
  },
  extractRequirements: (roleId: string) =>
    req<ExtractionSummary>(`/role-instances/${roleId}/extract-requirements`, { method: 'POST' }),
  listRequirements: (roleId: string) => req<RequirementClaim[]>(`/role-instances/${roleId}/requirements`),
  reviewRequirement: (roleId: string, claimId: string, action: 'accept' | 'reject') =>
    req<{ id: string; review_status: string }>(`/role-instances/${roleId}/requirements/${claimId}/review`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),

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
}
