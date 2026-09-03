import { useEffect, useState } from 'react'
import { api, type CapabilityCoverage, type ComparisonStatus } from '../lib/api'

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  evidenced: 'Evidenced',
  partial: 'Partially evidenced',
  user_asserted: 'User asserted',
  not_found: 'No evidence found',
}

const STATUS_ORDER: ComparisonStatus[] = ['evidenced', 'partial', 'user_asserted', 'not_found']

const STATUS_COLOR: Record<ComparisonStatus, string> = {
  evidenced: 'var(--good)',
  partial: 'var(--warning)',
  user_asserted: 'var(--series-1)',
  not_found: 'var(--muted, #888)',
}

function CoverageCard({ item }: { item: CapabilityCoverage }) {
  const [expanded, setExpanded] = useState(false)
  const compositional = item.trace.compositional
  const bestEpisode = 'best_episode' in compositional ? compositional.best_episode : null

  return (
    <div className="card">
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}
      >
        <strong>{item.canonical_name ?? item.capability_concept_id}</strong>
        <span className="muted" style={{ fontSize: 12, flexShrink: 0 }}>
          {expanded ? 'Hide' : 'Details'}
        </span>
      </div>
      <p className="secondary" style={{ fontSize: 13, margin: '4px 0 0' }}>
        {item.trace.status_reason.message}
      </p>

      {expanded && (
        <div style={{ marginTop: 10, fontSize: 13, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="secondary">
            Core components met: {item.core_components_met}/{item.core_components_total}
            {item.strongest_depth ? ` · strongest depth: ${item.strongest_depth}` : ''}
            {item.strongest_autonomy ? ` · strongest autonomy: ${item.strongest_autonomy}` : ''}
          </div>
          <div className="secondary">
            {item.last_demonstrated ? `Last demonstrated: ${item.last_demonstrated}` : 'Last demonstrated: unknown'}
            {item.years_active !== null ? ` · years active: ${item.years_active}` : ''}
          </div>

          {item.trace.direct_evidence.length > 0 && (
            <div>
              <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>
                Direct evidence
              </div>
              {item.trace.direct_evidence.map((e) => (
                <div key={e.mapping_id} className="secondary">
                  [{e.review_status}] {e.display ?? '(no text)'}
                  {e.depth ? ` — depth ${e.depth}` : ''}
                  {e.autonomy ? `, autonomy ${e.autonomy}` : ''}
                </div>
              ))}
            </div>
          )}

          {bestEpisode && (
            <div>
              <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>
                Strongest component evidence (one episode)
              </div>
              <div className="secondary">core met: {bestEpisode.core_met.join(', ') || 'none'}</div>
              {bestEpisode.core_missing.length > 0 && <div className="muted">core missing: {bestEpisode.core_missing.join(', ')}</div>}
              {bestEpisode.supporting_met.length > 0 && <div className="secondary">supporting met: {bestEpisode.supporting_met.join(', ')}</div>}
              {bestEpisode.contextual_met.length > 0 && <div className="secondary">contextual met: {bestEpisode.contextual_met.join(', ')}</div>}
            </div>
          )}

          {item.trace.assertion && (
            <div className="secondary">You asserted this{item.trace.assertion.note ? `: ${item.trace.assertion.note}` : ''}.</div>
          )}

          <div className="muted" style={{ fontSize: 11 }}>
            {item.supporting_profile360_claim_ids.length} supporting profile360 claim(s) traced.
          </div>
        </div>
      )}
    </div>
  )
}

export default function CapabilityCoveragePage() {
  const [items, setItems] = useState<CapabilityCoverage[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listCapabilityCoverage()
      .then(setItems)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const grouped = STATUS_ORDER.reduce(
    (acc, s) => {
      acc[s] = items.filter((i) => i.status === s)
      return acc
    },
    {} as Record<ComparisonStatus, CapabilityCoverage[]>,
  )

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Capability coverage</h1>
      <p className="secondary" style={{ marginTop: 4 }}>
        What you can evidence today, what is partial, what you have asserted, and where no evidence has been found —
        never "you lack this". Every status traces back to profile360.
      </p>

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {loading && <p className="muted">Loading…</p>}

      {!loading &&
        !error &&
        STATUS_ORDER.map((status) => (
          <section key={status} style={{ marginBottom: 24 }}>
            <h2 style={{ fontSize: 16, margin: '0 0 8px', color: STATUS_COLOR[status] }}>
              {STATUS_LABEL[status]} ({grouped[status].length})
            </h2>
            {grouped[status].length === 0 && <p className="muted">None.</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {grouped[status].map((item) => (
                <CoverageCard key={item.capability_concept_id} item={item} />
              ))}
            </div>
          </section>
        ))}
    </div>
  )
}
