import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ConceptType, type VocabClusterSummary, type VocabGraphPendingClusterNode } from '../../lib/api'
import { ClusterActionsPanel } from './ClusterActions'
import { FlagBadges, PriorityBandBadge } from './shared'

// Pending-cluster details (brief §14) — the graph node itself only carries
// summary counts (no duplicated heavy evidence payload per node, brief §17),
// so this fetches the same full evidence card the Review tab already computes
// (`GET /api/vocabulary/clusters/{cluster_key}`) and reuses the exact same
// Accept/Merge/Reject/Split controls (`ClusterActionsPanel`) — no forked
// mutation logic.

export default function PendingClusterMapDetails({
  node,
  conceptTypes,
  onChanged,
  onOpenClusterReview,
  onFocus,
}: {
  node: VocabGraphPendingClusterNode
  conceptTypes: ConceptType[]
  onChanged: () => Promise<void>
  onOpenClusterReview: (searchText: string) => void
  onFocus: (focusId: string) => void
}) {
  const [cluster, setCluster] = useState<VocabClusterSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    return api
      .getVocabClusterDetail(node.cluster_key)
      .then(setCluster)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.cluster_key])

  const refresh = async () => {
    await load()
    await onChanged()
  }

  const yearSpan =
    cluster?.distinct_years && cluster.distinct_years.length > 0
      ? cluster.distinct_years.length === 1
        ? String(cluster.distinct_years[0])
        : `${cluster.distinct_years[0]}–${cluster.distinct_years[cluster.distinct_years.length - 1]}`
      : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div>
        <div style={{ fontWeight: 600, fontSize: 15, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {node.label}
          <PriorityBandBadge band={node.priority_band} />
        </div>
        <div className="muted" style={{ fontSize: 12 }}>
          Pending cluster
        </div>
      </div>

      {loading && (
        <p className="muted" style={{ fontSize: 13 }}>
          Loading…
        </p>
      )}
      {error && <p style={{ color: 'var(--critical)', fontSize: 13 }}>{error}</p>}

      {cluster && (
        <>
          <div style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span>
              {cluster.surface_forms.length} surface form{cluster.surface_forms.length === 1 ? '' : 's'}
            </span>
            <span>
              {cluster.observation_count} observation{cluster.observation_count === 1 ? '' : 's'}
            </span>
            <span>
              {cluster.role_count} role{cluster.role_count === 1 ? '' : 's'}
            </span>
            {yearSpan && <span>Years: {yearSpan}</span>}
            {cluster.countries && cluster.countries.length > 0 && <span>Countries: {cluster.countries.join(', ')}</span>}
            {cluster.seniority_levels && cluster.seniority_levels.length > 0 && <span>Seniority: {cluster.seniority_levels.join(', ')}</span>}
          </div>

          <FlagBadges flags={cluster.flags} />

          <div>
            <div className="secondary" style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
              Surface forms
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {cluster.surface_forms.map((sf) => (
                <li key={sf}>{sf}</li>
              ))}
            </ul>
          </div>

          {cluster.example_roles && cluster.example_roles.length > 0 && (
            <div style={{ fontSize: 12 }}>
              <span className="muted">example roles: </span>
              {cluster.example_roles.slice(0, 5).map((r, i) => (
                <span key={r.id}>
                  {i > 0 && ', '}
                  <Link to={`/roles/${r.id}`}>{r.title ?? r.id}</Link>
                </span>
              ))}
            </div>
          )}

          {cluster.nearest_concept_id && cluster.nearest_similarity !== null && (
            <div className="muted" style={{ fontSize: 12 }}>
              Nearest accepted concept similarity: {Math.round((cluster.nearest_similarity ?? 0) * 100)}%
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button onClick={() => onOpenClusterReview(cluster.suggested_canonical_label)}>Review cluster</button>
            <button onClick={() => onFocus(`cluster:${cluster.cluster_key}`)}>View similarity neighbourhood</button>
          </div>

          <hr style={{ margin: '4px 0', border: 'none', borderTop: '1px solid var(--gridline)' }} />

          <ClusterActionsPanel cluster={cluster} conceptTypes={conceptTypes} onChanged={refresh} />
        </>
      )}
    </div>
  )
}
