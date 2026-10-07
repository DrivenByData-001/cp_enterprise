import { test, expect } from '@playwright/test'

test('review a salary estimate with role details on desktop and mobile', async ({ page }) => {
  let saved: Record<string, unknown> | null = null
  const unexpected: string[] = []
  const role = { id: 'role-1', title: 'Pricing Actuary', organisation: 'Hannover Re', location: 'Dublin', country: 'Ireland' }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const reply = (json: unknown) => route.fulfill({ json })
    if (path === '/api/auth/status') return reply({ authenticated: true })
    if (path === '/api/roles/role-1') return reply(role)
    if (path.endsWith('/requirements')) return reply({ items: [], review_summary: { complete: true, unresolved_proposals: 0, needs_reextraction: 0 } })
    if (path.endsWith('/metadata/propose')) return reply({ status: 'ok', proposal: role,
      compensation: { error: null, reason: 'No usable salary is stated.', model: 'gpt-5.4-mini', evidence: [],
        review: { run_id: 'run-1', stated_items: [], estimate: { amount_min: 85000, amount_max: 115000,
          currency: 'EUR', pay_period: 'annual', employment_basis: 'permanent', rationale: 'Pricing responsibilities and seniority in Dublin.',
          assumptions: 'Gross annual base salary, excluding bonus. General knowledge; no live market search.', confidence: 'low', evidence_ids: [] } } } })
    if (path.endsWith('/metadata') && route.request().method() === 'PATCH') {
      saved = route.request().postDataJSON(); return reply(role)
    }
    unexpected.push(path); return route.fulfill({ status: 404, json: { detail: path } })
  })
  await page.goto('/role-instances/role-1/requirements?step=details')
  await page.getByRole('button', { name: 'Suggest details from the source' }).click()
  await expect(page.getByText('AI-estimated salary range', { exact: true })).toBeVisible()
  await page.getByLabel('Estimated maximum').fill('120000')
  await page.screenshot({ path: 'test-results/salary-review-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/salary-review-mobile.png', fullPage: true })
  await page.getByRole('button', { name: 'Save details', exact: true }).click()
  await expect(page.getByText('Saved.', { exact: true })).toBeVisible()
  expect(saved).toMatchObject({ compensation_review: { estimate: { amount_max: 120000 } } })
  expect(unexpected).toEqual([])
})
