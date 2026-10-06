import { test, expect, type Page } from '@playwright/test'

const appId = 'a0000000-0000-0000-0000-000000000001'
const conceptId = 'c0000000-0000-0000-0000-000000000001'
const base = `/applications/${appId}/prepare`
const source = { ref: 'profile_claim:10000000-0000-0000-0000-000000000001', kind: 'profile_claim', label: 'Challenged model assumptions', content: 'In 2019 I challenged lapse and expense assumptions during an internal model change.', source_revision: 'source-v1', episode_id: null }

async function setup(page: Page, options: { conflict?: boolean; stale?: boolean } = {}) {
  const state = {
    application: { id: appId, role_instance_id: 'role-1', status: 'preparing', created_at: '2026-10-06', updated_at: '2026-10-06' },
    role: { id: 'role-1', title: 'Head of Capital' }, resume_concept_id: null as string | null,
    evidence_complete: false, review_summary: { complete: true },
    items: [{ concept: { id: conceptId, canonical_name: 'Model governance', type_code: 'tool' }, status: 'evidenced',
      role_side: { requirement_type: 'required', evidence_span: 'Experience challenging internal model assumptions', review_status: 'accepted', basis: 'stated', document: null },
      person_side: { mappings: [], assertion: null, coverage: null, component_of: [] }, sources: [source],
      decision: null as null | { selected_refs: string[]; disposition: string; rationale: string; revision: number }, stale: !!options.stale,
      attention_reason: options.stale ? 'The requirement or selected evidence changed. Review and save again.' : null,
      requirement_fingerprint: 'requirement-v1' }],
  }
  const unexpected: string[] = []
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/auth/status') return reply({ authenticated: true })
    if (path === `/api/applications/${appId}/mode`) return reply(state)
    if (path.endsWith('/mode/resume')) { state.resume_concept_id = request.postDataJSON().concept_id; return reply({ saved: true }) }
    if (path.endsWith(`/mode/evidence/${conceptId}/search`)) return reply({ sources: [source] })
    if (path.endsWith(`/mode/evidence/${conceptId}`)) {
      if (options.conflict) return reply({ detail: 'This review changed in another tab. Reload before saving' }, 409)
      const body = request.postDataJSON()
      expect(body.requirement_fingerprint).toBe('requirement-v1')
      if (body.selected_refs.length) expect(body.source_revisions[source.ref]).toBe('source-v1')
      state.items[0].decision = { ...body, revision: (state.items[0].decision?.revision ?? 0) + 1 }
      state.items[0].stale = false
      state.evidence_complete = body.disposition !== 'investigate'
      return reply(state.items[0].decision)
    }
    if (path === '/api/applications') return reply({ items: [{ ...state.application, role: { title: state.role.title, organisation: 'Example Employer' }, latest_event: null, next_interview: null }], total: 1, offset: 0, limit: 25 })
    if (path.endsWith('/notes')) return reply({ id: 'note-1', ...request.postDataJSON() })
    if (path.includes('/notes/note-1/promote')) return reply({ status: 'queued_pending' })
    unexpected.push(path)
    return reply({ detail: `Unexpected request ${path}` }, 500)
  })
  return { state, unexpected }
}

test('complete evidence journey survives refresh, exit and resume', async ({ page }) => {
  const { state, unexpected } = await setup(page)
  await page.goto('/applications')
  await page.getByRole('link', { name: 'Continue application', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Model governance Start review' }).click()
  await page.getByText('Challenged model assumptions', { exact: true }).click()
  await expect(page.getByText(source.content, { exact: true })).toBeVisible()
  await page.getByLabel('Use this evidence').check()
  await page.getByLabel('Your assessment').selectOption('covered')
  await page.getByLabel('How will you address this requirement?').fill('Lead with the 2019 challenge example; do not claim validation ownership.')
  await page.getByRole('button', { name: 'Save evidence review' }).click()
  await expect(page.getByText('Review saved', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Use this evidence')).toBeChecked()
  await expect(page.getByLabel('Your assessment')).toHaveValue('covered')
  await page.getByRole('button', { name: 'Save & exit', exact: true }).click()
  await expect(page).toHaveURL(/\/applications$/)
  await page.getByRole('link', { name: 'Continue application', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`${conceptId}$`))
  expect(state.resume_concept_id).toBe(conceptId)
  expect(state.items[0].decision?.selected_refs).toEqual([source.ref])
  expect(unexpected).toEqual([])
  await page.screenshot({ path: 'test-results/application-mode-desktop.png', fullPage: true })
})

test('a failed save preserves edits and prevents exit', async ({ page }) => {
  await setup(page, { conflict: true })
  await page.goto(`${base}/${conceptId}`)
  await page.getByLabel('Your assessment').selectOption('gap')
  await page.getByLabel('How will you address this requirement?').fill('I acknowledge the gap')
  await page.getByRole('button', { name: 'Save & exit', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('another tab')
  await expect(page).toHaveURL(new RegExp(`${conceptId}$`))
  await expect(page.getByLabel('How will you address this requirement?')).toHaveValue('I acknowledge the gap')
  page.on('dialog', dialog => dialog.accept())
  await page.reload()
  await expect(page.getByLabel('How will you address this requirement?')).toHaveValue('I acknowledge the gap')
})

test('mobile review shows source changes and keeps queueing separate from acceptance', async ({ page }) => {
  const { unexpected } = await setup(page, { stale: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${base}/${conceptId}`)
  await expect(page.getByText('The requirement or selected evidence changed.', { exact: false })).toBeVisible()
  await page.getByText('Remembered another example?').click()
  await page.getByLabel('Career example', { exact: true }).fill('Reviewed model assumptions in 2020.')
  await page.getByRole('button', { name: 'Save example & send for Profile360 review' }).click()
  await expect(page.getByText('Saved and queued for Profile360 review.', { exact: false })).toBeVisible()
  await expect(page.getByLabel('Use this evidence')).not.toBeChecked()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  expect(unexpected).toEqual([])
  await page.screenshot({ path: 'test-results/application-mode-mobile.png', fullPage: true })
})

test('removed requirement recovers to the application overview', async ({ page }) => {
  await setup(page)
  await page.goto(`${base}/removed-requirement`)
  await expect(page.getByRole('alert')).toContainText('no longer available')
  await page.getByRole('button', { name: 'Model governance Start review' }).click()
  await expect(page.getByRole('heading', { name: 'Model governance' })).toBeVisible()
})
