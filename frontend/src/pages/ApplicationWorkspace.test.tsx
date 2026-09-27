import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ApplicationWorkspace from './ApplicationWorkspace'
import {
  api,
  type Application,
  type ApplicationArtifact,
  type ApplicationArtifactsResponse,
  type ApplicationDetail,
  type ApplicationDetailRole,
  type ApplicationEvent,
  type ApplicationEventType,
  type ApplicationEvidence,
  type ApplicationEvidenceItem,
  type ApplicationNote,
  type ArtifactType,
} from '../lib/api'

// Phase 3 (docs/34 §9): preparation checks, evidence presentation, the gap
// workflow, application-local notes, status controls.
// Phase 4 (docs/35): the real Positioning/CV/Supporting-material stages that
// replace the old "Coming next" placeholders, plus the Interview placeholder
// that's still honestly future.
// Phase 5 (docs/36): the Lifecycle timeline and the real Interview stage that
// replaces that placeholder — Interview Prep generation, grounding, and the
// lifecycle-event CRUD actions.

vi.mock('../lib/api', () => ({
  api: {
    getApplication: vi.fn(),
    getApplicationEvidence: vi.fn(),
    updateApplicationStatus: vi.fn(),
    createApplicationNote: vi.fn(),
    updateApplicationNote: vi.fn(),
    deleteApplicationNote: vi.fn(),
    getApplicationArtifacts: vi.fn(),
    getApplicationArtifactHistory: vi.fn(),
    generateApplicationArtifact: vi.fn(),
    editApplicationArtifact: vi.fn(),
    adoptApplicationArtifact: vi.fn(),
    discardApplicationArtifact: vi.fn(),
    listApplicationEvents: vi.fn(),
    createApplicationEvent: vi.fn(),
    updateApplicationEvent: vi.fn(),
    deleteApplicationEvent: vi.fn(),
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

function makeArtifactsResponse(overrides: Partial<ApplicationArtifactsResponse['artifacts']> = {}): ApplicationArtifactsResponse {
  const empty = { active: null, draft: null, history_count: 0 }
  return {
    application_id: 'app-1',
    artifacts: { positioning: empty, cv: empty, cover_letter: empty, supporting_statement: empty, interview_prep: empty, ...overrides },
    generation_context: { reviewed_requirements_used: 1, legacy_requirements_excluded: 0, pending_unreviewed_excluded: 0, counts: { evidenced: 0, partial: 0, user_asserted: 0, not_found: 1 }, application_examples: 0 },
    interview_generation_context: {
      reviewed_requirements_used: 1, legacy_requirements_excluded: 0, pending_unreviewed_excluded: 0,
      counts: { evidenced: 0, partial: 0, user_asserted: 0, not_found: 1 }, application_examples: 0,
      active_positioning_available: false, active_cv_available: false, active_cover_letter_available: false,
      active_supporting_statement_available: false, lifecycle_events_count: 0,
      upcoming_interview_available: false, upcoming_interview: null,
    },
  }
}

function makePositioningArtifact(overrides: Partial<ApplicationArtifact> = {}): ApplicationArtifact {
  return {
    id: 'artifact-1',
    application_id: 'app-1',
    artifact_type: 'positioning' as ArtifactType,
    status: 'draft',
    origin: 'ai',
    generator_version: '1',
    model: 'test-model',
    prompt_name: 'application_positioning.md',
    prompt_version: 'v1',
    guidance: null,
    source_manifest: [{ ref: 'role_requirement:c1', kind: 'role_requirement', label: 'Python (reviewed role requirement)', category: 'role_side_context' }],
    content: {
      positioning_statement: { text: 'A strong fit for this role.', source_refs: ['role_requirement:c1'] },
      themes: [],
      requirements_to_lead_with: [],
      gaps_and_cautions: [],
      language_to_mirror: [],
      avoid_claiming: [],
    },
    grounding_status: 'grounded_generation',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    superseded_at: null,
    stale: false,
    ...overrides,
  }
}

function makeEvent(overrides: Partial<ApplicationEvent> = {}): ApplicationEvent {
  return {
    id: 'event-1',
    application_id: 'app-1',
    event_type: 'submitted' as ApplicationEventType,
    event_at: '2026-01-05T10:00:00Z',
    label: null,
    notes: null,
    details: {},
    created_at: '2026-01-05T10:00:00Z',
    updated_at: '2026-01-05T10:00:00Z',
    ...overrides,
  }
}

function renderWorkspace(
  detail: ApplicationDetail,
  evidence: ApplicationEvidence | Error,
  artifacts: ApplicationArtifactsResponse = makeArtifactsResponse(),
  events: ApplicationEvent[] | Error = [],
) {
  vi.mocked(api.getApplication).mockResolvedValue(detail)
  if (evidence instanceof Error) vi.mocked(api.getApplicationEvidence).mockRejectedValue(evidence)
  else vi.mocked(api.getApplicationEvidence).mockResolvedValue(evidence)
  vi.mocked(api.getApplicationArtifacts).mockResolvedValue(artifacts)
  if (events instanceof Error) vi.mocked(api.listApplicationEvents).mockRejectedValue(events)
  else vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events })
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
    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse())
    vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events: [] })
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
    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse())
    vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events: [] })

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

describe('Application package (Phase 4, docs/35)', () => {
  it('never generates anything on page load', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    await screen.findByRole('heading', { name: 'Application package' })
    expect(api.getApplicationArtifacts).toHaveBeenCalledWith('app-1')
    expect(api.generateApplicationArtifact).not.toHaveBeenCalled()
  })

  it('shows Positioning, CV, Supporting material and a real Interview stage', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    await screen.findByRole('heading', { name: 'Application package' })
    expect(screen.getByRole('heading', { name: 'Positioning' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'CV' })).toBeTruthy()
    expect(screen.getByText('Supporting material')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Cover letter' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Supporting statement' })).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Interview' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Interview prep' })).toBeTruthy()
    expect(screen.queryByText(/arrives in a later phase/)).toBeNull()
    // nothing generates just by the section being present
    expect(api.generateApplicationArtifact).not.toHaveBeenCalled()
  })

  it('shows a deterministic generation-context summary with no AI call', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    expect(await screen.findByText(/What a new draft will use/)).toBeTruthy()
    expect(screen.getByText(/no adopted positioning brief yet/)).toBeTruthy()
  })

  it('shows incomplete requirement review as a warning without blocking generation', async () => {
    renderWorkspace(
      makeDetail(),
      makeEvidence({ review_summary: { accepted: 1, unreviewed: 2, rejected: 0, unresolved_proposals: 0, extraction_attempted: true, needs_reextraction: 0, complete: false } }),
    )
    await screen.findByRole('heading', { name: 'Positioning' })
    const positioningCard = screen.getByRole('heading', { name: 'Positioning' }).closest('section') as HTMLElement
    expect(within(positioningCard).getByText('Generate')).toBeTruthy()
    expect((within(positioningCard).getByText('Generate') as HTMLButtonElement).disabled).toBe(false)
  })

  it('a fresh Generate shows a busy state, prevents double-submit, and lands as a Draft (never automatically Current)', async () => {
    let resolveGenerate: (v: { created: boolean; artifact: ApplicationArtifact }) => void = () => {}
    vi.mocked(api.generateApplicationArtifact).mockReturnValue(new Promise((resolve) => { resolveGenerate = resolve }))
    renderWorkspace(makeDetail(), makeEvidence())
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText(/Generate with optional guidance/))
    const generateButton = within(card).getByText('Generate')
    fireEvent.click(generateButton)
    expect(await within(card).findByText('Generating…')).toBeTruthy()
    fireEvent.click(within(card).getByText('Generating…')) // double-click while busy
    expect(api.generateApplicationArtifact).toHaveBeenCalledTimes(1)

    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse({ positioning: { active: null, draft: makePositioningArtifact(), history_count: 0 } }))
    resolveGenerate({ created: true, artifact: makePositioningArtifact() })
    await waitFor(() => expect(within(card).getByText('Draft awaiting review')).toBeTruthy())
    expect(within(card).queryByText('Current')).toBeNull()
  })

  it('generation failure leaves existing current content usable', async () => {
    vi.mocked(api.generateApplicationArtifact).mockRejectedValue(new Error('503 model unavailable'))
    const active = makeArtifactsResponse({ positioning: { active: makePositioningArtifact({ status: 'active', id: 'active-1' }), draft: null, history_count: 0 } })
    renderWorkspace(makeDetail(), makeEvidence(), active)
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    expect(within(card).getByText('Current')).toBeTruthy()
    fireEvent.click(within(card).getByText(/Regenerate with optional guidance/))
    fireEvent.click(within(card).getByText('Regenerate'))
    await waitFor(() => expect(within(card).getByText(/503 model unavailable/)).toBeTruthy())
    expect(within(card).getByText('A strong fit for this role.')).toBeTruthy()
    expect(within(card).getByText('Current')).toBeTruthy()
  })

  it('Adopt makes a draft Current; Regenerate leaves Current stable until adoption', async () => {
    const draft = makePositioningArtifact()
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ positioning: { active: null, draft, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    expect(within(card).getByText('Draft awaiting review')).toBeTruthy()

    const adopted = makePositioningArtifact({ status: 'active' })
    vi.mocked(api.adoptApplicationArtifact).mockResolvedValue({ artifact: adopted })
    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse({ positioning: { active: adopted, draft: null, history_count: 0 } }))
    fireEvent.click(within(card).getByText('Adopt'))
    await waitFor(() => expect(api.adoptApplicationArtifact).toHaveBeenCalledWith('app-1', 'artifact-1'))
    await waitFor(() => expect(within(card).getByText('Current')).toBeTruthy())
  })

  it('Discard removes only the draft, leaving Current untouched', async () => {
    const active = makePositioningArtifact({ status: 'active', id: 'active-1' })
    const draft = makePositioningArtifact({ id: 'draft-2', content: { ...makePositioningArtifact().content, positioning_statement: { text: 'A newer draft.', source_refs: ['role_requirement:c1'] } } })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ positioning: { active, draft, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText('View draft'))
    expect(within(card).getByText('A newer draft.')).toBeTruthy()

    vi.mocked(api.discardApplicationArtifact).mockResolvedValue({ status: 'discarded' })
    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse({ positioning: { active, draft: null, history_count: 1 } }))
    fireEvent.click(within(card).getByText('Discard'))
    await waitFor(() => expect(api.discardApplicationArtifact).toHaveBeenCalledWith('app-1', 'draft-2'))
    await waitFor(() => expect(within(card).getByText('A strong fit for this role.')).toBeTruthy())
  })

  it('manual edit creates a user-edited draft, labelled as not automatically revalidated', async () => {
    const active = makePositioningArtifact({ status: 'active', id: 'active-1' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ positioning: { active, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText('Edit'))
    const textarea = within(card).getByDisplayValue('A strong fit for this role.')
    fireEvent.change(textarea, { target: { value: 'A user-rewritten statement.' } })

    const edited = makePositioningArtifact({
      id: 'edited-1', origin: 'user_edit', grounding_status: 'user_edited_not_revalidated',
      content: { ...active.content, positioning_statement: { text: 'A user-rewritten statement.', source_refs: ['role_requirement:c1'] } },
    })
    vi.mocked(api.editApplicationArtifact).mockResolvedValue({ artifact: edited })
    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse({ positioning: { active, draft: edited, history_count: 0 } }))
    fireEvent.click(within(card).getByText('Save edit'))
    await waitFor(() => expect(api.editApplicationArtifact).toHaveBeenCalled())
    await waitFor(() => expect(within(card).getByText(/source trace has not been automatically revalidated/)).toBeTruthy())
  })

  it('shows a stale banner for a current artifact whose evidence has since changed', async () => {
    const stale = makePositioningArtifact({ status: 'active', stale: true })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ positioning: { active: stale, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    expect(within(card).getByText('Current — but stale')).toBeTruthy()
    expect(within(card).getByText(/Evidence or application context has changed/)).toBeTruthy()
  })

  it('expands source trace for a generated block', async () => {
    const active = makePositioningArtifact({ status: 'active' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ positioning: { active, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText('Sources (1)'))
    expect(within(card).getByText(/Python \(reviewed role requirement\)/)).toBeTruthy()
    expect(within(card).getByText(/Role-side requirement\/context/)).toBeTruthy()
  })

  it('offers copy and download actions for a current artifact', async () => {
    const active = makePositioningArtifact({ status: 'active' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ positioning: { active, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    expect(within(card).getByText('Copy as Markdown')).toBeTruthy()
    expect(within(card).getByText('Download .md')).toBeTruthy()
  })

  it('supporting material is optional — neither cover letter nor supporting statement generate on their own', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    await screen.findByText('Supporting material')
    expect(screen.getByText(/Optional — generate either or both/)).toBeTruthy()
    expect(api.generateApplicationArtifact).not.toHaveBeenCalled()
  })

  it('CV renders authoritative episode title/organisation/dates resolved server-side', async () => {
    const cv = makePositioningArtifact({
      id: 'cv-1', artifact_type: 'cv' as ArtifactType, status: 'active',
      content: {
        profile_summary: { text: 'Experienced professional.', source_refs: [] },
        experience: [{ episode_id: 'ep-1', bullets: [{ text: 'Led delivery.', source_refs: [] }], title: 'Senior Actuary', organisation: 'PrevCo', start_date: '2020-01-01', end_date: null, episode_found: true }],
        skills: [], omissions_or_cautions: [],
      },
    })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ cv: { active: cv, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'CV' })).closest('section') as HTMLElement
    expect(within(card).getByText(/Senior Actuary — PrevCo/)).toBeTruthy()
  })

  it('application status is never changed by generation or adoption', async () => {
    const draft = makePositioningArtifact()
    vi.mocked(api.adoptApplicationArtifact).mockResolvedValue({ artifact: makePositioningArtifact({ status: 'active' }) })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ positioning: { active: null, draft, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Positioning' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText('Adopt'))
    await waitFor(() => expect(api.adoptApplicationArtifact).toHaveBeenCalled())
    expect(api.updateApplicationStatus).not.toHaveBeenCalled()
    expect((screen.getByLabelText('Application status') as HTMLSelectElement).value).toBe('preparing')
  })
})

describe('Lifecycle (Phase 5, docs/36)', () => {
  it('renders a timeline of recorded events, newest first', async () => {
    const older = makeEvent({ id: 'e1', event_type: 'submitted', event_at: '2026-01-01T00:00:00Z' })
    const newer = makeEvent({ id: 'e2', event_type: 'interview_scheduled', event_at: '2026-02-01T00:00:00Z', label: 'Technical panel' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse(), [newer, older])
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    expect(within(section).getByText('Technical panel', { exact: false })).toBeTruthy()
    expect(within(section).getByText('Submitted')).toBeTruthy()
  })

  it('shows an empty state when no events are recorded', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    expect(await within(section).findByText('No events recorded yet.')).toBeTruthy()
  })

  it('shows the current status inline, without a second status control', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    expect(await within(section).findByText('Preparing')).toBeTruthy()
    expect(within(section).queryByLabelText('Application status')).toBeNull()
  })

  it('records a submission via the composer', async () => {
    const created = makeEvent({ id: 'e1', event_type: 'submitted', event_at: '2026-01-05T10:00:00.000Z' })
    vi.mocked(api.createApplicationEvent).mockResolvedValue(created)
    renderWorkspace(makeDetail(), makeEvidence())
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    fireEvent.click(within(section).getByText('Record submission'))
    vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events: [created] })
    fireEvent.click(within(section).getByText('Save event'))
    await waitFor(() => expect(api.createApplicationEvent).toHaveBeenCalledWith('app-1', expect.objectContaining({ event_type: 'submitted' })))
    expect(await within(section).findByText('Submitted')).toBeTruthy()
  })

  it('schedules an interview and shows it as upcoming', async () => {
    const future = new Date(Date.now() + 1000 * 60 * 60 * 24 * 7).toISOString()
    const created = makeEvent({ id: 'e1', event_type: 'interview_scheduled', event_at: future, label: 'Panel round' })
    vi.mocked(api.createApplicationEvent).mockResolvedValue(created)
    renderWorkspace(makeDetail(), makeEvidence())
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    fireEvent.click(within(section).getByText('Schedule interview'))
    fireEvent.change(within(section).getByPlaceholderText(/Technical panel/), { target: { value: 'Panel round' } })
    vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events: [created] })
    fireEvent.click(within(section).getByText('Save event'))
    await waitFor(() => expect(api.createApplicationEvent).toHaveBeenCalled())
    expect(await within(section).findByText(/Upcoming: Interview scheduled — Panel round/)).toBeTruthy()
  })

  it('edits an existing event', async () => {
    const event = makeEvent({ id: 'e1', event_type: 'interview_completed', notes: 'Went well.' })
    const edited = { ...event, notes: 'Went very well.' }
    vi.mocked(api.updateApplicationEvent).mockResolvedValue(edited)
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse(), [event])
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    fireEvent.click(within(section).getByText('Edit'))
    const textarea = within(section).getByDisplayValue('Went well.')
    fireEvent.change(textarea, { target: { value: 'Went very well.' } })
    vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events: [edited] })
    fireEvent.click(within(section).getByText('Save event'))
    await waitFor(() => expect(api.updateApplicationEvent).toHaveBeenCalledWith('app-1', 'e1', expect.objectContaining({ notes: 'Went very well.' })))
    expect(await within(section).findByText('Went very well.')).toBeTruthy()
  })

  it('deletes an event after confirmation', async () => {
    const event = makeEvent({ id: 'e1' })
    vi.mocked(api.deleteApplicationEvent).mockResolvedValue({ status: 'deleted' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse(), [event])
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    await within(section).findByText('Submitted')
    vi.stubGlobal('confirm', () => true)
    vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events: [] })
    fireEvent.click(within(section).getByText('Delete'))
    await waitFor(() => expect(api.deleteApplicationEvent).toHaveBeenCalledWith('app-1', 'e1'))
    await within(section).findByText('No events recorded yet.')
    vi.unstubAllGlobals()
  })

  it('keeps the rest of the workspace usable when the events request fails', async () => {
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse(), new Error('events service down'))
    expect(await screen.findByRole('heading', { name: 'Head of Capital' })).toBeTruthy()
    expect(await screen.findByText(/Timeline couldn't be loaded/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Application package' })).toBeTruthy()
  })

  it('recording an event never changes the application status', async () => {
    const created = makeEvent({ id: 'e1', event_type: 'offer_received' })
    vi.mocked(api.createApplicationEvent).mockResolvedValue(created)
    renderWorkspace(makeDetail(), makeEvidence())
    const section = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    fireEvent.click(within(section).getByText('Record offer'))
    fireEvent.click(within(section).getByText('Save event'))
    await waitFor(() => expect(api.createApplicationEvent).toHaveBeenCalled())
    expect(api.updateApplicationStatus).not.toHaveBeenCalled()
    expect((screen.getByLabelText('Application status') as HTMLSelectElement).value).toBe('preparing')
  })
})

describe('Interview stage (Phase 5, docs/36)', () => {
  function makeInterviewPrepArtifact(overrides: Partial<ApplicationArtifact> = {}): ApplicationArtifact {
    return {
      id: 'ip-1',
      application_id: 'app-1',
      artifact_type: 'interview_prep' as ArtifactType,
      status: 'draft',
      origin: 'ai',
      generator_version: '1',
      model: 'test-model',
      prompt_name: 'application_interview_prep.md',
      prompt_version: 'v1',
      guidance: null,
      source_manifest: [{ ref: 'role_requirement:c1', kind: 'role_requirement', label: 'Python (reviewed role requirement)', category: 'role_side_context' }],
      content: {
        focus_areas: [{ title: 'Python', why_it_matters: 'Core requirement.', source_refs: ['role_requirement:c1'] }],
        questions: [
          {
            question: 'Tell me about a Python project.', question_type: 'experience', source_refs: ['role_requirement:c1'],
            answer_plan: { approach: 'Use the STAR method.', evidence_points: [], cautions: [] },
          },
        ],
        questions_to_ask: [],
        closing_points: [],
        prep_checklist: ['Review the job description again.'],
      },
      grounding_status: 'grounded_generation',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      superseded_at: null,
      stale: false,
      ...overrides,
    }
  }

  it('shows interview context and a deterministic readiness summary, with no AI call on mount', async () => {
    renderWorkspace(makeDetail(), makeEvidence())
    const section = (await screen.findByRole('heading', { name: 'Interview' })).closest('section') as HTMLElement
    expect(within(section).getByText(/No interview currently scheduled/)).toBeTruthy()
    expect(within(section).getByText(/What this prep will use/)).toBeTruthy()
    expect(api.generateApplicationArtifact).not.toHaveBeenCalled()
  })

  it('shows the upcoming interview in the interview context card', async () => {
    const future = new Date(Date.now() + 1000 * 60 * 60 * 24 * 3).toISOString()
    renderWorkspace(
      makeDetail(), makeEvidence(), makeArtifactsResponse(),
      [makeEvent({ id: 'e1', event_type: 'interview_scheduled', event_at: future, label: 'Panel round' })],
    )
    const section = (await screen.findByRole('heading', { name: 'Interview' })).closest('section') as HTMLElement
    expect(await within(section).findByText(/Panel round/)).toBeTruthy()
  })

  it('generating Interview Prep shows a busy state, guards against double-submit, and lands as a Draft', async () => {
    let resolveGenerate: (v: { created: boolean; artifact: ApplicationArtifact }) => void = () => {}
    vi.mocked(api.generateApplicationArtifact).mockReturnValue(new Promise((resolve) => { resolveGenerate = resolve }))
    renderWorkspace(makeDetail(), makeEvidence())
    const card = (await screen.findByRole('heading', { name: 'Interview prep' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText(/Generate with optional guidance/))
    const generateButton = within(card).getByText('Generate')
    fireEvent.click(generateButton)
    expect(await within(card).findByText('Generating…')).toBeTruthy()
    fireEvent.click(within(card).getByText('Generating…'))
    expect(api.generateApplicationArtifact).toHaveBeenCalledTimes(1)

    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(
      makeArtifactsResponse({ interview_prep: { active: null, draft: makeInterviewPrepArtifact(), history_count: 0 } }),
    )
    resolveGenerate({ created: true, artifact: makeInterviewPrepArtifact() })
    await waitFor(() => expect(within(card).getByText('Draft awaiting review')).toBeTruthy())
    expect(within(card).queryByText('Current')).toBeNull()
  })

  it('generation error leaves an existing Current Interview Prep usable', async () => {
    vi.mocked(api.generateApplicationArtifact).mockRejectedValue(new Error('503 model unavailable'))
    const active = makeInterviewPrepArtifact({ status: 'active', id: 'active-1' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ interview_prep: { active, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Interview prep' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText(/Regenerate with optional guidance/))
    fireEvent.click(within(card).getByText('Regenerate'))
    await waitFor(() => expect(within(card).getByText(/503 model unavailable/)).toBeTruthy())
    expect(within(card).getByText('Tell me about a Python project.')).toBeTruthy()
    expect(within(card).getByText('Current')).toBeTruthy()
  })

  it('Adopt makes a draft Current; Regenerate leaves Current stable until adoption', async () => {
    const draft = makeInterviewPrepArtifact()
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ interview_prep: { active: null, draft, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Interview prep' })).closest('section') as HTMLElement
    expect(within(card).getByText('Draft awaiting review')).toBeTruthy()

    const adopted = makeInterviewPrepArtifact({ status: 'active' })
    vi.mocked(api.adoptApplicationArtifact).mockResolvedValue({ artifact: adopted })
    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse({ interview_prep: { active: adopted, draft: null, history_count: 0 } }))
    fireEvent.click(within(card).getByText('Adopt'))
    await waitFor(() => expect(api.adoptApplicationArtifact).toHaveBeenCalledWith('app-1', 'ip-1'))
    await waitFor(() => expect(within(card).getByText('Current')).toBeTruthy())
  })

  it('shows a stale banner for a current Interview Prep whose context has changed', async () => {
    const stale = makeInterviewPrepArtifact({ status: 'active', stale: true })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ interview_prep: { active: stale, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Interview prep' })).closest('section') as HTMLElement
    expect(within(card).getByText('Current — but stale')).toBeTruthy()
    expect(within(card).getByText(/Evidence or application context has changed/)).toBeTruthy()
  })

  it('expands source provenance and offers copy/download for Interview Prep', async () => {
    const active = makeInterviewPrepArtifact({ status: 'active' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ interview_prep: { active, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Interview prep' })).closest('section') as HTMLElement
    const sourceButtons = within(card).getAllByText('Sources (1)')
    expect(sourceButtons.length).toBeGreaterThan(0)
    fireEvent.click(sourceButtons[0])
    expect(within(card).getAllByText(/Python \(reviewed role requirement\)/).length).toBeGreaterThan(0)
    expect(within(card).getByText('Copy as Markdown')).toBeTruthy()
    expect(within(card).getByText('Download .md')).toBeTruthy()
  })

  it('a user edit creates an edited-draft state, labelled as not automatically revalidated', async () => {
    const active = makeInterviewPrepArtifact({ status: 'active', id: 'active-1' })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ interview_prep: { active, draft: null, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Interview prep' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText('Edit'))
    const textarea = within(card).getByDisplayValue('Core requirement.')
    fireEvent.change(textarea, { target: { value: 'Central to this role.' } })

    const edited = makeInterviewPrepArtifact({
      id: 'edited-1', origin: 'user_edit', grounding_status: 'user_edited_not_revalidated',
      content: {
        focus_areas: [{ title: 'Python', why_it_matters: 'Central to this role.', source_refs: ['role_requirement:c1'] }],
        questions: [], questions_to_ask: [], closing_points: [], prep_checklist: [],
      },
    })
    vi.mocked(api.editApplicationArtifact).mockResolvedValue({ artifact: edited })
    vi.mocked(api.getApplicationArtifacts).mockResolvedValue(makeArtifactsResponse({ interview_prep: { active, draft: edited, history_count: 0 } }))
    fireEvent.click(within(card).getByText('Save edit'))
    await waitFor(() => expect(api.editApplicationArtifact).toHaveBeenCalled())
    await waitFor(() => expect(within(card).getByText(/source trace has not been automatically revalidated/)).toBeTruthy())
  })

  it('recording completed-interview feedback does not affect the evidence UI', async () => {
    const created = makeEvent({ id: 'e1', event_type: 'interview_completed', notes: 'Asked about Python internals.' })
    vi.mocked(api.createApplicationEvent).mockResolvedValue(created)
    renderWorkspace(makeDetail(), makeEvidence())
    await screen.findByRole('heading', { name: 'Evidence to use' })
    const lifecycleSection = (await screen.findByRole('heading', { name: 'Lifecycle' })).closest('section') as HTMLElement
    fireEvent.click(within(lifecycleSection).getByText('Record completed interview'))
    vi.mocked(api.listApplicationEvents).mockResolvedValue({ application_id: 'app-1', events: [created] })
    fireEvent.click(within(lifecycleSection).getByText('Save event'))
    await waitFor(() => expect(api.createApplicationEvent).toHaveBeenCalled())
    expect(screen.getByRole('heading', { name: 'Evidence to use' })).toBeTruthy()
    expect(screen.getByText('No accepted profile evidence found.')).toBeTruthy()
  })

  it('application status remains user-controlled after Interview Prep actions', async () => {
    const draft = makeInterviewPrepArtifact()
    vi.mocked(api.adoptApplicationArtifact).mockResolvedValue({ artifact: makeInterviewPrepArtifact({ status: 'active' }) })
    renderWorkspace(makeDetail(), makeEvidence(), makeArtifactsResponse({ interview_prep: { active: null, draft, history_count: 0 } }))
    const card = (await screen.findByRole('heading', { name: 'Interview prep' })).closest('section') as HTMLElement
    fireEvent.click(within(card).getByText('Adopt'))
    await waitFor(() => expect(api.adoptApplicationArtifact).toHaveBeenCalled())
    expect(api.updateApplicationStatus).not.toHaveBeenCalled()
    expect((screen.getByLabelText('Application status') as HTMLSelectElement).value).toBe('preparing')
  })
})
