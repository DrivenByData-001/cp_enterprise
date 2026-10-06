import { req, type Application, type ComparisonItem, type RequirementReviewSummary } from './api'

export type EvidenceSource = { ref: string; kind: string; label: string; content: string; source_revision: string; episode_id: string | null }
export type Disposition = 'covered' | 'partial' | 'investigate' | 'gap'
export type EvidenceDecision = { disposition: Disposition; selected_refs: string[]; rationale: string; revision: number }
export type ModeItem = ComparisonItem & { sources: EvidenceSource[]; decision: EvidenceDecision | null; stale: boolean; attention_reason: string | null; requirement_fingerprint: string }
export type ApplicationModeData = { application: Application; role: { id: string; title: string; organisation?: string | null }; items: ModeItem[]; resume_concept_id: string | null; evidence_complete: boolean; review_summary: RequirementReviewSummary }
export type ClaimContext = { claim: { claim_text: string; episode_id: string | null; source_revision: string } | null; episodes: { id: string; title: string | null; organisation: string | null; start_date: string | null; end_date: string | null }[] }
export type ClaimAcceptance = { operation_id: string; claim_id: string | null; source_revision: string | null; claim_text: string; episode_id: string | null; reason: string; confirmed: true }
export const applicationMode = {
  claimContext: (id: string, concept: string, claimId?: string) => req<ClaimContext>(`/applications/${id}/mode/evidence/${concept}/claim-context${claimId ? `?claim_id=${encodeURIComponent(claimId)}` : ''}`),
  acceptClaim: (id: string, concept: string, payload: ClaimAcceptance) => req<{ status: 'accepted'; acceptance_id: string; source: EvidenceSource }>(`/applications/${id}/mode/evidence/${concept}/accept-claim`, { method: 'POST', body: JSON.stringify(payload) }),
  get: (id: string) => req<ApplicationModeData>(`/applications/${id}/mode`),
  resume: (id: string, concept_id: string | null) => req(`/applications/${id}/mode/resume`, { method: 'PUT', body: JSON.stringify({ concept_id }) }),
  save: (id: string, concept: string, decision: EvidenceDecision & { source_revisions: Record<string, string>; requirement_fingerprint: string }) => req<EvidenceDecision>(`/applications/${id}/mode/evidence/${concept}`, { method: 'PUT', body: JSON.stringify(decision) }),
  search: (id: string, concept: string, q: string) => req<{ sources: EvidenceSource[] }>(`/applications/${id}/mode/evidence/${concept}/search?q=${encodeURIComponent(q)}`),
}
