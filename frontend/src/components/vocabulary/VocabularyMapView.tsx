import { useEffect, useMemo, useState } from 'react'
import ConceptDetailsDrawer from '../ConceptDetailsDrawer'
import { api, type ConceptType, type VocabularyGraphNode, type VocabularyGraphResponse } from '../../lib/api'
import VocabularyMapControls from './VocabularyMapControls'
import { DEFAULT_MAP_FILTERS, type VocabMapFilterState } from './vocabConstants'
import VocabularyGraph from './VocabularyGraph'
import VocabularyMapLegend from './VocabularyMapLegend'
import VocabularyMapDetails from './VocabularyMapDetails'

// Map-level filter state, API loading, node selection, and graph/detail
// coordination (brief §21's `VocabularyMapView` responsibilities). No mutation
// happens directly in this file — every write goes through the shared
// ClusterActionsPanel / ConceptDetailsDrawer, exactly as the Review tab uses.

export default function VocabularyMapView({
  conceptTypes,
  onOpenClusterReview,
}: {
  conceptTypes: ConceptType[]
  /** "Open cluster review" (brief §14): hands a searchable label back up to
   * the page, which switches to Review pre-filtered to it — no duplicate
   * queue-filtering logic lives here. */
  onOpenClusterReview: (searchText: string) => void
}) {
  const [filters, setFilters] = useState<VocabMapFilterState>(DEFAULT_MAP_FILTERS)
  const [response, setResponse] = useState<VocabularyGraphResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<VocabularyGraphNode | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [focusLabel, setFocusLabel] = useState<string>('')
  const [drawerConceptId, setDrawerConceptId] = useState<string | null>(null)
  const [refreshSignal, setRefreshSignal] = useState(0)

  useEffect(() => {
    setLoading(true)
    setError(null)
    const request = focusId
      ? api.getVocabularySimilarityFocus(focusId)
      : api.getVocabularyGraph({
          status: filters.status,
          group_by: filters.group_by,
          band: filters.band,
          type_code: filters.type_code,
          q: filters.q || undefined,
          min_role_count: filters.min_role_count,
          min_observation_count: filters.min_observation_count,
          limit: filters.limit,
        })
    request
      .then((res) => {
        setResponse(res)
        // Keep the current selection alive across a refresh only if it still exists.
        setSelected((prev) => (prev ? (res.nodes.find((n) => n.id === prev.id) ?? null) : null))
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, focusId, refreshSignal])

  const onChanged = async () => {
    setRefreshSignal((n) => n + 1)
  }

  const enterFocus = (id: string, label: string) => {
    setFocusLabel(label)
    setFocusId(id)
    setSelected(null)
  }

  const exitFocus = () => {
    setFocusId(null)
    setSelected(null)
  }

  const summary = useMemo(() => {
    if (!response) return null
    return {
      returnedNodes: response.meta.returned_nodes,
      pendingClusters: response.nodes.filter((n) => n.kind === 'pending_cluster').length,
      concepts: response.nodes.filter((n) => n.kind === 'concept').length,
      surfaceForms: response.nodes.filter((n) => n.kind === 'surface_form').length,
    }
  }, [response])

  return (
    <div>
      {!focusId && <VocabularyMapControls filters={filters} conceptTypes={conceptTypes} onChange={setFilters} />}

      {focusId && (
        <div className="card" style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <span style={{ fontSize: 13 }}>
            Similarity focus — nearest accepted concepts to <strong>{focusLabel}</strong>
          </span>
          <button onClick={exitFocus}>Back to map</button>
        </div>
      )}

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {response?.meta.truncated && !focusId && (
        <p className="muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 12 }}>
          Showing the first {response.meta.returned_nodes} matching nodes. Narrow the filters or focus on a node to
          explore further.
        </p>
      )}

      <div className="vocab-map-grid">
        <div className="card" style={{ height: 'min(620px, 70vh)', minHeight: 420, padding: 0, overflow: 'hidden', position: 'relative' }}>
          {loading && (
            <div
              style={{
                position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 5, background: 'color-mix(in srgb, var(--surface-1) 60%, transparent)',
              }}
            >
              <span className="muted">Loading…</span>
            </div>
          )}
          {response && !loading && response.nodes.length <= 1 && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span className="muted" style={{ fontSize: 13 }}>Nothing matches the current filters.</span>
            </div>
          )}
          {response && (
            <VocabularyGraph nodes={response.nodes} edges={response.edges} selectedId={selected?.id ?? null} onSelect={setSelected} />
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <VocabularyMapDetails
            node={selected}
            conceptTypes={conceptTypes}
            summary={summary}
            onChanged={onChanged}
            onOpenClusterReview={onOpenClusterReview}
            onOpenConcept={setDrawerConceptId}
            onFocus={(id) => enterFocus(id, selected?.label ?? id)}
          />
          <VocabularyMapLegend />
        </div>
      </div>

      {drawerConceptId && (
        <ConceptDetailsDrawer
          conceptId={drawerConceptId}
          conceptTypes={conceptTypes}
          onClose={() => setDrawerConceptId(null)}
          onConceptChanged={() => setRefreshSignal((n) => n + 1)}
          onNavigate={setDrawerConceptId}
        />
      )}

      <p className="muted" style={{ fontSize: 11, marginTop: 12 }}>
        Similarity links show semantically nearby terms; they are not automatic merge recommendations. Viewing the map
        never changes curation state.
      </p>
    </div>
  )
}
