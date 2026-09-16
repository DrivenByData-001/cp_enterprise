import type {
  CompensationBasis,
  EconomicsFreshness,
  EvidenceQuality,
  PersonalComparison,
  ResolvedCompensation,
} from '../../lib/api'
import { formatMoney } from '../../lib/money'

// The four compensation bases must never blur together in the UI, so their
// colour and wording are defined once, here, and every surface reads them
// from this one place. `insufficient_evidence` is a first-class answer with
// its own presentation — not an error state and never rendered as a zero.
const BASIS_STYLE: Record<CompensationBasis, { label: string; colour: string; blurb: string }> = {
  advert_stated: {
    label: 'Advert salary',
    colour: 'var(--good)',
    blurb: 'Stated on this posting and reviewed against the source document.',
  },
  market_estimate: {
    label: 'Market estimate',
    colour: 'var(--series-1)',
    blurb: 'An archetype benchmark from accepted market evidence — not this role’s advertised salary.',
  },
  legacy_estimate: {
    label: 'Legacy estimate',
    colour: 'var(--warning)',
    blurb: 'An older estimate captured with the posting, with no recorded derivation.',
  },
  insufficient_evidence: {
    label: 'Insufficient evidence',
    colour: 'var(--text-muted)',
    blurb: 'Not enough accepted compensation evidence to state a figure.',
  },
}

const QUALITY_LABEL: Record<EvidenceQuality, string> = {
  insufficient: 'Insufficient',
  thin: 'Thin',
  moderate: 'Moderate',
  good: 'Good',
}

// An advert-stated figure says *what kind* of figure it is, so a total
// package is never read as a base salary and a day rate is never read as an
// annual one. The four bases stay four; only the advert label specialises.
function advertLabel(componentLabel: string | null | undefined): string {
  if (componentLabel === 'total package') return 'Advert total package'
  if (componentLabel === 'day rate') return 'Advert day rate'
  return 'Advert salary'
}

export function BasisBadge({ basis, componentLabel }: { basis: CompensationBasis; componentLabel?: string | null }) {
  const style = BASIS_STYLE[basis] ?? BASIS_STYLE.insufficient_evidence
  const label = basis === 'advert_stated' ? advertLabel(componentLabel) : style.label
  return (
    <span
      title={style.blurb}
      style={{
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.4,
        padding: '2px 7px',
        borderRadius: 4,
        color: style.colour,
        border: `1px solid ${style.colour}`,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  )
}

export function EvidenceQualityBadge({ quality }: { quality: EvidenceQuality }) {
  return (
    <span className="muted" style={{ fontSize: 12 }}>
      Evidence quality: <strong style={{ fontWeight: 600 }}>{QUALITY_LABEL[quality] ?? quality}</strong>
    </span>
  )
}

/** A compensation range with its basis attached. Never renders a number
 * without the basis that produced it. */
export function CompensationFigure({ compensation }: { compensation: ResolvedCompensation }) {
  const { currency, amount_min, amount_reference, amount_max } = compensation
  if (compensation.basis === 'insufficient_evidence' || amount_reference === null || !currency) {
    return (
      <div>
        <BasisBadge basis={compensation.basis} componentLabel={compensation.component_label} />
        <p className="secondary" style={{ margin: '6px 0 0', fontSize: 13 }}>
          {compensation.reason}
        </p>
        <SupplementaryFigures compensation={compensation} />
      </div>
    )
  }
  const hasRange = amount_min !== null && amount_max !== null && amount_min !== amount_max
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <BasisBadge basis={compensation.basis} componentLabel={compensation.component_label} />
        <span style={{ fontSize: 20, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
          {hasRange
            ? `${formatMoney(amount_min, currency)} – ${formatMoney(amount_max, currency)}`
            : formatMoney(amount_reference, currency)}
        </span>
      </div>
      {hasRange && (
        <div className="secondary" style={{ fontSize: 13, marginTop: 2 }}>
          Reference {formatMoney(amount_reference, currency)}
        </div>
      )}
      <div className="muted" style={{ fontSize: 12, marginTop: 6, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <EvidenceQualityBadge quality={compensation.evidence_quality} />
        {compensation.market && <span>Market: {compensation.market.label}</span>}
        {compensation.archetype && <span>Archetype: {compensation.archetype.name}</span>}
        {compensation.as_of && <span>As of {compensation.as_of}</span>}
        {compensation.component && compensation.pay_period && (
          <span>
            {compensation.component} · {compensation.pay_period}
          </span>
        )}
      </div>
      <p className="secondary" style={{ margin: '6px 0 0', fontSize: 12 }}>
        {compensation.reason}
      </p>
      <SupplementaryFigures compensation={compensation} />
    </div>
  )
}

/** Stated figures that are not the headline — a bonus percentage, a total
 * package alongside a base salary. Reported, never merged into the headline
 * and never promoted to it. */
function SupplementaryFigures({ compensation }: { compensation: ResolvedCompensation }) {
  const figures = compensation.supplementary ?? []
  if (figures.length === 0) return null
  return (
    <div style={{ marginTop: 8 }}>
      <div className="muted" style={{ fontSize: 12 }}>
        Also stated on this posting
      </div>
      <ul className="secondary" style={{ fontSize: 13, margin: '2px 0 0', paddingLeft: 18 }}>
        {figures.map((figure) => (
          <li key={figure.observation_id}>
            {figure.label}:{' '}
            {figure.bonus_pct !== null
              ? `${figure.bonus_pct}%`
              : `${formatMoney(figure.amount_min, figure.currency ?? '')}${
                  figure.amount_max !== null && figure.amount_max !== figure.amount_min
                    ? ` – ${formatMoney(figure.amount_max, figure.currency ?? '')}`
                    : ''
                }`}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Derived economics that no longer reflect current evidence are withheld,
 * not shown as current. This says so where a figure would otherwise be. */
export function StaleEconomicsNotice({
  freshness,
  onRebuild,
  busy = false,
}: {
  freshness: EconomicsFreshness
  onRebuild?: () => void
  busy?: boolean
}) {
  if (freshness.fresh) return null
  return (
    <div
      className="card"
      style={{ padding: 12, borderLeft: '3px solid var(--warning)', marginBottom: 12 }}
      role="status"
    >
      <div style={{ fontWeight: 600, fontSize: 13 }}>
        {freshness.state === 'never_rebuilt'
          ? 'Market benchmarks have never been built'
          : 'Market benchmarks are out of date'}
      </div>
      <p className="secondary" style={{ fontSize: 13, margin: '4px 0 0' }}>
        {freshness.reason}
      </p>
      {onRebuild && (
        <button type="button" onClick={onRebuild} disabled={busy} style={{ marginTop: 8 }}>
          {busy ? 'Rebuilding…' : 'Rebuild economics'}
        </button>
      )}
    </div>
  )
}

function signed(amount: number, currency: string): string {
  const formatted = formatMoney(Math.abs(amount), currency)
  return `${amount >= 0 ? '+' : '−'}${formatted}`
}

/** The personal current-vs-opportunity comparison. When the two sides are
 * not genuinely comparable this renders the reason rather than a delta —
 * there is no code path here that produces a number from an incompatible
 * pair. */
export function PersonalComparisonPanel({
  comparison,
  compact = false,
}: {
  comparison: PersonalComparison
  compact?: boolean
}) {
  if (!comparison.comparable || !comparison.baseline) {
    return (
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        <strong style={{ fontWeight: 600 }}>Not comparable.</strong> {comparison.reason}
      </p>
    )
  }
  const baseline = comparison.baseline
  const currency = baseline.currency ?? ''
  const { difference_min, difference_reference, difference_max } = comparison
  const hasRange =
    difference_min !== null && difference_max !== null && difference_min !== difference_max

  return (
    <div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <div>
          <div className="muted" style={{ fontSize: 12 }}>
            {baseline.label}
            {comparison.uses_planning_equivalent ? ' (planning equivalent)' : ''}
          </div>
          <div style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
            {formatMoney(baseline.amount, currency)}
            {baseline.unit && baseline.unit !== 'annual' ? ` / ${baseline.unit}` : ''}
          </div>
        </div>
        <div>
          <div className="muted" style={{ fontSize: 12 }}>
            Potential base-pay difference
          </div>
          <div
            style={{
              fontWeight: 600,
              fontVariantNumeric: 'tabular-nums',
              color:
                (difference_reference ?? 0) > 0
                  ? 'var(--good)'
                  : (difference_reference ?? 0) < 0
                    ? 'var(--critical)'
                    : 'inherit',
            }}
          >
            {hasRange
              ? `${signed(difference_min as number, currency)} to ${signed(difference_max as number, currency)}`
              : difference_reference !== null
                ? signed(difference_reference, currency)
                : '—'}
          </div>
        </div>
      </div>
      {comparison.uses_planning_equivalent && baseline.planning_equivalent && (
        <p className="muted" style={{ fontSize: 12, margin: '6px 0 0' }}>
          Assumption: {baseline.planning_equivalent.label}. {baseline.planning_equivalent.caveat}
        </p>
      )}
      {!compact && comparison.limitations && comparison.limitations.length > 0 && (
        <ul className="muted" style={{ fontSize: 12, margin: '8px 0 0', paddingLeft: 18 }}>
          {comparison.limitations.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
