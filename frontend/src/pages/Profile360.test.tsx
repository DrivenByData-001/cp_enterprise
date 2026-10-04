import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Profile360 from './Profile360'
import { api, type Profile360Mapping, type Profile360Row, type Profile360Workbench } from '../lib/api'

vi.mock('../lib/api', () => ({ api: {
  listProfile360Claims: vi.fn(), listProfile360Capabilities: vi.fn(),
  listProfile360Mappings: vi.fn(), reviewProfile360Mapping: vi.fn(),
  mapProfile360Claim: vi.fn(), mapProfile360Capability: vi.fn(),
  getProfile360Workbench: vi.fn(), addProfile360Mapping: vi.fn(), proposeProfile360Vocabulary: vi.fn(),
  searchProfile360Vocabulary: vi.fn(), listConceptTypes: vi.fn(),
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
  expect(api.listProfile360Claims).toHaveBeenCalledWith(50, 0, 'unmapped')
  fireEvent.click(screen.getByRole('button', { name: 'Capabilities' }))
  await screen.findByText('Model change governance')
  expect(api.listProfile360Capabilities).toHaveBeenCalledWith(50, 0, 'unmapped')
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

// --- curation workbench (claims and capabilities share it) ---------------------

const workbench = (kind: 'claim' | 'capability', over: Partial<Profile360Workbench> = {}): Profile360Workbench => ({
  kind, item: kind === 'claim' ? claim : capability, mapping_state: 'unmapped', mappings: [],
  candidates: [
    { concept_id: 'c1', canonical_name: 'Model validation', type_code: 'capability', definition: 'Independently testing models.',
      rank: 1, similarity: 0.83, ai_selected: true, mapping_id: 'map-ai', mapping_status: 'unreviewed' },
    { concept_id: 'c2', canonical_name: 'Model governance', type_code: 'capability', definition: 'Controls over model change.',
      rank: 2, similarity: 0.71, ai_selected: false, mapping_id: null, mapping_status: null },
  ],
  ai_outcome: 'recommended', latest_run: { id: 'r1', task: 't', status: 'ok', reasoning: 'close', error: null },
  proposals: [], allowed_type_codes: kind === 'capability' ? ['capability'] : null, ...over,
})

async function openWorkbench(kind: 'claim' | 'capability') {
  vi.mocked(api.listProfile360Mappings).mockResolvedValue([])
  render(<Profile360 />)
  if (kind === 'capability') fireEvent.click(await screen.findByRole('button', { name: 'Capabilities' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Curate mappings' }))
}

for (const kind of ['claim', 'capability'] as const) {
  it(`${kind}: shows candidates with definitions, similarity and the AI recommendation`, async () => {
    vi.mocked(api.getProfile360Workbench).mockResolvedValue(workbench(kind))
    await openWorkbench(kind)
    await screen.findByText('Independently testing models.')
    expect(screen.getByText('Controls over model change.')).toBeTruthy()
    expect(screen.getByText(/similarity 0\.83/)).toBeTruthy()
    expect(screen.getAllByText('[AI recommendation]')).toHaveLength(1)
    expect(api.getProfile360Workbench).toHaveBeenCalledWith(kind, kind === 'claim' ? 'claim-1' : 'cap-1')
  })

  it(`${kind}: accepts the AI recommendation or maps a different candidate`, async () => {
    vi.mocked(api.getProfile360Workbench).mockResolvedValue(workbench(kind))
    vi.mocked(api.reviewProfile360Mapping).mockResolvedValue({ id: 'map-ai', review_status: 'accepted' })
    vi.mocked(api.addProfile360Mapping).mockResolvedValue({ mapping_id: 'm2', concept_id: 'c2' })
    await openWorkbench(kind)
    await screen.findByText('Controls over model change.')
    fireEvent.click(screen.getByRole('button', { name: 'Map to this' }))
    await waitFor(() => expect(api.addProfile360Mapping).toHaveBeenCalledWith(kind, kind === 'claim' ? 'claim-1' : 'cap-1', 'c2'))
    fireEvent.click(screen.getAllByRole('button', { name: 'Accept' })[0])
    await waitFor(() => expect(api.reviewProfile360Mapping).toHaveBeenCalledWith('map-ai', kind, 'accept'))
  })

  it(`${kind}: searches the full vocabulary and maps a concept outside the candidates`, async () => {
    vi.mocked(api.getProfile360Workbench).mockResolvedValue(workbench(kind))
    vi.mocked(api.searchProfile360Vocabulary).mockResolvedValue([
      { id: 'far', type_code: 'capability', canonical_name: 'Zebra stewardship', definition: 'Looking after striped animals' }])
    vi.mocked(api.addProfile360Mapping).mockResolvedValue({ mapping_id: 'm3', concept_id: 'far' })
    await openWorkbench(kind)
    fireEvent.change(await screen.findByLabelText('Search vocabulary'), { target: { value: 'zebra' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await screen.findByText('Looking after striped animals')
    expect(api.searchProfile360Vocabulary).toHaveBeenCalledWith('zebra', kind)
    const buttons = screen.getAllByRole('button', { name: 'Map to this' })
    fireEvent.click(buttons[buttons.length - 1])
    await waitFor(() => expect(api.addProfile360Mapping).toHaveBeenCalledWith(kind, kind === 'claim' ? 'claim-1' : 'cap-1', 'far'))
  })

  it(`${kind}: suggests new vocabulary when none of the candidates fit`, async () => {
    vi.mocked(api.getProfile360Workbench).mockResolvedValue(workbench(kind, { ai_outcome: 'declined_all_candidates' }))
    vi.mocked(api.listConceptTypes).mockResolvedValue([{ code: 'tool', label: 'Tool', definition: '', is_atom: true, sort_order: 1 }])
    vi.mocked(api.proposeProfile360Vocabulary).mockResolvedValue({ proposal_id: 'p1', surface_form: 'new thing', created_proposal: true })
    await openWorkbench(kind)
    fireEvent.click(await screen.findByRole('button', { name: /None of these — suggest new vocabulary/ }))
    fireEvent.change(screen.getByLabelText('Canonical name'), { target: { value: 'New thing' } })
    fireEvent.change(screen.getByLabelText('Definition'), { target: { value: 'A new idea.' } })
    if (kind === 'claim') {
      await screen.findByRole('option', { name: 'Tool' })
      fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'tool' } })
    } else {
      expect(screen.queryByLabelText('Type')).toBeNull() // capabilities are always capability-typed
    }
    fireEvent.click(screen.getByRole('button', { name: 'Submit suggestion' }))
    await waitFor(() => expect(api.proposeProfile360Vocabulary).toHaveBeenCalledWith(
      kind, kind === 'claim' ? 'claim-1' : 'cap-1',
      { canonical_name: 'New thing', type_code: kind === 'claim' ? 'tool' : 'capability', definition: 'A new idea.' }))
  })

  it(`${kind}: shows existing mappings and vocabulary suggestions with their status`, async () => {
    vi.mocked(api.getProfile360Workbench).mockResolvedValue(workbench(kind, {
      mapping_state: 'mapped',
      mappings: [{ id: 'm1', concept_id: 'c9', canonical_name: 'Already mapped', type_code: 'capability',
        definition: 'Existing def', mapping_basis: 'curator_asserted', review_status: 'accepted' }],
      proposals: [{ id: 'p1', surface_form: 'drafted term', suggested_type: 'capability', suggested_definition: 'Drafted def',
        status: 'pending', origin: 'ai', nearest_canonical_name: 'Model validation', nearest_similarity: 0.6,
        resolved_concept_id: null, resolved_canonical_name: null }],
    }))
    await openWorkbench(kind)
    await screen.findByText('Already mapped')
    expect(screen.getByText('Existing def')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Unmap' })).toBeTruthy()
    expect(screen.getByText('drafted term')).toBeTruthy()
    expect(screen.getByText(/AI draft · pending/)).toBeTruthy()
  })
}

it('mapped items stay browsable and can still be curated', async () => {
  const mapped: Profile360Row = { ...claim, _mapping_state: 'mapped', _mapping_counts: { accepted: 1, unreviewed: 0, rejected: 0 } }
  vi.mocked(api.listProfile360Claims).mockImplementation(async (_l, _o, state) => (state === 'mapped' ? [mapped] : []))
  vi.mocked(api.getProfile360Workbench).mockResolvedValue(workbench('claim', { mapping_state: 'mapped' }))
  vi.mocked(api.addProfile360Mapping).mockResolvedValue({ mapping_id: 'm2', concept_id: 'c2' })
  vi.mocked(api.listProfile360Mappings).mockResolvedValue([])
  render(<Profile360 />)
  fireEvent.click(await screen.findByRole('button', { name: 'Mapped' }))
  await waitFor(() => expect(api.listProfile360Claims).toHaveBeenCalledWith(50, 0, 'mapped'))
  await screen.findByText(/Mapped \(1 accepted, 0 pending\)/)
  fireEvent.click(screen.getByRole('button', { name: 'Curate mappings' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Map to this' }))
  await waitFor(() => expect(api.addProfile360Mapping).toHaveBeenCalled())
  // the row stays open after its list changes, so further mappings can be added
  expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy()
})
