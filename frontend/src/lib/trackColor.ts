// Categorical color assignment. Capped at 3 validated slots (all-pairs safe for
// scatter use) — everything else folds into a neutral "other" bucket rather than
// generating a new hue.
const TRACK_COLORS: Record<string, string> = {
  actuarial: 'var(--series-1)',
  data_science: 'var(--series-2)',
  quant: 'var(--series-3)',
  risk: 'var(--series-3)',
}

export function trackColor(track: string | null): string {
  if (!track) return 'var(--series-other)'
  return TRACK_COLORS[track] ?? 'var(--series-other)'
}

export function trackLabel(track: string | null): string {
  if (!track) return 'Unclassified'
  const known: Record<string, string> = {
    actuarial: 'Actuarial',
    data_science: 'Data Science',
    quant: 'Quant',
    risk: 'Risk',
    finance: 'Finance',
    mixed: 'Mixed',
    other: 'Other',
  }
  return known[track] ?? track
}

export const LEGEND_TRACKS = [
  { key: 'actuarial', label: 'Actuarial', color: 'var(--series-1)' },
  { key: 'data_science', label: 'Data Science', color: 'var(--series-2)' },
  { key: 'quant_risk', label: 'Quant / Risk', color: 'var(--series-3)' },
  { key: 'other', label: 'Other', color: 'var(--series-other)' },
]
