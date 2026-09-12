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

    fireEvent.change(screen.getByDisplayValue('Recent (last few years)'), { target: { value: 'year' } })
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

    fireEvent.change(screen.getByDisplayValue('Recent (last few years)'), { target: { value: 'unknown_date' } })

    await waitFor(() =>
      expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ period: 'unknown_date' })),
    )
    expect(screen.getByText(/never assumed to be the date they were captured/)).toBeTruthy()
  })
})
