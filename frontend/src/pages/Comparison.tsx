import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type ComparisonItem, type ComparisonResult, type ComparisonStatus } from '../lib/api'

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  evidenced: 'Evidenced',
  partial: 'Partially evidenced',
  user_asserted: 'User asserted',
  not_found: 'No evidence found',
}

const STATUS_COLOR: Record<ComparisonStatus, string> = {
  evidenced: 'var(--good)',
  partial: 'var(--warning)',
  user_asserted: 'var(--series-1)',
  not_found: 'var(--muted, #888)',
}

function ItemDetail({ item }: { item: ComparisonItem }) {
  const coverage = item.person_side.coverage
  const compositional = coverage ? coverage.trace.compositional : null
  const bestEpisode = compositional && 'best_episode' in compositional ? compositional.best_episode : null

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 8, fontSize: 13 }}>
      <div>
        <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>
          Role side
        </div>
        <div className="secondary">
          {item.role_side.basis}
          {item.role_side.document
            ? ` · ${item.role_side.document.title ?? 'document'} (${item.role_side.document.provenance})`
            : ' · no source document'}
        </div>
        {item.role_side.evidence_span && <p style={{ fontStyle: 'italic', margin: '4px 0 0' }}>“{item.role_side.evidence_span}”</p>}
      </div>
      <div>
        <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>
          Person side
        </div>

        {coverage ? (
          <>
            <div className="secondary">{coverage.trace.status_reason.message}</div>
            {coverage.core_components_total > 0 && (
              <div className="muted" style={{ marginTop: 2 }}>
                Core components: {coverage.core_components_met}/{coverage.core_components_total}
                {bestEpisode && bestEpisode.core_missing.length > 0 ? ` — missing: ${bestEpisode.core_missing.join(', ')}` : ''}
              </div>
            )}
            {coverage.strongest_depth && (
              <div className="muted">
                Strongest evidence: {coverage.strongest_depth}
                {coverage.strongest_autonomy ? ` / ${coverage.strongest_autonomy}` : ''}
              </div>
            )}
          </>
        ) : item.person_side.mappings.length > 0 ? (
          item.person_side.mappings.map((m) => (
            <div key={m.id} className="secondary">
              {m.review_status} profile360 {m.mapping_kind} mapping{m.display ? `: ${m.display}` : ''}
            </div>
          ))
        ) : item.person_side.assertion ? (
          <div className="secondary">
            You asserted this{item.person_side.assertion.note ? `: ${item.person_side.assertion.note}` : ''}
          </div>
        ) : (
          <div className="muted">No accepted evidence currently shows this.</div>
        )}

        {item.person_side.component_of.length > 0 && (
          <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
            Component of: {item.person_side.component_of.map((c) => c.canonical_name).join(', ')}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Comparison() {
  const { id } = useParams()
  const roleId = id ?? ''
  const [data, setData] = useState<ComparisonResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyConceptId, setBusyConceptId] = useState<string | null>(null)

  const reload = () => api.compareRole(roleId).then(setData)

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleId])

  const assert = async (conceptId: string) => {
    setBusyConceptId(conceptId)
    try {
      await api.assertCapability(conceptId)
      await reload()
    } finally {
      setBusyConceptId(null)
    }
  }

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!data) return <p className="muted">Loading…</p>

  return (
    <div>
      <Link to={`/roles/${roleId}`} className="muted" style={{ fontSize: 13 }}>
        ← Back to {data.role.title}
      </Link>
      <h1 style={{ fontSize: 22, marginTop: 12 }}>Structural comparison: {data.role.title}</h1>
      <p className="secondary">
        Evidence-backed, not scored. "No evidence found" means exactly that — not that you lack the capability. Every
        row traces to its source on both sides.
      </p>

      <div style={{ display: 'flex', gap: 16, margin: '16px 0', flexWrap: 'wrap' }}>
        {(Object.keys(STATUS_LABEL) as ComparisonStatus[]).map((s) => (
          <div key={s} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: STATUS_COLOR[s] }}>{data.counts[s]}</div>
            <div className="muted" style={{ fontSize: 12 }}>
              {STATUS_LABEL[s]}
            </div>
          </div>
        ))}
      </div>

      {data.blocking_gaps.length > 0 && (
        <div className="card" style={{ borderColor: 'var(--critical)', marginBottom: 12 }}>
          <strong style={{ color: 'var(--critical)' }}>
            {data.blocking_gaps.length} blocking gap{data.blocking_gaps.length === 1 ? '' : 's'}
          </strong>
          <p className="secondary" style={{ margin: '4px 0 0', fontSize: 13 }}>
            Required, and no evidence found at all: {data.blocking_gaps.map((g) => g.canonical_name).join(', ')}
          </p>
        </div>
      )}

      {data.unverified_required.length > 0 && (
        <div className="card" style={{ borderColor: 'var(--warning)', marginBottom: 12 }}>
          <strong style={{ color: 'var(--warning)' }}>
            {data.unverified_required.length} required capability{data.unverified_required.length === 1 ? '' : 'ies'} not fully verified
          </strong>
          <p className="secondary" style={{ margin: '4px 0 0', fontSize: 13 }}>
            Required, but only {data.unverified_required.map((g) => `${g.canonical_name} (${STATUS_LABEL[g.status].toLowerCase()})`).join(', ')} —
            not silently treated as satisfied.
          </p>
        </div>
      )}

      {data.items.length === 0 && (
        <p className="muted">
          No requirement claims for this role yet — extract them from the role's Requirements page first.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {data.items.map((item) => (
          <div key={item.role_side.requirement_claim_id} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <div>
                <strong>{item.concept.canonical_name}</strong>{' '}
                <span className="muted" style={{ fontSize: 12 }}>
                  {item.concept.type_code} · {item.role_side.requirement_type}
                </span>
              </div>
              <span style={{ fontWeight: 600, color: STATUS_COLOR[item.status], fontSize: 13, flexShrink: 0 }}>
                {STATUS_LABEL[item.status]}
              </span>
            </div>

            <ItemDetail item={item} />

            {item.status === 'not_found' && (
              <button style={{ marginTop: 8 }} disabled={busyConceptId === item.concept.id} onClick={() => assert(item.concept.id)}>
                I have done this
              </button>
            )}
          </div>
        ))}
      </div>

      <p className="muted" style={{ fontSize: 12, marginTop: 20 }}>
        Secondary signals — not the result: fit score {data.fit_score !== null ? data.fit_score.toFixed(2) : 'n/a'}
        {data.embedding_similarity !== null ? `, embedding similarity ${data.embedding_similarity.toFixed(2)}` : ''}. These never
        override the structural status above. Engine: {data.engine_version}.
      </p>
    </div>
  )
}
