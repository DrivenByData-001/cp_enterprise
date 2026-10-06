import { req, type Role, type RoleMetadataInput, type RequirementReviewSummary } from './api'
import type { ModeItem } from './applicationMode'

export type Stage = 'overview' | 'opportunity' | 'requirements' | 'evidence'
export type StageState = 'not_started' | 'in_progress' | 'complete' | 'needs_attention'
export type PackageItem = { id: string; kind: 'cv' | 'cover_letter' | 'supporting_statement' | 'question' | 'portfolio' | 'other'; label: string; required: boolean; word_limit: number | null; instructions: string }
export type Preparation = { revision: number; deadline: string | null; package_items: PackageItem[]; updated_at?: string }
export type ProcessData = { application: { id: string; status: string }; role: Role; preparation: Preparation; target_revision: string; opportunity_fingerprint: string; requirements_fingerprint: string; stages: Record<Exclude<Stage, 'overview'>, StageState>; resume: { stage: Stage; concept_id: string | null }; items: ModeItem[]; legacy_requirement_count: number; review_summary: RequirementReviewSummary; next_stage: Stage }
export type ScopedProposal = { id: string; surface_form: string; suggested_type: string; role_evidence_span: string | null; revision: string }
export const applicationProcess = {
  get: (id: string) => req<ProcessData>(`/applications/${id}/process`),
  opportunity: (id: string, body: { revision: number; target_revision: string; metadata: RoleMetadataInput; deadline: string | null; package_items: PackageItem[]; confirm: boolean }) => req<ProcessData>(`/applications/${id}/process/opportunity`, { method: 'PUT', body: JSON.stringify(body) }),
  confirmRequirements: (id: string, fingerprint: string) => req<ProcessData>(`/applications/${id}/process/requirements`, { method: 'PUT', body: JSON.stringify({ fingerprint }) }),
  resume: (id: string, stage: Stage, concept_id: string | null = null) => req(`/applications/${id}/process/resume`, { method: 'PUT', body: JSON.stringify({ stage, concept_id }) }),
  proposals: (id: string) => req<{ items: ScopedProposal[] }>(`/applications/${id}/process/vocabulary`),
  resolve: (id: string, proposal: ScopedProposal, body: { action: 'accept_new' | 'accept_alias' | 'reject'; concept_id?: string; type_code?: string; canonical_name?: string; definition?: string }) => req(`/applications/${id}/process/vocabulary/${proposal.id}`, { method: 'POST', body: JSON.stringify({ ...body, revision: proposal.revision }) }),
}
export type StageSave = () => Promise<boolean>
export const stageLabels: Record<Stage, string> = { overview: 'Application overview', opportunity: 'Opportunity', requirements: 'Requirements', evidence: 'Evidence' }
export const stateLabels: Record<StageState, string> = { not_started: 'Not started', in_progress: 'In progress', complete: 'Complete', needs_attention: 'Needs attention' }
