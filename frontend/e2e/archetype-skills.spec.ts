import { test, expect } from '@playwright/test'

test('archetype radar renders and its target skillset can be saved', async ({ page }) => {
  const axes = ['Pricing strategy', 'Risk modelling', 'Stakeholder communication', 'Data analysis', 'Regulatory knowledge', 'Team leadership'].map((name, i) => ({
    id: `skill-${i}`, canonical_name: name, type_code: 'method', role_count: 5 - i % 3,
    status: ['evidenced', 'partial', 'user_asserted', 'not_found'][i % 4], coverage: [100, 50, 25, 0][i % 4], target: 100,
  }))
  let saved = false
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    const json = path === '/api/auth/status' ? { authenticated: true }
      : path === '/api/archetypes' ? [{ id: 'actuary', canonical_name: 'Senior pricing actuary' }]
        : path.endsWith('/skill-profile') ? (() => {
          if (route.request().method() === 'PUT') {
            expect(route.request().postDataJSON().concept_ids).toHaveLength(6)
            saved = true
          }
          return { archetype_id: 'actuary', source: saved ? 'saved' : 'suggested', sample_role_count: 7, unavailable_skill_ids: [], axes, candidates: axes }
        })() : []
    return route.fulfill({ json })
  })
  await page.goto('/coverage')
  await expect(page.getByRole('heading', { name: 'My skills vs a typical role' })).toBeVisible()
  await expect(page.locator('.recharts-radar')).toHaveCount(2)
  await expect(page.getByRole('table')).toContainText('Risk modelling')
  await page.screenshot({ path: 'test-results/archetype-skills-desktop.png', fullPage: true })
  await page.getByRole('button', { name: 'Edit target skillset' }).click()
  await page.getByRole('button', { name: 'Save target skillset' }).click()
  await expect(page.getByText('Target skillset saved for this archetype.')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.recharts-radar')).toHaveCount(2)
  await page.screenshot({ path: 'test-results/archetype-skills-mobile.png', fullPage: true })
})
