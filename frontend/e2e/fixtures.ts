import type { Page } from '@playwright/test'
import type { VocabularyGraphResponse } from '../src/lib/api'

// Fixture payloads shaped exactly like backend/app/vocabulary_graph.py's
// real per-node-kind fields (see docs/25-vocabulary-map.md §"Graph shape"
// and the field-availability audit behind this build's close-zoom work) —
// nothing here invents a field the real API never sends. Used to drive the
// real @xyflow/react graph in a real browser without a running backend.

export const PENDING_GRAPH: VocabularyGraphResponse = {
  meta: { total_nodes: 3, returned_nodes: 3, returned_edges: 2, truncated: false, status: 'pending', group_by: 'priority' },
  nodes: [
    {
      id: 'root', kind: 'group', label: 'Pending', count: 1,
    },
    {
      id: 'cluster-1', kind: 'pending_cluster', label: 'stochastic modelling', cluster_key: 'stochastic-modelling',
      priority_band: 'high', priority_score: 0.91, surface_count: 4, observation_count: 37, role_count: 12,
      country_count: 3, flags: [], target: { type: 'cluster_review', id: 'stochastic-modelling' },
    },
    {
      id: 'surface-1', kind: 'surface_form', label: 'stochastic reserving', status: 'pending',
      target: { type: 'surface_form', value: 'stochastic reserving' },
    },
  ],
  edges: [
    { source: 'root', target: 'cluster-1', relation: 'contains' },
    { source: 'surface-1', target: 'cluster-1', relation: 'member_of' },
  ],
}

export const ACCEPTED_GRAPH: VocabularyGraphResponse = {
  meta: { total_nodes: 3, returned_nodes: 3, returned_edges: 2, truncated: false, status: 'accepted', group_by: 'type' },
  nodes: [
    { id: 'root', kind: 'group', label: 'Accepted', count: 1 },
    {
      id: 'concept-1', kind: 'concept', label: 'Solvency II', type_code: 'domain_knowledge', status: 'active',
      alias_count: 5, target: { type: 'concept', id: 'concept-1' },
    },
    {
      id: 'surface-2', kind: 'surface_form', label: 'Solvency 2 compliance', status: 'accepted',
      observation_count: 14, target: { type: 'concept', id: 'concept-1' },
    },
  ],
  edges: [
    { source: 'root', target: 'concept-1', relation: 'contains' },
    { source: 'surface-2', target: 'concept-1', relation: 'alias_of' },
  ],
}

export const COMBINED_GRAPH: VocabularyGraphResponse = {
  meta: { total_nodes: 5, returned_nodes: 5, returned_edges: 4, truncated: false, status: 'combined', group_by: 'priority' },
  nodes: [
    { id: 'root', kind: 'group', label: 'Vocabulary', count: 2 },
    ...PENDING_GRAPH.nodes.filter((n) => n.id !== 'root'),
    ...ACCEPTED_GRAPH.nodes.filter((n) => n.id !== 'root'),
  ],
  edges: [
    { source: 'root', target: 'cluster-1', relation: 'contains' },
    { source: 'surface-1', target: 'cluster-1', relation: 'member_of' },
    { source: 'root', target: 'concept-1', relation: 'contains' },
    { source: 'surface-2', target: 'concept-1', relation: 'alias_of' },
  ],
}

/** Serves every network call the Vocabulary page needs to reach the Map tab
 * and render a real graph, without a running backend/database. `graphFor`
 * lets a test swap the fixture per `status` query param (pending/accepted/
 * combined) so one page load can exercise all three views via the map's own
 * status control. */
export async function mockVocabularyApi(page: Page, graphFor: (status: string) => VocabularyGraphResponse = () => PENDING_GRAPH) {
  await page.route('**/api/auth/status', (route) => route.fulfill({ json: { authenticated: true } }))
  await page.route('**/api/vocabulary/graph**', (route) => {
    const url = new URL(route.request().url())
    const status = url.searchParams.get('status') ?? 'pending'
    route.fulfill({ json: graphFor(status) })
  })
  // VocabularyReviewView stays mounted (hidden) behind the Map tab
  // (Vocabulary.tsx) and fires its own requests on mount — never asserted
  // on by these tests, but left unmocked they'd otherwise surface as noisy
  // failed-request console errors. A generic 404 keeps it quiet without
  // pretending to be a real review payload.
  await page.route('**/api/vocabulary/clusters**', (route) => route.fulfill({ status: 404, json: { detail: 'not mocked' } }))
  await page.route('**/api/vocabulary/progress**', (route) => route.fulfill({ status: 404, json: { detail: 'not mocked' } }))
  await page.route('**/api/vocabulary/methodology**', (route) => route.fulfill({ status: 404, json: { detail: 'not mocked' } }))
  await page.route('**/api/concepts**', (route) => route.fulfill({ status: 404, json: { detail: 'not mocked' } }))
  // Registered last so it wins over the broad **/api/concepts** catch-all
  // above (Playwright uses the most-recently-registered matching handler)
  // — Vocabulary.tsx's own conceptTypes fetch needs a real empty list, not
  // a 404, or the page never gets past its loading state for that prop.
  await page.route('**/api/concepts/types', (route) => route.fulfill({ json: [] }))
}

/** Reads the live zoom factor straight from React Flow's own viewport
 * transform (`translate(x,y) scale(zoom)`) — the same, and only, value
 * VocabularyGraph.tsx's `onMove`/`onInit` ever read (docs/25 §15: "never a
 * second measurement mechanism"), so this reads the real rendered state
 * rather than any test-side approximation of it. */
export async function readZoom(page: Page): Promise<number> {
  const transform = await page.locator('.react-flow__viewport').getAttribute('style')
  const match = transform?.match(/scale\(([\d.]+)\)/)
  if (!match) throw new Error(`could not read zoom from viewport style: ${transform}`)
  return Number(match[1])
}

/** Clicks the React Flow Controls zoom-out/in button repeatedly until the
 * live zoom crosses into the target band (far: <0.55, medium: 0.55-<1.05,
 * close: >=1.05 — docs/25 §15's own thresholds), or until zoom stops
 * changing (clamped at minZoom/maxZoom). Never assumes a fixed step size
 * per click — only ever trusts the real rendered zoom value. */
export async function zoomUntil(page: Page, direction: 'in' | 'out', predicate: (zoom: number) => boolean, maxClicks = 40) {
  const button = page.locator(`.react-flow__controls-zoom${direction}`)
  for (let i = 0; i < maxClicks; i++) {
    const zoom = await readZoom(page)
    if (predicate(zoom)) return zoom
    const before = zoom
    await button.click()
    await page.waitForTimeout(30)
    const after = await readZoom(page)
    if (after === before) break // clamped at min/maxZoom — nothing more to do
  }
  const finalZoom = await readZoom(page)
  if (!predicate(finalZoom)) throw new Error(`zoom ${finalZoom} never reached the target band after ${maxClicks} clicks`)
  return finalZoom
}
