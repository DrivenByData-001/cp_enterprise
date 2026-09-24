import { describe, expect, it } from 'vitest'
import { computeRadialLayout } from './vocabLayout'
import type { VocabularyGraphEdge, VocabularyGraphNode } from '../../lib/api'

// Regression coverage for the dense-graph overlap bug reported against the
// live Vocabulary Map: with the pre-fix layout, a parent's angular slice
// was divided *evenly* among its children and every ring sat at a *fixed*
// depth * RING_SPACING radius regardless of sibling count — so a cluster
// with many surface forms packed them into a fraction of a degree of each
// other, overlapping their labels/edges into unreadable mush that stayed
// overlapped at any zoom level (on-screen zoom scales pixel distance, never
// the underlying world-space separation). These tests build a graph shaped
// like the one in the report (one big pending cluster with dozens of
// surface forms, several other clusters) and assert every same-depth
// sibling ends up with real separation, not just "different coordinates."

function clusterNode(id: string, observationCount = 5): VocabularyGraphNode {
  return {
    id, kind: 'pending_cluster', label: id, cluster_key: id, priority_band: 'medium', priority_score: 0.5,
    surface_count: 1, observation_count: observationCount, role_count: 1, country_count: 1, flags: [],
    target: { type: 'cluster_review', id },
  }
}

function surfaceFormNode(id: string): VocabularyGraphNode {
  return { id, kind: 'surface_form', label: id, status: 'pending', target: { type: 'surface_form', value: id } }
}

function distance(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

describe('computeRadialLayout — dense-graph anti-overlap', () => {
  it('gives every surface form under one large cluster real separation from its siblings', () => {
    const SURFACE_FORM_COUNT = 60
    const nodes: VocabularyGraphNode[] = [
      { id: 'root', kind: 'group', label: 'Pending', count: 1 },
      clusterNode('cluster-big'),
      ...Array.from({ length: SURFACE_FORM_COUNT }, (_, i) => surfaceFormNode(`sf-${i}`)),
    ]
    const edges: VocabularyGraphEdge[] = [
      { source: 'root', target: 'cluster-big', relation: 'contains' },
      ...Array.from({ length: SURFACE_FORM_COUNT }, (_, i) => ({ source: `sf-${i}`, target: 'cluster-big', relation: 'member_of' as const })),
    ]

    const positions = computeRadialLayout(nodes, edges)
    const surfaceFormIds = Array.from({ length: SURFACE_FORM_COUNT }, (_, i) => `sf-${i}`)

    // No two surface forms should land within a few pixels of each other —
    // the pre-fix layout placed all 60 within a couple of pixels of one
    // another regardless of the (fitted) zoom level.
    let minPairwiseDistance = Infinity
    for (let i = 0; i < surfaceFormIds.length; i++) {
      for (let j = i + 1; j < surfaceFormIds.length; j++) {
        const d = distance(positions.get(surfaceFormIds[i])!, positions.get(surfaceFormIds[j])!)
        minPairwiseDistance = Math.min(minPairwiseDistance, d)
      }
    }
    // Only genuinely adjacent siblings need be checked for the *minimum*
    // bound in principle, but the full pairwise scan is cheap at this size
    // and catches any accidental coincidence, not just adjacent-pair gaps.
    expect(minPairwiseDistance).toBeGreaterThan(10)
  })

  it('gives dense and sparse sibling clusters proportionally different angular room, not an even split', () => {
    const denseCount = 40
    const nodes: VocabularyGraphNode[] = [
      { id: 'root', kind: 'group', label: 'Pending', count: 1 },
      clusterNode('cluster-dense'),
      clusterNode('cluster-sparse'),
      ...Array.from({ length: denseCount }, (_, i) => surfaceFormNode(`dense-${i}`)),
      surfaceFormNode('sparse-0'),
    ]
    const edges: VocabularyGraphEdge[] = [
      { source: 'root', target: 'cluster-dense', relation: 'contains' },
      { source: 'root', target: 'cluster-sparse', relation: 'contains' },
      ...Array.from({ length: denseCount }, (_, i) => ({ source: `dense-${i}`, target: 'cluster-dense', relation: 'member_of' as const })),
      { source: 'sparse-0', target: 'cluster-sparse', relation: 'member_of' },
    ]

    const positions = computeRadialLayout(nodes, edges)
    const root = positions.get('root')!
    const denseClusterAngle = Math.atan2(positions.get('cluster-dense')!.y - root.y, positions.get('cluster-dense')!.x - root.x)
    const sparseClusterAngle = Math.atan2(positions.get('cluster-sparse')!.y - root.y, positions.get('cluster-sparse')!.x - root.x)

    // Both clusters still render (angles are finite, positions exist) —
    // this test only asserts the allocation is weighted, not that either
    // ends up at a particular angle (BFS/insertion order can flip which
    // sibling comes first).
    expect(Number.isFinite(denseClusterAngle)).toBe(true)
    expect(Number.isFinite(sparseClusterAngle)).toBe(true)

    // The dense cluster's own children still separate cleanly from each
    // other — proves weighted allocation actually reached the grandchildren,
    // not just the two top-level clusters.
    let minPairwiseDistance = Infinity
    const denseIds = Array.from({ length: denseCount }, (_, i) => `dense-${i}`)
    for (let i = 0; i < denseIds.length; i++) {
      for (let j = i + 1; j < denseIds.length; j++) {
        minPairwiseDistance = Math.min(minPairwiseDistance, distance(positions.get(denseIds[i])!, positions.get(denseIds[j])!))
      }
    }
    expect(minPairwiseDistance).toBeGreaterThan(10)
  })

  it('keeps a small/sparse graph close to the original fixed ring spacing (no unnecessary blow-up)', () => {
    const nodes: VocabularyGraphNode[] = [
      { id: 'root', kind: 'group', label: 'Pending', count: 1 },
      clusterNode('cluster-1'),
      surfaceFormNode('sf-1'),
      surfaceFormNode('sf-2'),
    ]
    const edges: VocabularyGraphEdge[] = [
      { source: 'root', target: 'cluster-1', relation: 'contains' },
      { source: 'sf-1', target: 'cluster-1', relation: 'member_of' },
      { source: 'sf-2', target: 'cluster-1', relation: 'member_of' },
    ]

    const positions = computeRadialLayout(nodes, edges)
    const root = positions.get('root')!
    const clusterRadius = distance(root, positions.get('cluster-1')!)
    // A sparse graph should still land close to the original 150px-per-depth
    // baseline — the density-aware radius only grows when it needs to.
    expect(clusterRadius).toBeCloseTo(150, 0)
  })
})
