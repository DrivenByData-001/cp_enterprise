import { test, expect, type Page } from '@playwright/test'

const appId = 'a0000000-0000-0000-0000-000000000001'
const conceptId = 'c0000000-0000-0000-0000-000000000001'
const base = `/applications/${appId}/prepare`
const source = { ref: 'profile_claim:10000000-0000-0000-0000-000000000001', kind: 'profile_claim', label: 'Challenged model assumptions', content: 'In 2019 I challenged lapse and expense assumptions during an internal model change.', source_revision: 'source-v1', episode_id: null }

test('discover, inspect, edit and approve a recent career example', async ({ page }) => {
  const { unexpected } = await setup(page)
  let started = false
  const finding = { id: 'finding-1',revision: 1,status: 'pending',stale: false,reviewed_payload: null,
    proposal: { rationale:'Reframed unreliable translation as a deterministic transformation system.',limitations:'Architecture and validation ownership; substantial AI-assisted coding.',question:'Who reviewed the validation results?',depth:'owned',autonomy:'independent' },
    requirement_snapshot: { canonical_name:'Problem solving',evidence_span:'A self-driven unstructured problem solver' },
    source_snapshot: { claim: { claim_text:'Identified unreliable DCS-to-Python translation and designed a deterministic compiler/runtime approach.',evidence_class:'mixed',uncertainty:'Wider adoption is not established.' },
      episode: { organisation:'MetLife',title:'Actuary',start_date:'2024-09-01',autonomy:'Technical ownership under management review' },
      evidence: [{id:'e1',passage:'Owned the problem framing and validation approach.',locator:'project-review:ownership',document_title:'Project review',evidence_type:'user_asserted_plus_project_evidence',notes:null}] } }
  await page.route(`**/api/applications/${appId}/evidence-discovery**`, async route => {
    if (route.request().url().endsWith('/review')) {
      const body=route.request().postDataJSON()
      expect(body.action).toBe('approve')
      expect(body.clarification).toBe('Reviewed with the modelling team.')
      finding.status='accepted'; finding.revision=2
      return route.fulfill({json:finding})
    }
    if (route.request().method()==='POST') { started=true; return route.fulfill({json:{id:'run-1',status:'running'}}) }
    return route.fulfill({json:{run:started?{status:'complete',total_sources:308,model:'gpt-5.4-mini'}:null,findings:started?[finding]:[]}})
  })
  await page.goto(`${base}/evidence`)
  await page.getByRole('button',{name:'Find supporting evidence',exact:true}).click()
  await expect(page.getByText('Problem solving · MetLife')).toBeVisible()
  await page.getByText('Inspect supporting records (1)').click()
  await expect(page.getByText('Owned the problem framing and validation approach.')).toBeVisible()
  await page.getByLabel(/Your clarification/).fill('Reviewed with the modelling team.')
  await expect(page.getByRole('button',{name:'Approve finding'})).toBeDisabled()
  await page.getByLabel(/I have reviewed the source/).check()
  await page.screenshot({path:'test-results/evidence-discovery-desktop.png',fullPage:true})
  await page.setViewportSize({width:390,height:844})
  await expect(page.getByRole('button',{name:'Approve finding'})).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({path:'test-results/evidence-discovery-mobile.png',fullPage:true})
  await page.getByRole('button',{name:'Approve finding'}).click()
  await expect(page.getByText('Problem solving · MetLife')).toHaveCount(0)
  await page.getByLabel('Show accepted and rejected findings').check()
  await expect(page.getByText('Problem solving · MetLife')).toBeVisible()
  expect(unexpected).toEqual([])
})

async function setup(page: Page, options: { conflict?: boolean; stale?: boolean } = {}) {
  const state = {
    application: { id: appId, role_instance_id: 'role-1', status: 'preparing', created_at: '2026-10-06', updated_at: '2026-10-06' },
    role: { id: 'role-1', title: 'Head of Capital' }, resume_concept_id: null as string | null,
    preparation: { revision: 0, deadline: null, package_items: [] }, target_revision: 'target-v1',
    opportunity_fingerprint: 'opp-v1', requirements_fingerprint: 'req-v1', legacy_requirement_count: 0,
    stages: { opportunity: 'complete', requirements: 'complete', evidence: 'not_started' },
    resume: { stage: 'overview', concept_id: null as string | null }, next_stage: 'evidence',
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
    if (path.endsWith('/evidence-discovery')) return reply({ run: null, findings: [] })
    if (path === `/api/applications/${appId}/process`) return reply(state)
    if (path.endsWith('/process/resume')) { state.resume = request.postDataJSON(); state.resume_concept_id = state.resume.concept_id; return reply({ saved: true }) }
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

test('reviewed claim is accepted explicitly and selected without leaving the application', async ({ page }) => {
  await setup(page)
  let accepted = 0
  const added = { ...source, ref: 'profile_claim:20000000-0000-0000-0000-000000000002', label: 'Led model review in 2021', content: 'Led model review in 2021', source_revision: 'new-v1' }
  await page.route('**/claim-context', route => route.fulfill({ json: { claim: null, episodes: [] } }))
  await page.route('**/accept-claim', async route => {
    const body = route.request().postDataJSON()
    expect(body.confirmed).toBe(true)
    expect(body.claim_text).toBe(added.content)
    expect(body.reason).toBe('Checked project notes')
    expect(body.claim_id).toBeNull()
    accepted++
    await route.fulfill({ json: { status: 'accepted', acceptance_id: body.operation_id, source: added } })
  })
  await page.goto(`${base}/${conceptId}`)
  await page.getByRole('button', { name: 'Add reviewed career claim', exact: true }).click()
  await page.getByLabel('Career fact', { exact: true }).fill(added.content)
  await page.getByLabel('Source or correction reason', { exact: true }).fill('Checked project notes')
  await page.getByRole('button', { name: 'Review before accepting' }).click()
  expect(accepted).toBe(0)
  await expect(page.getByRole('heading', { name: 'Career fact to accept' })).toBeVisible()
  await page.getByRole('button', { name: 'Accept into Profile360 & use here' }).click()
  await expect(page.getByLabel('Use this evidence').nth(1)).toBeChecked()
  await expect(page).toHaveURL(new RegExp(`${conceptId}$`))
  page.on('dialog', dialog => dialog.accept())
  await page.reload()
  await expect(page.getByLabel('Use this evidence').nth(1)).toBeChecked()
  expect(accepted).toBe(1)
})

test('correction conflict retains draft and acceptance retry uses the same operation', async ({ page }) => {
  await setup(page)
  const requests: object[] = []
  await page.route('**/claim-context?*', route => route.fulfill({ json: { claim: { claim_text: source.content, episode_id: null, source_revision: source.source_revision }, episodes: [] } }))
  await page.route('**/accept-claim', async route => {
    requests.push(route.request().postDataJSON())
    await route.fulfill({ status: 409, json: { detail: 'The career claim changed. Reload it and review the correction again' } })
  })
  await page.goto(`${base}/${conceptId}`)
  await page.getByRole('button', { name: 'Correct career claim', exact: true }).click()
  await page.getByLabel('Career fact', { exact: true }).fill('Corrected to 2020')
  await page.getByLabel('Source or correction reason', { exact: true }).fill('Checked dates')
  await page.getByRole('button', { name: 'Review before accepting' }).click()
  await page.getByRole('button', { name: 'Accept into Profile360 & use here' }).click()
  await expect(page.getByRole('alert')).toContainText('career claim changed')
  await page.getByRole('button', { name: 'Accept into Profile360 & use here' }).click()
  expect(requests).toHaveLength(2)
  expect(requests[1]).toEqual(requests[0])
  await page.getByRole('button', { name: 'Back to edit' }).click()
  await expect(page.getByLabel('Career fact', { exact: true })).toHaveValue('Corrected to 2020')
  await page.getByRole('button', { name: 'Close and keep draft' }).click()
  await page.getByRole('button', { name: 'Correct career claim', exact: true }).click()
  await expect(page.getByLabel('Career fact', { exact: true })).toHaveValue('Corrected to 2020')
})


test('individual requirement leads to AI evidence discovery', async ({ page }) => {
  await setup(page)
  await page.goto(`${base}/evidence/${conceptId}`)
  await page.getByRole('button', { name: 'Find supporting evidence with AI', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Find supporting evidence', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Find supporting evidence', exact: true })).toBeVisible()
})
