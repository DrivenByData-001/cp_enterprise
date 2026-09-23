import { useCallback, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
  type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { VocabularyGraphEdge, VocabularyGraphNode } from '../../lib/api'
import { BAND_COLOR, BAND_LABEL, CONCEPT_COLOR, GROUP_COLOR, getZoomBand, type ZoomBand } from './vocabConstants'

// A minimal, generic-free view of the React Flow instance for callers that
// only ever need to re-fit the view externally (the fullscreen top bar's
// [Fit] button, brief §17) — avoids exposing `ReactFlowInstance<NodeData>`'s
// full, invariant-in-practice generic surface across the component boundary.
export type GraphApi = { fitView: (options?: { padding?: number }) => void }

// The graph-library wrapper (docs/25 §"Graph library"). Purely presentation —
// no API calls happen here; it only turns already-fetched nodes/edges into a
// laid-out, pannable/zoomable canvas and reports clicks back to its caller.
//
// Layout: a small hand-rolled radial layout (brief §5's "raw words toward the
// outside, organising vocabulary inward") computed from the *structural*
// edges (contains/member_of/maps_to/alias_of) via BFS depth from the
// synthetic "root" node, with each parent's angular slice divided evenly
// among its children — the standard "balloon" radial-tree technique. No
// layout dependency (dagre/elkjs) was added: at a few hundred nodes this
// closed-form approach is fast, deterministic, and exactly matches the
// brief's own concentric-rings diagram. Similarity-focus responses carry no
// structural edges at all (just a center + its neighbours) — those get a
// simple one-ring "star" layout instead, detected via the `focus` node flag.

const HIERARCHY_RELATIONS = new Set(['contains', 'member_of', 'maps_to', 'alias_of'])
const RING_SPACING = 150
const FOCUS_RADIUS = 220

type Point = { x: number; y: number }

function computeRadialLayout(nodes: VocabularyGraphNode[], edges: VocabularyGraphEdge[]): Map<string, Point> {
  const childrenOf = new Map<string, string[]>()
  const parentOf = new Map<string, string>()

  for (const e of edges) {
    if (!HIERARCHY_RELATIONS.has(e.relation)) continue
    const [parent, child] = e.relation === 'contains' ? [e.source, e.target] : [e.target, e.source]
    if (!parentOf.has(child)) {
      parentOf.set(child, parent)
      childrenOf.set(parent, [...(childrenOf.get(parent) ?? []), child])
    }
  }

  const rootId = nodes.find((n) => n.id === 'root')?.id ?? nodes[0]?.id
  const depth = new Map<string, number>()
  if (rootId) {
    depth.set(rootId, 0)
    const queue = [rootId]
    while (queue.length) {
      const cur = queue.shift()!
      for (const child of childrenOf.get(cur) ?? []) {
        if (!depth.has(child)) {
          depth.set(child, (depth.get(cur) ?? 0) + 1)
          queue.push(child)
        }
      }
    }
  }

  const angle = new Map<string, number>()
  const assign = (id: string, start: number, end: number) => {
    angle.set(id, (start + end) / 2)
    const kids = childrenOf.get(id) ?? []
    if (!kids.length) return
    const span = (end - start) / kids.length
    kids.forEach((kid, i) => assign(kid, start + i * span, start + (i + 1) * span))
  }
  if (rootId) assign(rootId, 0, Math.PI * 2)

  const positions = new Map<string, Point>()
  let orphanIndex = 0
  const orphanCount = nodes.filter((n) => !depth.has(n.id)).length
  for (const n of nodes) {
    if (depth.has(n.id)) {
      const d = depth.get(n.id) ?? 0
      const a = angle.get(n.id) ?? 0
      const r = d * RING_SPACING
      positions.set(n.id, { x: r * Math.cos(a), y: r * Math.sin(a) })
    } else {
      const a = (orphanIndex / Math.max(1, orphanCount)) * Math.PI * 2
      orphanIndex += 1
      const r = (Math.max(...depth.values(), 0) + 1) * RING_SPACING
      positions.set(n.id, { x: r * Math.cos(a), y: r * Math.sin(a) })
    }
  }
  return positions
}

function computeStarLayout(nodes: VocabularyGraphNode[], centerId: string): Map<string, Point> {
  const others = nodes.filter((n) => n.id !== centerId)
  const positions = new Map<string, Point>([[centerId, { x: 0, y: 0 }]])
  others.forEach((n, i) => {
    const a = (i / Math.max(1, others.length)) * Math.PI * 2
    positions.set(n.id, { x: FOCUS_RADIUS * Math.cos(a), y: FOCUS_RADIUS * Math.sin(a) })
  })
  return positions
}

type NodeData = { vocabNode: VocabularyGraphNode; selected: boolean; zoomBand: ZoomBand }

function selectionStyle(selected: boolean): React.CSSProperties {
  return selected ? { outline: '3px solid var(--series-1)', outlineOffset: 2 } : {}
}

function NodeLabel({ children, hidden, wide }: { children: React.ReactNode; hidden?: boolean; wide?: boolean }) {
  if (hidden) return null
  return (
    <div
      data-testid="node-label"
      data-wide={wide ? 'true' : 'false'}
      style={{
        position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)',
        marginTop: 4, fontSize: 10, color: 'var(--text-secondary)', whiteSpace: 'nowrap',
        maxWidth: wide ? 220 : 140, overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'center', pointerEvents: 'none',
      }}
    >
      {children}
    </div>
  )
}

function GroupNodeView({ data }: NodeProps<Node<NodeData>>) {
  const n = data.vocabNode
  if (n.kind !== 'group') return null
  return (
    <div
      style={{
        padding: '4px 12px', borderRadius: 999, border: `1px solid ${GROUP_COLOR}`,
        background: 'var(--surface-1)', color: 'var(--text-muted)', fontSize: 11, fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      {n.label} {n.id !== 'root' && <span style={{ fontWeight: 400 }}>({n.count})</span>}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  )
}

function ClusterNodeView({ data }: NodeProps<Node<NodeData>>) {
  const n = data.vocabNode
  if (n.kind !== 'pending_cluster') return null
  const color = n.priority_band ? BAND_COLOR[n.priority_band] : 'var(--text-muted)'
  const size = Math.max(24, Math.min(44, 24 + Math.log1p(n.observation_count ?? 0) * 4))
  const far = data.zoomBand === 'far'
  const close = data.zoomBand === 'close'
  // Real close-zoom detail only — every field here comes straight from the
  // graph API response (backend/app/vocabulary_graph.py's pending-cluster
  // node payload), never fabricated. role_count/observation_count/
  // surface_count are each independently optional on a cluster node, so
  // each is only rendered when actually present.
  const closeDetail = [
    n.role_count != null ? `${n.role_count} role${n.role_count === 1 ? '' : 's'}` : null,
    n.observation_count != null ? `${n.observation_count} obs` : null,
    n.surface_count != null ? `${n.surface_count} form${n.surface_count === 1 ? '' : 's'}` : null,
  ].filter((s): s is string => s !== null)
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div
        style={{
          width: size, height: size, borderRadius: '50%', background: `color-mix(in srgb, ${color} 22%, var(--surface-1))`,
          border: `2.5px solid ${color}`, cursor: 'pointer', ...selectionStyle(data.selected),
        }}
      />
      {/* Far zoom prioritises topology over detail (docs/25 §15): the
          cluster's own colour/size already carries priority + rough volume,
          so its text label is dropped entirely rather than cluttering an
          overview the same way medium/close zoom do. */}
      <NodeLabel hidden={far} wide={close}>
        <span>{n.label}</span>
        {close && closeDetail.length > 0 && (
          <span data-testid="cluster-close-detail" style={{ display: 'block', marginTop: 2, fontSize: 9, color: 'var(--text-muted)', fontWeight: 400 }}>
            {n.priority_band ? `${BAND_LABEL[n.priority_band]} · ` : ''}{closeDetail.join(' · ')}
          </span>
        )}
      </NodeLabel>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  )
}

function ConceptNodeView({ data }: NodeProps<Node<NodeData>>) {
  const n = data.vocabNode
  if (n.kind !== 'concept') return null
  const size = 30
  const far = data.zoomBand === 'far'
  const close = data.zoomBand === 'close'
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div
        style={{
          width: size, height: size, background: `color-mix(in srgb, ${CONCEPT_COLOR} 25%, var(--surface-1))`,
          border: `2.5px solid ${CONCEPT_COLOR}`, transform: 'rotate(45deg)', cursor: 'pointer',
          opacity: n.status === 'active' ? 1 : 0.55, ...selectionStyle(data.selected),
        }}
      />
      {/* Far zoom prioritises topology over detail (docs/25 §15): the
          diamond shape + colour already say "this is a concept"; its label
          is dropped entirely rather than cluttering an overview. */}
      <NodeLabel hidden={far} wide={close}>
        <span>{n.label}</span>
        {close && (
          <span data-testid="concept-close-detail" style={{ display: 'block', marginTop: 2, fontSize: 9, color: 'var(--text-muted)', fontWeight: 400 }}>
            {n.type_code}{n.alias_count !== undefined ? ` · ${n.alias_count} alias${n.alias_count === 1 ? '' : 'es'}` : ''}
          </span>
        )}
      </NodeLabel>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  )
}

function SurfaceFormNodeView({ data }: NodeProps<Node<NodeData>>) {
  const n = data.vocabNode
  if (n.kind !== 'surface_form') return null
  const size = 14
  const close = data.zoomBand === 'close'
  const color = n.status === 'accepted' ? CONCEPT_COLOR : 'var(--baseline)'
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div
        style={{
          width: size, height: size, borderRadius: '50%', background: 'var(--surface-1)',
          border: `1.5px solid ${color}`, cursor: 'pointer', ...selectionStyle(data.selected),
        }}
      />
      <NodeLabel hidden={data.zoomBand === 'far'} wide={close}>
        <span>{n.label}</span>
        {close && (
          <span data-testid="surface-form-close-detail" style={{ display: 'block', marginTop: 2, fontSize: 9, color: 'var(--text-muted)', fontWeight: 400 }}>
            {n.status}{n.observation_count != null ? ` · ${n.observation_count} obs` : ''}
          </span>
        )}
      </NodeLabel>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  )
}

function tooltipContent(n: VocabularyGraphNode): { title: string; lines: string[] } | null {
  if (n.kind === 'surface_form') {
    const lines = [n.status === 'accepted' ? 'Accepted surface form' : 'Pending surface form']
    if (n.observation_count != null) lines.push(`${n.observation_count} observation${n.observation_count === 1 ? '' : 's'}`)
    return { title: n.label, lines }
  }
  if (n.kind === 'pending_cluster') {
    const lines: string[] = []
    if (n.priority_band) lines.push(`${BAND_LABEL[n.priority_band]} priority`)
    const counts = [
      n.role_count != null ? `${n.role_count} role${n.role_count === 1 ? '' : 's'}` : null,
      n.observation_count != null ? `${n.observation_count} observation${n.observation_count === 1 ? '' : 's'}` : null,
    ].filter((s): s is string => s !== null)
    if (counts.length) lines.push(counts.join(' · '))
    return { title: n.label, lines }
  }
  if (n.kind === 'concept') {
    const lines = [n.type_code]
    if (n.alias_count !== undefined) lines.push(`${n.alias_count} alias${n.alias_count === 1 ? '' : 'es'}`)
    return { title: n.label, lines }
  }
  return null
}

function NodeTooltip({ x, y, content }: { x: number; y: number; content: { title: string; lines: string[] } }) {
  return (
    <div
      className="card"
      data-testid="node-tooltip"
      style={{
        position: 'absolute', left: x + 14, top: y + 14, zIndex: 20, pointerEvents: 'none',
        padding: '6px 10px', maxWidth: 220, fontSize: 12, boxShadow: '0 4px 16px rgba(0, 0, 0, 0.18)',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: content.lines.length ? 2 : 0 }}>{content.title}</div>
      {content.lines.map((line, i) => (
        <div key={i} className="muted" style={{ fontSize: 11 }}>
          {line}
        </div>
      ))}
    </div>
  )
}

const NODE_TYPES = {
  group: GroupNodeView,
  pending_cluster: ClusterNodeView,
  concept: ConceptNodeView,
  surface_form: SurfaceFormNodeView,
}

function edgeStyleFor(relation: VocabularyGraphEdge['relation']): { stroke: string; strokeWidth: number; strokeDasharray?: string; opacity?: number } {
  switch (relation) {
    case 'contains':
      return { stroke: 'var(--gridline)', strokeWidth: 1 }
    case 'member_of':
      return { stroke: 'var(--text-secondary)', strokeWidth: 1.5 }
    case 'maps_to':
      return { stroke: CONCEPT_COLOR, strokeWidth: 3 }
    case 'alias_of':
      return { stroke: CONCEPT_COLOR, strokeWidth: 2, strokeDasharray: '6,3' }
    case 'similar_to':
      return { stroke: 'var(--text-muted)', strokeWidth: 1, strokeDasharray: '1,4', opacity: 0.85 }
    case 'ontology':
      return { stroke: 'var(--series-2)', strokeWidth: 1.5, strokeDasharray: '8,3,2,3' }
    default:
      return { stroke: 'var(--gridline)', strokeWidth: 1 }
  }
}

function GraphCanvas({
  nodes,
  edges,
  selectedId,
  onSelect,
  onReady,
  initialViewport,
  onViewportChange,
}: {
  nodes: VocabularyGraphNode[]
  edges: VocabularyGraphEdge[]
  selectedId: string | null
  onSelect: (node: VocabularyGraphNode | null) => void
  onReady?: (api: GraphApi) => void
  initialViewport?: Viewport | null
  onViewportChange?: (viewport: Viewport) => void
}) {
  const focusCenter = nodes.find((n) => 'focus' in n && (n as { focus?: boolean }).focus)?.id ?? null

  const positions = useMemo(
    () => (focusCenter ? computeStarLayout(nodes, focusCenter) : computeRadialLayout(nodes, edges)),
    [nodes, edges, focusCenter],
  )

  const [zoomBand, setZoomBand] = useState<ZoomBand>('medium')
  const zoomBandRef = useRef<ZoomBand>('medium')

  const syncZoomBand = useCallback((zoom: number) => {
    const band = getZoomBand(zoom)
    if (band !== zoomBandRef.current) {
      zoomBandRef.current = band
      setZoomBand(band)
    }
  }, [])

  const handleInit = useCallback(
    (instance: ReactFlowInstance<Node<NodeData>, Edge>) => {
      const viewport = instance.getViewport()
      syncZoomBand(viewport.zoom)
      onViewportChange?.(viewport)
      onReady?.({ fitView: (options) => instance.fitView(options) })
    },
    [syncZoomBand, onViewportChange, onReady],
  )

  const handleMove = useCallback(
    (_event: unknown, viewport: Viewport) => {
      syncZoomBand(viewport.zoom)
      onViewportChange?.(viewport)
    },
    [syncZoomBand, onViewportChange],
  )

  const rfNodes: Node<NodeData>[] = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.id,
        type: n.kind,
        position: positions.get(n.id) ?? { x: 0, y: 0 },
        data: { vocabNode: n, selected: n.id === selectedId, zoomBand },
        draggable: false,
        selectable: n.kind !== 'group',
        connectable: false,
      })),
    [nodes, positions, selectedId, zoomBand],
  )

  const rfEdges: Edge[] = useMemo(
    () =>
      edges.map((e, i) => {
        const style = edgeStyleFor(e.relation)
        const label = e.relation === 'similar_to' && e.similarity !== undefined ? e.similarity.toFixed(2) : undefined
        return {
          id: `${e.source}->${e.target}:${e.relation}:${i}`,
          source: e.source,
          target: e.target,
          type: 'straight',
          style,
          label,
          labelStyle: { fontSize: 9, fill: 'var(--text-muted)' },
          labelBgStyle: { fill: 'var(--surface-1)', fillOpacity: 0.8 },
        }
      }),
    [edges],
  )

  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const containerRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null)

  const handleNodeMouseEnter = useCallback(
    (event: React.MouseEvent, node: Node) => {
      const original = nodeById.get(node.id)
      if (!original || original.kind === 'group') return
      const rect = containerRef.current?.getBoundingClientRect()
      setHover({ id: node.id, x: event.clientX - (rect?.left ?? 0), y: event.clientY - (rect?.top ?? 0) })
    },
    [nodeById],
  )
  const handleNodeMouseLeave = useCallback(() => setHover(null), [])

  const hoveredNode = hover ? nodeById.get(hover.id) : null
  const hoveredTooltip = zoomBand === 'close' && hoveredNode ? tooltipContent(hoveredNode) : null

  return (
    <div ref={containerRef} style={{ position: 'relative', width: '100%', height: '100%' }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        onNodeClick={(_evt, node) => {
          const original = nodeById.get(node.id)
          if (original && original.kind !== 'group') onSelect(original)
        }}
        onPaneClick={() => onSelect(null)}
        onNodeMouseEnter={handleNodeMouseEnter}
        onNodeMouseLeave={handleNodeMouseLeave}
        onInit={handleInit}
        onMove={handleMove}
        fitView={!initialViewport}
        fitViewOptions={{ padding: 0.25 }}
        defaultViewport={initialViewport ?? undefined}
        minZoom={0.15}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} color="var(--gridline)" />
        <Controls showInteractive={false} />
      </ReactFlow>
      {hover && hoveredTooltip && <NodeTooltip x={hover.x} y={hover.y} content={hoveredTooltip} />}
    </div>
  )
}

export default function VocabularyGraph(props: {
  nodes: VocabularyGraphNode[]
  edges: VocabularyGraphEdge[]
  selectedId: string | null
  onSelect: (node: VocabularyGraphNode | null) => void
  onReady?: (api: GraphApi) => void
  initialViewport?: Viewport | null
  onViewportChange?: (viewport: Viewport) => void
}) {
  return (
    <ReactFlowProvider>
      <GraphCanvas {...props} />
    </ReactFlowProvider>
  )
}
