import type { VocabularyGraphEdge, VocabularyGraphNode } from '../../lib/api'

// Layout helpers for VocabularyGraph.tsx, split into their own module so
// that file only ever exports React components (oxlint's
// react/only-export-components — needed for Fast Refresh) while
// `computeRadialLayout` stays independently unit-testable
// (VocabularyGraph.layout.test.ts) without rendering anything.

const HIERARCHY_RELATIONS = new Set(['contains', 'member_of', 'maps_to', 'alias_of'])
const RING_SPACING = 150

// Minimum world-space arc length (px, before any on-screen zoom) every node
// gets within its own angular slice — see computeRadialLayout's own comment
// for why this, not RING_SPACING alone, is what actually prevents overlap
// at high node density. Capped by MAX_RING_RADIUS so one pathologically
// large branch (e.g. one cluster with hundreds of surface forms) can't blow
// layout coordinates up without bound; some residual crowding in that one
// branch is an acceptable trade-off for keeping the rest of the graph at a
// sane scale, and it's still strictly better than the pre-fix "every
// sibling gets an identical slice regardless of count" behaviour.
const MIN_ARC_LENGTH = 26
const MAX_RING_RADIUS = 6000

export type Point = { x: number; y: number }

// A small hand-rolled "balloon" radial-tree layout (brief §5's "raw words
// toward the outside, organising vocabulary inward") computed from the
// *structural* edges (contains/member_of/maps_to/alias_of) via BFS from the
// synthetic "root" node. No layout dependency (dagre/elkjs) was added: at a
// few hundred nodes this closed-form approach is fast, deterministic, and
// matches the brief's own concentric-rings diagram.
//
// Two properties make dense, unevenly-branching real vocabulary data (one
// priority band with a handful of clusters, another with hundreds; one
// cluster with two surface forms, another with fifty) actually legible
// instead of visually merging into overlapping text/edges no matter how far
// a user zooms in — the root cause of a reported "looks like a scaled
// image, never sharper" regression traced back to this function's earlier,
// simpler version:
//
// 1. **Weighted angular allocation.** A parent's angular slice is divided
//    among its children in proportion to each child's own subtree size
//    (leaf-descendant count), not split evenly — a cluster with fifty
//    surface forms gets proportionally more room than a sibling cluster
//    with two, rather than both being squeezed into an identical slice.
// 2. **Per-parent (not per-depth), density-aware ring radius.** The
//    previous version placed every node at a fixed `depth * RING_SPACING`,
//    regardless of how many siblings it had — with enough siblings, their
//    angular separation shrinks to a fraction of a degree, and since
//    on-screen zoom scales *pixel* distance, not the underlying
//    world-space separation between nodes, no amount of zooming in can
//    ever visually pull two such siblings apart. Each node's children are
//    now placed at `own radius + max(RING_SPACING, whatever the *most
//    tightly-sliced* one of THIS node's own children needs for at least
//    MIN_ARC_LENGTH px of real separation)` — computed per parent, not
//    shared across the whole depth, so one crowded branch (a cluster with
//    fifty surface forms) doesn't inflate the radius for every *other*
//    branch's children too; a sparse sibling cluster's children stay close
//    to it.
export function computeRadialLayout(nodes: VocabularyGraphNode[], edges: VocabularyGraphEdge[]): Map<string, Point> {
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
  const reached = new Set<string>()
  if (rootId) {
    reached.add(rootId)
    const queue = [rootId]
    while (queue.length) {
      const cur = queue.shift()!
      for (const child of childrenOf.get(cur) ?? []) {
        if (!reached.has(child)) {
          reached.add(child)
          queue.push(child)
        }
      }
    }
  }

  const weight = new Map<string, number>()
  const computeWeight = (id: string): number => {
    const cached = weight.get(id)
    if (cached !== undefined) return cached
    const kids = childrenOf.get(id) ?? []
    const w = kids.length ? kids.reduce((sum, k) => sum + computeWeight(k), 0) : 1
    weight.set(id, w)
    return w
  }
  if (rootId) computeWeight(rootId)

  const positions = new Map<string, Point>()
  let maxRadiusUsed = 0
  const layout = (id: string, radius: number, start: number, end: number) => {
    const mid = (start + end) / 2
    positions.set(id, { x: radius * Math.cos(mid), y: radius * Math.sin(mid) })
    maxRadiusUsed = Math.max(maxRadiusUsed, radius)
    const kids = childrenOf.get(id) ?? []
    if (!kids.length) return
    const totalWeight = kids.reduce((sum, k) => sum + computeWeight(k), 0) || kids.length
    const slices = kids.map((kid) => ((end - start) * computeWeight(kid)) / totalWeight)
    const narrowestSlice = Math.min(...slices)
    const neededRadius = narrowestSlice > 0 ? Math.min(MIN_ARC_LENGTH / narrowestSlice, MAX_RING_RADIUS) : RING_SPACING
    const childRadius = Math.max(radius + RING_SPACING, neededRadius)
    let cursor = start
    kids.forEach((kid, i) => {
      layout(kid, childRadius, cursor, cursor + slices[i])
      cursor += slices[i]
    })
  }
  if (rootId) layout(rootId, 0, 0, Math.PI * 2)

  let orphanIndex = 0
  const orphanCount = nodes.filter((n) => !reached.has(n.id)).length
  const orphanRadius = maxRadiusUsed + RING_SPACING
  for (const n of nodes) {
    if (!reached.has(n.id)) {
      const a = (orphanIndex / Math.max(1, orphanCount)) * Math.PI * 2
      orphanIndex += 1
      positions.set(n.id, { x: orphanRadius * Math.cos(a), y: orphanRadius * Math.sin(a) })
    }
  }
  return positions
}

const FOCUS_RADIUS = 220

export function computeStarLayout(nodes: VocabularyGraphNode[], centerId: string): Map<string, Point> {
  const others = nodes.filter((n) => n.id !== centerId)
  const positions = new Map<string, Point>([[centerId, { x: 0, y: 0 }]])
  others.forEach((n, i) => {
    const a = (i / Math.max(1, others.length)) * Math.PI * 2
    positions.set(n.id, { x: FOCUS_RADIUS * Math.cos(a), y: FOCUS_RADIUS * Math.sin(a) })
  })
  return positions
}
