import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'
import { AuthContext } from './useAuth'
import { api, type Profile360Row, type Role, type RoleListResponse } from './lib/api'

vi.mock('./lib/api', () => ({
  api: {
    listRoles: vi.fn(),
    getFacets: vi.fn(),
    getProfile: vi.fn(),
    getProfileHistory: vi.fn(),
    listTargets: vi.fn(),
  },
}))

function rolesResponse(items: Role[] = [], total = items.length): RoleListResponse {
  return { items, total, limit: 20, offset: 0, period: 'current', year_range: { min: 2008, max: 2026 } }
}

function renderApp(url: string) {
  return render(
    <AuthContext.Provider value={{ logout: vi.fn() }}>
      <MemoryRouter initialEntries={[url]}>
        <App />
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Root route compatibility layer', () => {
  it('renders Home at / when there is no legacy Roles query', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    vi.mocked(api.getProfile).mockResolvedValue(null)
    renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Career cockpit' })).toBeTruthy()
  })

  it('redirects /?period=current to /opportunities?period=current', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    renderApp('/?period=current')
    await waitFor(() => expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ period: 'current' })))
    expect(await screen.findByRole('heading', { level: 1, name: 'Opportunities' })).toBeTruthy()
  })

  it('preserves multiple recognized legacy query params on redirect', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    renderApp('/?period=year&year=2024')
    await waitFor(() =>
      expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ period: 'all', year: 2024 })),
    )
    expect(await screen.findByRole('heading', { level: 1, name: 'Opportunities' })).toBeTruthy()
    expect(await screen.findByDisplayValue('2024')).toBeTruthy()
  })

  it('renders /opportunities directly without going through Home', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    renderApp('/opportunities')
    await waitFor(() =>
      expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ period: 'current', sort: 'captured_at' })),
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Opportunities' })).toBeTruthy()
  })

  it('does not redirect an unrelated query string on / away from Home', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    vi.mocked(api.getProfile).mockResolvedValue(null)
    renderApp('/?utm_source=newsletter')
    expect(await screen.findByRole('heading', { level: 1, name: 'Career cockpit' })).toBeTruthy()
  })
})

describe('Primary navigation active state', () => {
  it('highlights Explore my future on /future', async () => {
    renderApp('/future')
    const link = await screen.findByRole('link', { name: 'Explore my future' })
    expect(link.className).toContain('active')
  })

  it('inherits the Explore my future active state on /targets', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    renderApp('/targets')
    await waitFor(() => expect(api.listTargets).toHaveBeenCalled())
    const link = screen.getByRole('link', { name: 'Explore my future' })
    expect(link.className).toContain('active')
    const home = screen.getByRole('link', { name: 'Home' })
    expect(home.className).not.toContain('active')
  })

  it('highlights Applications on /applications', async () => {
    renderApp('/applications')
    const link = await screen.findByRole('link', { name: 'Applications' })
    expect(link.className).toContain('active')
  })
})

describe('Secondary navigation', () => {
  it('exposes My profile & evidence and Data & models links', async () => {
    renderApp('/applications')
    await screen.findByRole('heading', { level: 1, name: 'Applications' })
    expect((screen.getByText('Vocabulary').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/vocabulary')
    expect((screen.getByText('Profile overview').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/profile')
    expect((screen.getByText('Preferences').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/preferences')
  })
})

describe('Existing deep links still work', () => {
  it('still renders /profile', async () => {
    vi.mocked(api.getProfile).mockResolvedValue({ id: 'snap-1', _display: 'Narrative.' } as Profile360Row)
    renderApp('/profile')
    expect(await screen.findByRole('heading', { level: 1, name: 'Your profile' })).toBeTruthy()
  })
})

describe('Add posting placement', () => {
  it('is reachable from Home', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    vi.mocked(api.getProfile).mockResolvedValue(null)
    renderApp('/')
    await screen.findByRole('heading', { level: 1, name: 'Career cockpit' })
    const links = screen.getAllByText('Add posting')
    expect(links.length).toBeGreaterThan(0)
    links.forEach((el) => expect((el.closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/import'))
  })

  it('is reachable from Opportunities', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    renderApp('/opportunities')
    await screen.findByRole('heading', { level: 1, name: 'Opportunities' })
    const links = screen.getAllByText('Add posting')
    expect(links.length).toBeGreaterThan(0)
    links.forEach((el) => expect((el.closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/import'))
  })
})

describe('Accessibility', () => {
  it('gives the main nav an accessible label', async () => {
    renderApp('/applications')
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeTruthy()
  })

  it('renders Add posting and Log out as native, keyboard-addressable controls', async () => {
    renderApp('/applications')
    expect(screen.getByRole('link', { name: 'Add posting' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Log out/ })).toBeTruthy()
  })

  it('gives Home, Explore, Opportunities and Applications each a level-1 heading', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    vi.mocked(api.getProfile).mockResolvedValue(null)
    renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Career cockpit' })).toBeTruthy()
    cleanup()

    renderApp('/future')
    expect(screen.getByRole('heading', { level: 1, name: 'Explore my future' })).toBeTruthy()
    cleanup()

    vi.mocked(api.listRoles).mockResolvedValue(rolesResponse())
    renderApp('/opportunities')
    expect(await screen.findByRole('heading', { level: 1, name: 'Opportunities' })).toBeTruthy()
    cleanup()

    renderApp('/applications')
    expect(screen.getByRole('heading', { level: 1, name: 'Applications' })).toBeTruthy()
  })
})
