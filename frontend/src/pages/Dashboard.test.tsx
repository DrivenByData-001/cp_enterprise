import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from './Dashboard'
import { api, type RoleListResponse } from '../lib/api'

// Source-aware ingest cleanup, problem #8: posting-year filter labels must
// explicitly say "posting year" (never ambiguous with capture date), and an
// "Unknown posting date" filter must exist and be reachable.

vi.mock('../lib/api', () => ({
  api: {
    listRoles: vi.fn(),
    getFacets: vi.fn(),
  },
}))

function emptyRolesResponse(period: RoleListResponse['period']): RoleListResponse {
  return { items: [], total: 0, limit: 20, offset: 0, period, year_range: { min: 2008, max: 2026 } }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Dashboard — posting-year filter', () => {
  it('labels the year picker explicitly as posting year', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(emptyRolesResponse('recent'))
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )
    await waitFor(() => expect(api.listRoles).toHaveBeenCalled())

    fireEvent.change(screen.getByDisplayValue('Current roles'), { target: { value: 'year' } })
    expect(screen.getByText('Posting year:')).toBeTruthy()
  })

  it('offers an Unknown posting date filter that calls the API with period=unknown_date', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(emptyRolesResponse('recent'))
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )
    await waitFor(() => expect(api.listRoles).toHaveBeenCalled())

    fireEvent.change(screen.getByDisplayValue('Current roles'), { target: { value: 'unknown_date' } })

    await waitFor(() =>
      expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ period: 'unknown_date' })),
    )
    expect(screen.getByText(/never assumed to be the date they were captured/)).toBeTruthy()
  })
})

// Save-checkpoint / Current-roles brief §6/§13: with no query params, Roles
// must default to the Current view, newest/recently-captured first (never
// similarity) — a role the user just saved must show up immediately without
// digging through the historical corpus or an unrelated similarity ranking.
describe('Dashboard — Current roles default', () => {
  it('defaults to period=current with sort=captured_at (the same recency default the server applies on its own) when no query params are set', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(emptyRolesResponse('current'))
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ period: 'current', sort: 'captured_at' })),
    )
    expect(screen.getByDisplayValue('Current roles')).toBeTruthy()
    expect(screen.getByDisplayValue('Sort: captured')).toBeTruthy()
    expect(screen.getByText(/Roles posted this calendar year/)).toBeTruthy()
  })

  it('retains similarity as an explicit, user-selectable sort under Current', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(emptyRolesResponse('current'))
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )
    await waitFor(() => expect(api.listRoles).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('Sort roles'), { target: { value: 'similarity' } })
    await waitFor(() =>
      expect(api.listRoles).toHaveBeenLastCalledWith(expect.objectContaining({ period: 'current', sort: 'similarity' })),
    )
  })
})
