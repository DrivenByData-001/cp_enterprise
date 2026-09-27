import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Home from './Home'
import { api, type Application, type ApplicationRoleSummary, type Role, type RoleListResponse } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listRoles: vi.fn(),
    getProfile: vi.fn(),
    listApplications: vi.fn(),
  },
}))

function rolesResponse(items: Role[], total = items.length): RoleListResponse {
  return { items, total, limit: 5, offset: 0, period: 'current', year_range: { min: 2008, max: 2026 } }
}

function applicationsResponse(items: (Application & { role: ApplicationRoleSummary })[] = []) {
  return { items, total: items.length, limit: 20, offset: 0 }
}

beforeEach(() => {
  // Most of these tests don't care about the Applications card — give it a
  // harmless default so they don't each have to know about it.
  vi.mocked(api.listApplications).mockResolvedValue(applicationsResponse())
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Home — Career direction', () => {
  it('never claims a selected direction — the data model has no such concept yet', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([]))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    render(<MemoryRouter><Home /></MemoryRouter>)
    await waitFor(() => expect(api.listRoles).toHaveBeenCalled())
    expect(screen.getByText('No career direction selected yet')).toBeTruthy()
  })
})

describe('Home — Opportunities card', () => {
  it('shows a bounded, populated list of current opportunities', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([
      { id: 'r1', title: 'Actuarial Analyst', organisation: 'Acme', location: 'Dublin', posting_date: '2026-01-05' } as Role,
      { id: 'r2', title: 'Risk Quant', organisation: 'Beta', location: null, posting_date: null } as Role,
    ], 2))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    render(<MemoryRouter><Home /></MemoryRouter>)
    await waitFor(() => expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ period: 'current', limit: 5 })))
    expect(await screen.findByText('Actuarial Analyst')).toBeTruthy()
    expect(screen.getByText('Risk Quant')).toBeTruthy()
    expect(screen.getByText('Review current opportunities →')).toBeTruthy()
  })

  it('shows an honest empty state and points to Add a posting when there are no current roles', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([]))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    render(<MemoryRouter><Home /></MemoryRouter>)
    await screen.findByText('No current roles captured yet.')
    expect(screen.getByText('Add a posting →')).toBeTruthy()
  })
})

describe('Home — failure isolation', () => {
  it('keeps the profile card visible when the opportunities request fails', async () => {
    vi.mocked(api.listRoles).mockRejectedValue(new Error('roles down'))
    vi.mocked(api.getProfile).mockResolvedValue({ id: 'snap-1', _display: 'A career narrative.' })
    render(<MemoryRouter><Home /></MemoryRouter>)
    await screen.findByText(/Could not load current opportunities/)
    expect(await screen.findByText('A career narrative.')).toBeTruthy()
  })

  it('keeps the opportunities card visible when the profile request fails', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([{ id: 'r1', title: 'Actuarial Analyst', organisation: null, location: null, posting_date: null } as Role], 1))
    vi.mocked(api.getProfile).mockRejectedValue(new Error('profile360 unavailable'))
    render(<MemoryRouter><Home /></MemoryRouter>)
    await screen.findByText('Could not load your profile summary.')
    expect(await screen.findByText('Actuarial Analyst')).toBeTruthy()
  })

  it('keeps Opportunities and Profile visible when the applications request fails (build §11)', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([{ id: 'r1', title: 'Actuarial Analyst', organisation: null, location: null, posting_date: null } as Role], 1))
    vi.mocked(api.getProfile).mockResolvedValue({ id: 'snap-1', _display: 'A career narrative.' })
    vi.mocked(api.listApplications).mockRejectedValue(new Error('applications down'))
    render(<MemoryRouter><Home /></MemoryRouter>)
    await screen.findByText(/Could not load applications/)
    expect(await screen.findByText('Actuarial Analyst')).toBeTruthy()
    expect(await screen.findByText('A career narrative.')).toBeTruthy()
  })
})

describe('Home — Applications summary (docs/34 §11)', () => {
  it('shows an honest empty state pointing to Opportunities when there are no applications', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([]))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    vi.mocked(api.listApplications).mockResolvedValue(applicationsResponse([]))
    render(<MemoryRouter><Home /></MemoryRouter>)
    expect(await screen.findByText('No applications yet')).toBeTruthy()
    expect(screen.getByText('Choose an opportunity when you decide you want to pursue it.')).toBeTruthy()
  })

  it('shows up to a few recent active applications, each linking to its workspace', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([]))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    vi.mocked(api.listApplications).mockResolvedValue(
      applicationsResponse([
        {
          id: 'app-1', role_instance_id: 'role-1', status: 'preparing', created_at: 't', updated_at: 't',
          role: { id: 'role-1', title: 'Head of Capital', organisation: 'An insurer', location: null, posting_date: null },
        },
      ]),
    )
    render(<MemoryRouter><Home /></MemoryRouter>)
    const link = await screen.findByText('Head of Capital')
    expect((link.closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/applications/app-1')
    expect(screen.getByText('View applications').closest('a')?.getAttribute('href')).toBe('/applications')
  })

  it('never shows a closed/withdrawn application in the recent-active preview', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([]))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    vi.mocked(api.listApplications).mockResolvedValue(
      applicationsResponse([
        {
          id: 'app-closed', role_instance_id: 'role-2', status: 'withdrawn', created_at: 't', updated_at: 't',
          role: { id: 'role-2', title: 'Withdrawn Role', organisation: null, location: null, posting_date: null },
        },
      ]),
    )
    render(<MemoryRouter><Home /></MemoryRouter>)
    await screen.findByText('No applications yet')
    expect(screen.queryByText('Withdrawn Role')).toBeNull()
  })
})

describe('Home — Add posting', () => {
  it('links Add posting from the Opportunities card to Import', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([]))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    render(<MemoryRouter><Home /></MemoryRouter>)
    const link = await screen.findByText('Add posting')
    expect((link.closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/import')
  })
})
