import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Profile360 from './Profile360'
import { api, type Profile360Mapping, type Profile360Row } from '../lib/api'

vi.mock('../lib/api', () => ({ api: {
  listProfile360Claims: vi.fn(), listProfile360Capabilities: vi.fn(),
  listProfile360Mappings: vi.fn(), reviewProfile360Mapping: vi.fn(),
  mapProfile360Claim: vi.fn(), mapProfile360Capability: vi.fn(),
} }))

const capability: Profile360Row = { id: 'cap-1', _display: 'Model change governance' }
const claim: Profile360Row = { id: 'claim-1', _display: 'Led model validation' }
const pending = (kind: 'capability' | 'claim'): Profile360Mapping => ({
  id: 'map-1', profile360_id: kind === 'capability' ? 'cap-1' : 'claim-1',
  mapping_basis: 'ai_suggested', review_status: 'unreviewed', reviewed_at: null, created_at: '2026-10-01',
  extraction_run_id: null, concept_id: 'c1', canonical_name: 'model change management', type_code: 'capability',
  _display: kind === 'capability' ? capability._display : claim._display,
})

beforeEach(() => {
  vi.mocked(api.listProfile360Claims).mockResolvedValue([claim])
  vi.mocked(api.listProfile360Capabilities).mockResolvedValue([capability])
})
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('requests only unmapped rows from the backend', async () => {
  vi.mocked(api.listProfile360Mappings).mockResolvedValue([])
  render(<Profile360 />)
  await screen.findByText('Led model validation')
  expect(api.listProfile360Claims).toHaveBeenCalledWith(50, 0, true)
  fireEvent.click(screen.getByRole('button', { name: 'Capabilities' }))
  await screen.findByText('Model change governance')
  expect(api.listProfile360Capabilities).toHaveBeenCalledWith(50, 0, true)
})

for (const action of ['Accept', 'Reject'] as const) {
  it(`${action} refreshes both the review queue and the unmapped list`, async () => {
    let reviewed = false
    vi.mocked(api.listProfile360Mappings).mockImplementation(async (kind) =>
      kind === 'capability' && !reviewed ? [pending('capability')] : [])
    vi.mocked(api.reviewProfile360Mapping).mockImplementation(async () => {
      reviewed = true
      return { id: 'map-1', review_status: 'x' }
    })
    // Backend semantics: unreviewed -> not unmapped; accepted -> gone; rejected -> eligible again.
    vi.mocked(api.listProfile360Capabilities).mockImplementation(async () =>
      reviewed && action === 'Reject' ? [capability] : [])

    render(<Profile360 />)
    fireEvent.click(await screen.findByRole('button', { name: 'Capabilities' }))
    fireEvent.click(await screen.findByRole('button', { name: action }))

    await waitFor(() => expect(api.reviewProfile360Mapping).toHaveBeenCalledWith(
      'map-1', 'capability', action === 'Accept' ? 'accept' : 'reject'))
    await waitFor(() => expect(screen.getByText('Nothing pending review.')).toBeTruthy())
    if (action === 'Accept') {
      await waitFor(() => expect(screen.queryByText('Model change governance')).toBeNull())
    } else {
      await screen.findByText('Model change governance')
    }
    // initial claim tab + capability tab + post-review refresh
    expect(vi.mocked(api.listProfile360Capabilities).mock.calls.length).toBeGreaterThanOrEqual(2)
  })
}

it('applies the same refresh to the claims tab', async () => {
  vi.mocked(api.listProfile360Mappings).mockResolvedValueOnce([pending('claim')]).mockResolvedValue([])
  vi.mocked(api.reviewProfile360Mapping).mockResolvedValue({ id: 'map-1', review_status: 'accepted' })
  vi.mocked(api.listProfile360Claims).mockResolvedValueOnce([]).mockResolvedValue([])
  render(<Profile360 />)
  fireEvent.click(await screen.findByRole('button', { name: 'Accept' }))
  await waitFor(() => expect(api.listProfile360Claims).toHaveBeenCalledTimes(2))
  expect(api.reviewProfile360Mapping).toHaveBeenCalledWith('map-1', 'claim', 'accept')
})
