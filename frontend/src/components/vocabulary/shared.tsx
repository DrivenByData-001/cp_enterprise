import type { PriorityBand } from '../../lib/api'
import { BAND_COLOR, BAND_LABEL, FLAG_LABEL } from './vocabConstants'

// Small components shared between the Review tab and the Map tab so the two
// views never silently disagree on what a colour/label means (e.g. "High"
// priority must look the same badge everywhere it appears).

export function Badge({ children, color, title }: { children: React.ReactNode; color: string; title?: string }) {
  return (
    <span
      title={title}
      style={{
        fontSize: 11,
        fontWeight: 600,
        color,
        border: `1px solid ${color}`,
        borderRadius: 999,
        padding: '1px 8px',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

export function PriorityBandBadge({ band }: { band: PriorityBand | null }) {
  if (!band) return null
  return <Badge color={BAND_COLOR[band]}>{BAND_LABEL[band]}</Badge>
}

export function FlagBadges({ flags }: { flags: string[] }) {
  if (!flags.length) return null
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {flags.map((f) => (
        <Badge key={f} color="var(--warning)" title="Advisory only — not automatically rejected">
          {FLAG_LABEL[f] ?? f}
        </Badge>
      ))}
    </div>
  )
}
