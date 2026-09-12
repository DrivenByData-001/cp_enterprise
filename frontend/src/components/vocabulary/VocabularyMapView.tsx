import { useEffect, useMemo, useRef, useState } from 'react'
import type { Viewport } from '@xyflow/react'
import ConceptDetailsDrawer from '../ConceptDetailsDrawer'
import { api, type ConceptType, type VocabularyGraphNode, type VocabularyGraphResponse } from '../../lib/api'
import VocabularyMapControls from './VocabularyMapControls'
import { DEFAULT_MAP_FILTERS, type VocabMapFilterState } from './vocabConstants'
import VocabularyMapWorkspace from './VocabularyMapWorkspace'
import VocabularyMapFullscreenOverlay from './VocabularyMapFullscreenOverlay'
import type { GraphApi } from './VocabularyGraph'

// Map-level filter state, API loading, node selection, and graph/detail
// coordination (brief §21's `VocabularyMapView` responsibilities). No mutation
// happens directly in this file — every write goes through the shared
// ClusterActionsPanel / ConceptDetailsDrawer, exactly as the Review tab uses.
//
// Fullscreen + semantic zoom (vocab-graph-II brief): `isFullscreen` and
// `detailsCollapsed` are the only two pieces of state this pass adds here.
// Everything else fullscreen needs to preserve — filters, graph response,
// selection, focus mode, node limit — already lived in this component before
// fullscreen existed, so entering/exiting it is purely a render-mode switch:
// exactly one of the embedded workspace or the fullscreen overlay is mounted
// at a time (never both), so there is only ever one `VocabularyGraph`/React
// Flow instance alive, and toggling never touches `filters`/`focusId`/
// `refreshSignal` — the only things the data-fetch effect below depends on —
// so it never causes an extra API call either.
//
// The React Flow viewport (pan/zoom) is carried across that same remount via
// `viewportRef` below — a plain ref, not state: `onMove` fires continuously
// while panning/zooming, and writing to a ref costs nothing per tick (no
// re-render), whereas the *next* render only happens when some other state
// change (e.g. the fullscreen toggle) needs it — by which point the ref
// already holds the latest value. The new instance then restores it via
// `defaultViewport` instead of running `fitView` again.

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
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [detailsCollapsed, setDetailsCollapsed] = useState(false)
  const [graphApi, setGraphApi] = useState<GraphApi | null>(null)
  const viewportRef = useRef<Viewport | null>(null)

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

  const workspaceProps = {
    response,
    loading,
    error,
    focusId,
    focusLabel,
    onExitFocus: exitFocus,
    selected,
    onSelect: setSelected,
    summary,
    conceptTypes,
    onChanged,
    onOpenClusterReview,
    onOpenConcept: setDrawerConceptId,
    onFocus: (id: string) => enterFocus(id, selected?.label ?? id),
    detailsCollapsed,
    onGraphReady: setGraphApi,
    initialViewport: viewportRef.current,
    onViewportChange: (viewport: Viewport) => {
      viewportRef.current = viewport
    },
  }

  return (
    <div>
      {!isFullscreen && (
        <>
          {!focusId && <VocabularyMapControls filters={filters} conceptTypes={conceptTypes} onChange={setFilters} />}
          <VocabularyMapWorkspace mode="embedded" {...workspaceProps} onEnterFullscreen={() => setIsFullscreen(true)} />
        </>
      )}

      {isFullscreen && (
        <VocabularyMapFullscreenOverlay onExit={() => setIsFullscreen(false)}>
          <div className="vocab-map-fullscreen-topbar">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <h2 style={{ fontSize: 16, margin: 0 }}>Vocabulary Map</h2>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button onClick={() => graphApi?.fitView({ padding: 0.25 })}>Fit</button>
                <button onClick={() => setDetailsCollapsed((v) => !v)} aria-label={detailsCollapsed ? 'Show details panel' : 'Hide details panel'}>
                  {detailsCollapsed ? 'Show details' : 'Hide details'}
                </button>
                <button onClick={() => setIsFullscreen(false)} aria-label="Exit fullscreen">
                  × Exit fullscreen
                </button>
              </div>
            </div>
            {!focusId && <VocabularyMapControls filters={filters} conceptTypes={conceptTypes} onChange={setFilters} />}
          </div>
          <VocabularyMapWorkspace mode="fullscreen" {...workspaceProps} />
        </VocabularyMapFullscreenOverlay>
      )}

      {drawerConceptId && (
        <ConceptDetailsDrawer
          conceptId={drawerConceptId}
          conceptTypes={conceptTypes}
          onClose={() => setDrawerConceptId(null)}
          onConceptChanged={() => setRefreshSignal((n) => n + 1)}
          onNavigate={setDrawerConceptId}
        />
      )}
    </div>
  )
}
