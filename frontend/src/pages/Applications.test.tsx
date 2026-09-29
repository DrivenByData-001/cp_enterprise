import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Applications from './Applications'
import { api, type ApplicationListItem, type ApplicationListResponse } from '../lib/api'

// Phase 3 (docs/34 §8): a real, persisted Applications index — replaces the
// Phase 1 placeholder. Covers the empty state, the populated active/
// historical split, and that role metadata renders straight from the one
// bounded list call (never a per-row fetch).

vi.mock('../lib/api', () => ({
  api: { listApplications: vi.fn() },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function item(overrides: Partial<ApplicationListItem> = {}): ApplicationListItem {
  return {
    id: 'app-1',
    role_instance_id: 'role-1',
    status: 'preparing',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    latest_event: null,
    next_interview: null,
    role: { id: 'role-1', title: 'Head of Capital', organisation: 'An insurer', location: 'London', posting_date: '2026-01-01' },
    ...overrides,
  }
}

function listResponse(items: ApplicationListItem[]): ApplicationListResponse {
  return { items, total: items.length, limit: 200, offset: 0 }
}

function renderPage() {
  return render(<MemoryRouter><Applications /></MemoryRouter>)
}

describe('Applications index — empty state', () => {
  it('shows an honest empty state pointing to Opportunities', async () => {
    vi.mocked(api.listApplications).mockResolvedValue(listResponse([]))
    renderPage()
    expect(await screen.findByText(/No applications yet\. Choose an opportunity/)).toBeTruthy()
    const link = screen.getByText('Browse opportunities').closest('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/opportunities')
  })
})

describe('Applications index — populated', () => {
  it('uses exactly one bounded list call, with role metadata already attached', async () => {
    vi.mocked(api.listApplications).mockResolvedValue(listResponse([item()]))
    renderPage()
    expect(await screen.findByText('Head of Capital')).toBeTruthy()
    expect(screen.getByText(/An insurer/)).toBeTruthy()
    expect(api.listApplications).toHaveBeenCalledTimes(1)
  })

  it('shows active applications under Active and closed/withdrawn under Past attempts', async () => {
    vi.mocked(api.listApplications).mockResolvedValue(
      listResponse([
        item({ id: 'active-1', status: 'submitted', role: { id: 'r1', title: 'Active Role', organisation: null, location: null, posting_date: null } }),
        item({ id: 'closed-1', status: 'withdrawn', role: { id: 'r2', title: 'Withdrawn Role', organisation: null, location: null, posting_date: null } }),
      ]),
    )
    renderPage()
    await screen.findByText('Active Role')
    const activeSection = screen.getByRole('heading', { name: 'Active' }).closest('section') as HTMLElement
    const pastSection = screen.getByRole('heading', { name: 'Past attempts' }).closest('section') as HTMLElement
    expect(activeSection.textContent).toContain('Active Role')
    expect(activeSection.textContent).not.toContain('Withdrawn Role')
    expect(pastSection.textContent).toContain('Withdrawn Role')
  })

  it('provides Open application, View opportunity and Browse opportunities', async () => {
    vi.mocked(api.listApplications).mockResolvedValue(listResponse([item()]))
    renderPage()
    await screen.findByText('Head of Capital')
    const open = screen.getByText('Open application').closest('a') as HTMLAnchorElement
    expect(open.getAttribute('href')).toBe('/applications/app-1')
    const view = screen.getByText('View opportunity').closest('a') as HTMLAnchorElement
    expect(view.getAttribute('href')).toBe('/roles/role-1')
    expect(screen.getByText('Browse opportunities').closest('a')?.getAttribute('href')).toBe('/opportunities')
  })
})

describe('Applications index — failure', () => {
  it('shows a clear error rather than a blank page', async () => {
    vi.mocked(api.listApplications).mockRejectedValue(new Error('service unavailable'))
    renderPage()
    expect(await screen.findByText(/Applications couldn't be loaded/)).toBeTruthy()
  })
})

describe('Applications index — lifecycle summary (Phase 5, docs/36 §6)', () => {
  it('shows the next scheduled interview from the one bounded list response', async () => {
    vi.mocked(api.listApplications).mockResolvedValue(
      listResponse([item({ next_interview: { event_at: '2026-03-01T00:00:00Z', label: 'Technical panel' } })]),
    )
    renderPage()
    expect(await screen.findByText(/Next interview — Technical panel/)).toBeTruthy()
    // still exactly one request — the summary rides the existing list call
    expect(api.listApplications).toHaveBeenCalledTimes(1)
  })

  it('falls back to the latest event when there is no upcoming interview', async () => {
    vi.mocked(api.listApplications).mockResolvedValue(
      listResponse([item({ latest_event: { event_type: 'rejected', event_at: '2026-02-01T00:00:00Z' }, next_interview: null })]),
    )
    renderPage()
    expect(await screen.findByText(/Latest: Rejected/)).toBeTruthy()
  })

  it('shows nothing extra for an application with no recorded lifecycle history', async () => {
    vi.mocked(api.listApplications).mockResolvedValue(listResponse([item()]))
    renderPage()
    await screen.findByText('Head of Capital')
    expect(screen.queryByText(/Next interview/)).toBeNull()
    expect(screen.queryByText(/Latest:/)).toBeNull()
  })
})

describe('Application pagination and recovery', () => {
  it('makes records beyond 200 accessible and filters on the server', async () => {
    vi.mocked(api.listApplications).mockImplementation(async params => ({ items: [item({ role: { ...item().role, title: `Application ${Number(params?.offset ?? 0) + 1}` } })], total: 260, limit: 25, offset: params?.offset ?? 0 }))
    render(<MemoryRouter initialEntries={['/applications?offset=200']}><Applications /></MemoryRouter>)
    await screen.findByText('Application 201')
    fireEvent.click(screen.getByText('Next page'))
    await screen.findByText('Application 226')
    fireEvent.change(screen.getByLabelText('Application status'), { target: { value: 'interviewing' } })
    await waitFor(() => expect(api.listApplications).toHaveBeenLastCalledWith({ limit: 25, offset: 0, status: 'interviewing' }))
    await screen.findByText('Application 1')
  })
  it('retries errors and distinguishes an empty filter from no applications', async () => {
    vi.mocked(api.listApplications).mockRejectedValueOnce(new Error('Offline')).mockResolvedValueOnce(listResponse([]))
    render(<MemoryRouter initialEntries={['/applications?status=closed']}><Applications /></MemoryRouter>)
    fireEvent.click(await screen.findByText('Retry applications'))
    await screen.findByText('No applications match this status.')
    expect(screen.queryByText(/No applications yet/)).toBeNull()
  })
  it('ignores a stale page response after the filter changes', async () => {
    let old!: (data: ApplicationListResponse) => void
    vi.mocked(api.listApplications).mockReturnValueOnce(new Promise(resolve => { old = resolve })).mockResolvedValueOnce(listResponse([item({ role: { ...item().role, title: 'Latest filtered role' } })]))
    renderPage()
    fireEvent.change(screen.getByLabelText('Application status'), { target: { value: 'ready' } })
    await screen.findByText('Latest filtered role')
    old(listResponse([item()]))
    await waitFor(() => expect(screen.queryByText('Head of Capital')).toBeNull())
  })
})
