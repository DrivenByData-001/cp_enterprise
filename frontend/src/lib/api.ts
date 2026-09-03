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
  x: number
  y: number
  z: number
}

export type SpaceResponse = {
  points: SpacePoint[]
  profile: { x: number; y: number; z: number } | null
  note?: string
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
  surface_form: string
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

export type ProposalStats = {
  pending_groups: number
  total_documents: number
  proposals_per_document: number | null
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
}

export type ComparisonStatus = 'evidenced' | 'partial' | 'user_asserted' | 'not_found'

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
  }
}

export type ComparisonResult = {
  role: { id: string; title: string; kind: string }
  items: ComparisonItem[]
  counts: Record<ComparisonStatus, number>
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
    params: { career_track?: string; concept_id?: string; min_similarity?: number; sort?: string } = {},
  ) => {
    const qs = new URLSearchParams()
    if (params.career_track) qs.set('career_track', params.career_track)
    if (params.concept_id !== undefined) qs.set('concept_id', params.concept_id)
    if (params.min_similarity !== undefined) qs.set('min_similarity', String(params.min_similarity))
    if (params.sort) qs.set('sort', params.sort)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<Role[]>(`/roles${suffix}`)
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
  getSpace: () => req<SpaceResponse>('/space'),
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
}
