export type RoleSkill = {
  name: string
  category: string | null
  importance: number | null
  requirement_type: string | null
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
  listRoles: (params: { career_track?: string; min_similarity?: number; sort?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.career_track) qs.set('career_track', params.career_track)
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
}
