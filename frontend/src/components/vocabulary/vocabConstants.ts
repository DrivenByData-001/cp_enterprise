import type { PriorityBand } from '../../lib/api'

// Plain constants shared between the Review tab and the Map tab, kept in
// their own module (no components here) so Vite Fast Refresh can treat every
// *.tsx file in this folder as component-only.

export const BAND_COLOR: Record<PriorityBand, string> = {
  high: 'var(--good)',
  medium: 'var(--series-1)',
  low: 'var(--warning)',
  sparse: 'var(--text-muted)',
}

export const BAND_LABEL: Record<PriorityBand, string> = { high: 'High', medium: 'Medium', low: 'Low', sparse: 'Sparse' }

export const FLAG_LABEL: Record<string, string> = {
  single_role: 'Seen in only 1 role',
  single_observation: 'Seen only once',
  long_phrase: 'Unusually long phrase',
  possible_fragment: 'Possible extraction fragment',
  employer_or_process_specific: 'Employer/process-specific wording',
  malformed: 'Malformed text',
}

// Node/edge visual language shared between VocabularyGraph and
// VocabularyMapLegend, so the legend never drifts out of sync with what is
// actually drawn.
export const CONCEPT_COLOR = 'var(--series-1)'
export const GROUP_COLOR = 'var(--text-muted)'

export type VocabMapFilterState = {
  status: 'pending' | 'accepted' | 'combined'
  group_by: 'priority' | 'type' | 'none'
  band?: PriorityBand
  type_code?: string
  q: string
  min_role_count?: number
  min_observation_count?: number
  limit: number
}

export const DEFAULT_MAP_FILTERS: VocabMapFilterState = {
  status: 'pending',
  group_by: 'priority',
  q: '',
  limit: 300,
}
