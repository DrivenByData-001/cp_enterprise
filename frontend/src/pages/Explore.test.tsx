import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Explore from './Explore'
import { api, type CareerDirection } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    getSelectedCareerDirection: vi.fn(),
    listCareerDirections: vi.fn(),
  },
}))

function direction(overrides: Partial<CareerDirection> = {}): CareerDirection {
  return {
    id: 'dir-1', name: 'Technical actuarial leadership', summary: '', state: 'exploring', origin: 'user',
    dimensions: [], constraints: { locations: [], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [] },
    target: null, archetype: null, source_discovery_run_id: null, source_candidate_id: null,
    selected_at: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Explore my future — supporting links', () => {
  it('links every existing future-oriented tool in plain language', async () => {
    vi.mocked(api.getSelectedCareerDirection).mockResolvedValue({ direction: null })
    vi.mocked(api.listCareerDirections).mockResolvedValue({ items: [] })
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(screen.getByRole('heading', { level: 1, name: 'Explore my future' })).toBeTruthy()
    await waitFor(() => expect(api.getSelectedCareerDirection).toHaveBeenCalled())
    expect((screen.getByText('Preferences →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/preferences')
    expect((screen.getByText('Targets →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/targets')
    expect((screen.getByText('Pathways →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/pathways')
    expect((screen.getByText('Trends →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/trends')
    expect((screen.getByText('Economics →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/economics')
    expect((screen.getByText('Career space →').closest('a') as HTMLAnchorElement).getAttribute('href')).toBe('/space')
  })

  it('distinguishes the observed corpus from the wider market', async () => {
    vi.mocked(api.getSelectedCareerDirection).mockResolvedValue({ direction: null })
    vi.mocked(api.listCareerDirections).mockResolvedValue({ items: [] })
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(await screen.findByText(/never a claim it is fully representative/)).toBeTruthy()
  })

  it('states that the Career Space visualization is not a recommendation', async () => {
    vi.mocked(api.getSelectedCareerDirection).mockResolvedValue({ direction: null })
    vi.mocked(api.listCareerDirections).mockResolvedValue({ items: [] })
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(await screen.findByText(/not a career recommendation/)).toBeTruthy()
  })
})

describe('Explore my future — current direction', () => {
  it('shows an honest empty state and a Design a direction CTA when nothing is selected', async () => {
    vi.mocked(api.getSelectedCareerDirection).mockResolvedValue({ direction: null })
    vi.mocked(api.listCareerDirections).mockResolvedValue({ items: [] })
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(await screen.findByText('You have not selected a Career Direction yet.')).toBeTruthy()
    expect(screen.getByText('Design a direction').closest('a')?.getAttribute('href')).toBe('/future/build')
  })

  it('shows the selected direction with a link to its detail page', async () => {
    const selected = direction({ id: 'dir-selected', name: 'Deep technical track', state: 'selected' })
    vi.mocked(api.getSelectedCareerDirection).mockResolvedValue({ direction: selected })
    vi.mocked(api.listCareerDirections).mockResolvedValue({ items: [selected] })
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(await screen.findByText('Deep technical track')).toBeTruthy()
    expect(screen.getByText('Open direction').closest('a')?.getAttribute('href')).toBe('/future/directions/dir-selected')
  })

  it('never scores or ranks directions I am exploring', async () => {
    vi.mocked(api.getSelectedCareerDirection).mockResolvedValue({ direction: null })
    vi.mocked(api.listCareerDirections).mockResolvedValue({
      items: [direction({ id: 'a', name: 'Direction A' }), direction({ id: 'b', name: 'Direction B' })],
    })
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(await screen.findByText('Direction A')).toBeTruthy()
    expect(screen.getByText('Direction B')).toBeTruthy()
    expect(screen.queryByText(/#1|best|top pick|recommended/i)).toBeNull()
  })

  it('keeps supporting links visible when the Career Direction API fails', async () => {
    vi.mocked(api.getSelectedCareerDirection).mockRejectedValue(new Error('career-directions down'))
    vi.mocked(api.listCareerDirections).mockRejectedValue(new Error('career-directions down'))
    render(<MemoryRouter><Explore /></MemoryRouter>)
    expect(await screen.findByText(/Could not load your current direction/)).toBeTruthy()
    expect(screen.getByText('Preferences →').closest('a')?.getAttribute('href')).toBe('/preferences')
  })
})
