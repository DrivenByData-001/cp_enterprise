import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import CareerDirectionBuilder from './CareerDirectionBuilder'
import {
  api,
  type CareerDirection,
  type CareerDirectionDiscoverResponse,
  type PreferenceDimension,
  type PreferenceSummaryEntry,
} from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listPreferenceDimensions: vi.fn(),
    getCareerDirectionPreferenceSummary: vi.fn(),
    createCareerDirection: vi.fn(),
    discoverCareerDirections: vi.fn(),
    adoptCareerDirectionCandidate: vi.fn(),
    selectCareerDirection: vi.fn(),
  },
  EMPTY_CAREER_DIRECTION_CONSTRAINTS: {
    locations: [], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [],
  },
}))

const DIMENSIONS: PreferenceDimension[] = [
  { code: 'autonomy', label: 'Autonomy', definition: 'Independent latitude', sort_order: 1 },
  { code: 'technical_engagement', label: 'Technical engagement', definition: 'Hands-on work', sort_order: 2 },
]

function emptySummary(): Record<string, PreferenceSummaryEntry> {
  const entry = (code: string, label: string): PreferenceSummaryEntry => ({
    dimension_code: code, label, recent_observations: [], strongest_basis: null,
    agreement: 'no_evidence', suggested_direction: null, basis: null, conflict: false,
  })
  return { autonomy: entry('autonomy', 'Autonomy'), technical_engagement: entry('technical_engagement', 'Technical engagement') }
}

function direction(overrides: Partial<CareerDirection> = {}): CareerDirection {
  return {
    id: 'dir-new', name: 'x', summary: '', state: 'exploring', origin: 'user',
    dimensions: [], constraints: { locations: [], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [] },
    target: null, archetype: null, source_discovery_run_id: null, source_candidate_id: null,
    selected_at: null, created_at: 't', updated_at: 't',
    ...overrides,
  }
}

function discoverResponse(overrides: Partial<CareerDirectionDiscoverResponse> = {}): CareerDirectionDiscoverResponse {
  return {
    status: 'ok', discovery_run_id: 'run-1',
    result: { candidates: [], insufficient_evidence: false, insufficient_evidence_reason: null },
    caveats: [],
    corpus_disclosure: {
      total_observed_postings: 10, postings_with_archetype_assignment: 4, postings_with_reviewed_requirements: 3,
      active_archetypes_total: 2, supported_archetypes: 1, archetypes_with_compensation_evidence: 0,
      economics_freshness: { state: 'fresh', fresh: true, reason: null },
    },
    ...overrides,
  }
}

function DirectionDetailStub() {
  const { id } = useParams<{ id: string }>()
  return <div>Direction detail: {id}</div>
}

function renderBuilder() {
  return render(
    <MemoryRouter initialEntries={['/future/build']}>
      <Routes>
        <Route path="/future/build" element={<CareerDirectionBuilder />} />
        <Route path="/future/directions/:id" element={<DirectionDetailStub />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Career Direction builder — dimensions', () => {
  it('never pre-populates a dimension — every control starts at "Not set"', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    renderBuilder()
    const selects = await screen.findAllByDisplayValue('Not set')
    expect(selects.length).toBe(DIMENSIONS.length)
  })

  it('seeds only non-conflicting suggested dimensions from recorded preferences', async () => {
    const summary = emptySummary()
    summary.autonomy = { ...summary.autonomy, suggested_direction: 'toward', basis: '2 user-stated observations', agreement: 'agree' }
    summary.technical_engagement = { ...summary.technical_engagement, conflict: true, agreement: 'conflict' }
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: summary })
    renderBuilder()
    await screen.findByText('Mixed evidence recorded for this dimension — no suggestion, you decide.')
    fireEvent.click(screen.getByText('Use my recorded preferences as a starting point'))
    const autonomySelect = (await screen.findAllByRole('combobox')).find((el) => (el as HTMLSelectElement).value === 'toward')
    expect(autonomySelect).toBeTruthy()
  })
})

describe('Career Direction builder — manual save', () => {
  it('disables manual save until a name is entered, and requires no AI call', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    const saveButton = screen.getByText('Save this direction manually') as HTMLButtonElement
    expect(saveButton.disabled).toBe(true)
    fireEvent.change(screen.getByPlaceholderText(/Technical actuarial leadership/), { target: { value: 'My direction' } })
    expect(saveButton.disabled).toBe(false)
    expect(api.discoverCareerDirections).not.toHaveBeenCalled()
  })

  it('saves manually with the current dimensions/constraints and navigates to the new direction', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    vi.mocked(api.createCareerDirection).mockResolvedValue(direction({ id: 'dir-saved', name: 'My direction' }))
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    fireEvent.change(screen.getByPlaceholderText(/Technical actuarial leadership/), { target: { value: 'My direction' } })

    const [autonomySelect] = screen.getAllByRole('combobox')
    fireEvent.change(autonomySelect, { target: { value: 'toward' } })
    fireEvent.change(screen.getByText('Locations (one per line)').parentElement!.querySelector('textarea')!, { target: { value: 'Ireland\nUK' } })

    fireEvent.click(screen.getByText('Save this direction manually'))
    await waitFor(() => expect(api.createCareerDirection).toHaveBeenCalled())
    const payload = vi.mocked(api.createCareerDirection).mock.calls[0][0]
    expect(payload.name).toBe('My direction')
    expect(payload.dimensions).toEqual([{ dimension_code: 'autonomy', desired_direction: 'toward', importance: 2, note: null }])
    expect(payload.constraints?.locations).toEqual(['Ireland', 'UK'])
    expect(await screen.findByText('Direction detail: dir-saved')).toBeTruthy()
  })
})

describe('Career Direction builder — AI discovery', () => {
  it('generates hypotheses and renders them unordered, with no rank/score language', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    vi.mocked(api.discoverCareerDirections).mockResolvedValue(discoverResponse({
      result: {
        insufficient_evidence: false, insufficient_evidence_reason: null,
        candidates: [
          {
            id: 'cand-1', name: 'Direction A', summary: { text: 'Summary A', source_refs: ['direction_input:autonomy'] },
            primary_archetype_id: null, related_archetype_ids: [],
            priority_alignment: [{ dimension_code: 'autonomy', alignment: 'supports', explanation: 'Matches.', source_refs: ['x'] }],
            market_basis: [{ text: 'Market evidence A', source_refs: ['archetype:a'] }],
            person_basis: [], compensation_context: null, tradeoffs: [], unknowns: [],
          },
          {
            id: 'cand-2', name: 'Direction B', summary: { text: 'Summary B', source_refs: ['direction_input:autonomy'] },
            primary_archetype_id: null, related_archetype_ids: [],
            priority_alignment: [], market_basis: [], person_basis: [], compensation_context: null, tradeoffs: [], unknowns: [],
          },
        ],
      },
    }))
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    fireEvent.click(screen.getByText('Generate direction hypotheses'))
    expect(await screen.findByDisplayValue('Direction A')).toBeTruthy()
    expect(screen.getByDisplayValue('Direction B')).toBeTruthy()
    expect(screen.queryByText(/#1|#2|best|top pick|recommended|winner/i)).toBeNull()
    expect(screen.getByText('Market evidence A')).toBeTruthy()
  })

  it('shows "no comparable compensation evidence" when compensation_context is null', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    vi.mocked(api.discoverCareerDirections).mockResolvedValue(discoverResponse({
      result: {
        insufficient_evidence: false, insufficient_evidence_reason: null,
        candidates: [{
          id: 'cand-1', name: 'Direction A', summary: { text: 'Summary A', source_refs: ['x'] },
          primary_archetype_id: null, related_archetype_ids: [], priority_alignment: [], market_basis: [],
          person_basis: [], compensation_context: null, tradeoffs: [], unknowns: [],
        }],
      },
    }))
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    fireEvent.click(screen.getByText('Generate direction hypotheses'))
    expect(await screen.findByText('No comparable compensation evidence available for this hypothesis.')).toBeTruthy()
  })

  it('shows the corpus disclosure alongside generated hypotheses', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    vi.mocked(api.discoverCareerDirections).mockResolvedValue(discoverResponse())
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    fireEvent.click(screen.getByText('Generate direction hypotheses'))
    expect(await screen.findByText(/10 observed postings captured/)).toBeTruthy()
  })

  it('shows an insufficient-evidence state and still allows manual save', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    vi.mocked(api.discoverCareerDirections).mockResolvedValue(discoverResponse({
      result: { candidates: [], insufficient_evidence: true, insufficient_evidence_reason: 'No archetypes exist yet.' },
    }))
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    fireEvent.click(screen.getByText('Generate direction hypotheses'))
    expect(await screen.findByText('No archetypes exist yet.')).toBeTruthy()
    expect(screen.getByText('Save this direction manually')).toBeTruthy()
  })

  it('preserves builder inputs when generation fails', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    vi.mocked(api.discoverCareerDirections).mockRejectedValue(new Error('503 Service Unavailable'))
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    fireEvent.change(screen.getByPlaceholderText(/Technical actuarial leadership/), { target: { value: 'My direction' } })
    const [autonomySelect] = screen.getAllByRole('combobox')
    fireEvent.change(autonomySelect, { target: { value: 'toward' } })

    fireEvent.click(screen.getByText('Generate direction hypotheses'))
    expect(await screen.findByText(/503 Service Unavailable/)).toBeTruthy()
    expect(screen.getByDisplayValue('My direction')).toBeTruthy()
    expect((autonomySelect as HTMLSelectElement).value).toBe('toward')
  })

  it('adopting a candidate saves it without selecting it', async () => {
    vi.mocked(api.listPreferenceDimensions).mockResolvedValue(DIMENSIONS)
    vi.mocked(api.getCareerDirectionPreferenceSummary).mockResolvedValue({ dimensions: emptySummary() })
    vi.mocked(api.discoverCareerDirections).mockResolvedValue(discoverResponse({
      result: {
        insufficient_evidence: false, insufficient_evidence_reason: null,
        candidates: [{
          id: 'cand-1', name: 'Direction A', summary: { text: 'Summary A', source_refs: ['x'] },
          primary_archetype_id: null, related_archetype_ids: [], priority_alignment: [], market_basis: [],
          person_basis: [], compensation_context: null, tradeoffs: [], unknowns: [],
        }],
      },
    }))
    vi.mocked(api.adoptCareerDirectionCandidate).mockResolvedValue({ created: true, direction: direction({ id: 'dir-adopted', name: 'Direction A', origin: 'ai_adopted' }) })
    renderBuilder()
    await screen.findAllByDisplayValue('Not set')
    fireEvent.click(screen.getByText('Generate direction hypotheses'))
    await screen.findByDisplayValue('Direction A')
    fireEvent.click(screen.getByText('Save as Career Direction'))
    await waitFor(() => expect(api.adoptCareerDirectionCandidate).toHaveBeenCalledWith('run-1', 'cand-1', { name: 'Direction A' }))
    expect(await screen.findByText('Direction detail: dir-adopted')).toBeTruthy()
    expect(api.selectCareerDirection).not.toHaveBeenCalled()
  })
})
