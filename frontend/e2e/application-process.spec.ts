import { test, expect, type Page } from '@playwright/test'

const appId = 'a0000000-0000-0000-0000-000000000001'
const base = `/applications/${appId}/prepare`
const concept = { id: 'c0000000-0000-0000-0000-000000000001', canonical_name: 'Model governance', type_code: 'tool' }
const claim = { id: 'claim-1', concept_id: concept.id, canonical_name: concept.canonical_name, type_code: 'tool', requirement_type: 'required', basis: 'stated', importance: 4, evidence_span: 'Experience in model governance.', review_status: 'unreviewed' }
async function setup(page: Page, conflict = false) {
  const state = {
    application: { id: appId, role_instance_id: 'role-1', status: 'preparing' },
    role: { id: 'role-1', title: 'Head of Capital', organisation: null as string | null, url: 'https://example.com/job', description: 'Experience in model governance. Submit a CV and cover letter.', source_document_text: null },
    preparation: { revision: 0, deadline: null as string | null, package_items: [] as object[] },
    target_revision: 'target-v1', opportunity_fingerprint: 'opp-v1', requirements_fingerprint: 'req-v1',
    stages: { opportunity: 'not_started', requirements: 'in_progress', evidence: 'not_started' },
    resume: { stage: 'overview', concept_id: null as string | null }, next_stage: 'opportunity',
    legacy_requirement_count: 0,
    review_summary: { complete: false, accepted: 0, unreviewed: 1, rejected: 0, unresolved_proposals: 1, needs_reextraction: 0 },
    items: [] as object[],
  }
  let claims = [{ ...claim }]
  let proposals = [{ id: 'proposal-1', surface_form: 'modelling', suggested_type: 'tool', role_evidence_span: 'Experience in model governance.', revision: 'proposal-v1' }]
  const writes: { path: string; body: Record<string, unknown> }[] = [], unexpected: string[] = []
  const refresh = () => {
    state.review_summary.accepted = claims.filter(c => c.review_status === 'accepted').length
    state.review_summary.unreviewed = claims.filter(c => c.review_status === 'unreviewed').length
    state.review_summary.unresolved_proposals = proposals.length
    state.review_summary.complete = !state.review_summary.unreviewed && !proposals.length
    state.items = claims.filter(c => c.review_status === 'accepted').map(c => ({ concept: { ...concept, id: c.concept_id }, role_side: { ...c, document: null }, person_side: { mappings: [], assertion: null, coverage: null, component_of: [] }, sources: [], decision: null, stale: false, requirement_fingerprint: 'item-v1' }))
  }
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, body = request.postData() ? request.postDataJSON() : {}
    const reply = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (request.method() !== 'GET') writes.push({ path, body })
    if (path === '/api/auth/status') return reply({ authenticated: true })
    if (path.endsWith('/evidence-discovery')) return reply({ run: null, findings: [] })
    if (path === `/api/applications/${appId}/process`) { refresh(); return reply(state) }
    if (path.endsWith('/process/opportunity')) {
      if (conflict) return reply({ detail: 'Opportunity changed. Reload and review before saving' }, 409)
      Object.assign(state.role, body.metadata)
      Object.assign(state.preparation, { revision: state.preparation.revision + 1, deadline: body.deadline, package_items: body.package_items })
      state.stages.opportunity = body.confirm ? 'complete' : 'in_progress'
      state.next_stage = body.confirm ? 'requirements' : 'opportunity'
      return reply(state)
    }
    if (path.endsWith('/process/resume')) { state.resume = body; return reply({ saved: true }) }
    if (path.endsWith('/process/requirements')) { expect(state.review_summary.complete).toBeTruthy(); state.stages.requirements = 'complete'; state.next_stage = 'evidence'; return reply(state) }
    if (path.endsWith('/process/vocabulary')) return reply({ items: proposals })
    if (path.endsWith('/process/vocabulary/proposal-1')) { expect(body.action).toBe('accept_alias'); proposals = []; return reply({ status: 'accepted', concept_id: concept.id }) }
    if (path === '/api/concepts/types') return reply([{ code: 'tool', label: 'Tool' }])
    if (path === '/api/concepts') return reply([concept])
    if (path === '/api/role-instances/role-1/requirements') {
      if (request.method() === 'POST') { const added = { ...claim, ...body, id: 'claim-2', review_status: 'accepted' }; claims.push(added); return reply(added) }
      return reply({ items: claims, review_summary: state.review_summary })
    }
    if (path.endsWith('/requirements/claim-1/accept')) { claims[0].review_status = 'accepted'; return reply(claims[0]) }
    if (path.endsWith('/requirements/claim-1/edit')) { claims[0] = { ...claims[0], ...body, review_status: 'accepted' }; return reply(claims[0]) }
    if (path === '/api/applications') return reply({ items: [{ ...state.application, role: state.role, latest_event: null, next_interview: null }], total: 1, offset: 0, limit: 25 })
    unexpected.push(path); return reply({ detail: `Unexpected ${path}` }, 500)
  })
  return { state, writes, unexpected }
}

const stage = (page: Page, name: string) => page.getByRole('navigation', { name: 'Application stages' }).getByRole('button', { name: new RegExp(`^${name}`) }).click()

test('front half journey saves package, reviews scoped vocabulary, confirms requirements and resumes', async ({ page }) => {
  const { state, writes, unexpected } = await setup(page)
  await page.goto(base)
  await stage(page, 'Opportunity')
  await page.getByLabel('Employer / Organisation').fill('Example Mutual')
  await page.getByLabel('Application deadline, if known').fill('2026-12-01')
  await page.getByRole('button', { name: 'Suggest materials mentioned in posting' }).click()
  await expect(page.getByLabel('Material name')).toHaveCount(2)
  await expect(page.getByLabel('Required', { exact: true }).first()).not.toBeChecked()
  await page.getByLabel('Required', { exact: true }).first().check()
  await page.getByRole('button', { name: 'Save & exit', exact: true }).click()
  await expect(page).toHaveURL(/\/applications$/)
  expect(state.preparation.package_items).toHaveLength(2)
  await page.getByRole('link', { name: 'Continue application', exact: true }).click()
  await expect(page.getByLabel('Employer / Organisation')).toHaveValue('Example Mutual')
  await page.getByRole('button', { name: 'Confirm opportunity & package' }).click()
  await expect(page.getByRole('navigation', { name: 'Application stages' }).getByRole('button', { name: 'Opportunity Complete' })).toBeVisible()
  await stage(page, 'Requirements')
  await page.getByRole('button', { name: 'Accept', exact: true }).click()
  const vocabulary = page.getByRole('region', { name: 'Vocabulary for this role' })
  await vocabulary.getByLabel('Search active vocabulary concepts').fill('Model')
  await vocabulary.getByRole('button', { name: 'Model governance (tool)' }).click()
  await page.getByRole('button', { name: 'Save & exit', exact: true }).click()
  expect(writes.filter(w => w.path.endsWith('/vocabulary/proposal-1'))).toHaveLength(0)
  await page.getByRole('link', { name: 'Continue application', exact: true }).click()
  await expect(vocabulary.getByText('Selected: Model governance')).toBeVisible()
  await vocabulary.getByRole('button', { name: 'Confirm vocabulary resolution' }).click()
  await expect(vocabulary).toHaveCount(0)
  await page.getByRole('button', { name: 'Confirm requirement set' }).click()
  await expect(page.getByRole('navigation', { name: 'Application stages' }).getByRole('button', { name: 'Requirements Complete' })).toBeVisible()
  await stage(page, 'Evidence')
  await expect(page.getByRole('button', { name: 'Model governance Start review' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0)
  expect(await page.locator('.application-mode a').evaluateAll(links => links.map(a => a.getAttribute('href')))).toEqual([])
  expect(unexpected).toEqual([])
  await page.screenshot({ path: 'test-results/application-process-desktop.png', fullPage: true })
})

test('failed Opportunity save preserves draft through refresh and blocks leaving', async ({ page }) => {
  await setup(page, true)
  await page.goto(`${base}/opportunity`)
  await page.getByLabel('Employer / Organisation').fill('Corrected employer')
  await stage(page, 'Requirements')
  await expect(page).toHaveURL(/\/opportunity$/)
  await expect(page.getByRole('alert')).toContainText('Opportunity changed')
  page.on('dialog', dialog => dialog.accept())
  await page.reload()
  await expect(page.getByLabel('Employer / Organisation')).toHaveValue('Corrected employer')
})

test('requirement edit and add drafts survive refresh and save before leaving', async ({ page }) => {
  const { writes, unexpected } = await setup(page)
  await page.goto(`${base}/requirements`)
  await page.getByRole('button', { name: 'Edit & accept', exact: true }).click()
  await page.getByLabel('Requirement type').selectOption('preferred')
  await page.reload()
  await expect(page.getByLabel('Requirement type')).toHaveValue('preferred')
  await stage(page, 'Opportunity')
  expect(writes.find(w => w.path.endsWith('/claim-1/edit'))?.body.requirement_type).toBe('preferred')
  await stage(page, 'Requirements')
  await page.getByRole('button', { name: 'Add requirement', exact: true }).click()
  await page.getByLabel('Evidence span (exact quote from the source document)').fill('Experience in model governance.')
  await page.reload()
  await expect(page.getByLabel('Evidence span (exact quote from the source document)')).toHaveValue('Experience in model governance.')
  await page.getByRole('button', { name: 'Save & exit', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Choose an active vocabulary concept')
  await expect(page).toHaveURL(/\/requirements$/)
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await stage(page, 'Opportunity')
  expect(unexpected).toEqual([])
})

test('mobile stages fit long titles without horizontal navigation', async ({ page }) => {
  const { state } = await setup(page)
  state.role.title = 'Senior Head of Enterprise Capital and Risk Management for International Business Operations'
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${base}/opportunity`)
  await expect(page.getByRole('navigation', { name: 'Application stages' })).not.toBeVisible()
  await expect(page.getByRole('combobox', { name: /^Application stage/ })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'test-results/application-process-mobile.png', fullPage: true })
  await page.getByRole('combobox', { name: /^Application stage/ }).selectOption('requirements')
  await expect(page.getByRole('heading', { name: 'Review the employer’s requirements' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  state.items = Array.from({ length: 50 }, (_, i) => ({ concept: { ...concept, id: `concept-${i}`, canonical_name: `Requirement ${i}` }, decision: null, stale: false }))
  await page.route(`**/api/applications/${appId}/process`, route => route.fulfill({ json: state }))
  await page.getByRole('combobox', { name: /^Application stage/ }).selectOption('evidence')
  await expect(page.getByRole('button', { name: /Requirement \d+ Start review/ })).toHaveCount(50)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('role search retains query and offers all dates when employer is absent', async ({ page }) => {
  const searches: URLSearchParams[] = []
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/status') return route.fulfill({ json: { authenticated: true } })
    if (url.pathname === '/api/roles') {
      searches.push(url.searchParams)
      const match = url.searchParams.get('q') === 'Example Mutual' && url.searchParams.get('period') === 'all'
      return route.fulfill({ json: { items: match ? [{ id: 'role-1', title: 'Capital Analyst', organisation: null, location: 'Dublin', similarity: null }] : [], total: match ? 1 : 0, year_range: null } })
    }
    return route.fulfill({ json: {} })
  })
  await page.goto('/opportunities?offset=20')
  await page.getByLabel('Find a role').fill('Example Mutual')
  await page.getByRole('button', { name: 'Search roles', exact: true }).click()
  await expect(page).toHaveURL(/q=Example\+Mutual/)
  expect(new URL(page.url()).searchParams.has('offset')).toBeFalsy()
  await page.getByRole('button', { name: 'Search all dates' }).click()
  await expect(page.getByRole('link', { name: /Capital Analyst/ })).toContainText('Employer not recorded')
  await expect(page.getByRole('link', { name: /Capital Analyst/ })).toHaveAttribute('href', '/roles/role-1')
  await page.reload()
  await expect(page.getByLabel('Find a role')).toHaveValue('Example Mutual')
  await expect(page.getByLabel('Sort roles')).toHaveValue('relevance')
  expect(searches.at(-1)?.get('q')).toBe('Example Mutual')
})
