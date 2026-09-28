import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import CareerDirectionDetail from './CareerDirectionDetail'
import { api, type CareerDirection, type CareerDirectionDiscoveryRun, type PreferenceDimension, type Role } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    getCareerDirection: vi.fn(),
    listPreferenceDimensions: vi.fn(),
    selectCareerDirection: vi.fn(),
    archiveCareerDirection: vi.fn(),
    reopenCareerDirection: vi.fn(),
    getCareerDirectionDiscoveryRun: vi.fn(),
    listTargets: vi.fn(),
    updateCareerDirection: vi.fn(),
  },
}))

const DIMENSIONS: PreferenceDimension[] = [
  { code: 'autonomy', label: 'Autonomy', definition: 'Independent latitude', sort_order: 1 },
]

function direction(overrides: Partial<CareerDirection> = {}): CareerDirection {
  return {
    id: 'dir-1', name: 'Technical actuarial leadership', summary: 'Stay hands-on while leading.', state: 'exploring', origin: 'user',
    dimensions: [{ dimension_code: 'autonomy', desired_direction: 'toward', importance: 3, note: 'Important' }],
    constraints: { locations: ['Ireland'], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [] },
    target: null, archetype: null, source_discovery_run_id: null, source_candidate_id: null,
    selected_at: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function renderDetail(id = 'dir-1') {
  return render(
    <MemoryRouter initialEntries={[`/future/directions/${id}`]}>
      <Routes>
        <Route path="/future/directions/:id" element={<CareerDirectionDetail />} />
        <Route path="/targets/new" element={<div>Add target page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Career Direction detail — content', () => {
  it('shows name, summary, desired properties and constraints', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction())
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    renderDetail()
    expect(await screen.findByRole('heading', { level: 1, name: 'Technical actuarial leadership' })).toBeTruthy()
    expect(screen.getByText('Stay hands-on while leading.')).toBeTruthy()
    expect(screen.getByText(/Autonomy/)).toBeTruthy()
    expect(screen.getByText(/toward \(importance 3\/3\)/)).toBeTruthy()
    expect(screen.getByText(/Ireland/)).toBeTruthy()
  })

  it('states plainly that a manual direction was user-defined', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ origin: 'user' }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    renderDetail()
    expect(await screen.findByText('This direction was defined manually by you.')).toBeTruthy()
  })
})

describe('Career Direction detail — selection', () => {
  it('selects an exploring direction and reflects the new state with no auto-pick of a replacement', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ state: 'exploring' }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.selectCareerDirection).mockResolvedValue(direction({ state: 'selected', selected_at: 't' }))
    renderDetail()
    const button = await screen.findByText('Select as my current direction')
    fireEvent.click(button)
    await waitFor(() => expect(api.selectCareerDirection).toHaveBeenCalledWith('dir-1'))
    expect(await screen.findByText('Current direction')).toBeTruthy()
  })

  it('shows "Current direction" with no select button once already selected', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ state: 'selected', selected_at: 't' }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    renderDetail()
    expect(await screen.findByText('Current direction')).toBeTruthy()
    expect(screen.queryByText('Select as my current direction')).toBeNull()
  })

  it('archives and then offers to reopen', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ state: 'exploring' }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.archiveCareerDirection).mockResolvedValue(direction({ state: 'archived' }))
    renderDetail()
    fireEvent.click(await screen.findByText('Archive'))
    await waitFor(() => expect(api.archiveCareerDirection).toHaveBeenCalledWith('dir-1'))
    expect(await screen.findByText('Reopen')).toBeTruthy()
  })
})

describe('Career Direction detail — provenance', () => {
  it('shows the adopted candidate\'s own rationale for an AI-adopted direction', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ origin: 'ai_adopted', source_discovery_run_id: 'run-1', source_candidate_id: 'cand-1' }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    const run: CareerDirectionDiscoveryRun = {
      id: 'run-1', status: 'ok', model: 'test', prompt_name: 'p', prompt_version: 'v', guidance: null,
      criteria: { name: null, dimensions: [], constraints: { locations: [], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [] }, guidance: null },
      source_manifest: [], error_type: null, error_message: null, created_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:00:01Z',
      output: {
        insufficient_evidence: false, insufficient_evidence_reason: null,
        candidates: [{
          id: 'cand-1', name: 'Technical actuarial leadership', summary: { text: 'Grounded summary.', source_refs: [] },
          primary_archetype_id: null, related_archetype_ids: [], priority_alignment: [],
          market_basis: [{ text: 'Demand across the corpus.', source_refs: [] }],
          person_basis: [], compensation_context: null,
          tradeoffs: [{ text: 'May require relocation.', source_refs: [] }], unknowns: [],
        }],
      },
    }
    vi.mocked(api.getCareerDirectionDiscoveryRun).mockResolvedValue(run)
    renderDetail()
    expect(await screen.findByText('Grounded summary.')).toBeTruthy()
    expect(screen.getByText('Demand across the corpus.')).toBeTruthy()
    expect(screen.getByText('May require relocation.')).toBeTruthy()
    expect(screen.getByText(/never a ranked recommendation/)).toBeTruthy()
  })
})

describe('Career Direction detail — Target linking', () => {
  it('shows an honest not-yet-a-Target state with a Create-a-Target link carrying the direction id', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ target: null }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    renderDetail()
    expect(await screen.findByText('This is still a direction, not yet a concrete Target.')).toBeTruthy()
    const link = screen.getByText('Create a Target from this direction').closest('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/targets/new?direction_id=dir-1')
  })

  it('shows the linked Target and a Pathways link when one exists', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ target: { id: 'target-1', title: 'Head of Capital', organisation: 'Acme' } }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    renderDetail()
    expect(await screen.findByText('Head of Capital')).toBeTruthy()
    expect(screen.getByText('Open Target').closest('a')?.getAttribute('href')).toBe('/targets/target-1')
    expect(screen.getByText('Pathways').closest('a')?.getAttribute('href')).toBe('/pathways/target-1')
  })

  it('links an existing Target from the picker', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction({ target: null }))
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.listTargets).mockResolvedValue([{ id: 'target-2', title: 'Head of Risk' } as Role])
    vi.mocked(api.updateCareerDirection).mockResolvedValue(direction({ target: { id: 'target-2', title: 'Head of Risk', organisation: null } }))
    renderDetail()
    fireEvent.click(await screen.findByText('Link an existing Target instead'))
    const select = await screen.findByDisplayValue('Choose a target…')
    fireEvent.change(select, { target: { value: 'target-2' } })
    fireEvent.click(screen.getByText('Link Target'))
    await waitFor(() => expect(api.updateCareerDirection).toHaveBeenCalledWith('dir-1', { target_role_instance_id: 'target-2' }))
  })
})
