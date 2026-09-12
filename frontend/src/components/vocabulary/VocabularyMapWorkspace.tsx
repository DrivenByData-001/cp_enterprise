import type { Viewport } from '@xyflow/react'
import type { ConceptType, VocabularyGraphNode, VocabularyGraphResponse } from '../../lib/api'
import VocabularyGraph, { type GraphApi } from './VocabularyGraph'
import VocabularyMapDetails, { type VocabularyMapSummaryCounts } from './VocabularyMapDetails'
import VocabularyMapLegend from './VocabularyMapLegend'

// Shared graph + details(+legend) layout (vocab-graph-II brief §14/§22):
// embedded and fullscreen render the exact same tree off the exact same
// lifted state (VocabularyMapView owns filters/response/selection/focus), so
// there is only ever one `VocabularyGraph` instance mounted at a time and
// never a second, forked implementation to keep in sync. `mode` changes
// layout only — never data-fetching or vocabulary semantics.
//
// Details/legend collapse (brief §13) is a fullscreen-only affordance:
// embedded keeps its existing, unconditional "graph + details" layout no
// matter what `detailsCollapsed` currently holds, so returning from
// fullscreen never surprises the embedded view — only fullscreen actually
// reads it.

export default function VocabularyMapWorkspace({
  mode,
  response,
  loading,
  error,
  focusId,
  focusLabel,
  onExitFocus,
  selected,
  onSelect,
  summary,
  conceptTypes,
  onChanged,
  onOpenClusterReview,
  onOpenConcept,
  onFocus,
  detailsCollapsed,
  onGraphReady,
  initialViewport,
  onViewportChange,
  onEnterFullscreen,
}: {
  mode: 'embedded' | 'fullscreen'
  response: VocabularyGraphResponse | null
  loading: boolean
  error: string | null
  focusId: string | null
  focusLabel: string
  onExitFocus: () => void
  selected: VocabularyGraphNode | null
  onSelect: (node: VocabularyGraphNode | null) => void
  summary: VocabularyMapSummaryCounts | null
  conceptTypes: ConceptType[]
  onChanged: () => Promise<void>
  onOpenClusterReview: (searchText: string) => void
  onOpenConcept: (conceptId: string) => void
  onFocus: (focusId: string) => void
  detailsCollapsed: boolean
  onGraphReady?: (api: GraphApi) => void
  /** Last-known React Flow viewport, carried across the embedded/fullscreen
   * remount (VocabularyMapView owns the ref; null on the very first mount). */
  initialViewport?: Viewport | null
  onViewportChange?: (viewport: Viewport) => void
  /** Present only in embedded mode — draws the graph-corner ⛶ control. */
  onEnterFullscreen?: () => void
}) {
  const showDetails = mode === 'embedded' || !detailsCollapsed

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        // A flex item filling the rest of the fullscreen overlay's column
        // needs `flex`, not `height: 100%` — the latter resolves against the
        // flex container's own height (not "remaining space after the top
        // bar"), which left the graph canvas measuring a too-small, stale
        // box and rendering small/off-centre after a real fitView.
        flex: mode === 'fullscreen' ? '1 1 auto' : undefined,
      }}
    >
      {focusId && (
        <div
          className="card"
          style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, flexShrink: 0 }}
        >
          <span style={{ fontSize: 13 }}>
            Similarity focus — nearest accepted concepts to <strong>{focusLabel}</strong>
          </span>
          <button onClick={onExitFocus}>Back to map</button>
        </div>
      )}

      {error && (
        <p style={{ color: 'var(--critical)', flexShrink: 0 }}>{error}</p>
      )}
      {response?.meta.truncated && !focusId && (
        <p className="muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 12, flexShrink: 0 }}>
          Showing the first {response.meta.returned_nodes} matching nodes. Narrow the filters or focus on a node to
          explore further.
        </p>
      )}

      <div
        className={mode === 'fullscreen' ? 'vocab-map-grid vocab-map-grid--fullscreen' : 'vocab-map-grid'}
        style={{
          flex: mode === 'fullscreen' ? '1 1 auto' : undefined,
          minHeight: 0,
          // Collapsing details (fullscreen-only, brief §13) reclaims the
          // whole width rather than leaving an empty 320px column-track.
          gridTemplateColumns: mode === 'fullscreen' && !showDetails ? '1fr' : undefined,
        }}
      >
        <div
          className="card"
          style={
            mode === 'fullscreen'
              ? { height: '100%', minHeight: 0, padding: 0, overflow: 'hidden', position: 'relative' }
              : { height: 'min(620px, 70vh)', minHeight: 420, padding: 0, overflow: 'hidden', position: 'relative' }
          }
        >
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
            <VocabularyGraph
              nodes={response.nodes}
              edges={response.edges}
              selectedId={selected?.id ?? null}
              onSelect={onSelect}
              onReady={onGraphReady}
              initialViewport={initialViewport}
              onViewportChange={onViewportChange}
            />
          )}
          {onEnterFullscreen && (
            <button
              onClick={onEnterFullscreen}
              aria-label="Open Vocabulary Map in fullscreen"
              title="Fullscreen"
              style={{ position: 'absolute', top: 10, right: 10, zIndex: 6, fontSize: 12, padding: '5px 10px' }}
            >
              ⛶ Fullscreen
            </button>
          )}
        </div>
        {showDetails && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minHeight: 0, overflowY: mode === 'fullscreen' ? 'auto' : undefined }}>
            <VocabularyMapDetails
              node={selected}
              conceptTypes={conceptTypes}
              summary={summary}
              onChanged={onChanged}
              onOpenClusterReview={onOpenClusterReview}
              onOpenConcept={onOpenConcept}
              onFocus={onFocus}
            />
            <VocabularyMapLegend />
          </div>
        )}
      </div>

      <p className="muted" style={{ fontSize: 11, marginTop: 12, flexShrink: 0 }}>
        Similarity links show semantically nearby terms; they are not automatic merge recommendations. Viewing the map
        never changes curation state.
      </p>
    </div>
  )
}
