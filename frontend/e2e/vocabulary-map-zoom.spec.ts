import { expect, test } from '@playwright/test'
import { ACCEPTED_GRAPH, COMBINED_GRAPH, mockVocabularyApi, PENDING_GRAPH, readZoom, zoomUntil } from './fixtures'

// Real-browser regression coverage for Vocabulary Map semantic zoom
// (docs/25-vocabulary-map.md §15-17), which VocabularyMapView.test.tsx
// (vitest + jsdom) deliberately cannot cover — that suite mocks the whole
// VocabularyGraph component (see its own top comment) specifically because
// real React Flow zoom/label/tooltip rendering needs a real browser.

async function openMapTab(page: import('@playwright/test').Page) {
  await page.goto('/vocabulary')
  await page.getByRole('button', { name: 'Map' }).click()
  await expect(page.locator('.react-flow__viewport')).toBeVisible()
}

test.describe('Pending view', () => {
  test.beforeEach(async ({ page }) => {
    await mockVocabularyApi(page, () => PENDING_GRAPH)
  })

  test('far zoom hides cluster/surface-form labels; medium shows them; close adds real detail', async ({ page }) => {
    await openMapTab(page)

    // Far: topology only — no node labels at all (docs/25 §15 "far:
    // prioritise topology, hide low-value detail").
    await zoomUntil(page, 'out', (z) => z < 0.55)
    await expect(page.getByTestId('node-label')).toHaveCount(0)

    // Medium: normal surface-form/cluster labels reappear, but no
    // close-only detail sub-lines yet.
    await zoomUntil(page, 'in', (z) => z >= 0.55 && z < 1.05)
    await expect(page.getByText('stochastic modelling')).toBeVisible()
    await expect(page.getByText('stochastic reserving')).toBeVisible()
    await expect(page.getByTestId('cluster-close-detail')).toHaveCount(0)

    // Close: genuinely richer detail, not merely larger text — the
    // cluster's real role_count/observation_count/surface_count/
    // priority_band, and the surface form's real status, both actually
    // present in the DOM without needing to hover.
    await zoomUntil(page, 'in', (z) => z >= 1.05)
    const clusterDetail = page.getByTestId('cluster-close-detail')
    await expect(clusterDetail).toBeVisible()
    await expect(clusterDetail).toContainText('12 roles')
    await expect(clusterDetail).toContainText('37 obs')
    await expect(clusterDetail).toContainText('4 forms')
    await expect(clusterDetail).toContainText('High')

    const surfaceDetail = page.getByTestId('surface-form-close-detail')
    await expect(surfaceDetail).toBeVisible()
    await expect(surfaceDetail).toContainText('pending')
  })

  test('hovering a node at close zoom shows a tooltip with real fields', async ({ page }) => {
    await openMapTab(page)
    await zoomUntil(page, 'in', (z) => z >= 1.05)
    // The node label itself is deliberately pointer-events:none
    // (VocabularyGraph.tsx's NodeLabel) so it never blocks the shape or
    // steals a click meant for the node — hover the actual node (React
    // Flow's own wrapper, addressed by data-id) instead of its text.
    await page.locator('.react-flow__node[data-id="cluster-1"]').hover()
    const tooltip = page.getByTestId('node-tooltip')
    await expect(tooltip).toBeVisible()
    await expect(tooltip).toContainText('stochastic modelling')
    await expect(tooltip).toContainText('High priority')
  })
})

test.describe('Accepted view', () => {
  test.beforeEach(async ({ page }) => {
    await mockVocabularyApi(page, () => ACCEPTED_GRAPH)
  })

  test('close zoom shows real concept type/alias detail and surface-form observation detail', async ({ page }) => {
    await openMapTab(page)
    // Field's implicit <label> wraps both its text and the <select>, which
    // makes the accessible name include every <option>'s text too (e.g.
    // "ViewPendingAcceptedCombined") — not unique enough for getByLabel.
    // Target the "View" field's select by DOM structure instead.
    await page.locator('xpath=//label[.//span[text()="View"]]/select').selectOption('accepted')
    await expect(page.getByText('Solvency II')).toBeVisible()

    await zoomUntil(page, 'in', (z) => z >= 1.05)
    const conceptDetail = page.getByTestId('concept-close-detail')
    await expect(conceptDetail).toBeVisible()
    await expect(conceptDetail).toContainText('domain_knowledge')
    await expect(conceptDetail).toContainText('5 aliases')

    const surfaceDetail = page.getByTestId('surface-form-close-detail')
    await expect(surfaceDetail).toBeVisible()
    await expect(surfaceDetail).toContainText('accepted')
    await expect(surfaceDetail).toContainText('14 obs')
  })
})

test.describe('Combined view', () => {
  test.beforeEach(async ({ page }) => {
    await mockVocabularyApi(page, () => COMBINED_GRAPH)
  })

  test('renders both pending-cluster and concept node kinds with their own close-zoom detail', async ({ page }) => {
    await openMapTab(page)
    await zoomUntil(page, 'in', (z) => z >= 1.05)
    await expect(page.getByTestId('cluster-close-detail')).toBeVisible()
    await expect(page.getByTestId('concept-close-detail')).toBeVisible()
  })
})

test.describe('Zoom mechanics', () => {
  test.beforeEach(async ({ page }) => {
    await mockVocabularyApi(page, () => PENDING_GRAPH)
  })

  test('crossing zoom-band thresholds never issues an extra graph API call', async ({ page }) => {
    let graphCalls = 0
    await mockVocabularyApi(page, () => PENDING_GRAPH)
    await page.route('**/api/vocabulary/graph**', async (route) => {
      graphCalls += 1
      await route.fulfill({ json: PENDING_GRAPH })
    })

    await openMapTab(page)
    const callsAfterLoad = graphCalls
    expect(callsAfterLoad).toBeGreaterThan(0)

    await zoomUntil(page, 'out', (z) => z < 0.55)
    await zoomUntil(page, 'in', (z) => z >= 0.55 && z < 1.05)
    await zoomUntil(page, 'in', (z) => z >= 1.05)
    await zoomUntil(page, 'out', (z) => z < 0.55)

    // Zoom-band transitions are purely client-side re-rendering
    // (VocabularyGraph.tsx's own zoomBand state) — never a fresh fetch.
    expect(graphCalls).toBe(callsAfterLoad)
  })

  test('semantic zoom still works in fullscreen, and viewport preservation does not block band transitions', async ({ page }) => {
    await openMapTab(page)

    // Establish a close-zoom state embedded, then enter fullscreen — the
    // instance remounts (VocabularyMapView.tsx preserves the viewport via
    // a ref, restored as the new mount's initialViewport) and must still
    // both preserve *and* keep responding to zoom changes afterwards.
    await zoomUntil(page, 'in', (z) => z >= 1.05)
    await page.getByRole('button', { name: /fullscreen/i }).click()
    await expect(page.locator('.react-flow__viewport')).toBeVisible()

    const restoredZoom = await readZoom(page)
    expect(restoredZoom).toBeGreaterThanOrEqual(1.05) // preserved across the remount

    await expect(page.getByTestId('cluster-close-detail')).toBeVisible() // still close-band detail, not reset to medium

    // And the band can still change after the remount — preservation must
    // not freeze future zoom-band transitions.
    await zoomUntil(page, 'out', (z) => z < 0.55)
    await expect(page.getByTestId('node-label')).toHaveCount(0)
  })
})
