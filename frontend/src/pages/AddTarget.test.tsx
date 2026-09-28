import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import AddTarget from './AddTarget'
import { api, type CareerDirection } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    getCareerDirection: vi.fn(),
    updateCareerDirection: vi.fn(),
    previewTarget: vi.fn(),
    importTarget: vi.fn(),
  },
}))

function direction(overrides: Partial<CareerDirection> = {}): CareerDirection {
  return {
    id: 'dir-1', name: 'Technical actuarial leadership', summary: 'Stay hands-on while leading.', state: 'exploring', origin: 'user',
    dimensions: [{ dimension_code: 'autonomy', desired_direction: 'toward', importance: 3, note: null }],
    constraints: { locations: ['Ireland'], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [] },
    target: null, archetype: null, source_discovery_run_id: null, source_candidate_id: null,
    selected_at: null, created_at: 't', updated_at: 't',
    ...overrides,
  }
}

function renderPage(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/targets/new" element={<AddTarget />} />
        <Route path="/targets/:id" element={<div>Target detail page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Add target — normal flow (no direction_id)', () => {
  it('renders an empty form and never calls the Career Direction API', async () => {
    renderPage('/targets/new')
    expect(screen.getByRole('heading', { name: 'Add a target' })).toBeTruthy()
    expect(api.getCareerDirection).not.toHaveBeenCalled()
    expect(screen.getByText('← Back to targets').getAttribute('href')).toBe('/targets')
  })
})

describe('Add target — prefilled from a Career Direction', () => {
  it('prefills title, marks imagined, and describes the direction in the description/support fields', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction())
    renderPage('/targets/new?direction_id=dir-1')
    expect(await screen.findByText(/Prefilled from your Career Direction/)).toBeTruthy()
    const titleInput = screen.getByDisplayValue('Technical actuarial leadership') as HTMLInputElement
    expect(titleInput).toBeTruthy()
    const roleTypeSelect = screen.getByDisplayValue('Imagined role') as HTMLSelectElement
    expect(roleTypeSelect.value).toBe('imagined')
    expect(screen.getByText('← Back to direction').getAttribute('href')).toBe('/future/directions/dir-1')
  })

  it('lets the user edit every prefilled field before saving', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction())
    renderPage('/targets/new?direction_id=dir-1')
    const titleInput = await screen.findByDisplayValue('Technical actuarial leadership')
    fireEvent.change(titleInput, { target: { value: 'Edited title' } })
    expect(screen.getByDisplayValue('Edited title')).toBeTruthy()
  })

  it('shows an honest error if the Career Direction fails to load, without blocking the form', async () => {
    vi.mocked(api.getCareerDirection).mockRejectedValue(new Error('not found'))
    renderPage('/targets/new?direction_id=dir-missing')
    expect(await screen.findByText(/Could not load the Career Direction to prefill from/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Add a target' })).toBeTruthy()
  })

  it('links the new Target back to the Career Direction after saving', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction())
    vi.mocked(api.importTarget).mockResolvedValue({ id: 'new-target', status: 'imported' })
    vi.mocked(api.updateCareerDirection).mockResolvedValue(direction({ target: { id: 'new-target', title: 'Technical actuarial leadership', organisation: null } }))
    renderPage('/targets/new?direction_id=dir-1')
    await screen.findByDisplayValue('Technical actuarial leadership')
    fireEvent.click(screen.getByText('Continue manually'))
    fireEvent.click(await screen.findByText('Save target'))
    await waitFor(() => expect(api.importTarget).toHaveBeenCalled())
    await waitFor(() => expect(api.updateCareerDirection).toHaveBeenCalledWith('dir-1', { target_role_instance_id: 'new-target' }))
    expect(await screen.findByText('Target detail page')).toBeTruthy()
  })

  it('still navigates to the new Target even if linking back fails', async () => {
    vi.mocked(api.getCareerDirection).mockResolvedValue(direction())
    vi.mocked(api.importTarget).mockResolvedValue({ id: 'new-target', status: 'imported' })
    vi.mocked(api.updateCareerDirection).mockRejectedValue(new Error('link failed'))
    renderPage('/targets/new?direction_id=dir-1')
    await screen.findByDisplayValue('Technical actuarial leadership')
    fireEvent.click(screen.getByText('Continue manually'))
    fireEvent.click(await screen.findByText('Save target'))
    expect(await screen.findByText('Target detail page')).toBeTruthy()
  })
})
