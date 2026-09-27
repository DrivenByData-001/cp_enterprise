import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Home from './Home'
import { api, type Role, type RoleListResponse } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listRoles: vi.fn(),
    getProfile: vi.fn(),
  },
}))

function rolesResponse(items: Role[], total = items.length): RoleListResponse {
  return { items, total, limit: 5, offset: 0, period: 'current', year_range: { min: 2008, max: 2026 } }
}

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
})

describe('Home — Applications placeholder', () => {
  it('never implies application workspaces exist yet', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse([]))
    vi.mocked(api.getProfile).mockResolvedValue(null)
    render(<MemoryRouter><Home /></MemoryRouter>)
    await waitFor(() => expect(api.listRoles).toHaveBeenCalled())
    expect(screen.getByText('No application workspaces yet')).toBeTruthy()
    expect(screen.getByText('Browse opportunities')).toBeTruthy()
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
