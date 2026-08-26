export type RoleSkill = {
  name: string
  category: string | null
  importance: number | null
  requirement_type: string | null
  resolved_concept_id: number | null
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
  id: number
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
  id: number
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
  // target-only fields
  typical_tasks?: string[] | null
  skill_decomposition?: SkillDecompositionItem[] | null
  technical_subjects?: TechnicalSubjectItem[] | null
  grounding_note?: string | null
  feasibility_note?: string | null
  is_plausible?: boolean | null
  path?: TargetPath
}

export type Profile = {
  id: number
  narrative_text: string
  embedding_model: string | null
  created_at: string
} | null

export type SpacePoint = {
  id: number
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

export type EpisodeKind = 'employment' | 'project' | 'study' | 'qualification' | 'other'

export type Episode = {
  id: number
  person_id: number
  kind: EpisodeKind
  title: string
  organisation: string | null
  start_date: string | null
  end_date: string | null
  date_precision: 'day' | 'month' | 'year'
  parent_episode_id: number | null
  domain_hint: string | null
  context_note: string | null
  created_at: string
  duration_years: number | null
}

export type EpisodeInput = {
  kind: EpisodeKind
  title: string
  organisation?: string | null
  start_date?: string | null
  end_date?: string | null
  date_precision?: 'day' | 'month' | 'year'
  parent_episode_id?: number | null
  domain_hint?: string | null
  context_note?: string | null
}

export type Timeline = {
  episodes: Episode[]
  total_span_years: number
  earliest_start: string | null
  latest_end: string | null
}

export type ConceptType = {
  code: string
  label: string
  definition: string
  is_atom: number
  sort_order: number
}

export type Concept = {
  id: number
  type_code: string
  canonical_name: string
  definition: string | null
  status: string
  merged_into: number | null
  origin: string
  created_at: string
  reviewed_at: string | null
  aliases?: { id: number; alias: string; origin: string }[]
}

export type ConceptInput = {
  type_code: string
  canonical_name: string
  definition?: string | null
  status?: string
}

export type Facet = {
  id: number
  canonical_name: string
  role_count: number
}

export type ProposalGroup = {
  surface_form: string
  proposal_ids: number[]
  suggested_type: string | null
  nearest_concept_id: number | null
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
  concept_id?: number
}

export type ProposalStats = {
  pending_groups: number
  total_documents: number
  proposals_per_document: number | null
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
    params: { career_track?: string; concept_id?: number; min_similarity?: number; sort?: string } = {},
  ) => {
    const qs = new URLSearchParams()
    if (params.career_track) qs.set('career_track', params.career_track)
    if (params.concept_id !== undefined) qs.set('concept_id', String(params.concept_id))
    if (params.min_similarity !== undefined) qs.set('min_similarity', String(params.min_similarity))
    if (params.sort) qs.set('sort', params.sort)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<Role[]>(`/roles${suffix}`)
  },
  getRole: (id: number) => req<Role>(`/roles/${id}`),
  updateRole: (id: number, payload: unknown) =>
    req<{ id: number; status: string }>(`/roles/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteRole: (id: number) => req<{ status: string }>(`/roles/${id}`, { method: 'DELETE' }),
  importPosting: (payload: unknown) =>
    req<{ id: number; status: string }>('/import', { method: 'POST', body: JSON.stringify(payload) }),
  importBulk: (files: File[]) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    return fetch('/api/import/bulk', { method: 'POST', body: form }).then((r) => r.json())
  },
  getProfile: () => req<Profile>('/profile'),
  updateProfile: (narrative_text: string) =>
    req<{ id: number; status: string }>('/profile', { method: 'POST', body: JSON.stringify({ narrative_text }) }),
  getSpace: () => req<SpaceResponse>('/space'),
  listTargets: () => req<Role[]>('/targets'),
  importTarget: (payload: unknown) =>
    req<{ id: number; status: string }>('/targets', { method: 'POST', body: JSON.stringify(payload) }),
  updateTarget: (id: number, payload: unknown) =>
    req<{ id: number; status: string }>(`/targets/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  listEpisodes: () => req<Episode[]>('/episodes'),
  getTimeline: () => req<Timeline>('/episodes/timeline'),
  createEpisode: (payload: EpisodeInput) =>
    req<{ id: number; status: string }>('/episodes', { method: 'POST', body: JSON.stringify(payload) }),
  updateEpisode: (id: number, payload: EpisodeInput) =>
    req<{ id: number; status: string }>(`/episodes/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteEpisode: (id: number) => req<{ status: string }>(`/episodes/${id}`, { method: 'DELETE' }),
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
    req<{ id: number; status: string }>('/concepts', { method: 'POST', body: JSON.stringify(payload) }),
  getFacets: (type_code: string) => req<Facet[]>(`/concepts/facets?type_code=${encodeURIComponent(type_code)}`),
  listProposals: (status = 'pending') =>
    req<ProposalGroup[]>(`/concepts/proposals?status=${encodeURIComponent(status)}`),
  getProposalStats: () => req<ProposalStats>('/concepts/proposals/stats'),
  resolveProposal: (payload: ProposalResolveInput) =>
    req<{ surface_form: string; status: string; resolved_concept_id: number | null }>(
      '/concepts/proposals/resolve',
      { method: 'POST', body: JSON.stringify(payload) },
    ),
}
