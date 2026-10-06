import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import App from './App'
import { AuthContext } from './useAuth'
import { api, type ApplicationDetail, type Role } from './lib/api'
import { keepWorkflow, readWorkflowContext, withWorkflow } from './lib/workflowContext'

// Application workflow ownership: ?application=<id> / ?intent=apply keep an
// application the owning context while the user visits shared Opportunity,
// Requirements and Comparison screens. Real pages + real App shell; only the
// API (and the heavyweight workspace page, covered in its own test) is mocked.

vi.mock('./pages/ApplicationWorkspace', () => ({ default: () => <h1>Application workspace</h1> }))
vi.mock('./lib/api', () => ({
  api: {
    listApplications: vi.fn(), getApplication: vi.fn(), createOrReopenApplication: vi.fn(),
    listRoles: vi.fn(), getFacets: vi.fn(), getRole: vi.fn(), getRoleContext: vi.fn(),
    getRoleCompensation: vi.fn(), compareRole: vi.fn(), getCareerAlignment: vi.fn(),
    listRequirements: vi.fn(), listDevelopmentActions: vi.fn(), updateRoleMetadata: vi.fn(),
  },
}))

const REVIEW = { accepted: 1, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: true, needs_reextraction: 0, complete: true }
const role = {
  id: 'role-1', node_type: 'posting', title: 'Head of Capital', organisation: 'An insurer', location: 'London',
  posting_date: '2026-01-15', career_track: null, similarity: 0.4, skills: [], legacy_skills: [], requirement_review: REVIEW,
  extraction_status: 'ok',
} as unknown as Role

function detail(roleId = 'role-1'): ApplicationDetail {
  return {
    application: { id: 'app-1', role_instance_id: roleId, status: 'preparing', created_at: '', updated_at: '' },
    role: { id: roleId, title: 'Head of Capital', organisation: 'An insurer' },
    notes: [],
  } as unknown as ApplicationDetail
}

function Probe() {
  const l = useLocation()
  return <output data-testid="loc">{l.pathname + l.search}</output>
}

function renderApp(url: string) {
  return render(
    <AuthContext.Provider value={{ logout: vi.fn() }}>
      <MemoryRouter initialEntries={[url]}><App /><Probe /></MemoryRouter>
    </AuthContext.Provider>,
  )
}

const loc = () => screen.getByTestId('loc').textContent ?? ''
const activeNav = () => Array.from(document.querySelectorAll('.nav-primary a.active')).map(a => a.textContent)

beforeEach(() => {
  vi.mocked(api.listApplications).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
  vi.mocked(api.listRoles).mockResolvedValue({ items: [role], total: 1, limit: 20, offset: 0, period: 'current', year_range: { min: 2020, max: 2026 } } as never)
  vi.mocked(api.getFacets).mockResolvedValue([])
  vi.mocked(api.getApplication).mockResolvedValue(detail())
  vi.mocked(api.getRole).mockResolvedValue(role)
  vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: 'role-1', enrichment: null } as never)
  vi.mocked(api.getRoleCompensation).mockRejectedValue(new Error('n/a'))
  vi.mocked(api.getCareerAlignment).mockRejectedValue(new Error('n/a'))
  vi.mocked(api.compareRole).mockResolvedValue({
    role: { id: 'role-1', title: 'Head of Capital', kind: 'posting' }, items: [],
    counts: { evidenced: 0, partial: 0, user_asserted: 0, not_found: 0 }, blocking_gaps: [], unverified_required: [],
    fit_score: null, embedding_similarity: null, engine_version: 't', review_summary: REVIEW,
  } as never)
  vi.mocked(api.listDevelopmentActions).mockResolvedValue([])
  vi.mocked(api.listRequirements).mockResolvedValue({ items: [], review_summary: REVIEW } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('workflowContext helpers', () => {
  it('reads, appends and preserves context; application wins over intent', () => {
    expect(readWorkflowContext('?application=app-1&intent=apply')).toEqual({ applicationId: 'app-1', applyIntent: false })
    expect(readWorkflowContext('?intent=apply')).toEqual({ applicationId: null, applyIntent: true })
    expect(readWorkflowContext('?application=../x')).toEqual({ applicationId: null, applyIntent: false })
    expect(withWorkflow('/comparison/r?x=1', { applicationId: 'app-1', applyIntent: false })).toBe('/comparison/r?x=1&application=app-1')
    expect(withWorkflow('/comparison/r', { applicationId: null, applyIntent: false })).toBe('/comparison/r')
    expect(keepWorkflow({ step: 'details' }, new URLSearchParams('application=app-1')).toString()).toBe('step=details&application=app-1')
  })
})

describe('pre-application intent (Applications → Opportunities)', () => {
  it('keeps Applications owning the journey through browsing, filtering, opening a role and applying', async () => {
    vi.mocked(api.createOrReopenApplication).mockResolvedValue({ id: 'app-1', role_instance_id: 'role-1', status: 'preparing', created: false } as never)
    renderApp('/applications')
    fireEvent.click(await screen.findByText('Browse opportunities'))
    expect(await screen.findByText('Choosing an opportunity to apply for')).toBeTruthy()
    expect(loc()).toBe('/opportunities?intent=apply')
    expect(activeNav()).toEqual(['Applications'])

    fireEvent.change(await screen.findByLabelText('Sort roles'), { target: { value: 'title' } })
    await waitFor(() => expect(loc()).toContain('sort=title'))
    expect(loc()).toContain('intent=apply')
    expect(screen.getByText('Choosing an opportunity to apply for')).toBeTruthy()

    fireEvent.click(await screen.findByText('Head of Capital'))
    await waitFor(() => expect(loc()).toMatch(/^\/roles\/role-1\?intent=apply/))
    expect(activeNav()).toEqual(['Applications'])
    fireEvent.click(await screen.findByText('I want to apply'))
    await waitFor(() => expect(loc()).toBe('/applications/app-1'))
    expect(await screen.findByText('Application workspace')).toBeTruthy()
    expect(api.createOrReopenApplication).toHaveBeenCalledTimes(1)
    expect(api.createOrReopenApplication).toHaveBeenCalledWith('role-1')
  })

  it('"clear filters" keeps the intent', async () => {
    vi.mocked(api.listRoles).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, period: 'current', year_range: { min: 2020, max: 2026 } } as never)
    renderApp('/opportunities?intent=apply&track=x')
    fireEvent.click(await screen.findByText('Clear filters and show all years'))
    await waitFor(() => expect(loc()).toContain('period=all'))
    expect(loc()).toContain('intent=apply')
  })
})

describe('application context on shared screens', () => {
  it('Role Detail shows the application indicator, Back to application, Applications nav and no second "apply"', async () => {
    renderApp('/roles/role-1?application=app-1')
    expect(await screen.findByText('Working on your application')).toBeTruthy()
    expect((await screen.findByText('Back to application')).closest('a')?.getAttribute('href')).toBe('/applications/app-1')
    expect(activeNav()).toEqual(['Applications'])
    expect(screen.queryByText('I want to apply')).toBeNull()
  })

  it('carries the application through Role → Requirements → (substep) → Comparison → Requirements', async () => {
    renderApp('/roles/role-1?application=app-1')
    fireEvent.click(await screen.findByText('Review requirements', { selector: 'a.button' }))
    await waitFor(() => expect(loc()).toBe('/role-instances/role-1/requirements?application=app-1'))
    expect(await screen.findByText('Back to application')).toBeTruthy()

    fireEvent.click(await screen.findByText('Back to role details'))
    await waitFor(() => expect(loc()).toContain('step=details'))
    expect(loc()).toContain('application=app-1')
    expect(screen.getByText('Back to application')).toBeTruthy()
    fireEvent.click(screen.getByText('Continue to requirements'))
    await waitFor(() => expect(loc()).toBe('/role-instances/role-1/requirements?application=app-1'))

    fireEvent.click(await screen.findByText(/Continue to comparison/))
    await waitFor(() => expect(loc()).toBe('/comparison/role-1?application=app-1'))
    expect(await screen.findByText('Working on your application')).toBeTruthy()
    expect(activeNav()).toEqual(['Applications'])

    fireEvent.click(await screen.findByText('Review and extract requirements'))
    await waitFor(() => expect(loc()).toBe('/role-instances/role-1/requirements?application=app-1'))
  })

  it('reconstructs the indicator on a refresh / deep link to Comparison', async () => {
    renderApp('/comparison/role-1?application=app-1')
    expect(await screen.findByText('Working on your application')).toBeTruthy()
    expect(await screen.findByText('Back to application')).toBeTruthy()
    expect(api.getApplication).toHaveBeenCalledWith('app-1')
  })

  it('discards an unavailable application: nav, Apply and later links become ordinary', async () => {
    vi.mocked(api.getApplication).mockRejectedValue(new Error('404'))
    renderApp('/roles/role-1?application=gone')
    expect(await screen.findByText(/application that couldn't be loaded/)).toBeTruthy()
    await waitFor(() => expect(loc()).toBe('/roles/role-1'))
    expect(activeNav()).toEqual(['Opportunities'])
    expect(await screen.findByText('I want to apply')).toBeTruthy()
    expect(screen.queryByText('Back to application')).toBeNull()
    expect(screen.getByText('Review requirements', { selector: 'a.button' }).getAttribute('href')).toBe('/role-instances/role-1/requirements')
    expect(screen.getByText('Correct role details').closest('a')?.getAttribute('href')).toBe('/roles/role-1/edit')
  })

  it('discards an application that belongs to another role (shared screen still renders)', async () => {
    vi.mocked(api.getApplication).mockResolvedValue(detail('other-role'))
    renderApp('/comparison/role-1?application=app-1')
    expect(await screen.findByText(/couldn't be matched to this opportunity/)).toBeTruthy()
    expect(await screen.findByText(/Structural comparison/)).toBeTruthy()
    await waitFor(() => expect(loc()).toBe('/comparison/role-1'))
    expect(activeNav()).toEqual(['Opportunities'])
    expect(screen.queryByText('Back to application')).toBeNull()
  })

  it('keeps the application through Role Detail → RoleEdit → back, including a deep link', async () => {
    renderApp('/roles/role-1?application=app-1')
    fireEvent.click(await screen.findByText('Correct role details'))
    await waitFor(() => expect(loc()).toBe('/roles/role-1/edit?application=app-1'))
    expect(await screen.findByText('Back to application')).toBeTruthy()
    expect(activeNav()).toEqual(['Applications'])
    expect(screen.getByText(/Back to Head of Capital/).closest('a')?.getAttribute('href')).toBe('/roles/role-1?application=app-1')
    fireEvent.click(screen.getByText(/Back to Head of Capital/))
    await waitFor(() => expect(loc()).toBe('/roles/role-1?application=app-1'))
    expect(await screen.findByText('Working on your application')).toBeTruthy()
    cleanup()
    renderApp('/roles/role-1/edit?application=app-1')
    expect(await screen.findByText('Back to application')).toBeTruthy()
    expect(activeNav()).toEqual(['Applications'])
  })
})

describe('ordinary Opportunities journey is unchanged', () => {
  it('highlights Opportunities and shows no application UI', async () => {
    renderApp('/opportunities')
    expect(await screen.findByText('Head of Capital')).toBeTruthy()
    expect(activeNav()).toEqual(['Opportunities'])
    expect(screen.queryByText('Choosing an opportunity to apply for')).toBeNull()
    fireEvent.click(screen.getByText('Head of Capital'))
    await waitFor(() => expect(loc()).toBe('/roles/role-1'))
    expect(await screen.findByText('I want to apply')).toBeTruthy()
    expect(screen.queryByText('Working on your application')).toBeNull()
    expect(activeNav()).toEqual(['Opportunities'])
    expect(api.getApplication).not.toHaveBeenCalled()
  })
})
