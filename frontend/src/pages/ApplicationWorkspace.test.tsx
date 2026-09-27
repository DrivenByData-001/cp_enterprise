import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ApplicationWorkspace from './ApplicationWorkspace'
import {
  api,
  type Application,
  type ApplicationDetail,
  type ApplicationDetailRole,
  type ApplicationEvidence,
  type ApplicationEvidenceItem,
  type ApplicationNote,
} from '../lib/api'

// Phase 3 (docs/34 §9): the Application workspace — preparation checks,
// evidence presentation, the gap workflow, application-local notes, status
// controls and the future-stage placeholders.

vi.mock('../lib/api', () => ({
  api: {
    getApplication: vi.fn(),
    getApplicationEvidence: vi.fn(),
    updateApplicationStatus: vi.fn(),
    createApplicationNote: vi.fn(),
    updateApplicationNote: vi.fn(),
    deleteApplicationNote: vi.fn(),
  },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const baseApplication: Application = {
  id: 'app-1',
  role_instance_id: 'role-1',
  status: 'preparing',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

const baseRole: ApplicationDetailRole = {
  id: 'role-1',
  title: 'Head of Capital',
  organisation: 'An insurer',
  location: 'London',
  country: 'United Kingdom',
  remote_type: 'hybrid',
  posting_date: '2026-01-01',
  instance_type: 'observed_posting',
  url: null,
}

function makeDetail(overrides: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return { application: baseApplication, role: baseRole, notes: [], ...overrides }
}

function makeItem(overrides: Partial<ApplicationEvidenceItem> = {}): ApplicationEvidenceItem {
  return {
    concept: { id: 'c1', canonical_name: 'Python', type_code: 'tool' },
    status: 'not_found',
    role_side: {
      requirement_claim_id: 'rc1', role_skill_observation_id: null, requirement_type: 'required',
      basis: 'stated', review_status: 'accepted', evidence_span: 'Requires Python.', document: null,
    },
    person_side: { mappings: [], assertion: null, component_of: [], coverage: null },
    role_requirement_reviewed: true,
    notes: [],
    ...overrides,
  }
}

function makeEvidence(overrides: Partial<ApplicationEvidence> = {}): ApplicationEvidence {
  return {
    application_id: 'app-1',
    role_instance_id: 'role-1',
    role: { id: 'role-1', title: 'Head of Capital', kind: 'posting' },
    review_summary: {
      accepted: 1, unreviewed: 0, rejected: 0, unresolved_proposals: 0,
      extraction_attempted: true, needs_reextraction: 0, complete: true,
    },
    counts: { evidenced: 0, partial: 0, user_asserted: 0, not_found: 1 },
    blocking_gaps: [],
    unverified_required: [],
    items: [makeItem()],
    notes: [],
    engine_version: 'capability-engine-v1',
    ...overrides,
  }
}

function renderWorkspace(detail: ApplicationDetail, evidence: ApplicationEvidence | Error) {
  vi.mocked(api.getApplication).mockResolvedValue(detail)
  if (evidence instanceof Error) vi.mocked(api.getApplicationEvidence).mockRejectedValue(evidence)
  else vi.mocked(api.getApplicationEvidence).mockResolvedValue(evidence)
  return render(
    <MemoryRouter initialEntries={['/applications/app-1']}>
      <Routes>
        <Route path="/applications/:id" element={<ApplicationWorkspace />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Header', () => {
  it('shows role, status control, and a link back to the opportunity', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    expect(await screen.findByRole('heading', { name: 'Head of Capital' })).toBeTruthy()
    expect(screen.getByText(/An insurer/)).toBeTruthy()
    const back = screen.getByText('← Back to opportunity').closest('a') as HTMLAnchorElement
    expect(back.getAttribute('href')).toBe('/roles/role-1')
    expect((screen.getByLabelText('Application status') as HTMLSelectElement).value).toBe('preparing')
  })
})

describe('Preparation checks', () => {
  it('shows a complete requirement review without treating it as a problem', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({ items: [makeItem({ status: 'evidenced', role_requirement_reviewed: true })], counts: { evidenced: 1, partial: 0, user_asserted: 0, not_found: 0 } }),
    )
    expect(await screen.findByText('Requirement review complete')).toBeTruthy()
    expect(screen.queryByText('Requirement review incomplete')).toBeNull()
  })

  it('shows an incomplete requirement review with a review link', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({
        review_summary: { accepted: 1, unreviewed: 2, rejected: 0, unresolved_proposals: 1, extraction_attempted: true, needs_reextraction: 0, complete: false },
      }),
    )
    expect(await screen.findByText('Requirement review incomplete')).toBeTruthy()
    expect(screen.getByText('Review now').closest('a')?.getAttribute('href')).toBe('/role-instances/role-1/requirements')
  })

  it('shows a never-extracted role distinctly from a reviewed-and-empty one', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({
        review_summary: { accepted: 0, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: false, needs_reextraction: 0, complete: true },
        items: [],
        counts: { evidenced: 0, partial: 0, user_asserted: 0, not_found: 0 },
      }),
    )
    expect(await screen.findByText("Requirements haven't been extracted from this posting yet.")).toBeTruthy()
  })

  it('distinguishes blocking gaps from unverified requirements in the counts', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({
        blocking_gaps: [{ id: 'c1', canonical_name: 'Python', type_code: 'tool' }],
        unverified_required: [{ id: 'c2', canonical_name: 'Excel', type_code: 'tool', status: 'partial' }],
      }),
    )
    expect(await screen.findByText('1 required evidence gap')).toBeTruthy()
    expect(screen.getByText('1 required capability not fully verified')).toBeTruthy()
  })

  it('shows the applications-only example count', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({ notes: [{ id: 'n1', application_id: 'app-1', concept_id: 'c1', note_type: 'evidence_example', note_text: 'x', created_at: 't', updated_at: 't' }] }),
    )
    expect(await screen.findByText('1 example added for this application.')).toBeTruthy()
  })

  it('keeps the page usable when the evidence request fails', async () => {
    renderWorkspace(makeDetail(), new Error('evidence service down'))
    expect(await screen.findByRole('heading', { name: 'Head of Capital' })).toBeTruthy()
    expect(await screen.findByText(/Evidence couldn't be loaded/)).toBeTruthy()
  })
})

describe('Evidence to use', () => {
  it('shows the strongest available evidence and flags legacy vs reviewed requirements', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({
        items: [
          makeItem({ concept: { id: 'c1', canonical_name: 'Python', type_code: 'tool' }, status: 'evidenced', role_requirement_reviewed: true }),
          makeItem({ concept: { id: 'c2', canonical_name: 'SQL', type_code: 'tool' }, status: 'not_found', role_requirement_reviewed: false }),
        ],
      }),
    )
    const section = (await screen.findByRole('heading', { name: 'Evidence to use' })).closest('section') as HTMLElement
    expect(within(section).getByText(/Reviewed requirement/)).toBeTruthy()
    expect(within(section).getByText(/Legacy role extraction — not human-reviewed/)).toBeTruthy()
  })
})

describe('Gaps and uncertainties', () => {
  it('offers focused actions for a blocking gap without ever blocking the user', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({ blocking_gaps: [{ id: 'c1', canonical_name: 'Python', type_code: 'tool' }] }),
    )
    const gapsSection = (await screen.findByRole('heading', { name: 'Gaps and uncertainties' })).closest('section') as HTMLElement
    expect(within(gapsSection).getByText('No accepted evidence found for this requirement. This does not mean you lack the capability.')).toBeTruthy()
    expect(within(gapsSection).getByText('Add an example for this application')).toBeTruthy()
    expect(within(gapsSection).getByText('Review my evidence').closest('a')?.getAttribute('href')).toBe('/comparison/role-1')
    expect(within(gapsSection).getByText(/continue without adding anything/i)).toBeTruthy()
  })

  it('adding an application-only example does not convert a not_found requirement into evidenced', async () => {
    vi.mocked(api.createApplicationNote).mockResolvedValue({
      id: 'note-1', application_id: 'app-1', concept_id: 'c1', note_type: 'evidence_example', note_text: 'Led a migration.', created_at: 't', updated_at: 't',
    })
    const withoutNote = makeEvidence({ blocking_gaps: [{ id: 'c1', canonical_name: 'Python', type_code: 'tool' }] })
    const withNote = makeEvidence({
      blocking_gaps: [{ id: 'c1', canonical_name: 'Python', type_code: 'tool' }],
      items: [makeItem({ notes: [{ id: 'note-1', application_id: 'app-1', concept_id: 'c1', note_type: 'evidence_example', note_text: 'Led a migration.', created_at: 't', updated_at: 't' }] })],
      notes: [{ id: 'note-1', application_id: 'app-1', concept_id: 'c1', note_type: 'evidence_example', note_text: 'Led a migration.', created_at: 't', updated_at: 't' }],
    })
    vi.mocked(api.getApplication).mockResolvedValue(makeDetail())
    vi.mocked(api.getApplicationEvidence).mockResolvedValueOnce(withoutNote).mockResolvedValueOnce(withNote)
    render(
      <MemoryRouter initialEntries={['/applications/app-1']}>
        <Routes><Route path="/applications/:id" element={<ApplicationWorkspace />} /></Routes>
      </MemoryRouter>,
    )

    const gapsSection = (await screen.findByRole('heading', { name: 'Gaps and uncertainties' })).closest('section') as HTMLElement
    fireEvent.click(within(gapsSection).getByText('Add an example for this application'))
    fireEvent.change(within(gapsSection).getByPlaceholderText(/Describe an example/), { target: { value: 'Led a migration.' } })
    fireEvent.click(within(gapsSection).getByText('Save note'))

    await waitFor(() => expect(api.createApplicationNote).toHaveBeenCalledWith('app-1', { concept_id: 'c1', note_type: 'evidence_example', note_text: 'Led a migration.' }))
    // The status shown in "Gaps and uncertainties" is still the blocking gap
    // — status is still not_found/required, never silently promoted.
    await screen.findByText('Application-only example added (1).')
    expect(within(gapsSection).getByText('No accepted evidence found for this requirement. This does not mean you lack the capability.')).toBeTruthy()
    expect(screen.getByText('1 required evidence gap')).toBeTruthy()
  })
})

describe('Application notes', () => {
  it('adds, edits and deletes a general note', async () => {
    const withoutNote = makeDetail()
    const noteObj: ApplicationNote = { id: 'note-1', application_id: 'app-1', concept_id: null, note_type: 'general', note_text: 'Ask about hybrid policy.', created_at: 't', updated_at: 't' }
    vi.mocked(api.getApplication).mockResolvedValue(withoutNote)
    vi.mocked(api.getApplicationEvidence)
      .mockResolvedValueOnce(makeEvidence({ notes: [] }))
      .mockResolvedValueOnce(makeEvidence({ notes: [noteObj] }))
      .mockResolvedValueOnce(makeEvidence({ notes: [{ ...noteObj, note_text: 'Ask about hybrid policy in the first call.' }] }))
      .mockResolvedValueOnce(makeEvidence({ notes: [] }))
    vi.mocked(api.createApplicationNote).mockResolvedValue(noteObj)
    vi.mocked(api.updateApplicationNote).mockResolvedValue({ ...noteObj, note_text: 'Ask about hybrid policy in the first call.' })
    vi.mocked(api.deleteApplicationNote).mockResolvedValue({ status: 'deleted' })

    render(
      <MemoryRouter initialEntries={['/applications/app-1']}>
        <Routes><Route path="/applications/:id" element={<ApplicationWorkspace />} /></Routes>
      </MemoryRouter>,
    )

    const notesSection = (await screen.findByRole('heading', { name: 'Application notes' })).closest('section') as HTMLElement
    fireEvent.click(within(notesSection).getByText('Add a note'))
    fireEvent.change(within(notesSection).getByPlaceholderText('Add a note…'), { target: { value: 'Ask about hybrid policy.' } })
    fireEvent.click(within(notesSection).getByText('Save note'))
    await waitFor(() => expect(api.createApplicationNote).toHaveBeenCalledWith('app-1', { note_type: 'general', note_text: 'Ask about hybrid policy.' }))

    await screen.findByText('Ask about hybrid policy.')
    fireEvent.click(screen.getByText('Edit'))
    const textarea = screen.getByDisplayValue('Ask about hybrid policy.')
    fireEvent.change(textarea, { target: { value: 'Ask about hybrid policy in the first call.' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(api.updateApplicationNote).toHaveBeenCalledWith('app-1', 'note-1', { note_text: 'Ask about hybrid policy in the first call.' }))

    await screen.findByText('Ask about hybrid policy in the first call.')
    vi.stubGlobal('confirm', () => true)
    fireEvent.click(screen.getByText('Delete'))
    await waitFor(() => expect(api.deleteApplicationNote).toHaveBeenCalledWith('app-1', 'note-1'))
    vi.unstubAllGlobals()
  })

  it('states plainly that notes never become Profile360 evidence', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    expect(await screen.findByText(/never become Profile360 claims, mappings or evidence/)).toBeTruthy()
  })
})

describe('Status controls', () => {
  it('changes status via the API and reflects the update without a full page reload', async () => {
    vi.mocked(api.updateApplicationStatus).mockResolvedValue({ ...baseApplication, status: 'submitted' })
    renderWorkspace(makeDetail(), makeEvidence())
    const select = await screen.findByLabelText('Application status')
    fireEvent.change(select, { target: { value: 'submitted' } })
    await waitFor(() => expect(api.updateApplicationStatus).toHaveBeenCalledWith('app-1', 'submitted'))
    expect((select as HTMLSelectElement).value).toBe('submitted')
  })

  it('never derives status from structural readiness', async () => {
    renderWorkspace(makeDetail(), makeEvidence({ blocking_gaps: [{ id: 'c1', canonical_name: 'Python', type_code: 'tool' }] }))
    await screen.findByText('1 required evidence gap')
    expect(api.updateApplicationStatus).not.toHaveBeenCalled()
    expect(screen.getByText(/never changes automatically based on the preparation checks/)).toBeTruthy()
  })
})

describe('Future stages', () => {
  it('shows restrained placeholders that never pretend the features exist', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    expect(await screen.findByText('Positioning')).toBeTruthy()
    expect(screen.getByText('CV / application material')).toBeTruthy()
    expect(screen.getByText('Interview preparation')).toBeTruthy()
    expect(screen.getAllByText(/arrive in the next phase|arrives in a later phase/).length).toBe(3)
  })
})
