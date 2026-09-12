import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SurfaceFormDetail, type VocabGraphSurfaceFormNode } from '../../lib/api'

// Word Details (brief §13) — fetches single-surface-form evidence not
// carried on the (deliberately light) graph node payload itself. No action
// here ever mutates vocabulary.

export default function SurfaceFormDetails({
  node,
  onOpenClusterReview,
  onOpenConcept,
  onFocus,
}: {
  node: VocabGraphSurfaceFormNode
  onOpenClusterReview: (searchText: string) => void
  onOpenConcept: (conceptId: string) => void
  onFocus: (focusId: string) => void
}) {
  const [detail, setDetail] = useState<SurfaceFormDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    setDetail(null)
    api
      .getSurfaceFormDetail(node.label)
      .then(setDetail)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [node.label])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div>
        <div style={{ fontWeight: 600, fontSize: 15 }}>{node.label}</div>
        <div className="muted" style={{ fontSize: 12 }}>
          Raw surface form
        </div>
      </div>

      {loading && (
        <p className="muted" style={{ fontSize: 13 }}>
          Loading…
        </p>
      )}
      {error && (
        <p style={{ color: 'var(--critical)', fontSize: 13 }}>
          {error}
        </p>
      )}

      {detail && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
          <span>
            Status: <strong>{detail.status}</strong>
          </span>
          {detail.status === 'pending' && (
            <>
              {detail.cluster_key && <span className="secondary">Cluster: {detail.cluster_key}</span>}
              <span>
                {detail.observation_count ?? 0} observation{detail.observation_count === 1 ? '' : 's'}
              </span>
              <span>
                {detail.role_count ?? 0} role{detail.role_count === 1 ? '' : 's'}
              </span>
              {detail.countries.length > 0 && <span>Countries: {detail.countries.join(', ')}</span>}
              {detail.seniority_levels.length > 0 && <span>Seniority: {detail.seniority_levels.join(', ')}</span>}
              {detail.career_tracks.length > 0 && <span>Career tracks: {detail.career_tracks.join(', ')}</span>}
              {detail.years.length > 0 && <span>Years: {detail.years.join(', ')}</span>}
              {detail.example_roles.length > 0 && (
                <span>
                  Example roles:{' '}
                  {detail.example_roles.slice(0, 5).map((r, i) => (
                    <span key={r.id}>
                      {i > 0 && ', '}
                      <Link to={`/roles/${r.id}`}>{r.title ?? r.id}</Link>
                    </span>
                  ))}
                </span>
              )}
            </>
          )}
          {detail.resolved_concept && (
            <span className="secondary">
              Accepted concept: <strong>{detail.resolved_concept.canonical_name}</strong>
              {detail.alias_origin === 'curator' && <span className="muted"> (curator-added alias — not necessarily observed verbatim)</span>}
            </span>
          )}
          {detail.nearest_concept && (
            <div className="card" style={{ padding: 8 }}>
              <div className="muted" style={{ fontSize: 11 }}>
                Nearest accepted concept
              </div>
              <div style={{ fontSize: 13 }}>
                {detail.nearest_concept.canonical_name}
                {detail.nearest_concept.similarity !== null && (
                  <span className="muted"> — similarity {Math.round(detail.nearest_concept.similarity * 100)}%</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
        {detail?.status === 'pending' && (
          <button className="primary" onClick={() => onOpenClusterReview(node.label)}>
            Open cluster review
          </button>
        )}
        {detail?.resolved_concept && <button onClick={() => onOpenConcept(detail.resolved_concept!.id)}>Open concept</button>}
        <button onClick={() => onFocus(`surface_form:${node.label}`)}>View similarity neighbourhood</button>
      </div>
    </div>
  )
}
