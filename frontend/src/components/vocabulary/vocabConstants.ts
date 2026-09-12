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

// Semantic zoom (vocab-graph-II brief §6): three rendering-detail bands
// driven by React Flow's own viewport zoom value. A single source of
// threshold truth — VocabularyGraph reads only `getZoomBand`, never a
// hard-coded number, so the bands can't drift out of sync between the
// label-density logic and the tooltip/legend copy that describes them.
export type ZoomBand = 'far' | 'medium' | 'close'

export const ZOOM_BAND_THRESHOLDS: { medium: number; close: number } = {
  medium: 0.55,
  close: 1.05,
}

export function getZoomBand(zoom: number): ZoomBand {
  if (zoom < ZOOM_BAND_THRESHOLDS.medium) return 'far'
  if (zoom < ZOOM_BAND_THRESHOLDS.close) return 'medium'
  return 'close'
}

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
