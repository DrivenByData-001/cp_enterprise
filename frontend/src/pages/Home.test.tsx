import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Home from './Home'
import {
  api,
  type CareerDirectionArchetypeSummary,
  type CareerDirectionConstraints,
  type CareerDirectionDimension,
  type CareerDirectionTargetSummary,
  type Cockpit,
  type CockpitCurrentTargetState,
  type CockpitDirection,
  type ProgressDiff,
} from '../lib/api'

// Phase 9 (docs/40): the Career Cockpit is one composed GET — every test
// here mocks `api.getCockpit` alone (plus the explicit checkpoint POST for
// the checkpoint-recording tests), never the four independent calls the
// pre-Phase-9 Home page used to make.

vi.mock('../lib/api', () => ({
  api: {
    getCockpit: vi.fn(),
    recordProgressCheckpoint: vi.fn(),
  },
}))

const noDirection: CockpitDirection = { state: 'no_direction', direction: null, target: null }

type DirectionFields = {
  id: string
  name: string
  summary: string
  selected_at: string | null
  constraints: CareerDirectionConstraints
  top_dimensions: CareerDirectionDimension[]
  archetype: CareerDirectionArchetypeSummary | null
}

function withDirection(overrides: Partial<DirectionFields> = {}, target: CareerDirectionTargetSummary | null = null): CockpitDirection {
  const direction: DirectionFields = {
    id: 'dir-1', name: 'Technical actuarial leadership', summary: '', selected_at: '2026-01-01T00:00:00Z',
    constraints: { locations: [], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [] },
    top_dimensions: [], archetype: null,
    ...overrides,
  }
  return { state: target ? 'with_target' : 'no_target', direction, target } as CockpitDirection
}

function currentState(overrides: Partial<CockpitCurrentTargetState> = {}): CockpitCurrentTargetState {
  return {
    direction: { id: 'dir-1', name: 'Technical actuarial leadership' },
    target: { id: 'target-1', title: 'Head of Capital' },
    review: { target_review_complete: true, target_mapping_complete: true },
    counts: { requirements_total: 2, required_total: 2, evidenced: 1, partial: 0, user_asserted: 0, not_found: 1, blocking_required: 1, unverified_required: 0 },
    requirements: [
      { concept_id: 'c1', canonical_name: 'Reserving', requirement_type: 'required', status: 'evidenced' },
      { concept_id: 'c2', canonical_name: 'Capital management', requirement_type: 'required', status: 'not_found' },
    ],
    development_actions: { open: 0, done: 0 },
    ...overrides,
  }
}

function emptyDiff(): ProgressDiff {
  return {
    evidence_strengthened: [], assertion_added: [], evidence_weakened: [],
    target_definition_changed: { requirements_added: [], requirements_removed: [], requirement_type_changed: [] },
    unchanged_count: 2,
  }
}

function baseCockpit(overrides: Partial<Cockpit> = {}): Cockpit {
  return {
    direction: noDirection,
    target_progress: { state: 'no_direction', direction: null, current: null, checkpoint: null, comparison_state: null, diff: null, history: [] },
    opportunities: { state: 'available', items: [] },
    applications: { state: 'available', active_count: 0, by_status: {}, active_items: [], next_interview: null, recent_outcomes: [] },
    learning: { state: 'available', items: [] },
    market_context: { state: 'no_direction' },
    next_actions: [{ code: 'select_direction', title: 'Define or select a Career Direction', reason: 'No Career Direction is currently selected.', href: '/future' }],
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(api.recordProgressCheckpoint).mockResolvedValue({
    id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline',
    label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z',
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderHome(cockpit: Cockpit) {
  vi.mocked(api.getCockpit).mockResolvedValue(cockpit)
  const view = render(<MemoryRouter><Home /></MemoryRouter>)
  await waitFor(() => expect(api.getCockpit).toHaveBeenCalled())
  return view
}

// The Direction section's own empty-state title and the injected "select a
// direction" next action legitimately repeat the same wording — most
// section-specific assertions below scope their queries to one section via
// this helper rather than a page-wide getByText/findByText.
async function findSection(headingText: string): Promise<HTMLElement> {
  return (await screen.findByRole('heading', { name: headingText })).closest('section') as HTMLElement
}

describe('Home — one composed request', () => {
  it('loads the whole page from a single GET /api/cockpit call', async () => {
    await renderHome(baseCockpit())
    await findSection('Current direction')
    expect(api.getCockpit).toHaveBeenCalledTimes(1)
  })

  it('never calls the checkpoint POST merely by loading', async () => {
    await renderHome(baseCockpit({ direction: withDirection({}, { id: 'target-1', title: 'Head of Capital', organisation: null }) }))
    await screen.findByText('Technical actuarial leadership')
    expect(api.recordProgressCheckpoint).not.toHaveBeenCalled()
  })
})

describe('Home — Direction section (build §17/§29)', () => {
  it('shows an honest empty state and links to /future when none is selected', async () => {
    await renderHome(baseCockpit())
    const section = await findSection('Current direction')
    expect(within(section).getByText('Define or select a Career Direction')).toBeTruthy()
    expect(within(section).getByText('Explore my future').closest('a')?.getAttribute('href')).toBe('/future')
  })

  it('shows a Direction without a Target and prompts to link one', async () => {
    await renderHome(baseCockpit({ direction: withDirection() }))
    expect(await screen.findByText('Technical actuarial leadership')).toBeTruthy()
    expect(screen.getByText('No concrete Target linked yet.')).toBeTruthy()
  })

  it('shows the Direction and linked Target with no fabricated score', async () => {
    await renderHome(baseCockpit({ direction: withDirection({}, { id: 'target-1', title: 'Head of Capital', organisation: null }) }))
    expect(await screen.findByText('Technical actuarial leadership')).toBeTruthy()
    expect(screen.getByText('Target: Head of Capital')).toBeTruthy()
    expect(screen.queryByText(/%|score|fit/i)).toBeNull()
  })

  it('section failure isolation: a Direction failure never blanks the rest of the page', async () => {
    await renderHome(baseCockpit({ direction: { state: 'unavailable', reason: 'career-directions down' } }))
    expect(await screen.findByText(/Could not load your career direction/)).toBeTruthy()
    expect(screen.getByText('Applications in motion')).toBeTruthy()
  })
})

describe('Home — Target progress (build §18/§29/§30/§31)', () => {
  const withTarget = withDirection({}, { id: 'target-1', title: 'Head of Capital', organisation: null })

  it('shows current evidence counts distinctly (evidenced/partial/asserted/not_found)', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: { state: 'no_checkpoint', direction: { id: 'dir-1', name: 'X' }, current: currentState(), checkpoint: null, comparison_state: null, diff: null, history: [] },
    }))
    const section = await findSection('Progress toward Target')
    expect(within(section).getAllByText('1')).toHaveLength(2) // evidenced=1 and not_found=1
    expect(within(section).getByText('Evidenced')).toBeTruthy()
    expect(within(section).getByText('Not found')).toBeTruthy()
  })

  it('with no comparable checkpoint, offers "Record progress baseline" with a non-evidence explanation', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: { state: 'no_checkpoint', direction: { id: 'dir-1', name: 'X' }, current: currentState(), checkpoint: null, comparison_state: null, diff: null, history: [] },
    }))
    expect(await screen.findByText('Record progress baseline')).toBeTruthy()
    expect(screen.getByText(/does not create evidence or change your profile/)).toBeTruthy()
  })

  it('recording a baseline is POST-only on click, never automatic on mount', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: { state: 'no_checkpoint', direction: { id: 'dir-1', name: 'X' }, current: currentState(), checkpoint: null, comparison_state: null, diff: null, history: [] },
    }))
    await screen.findByText('Record progress baseline')
    expect(api.recordProgressCheckpoint).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Record progress baseline'))
    await waitFor(() => expect(api.recordProgressCheckpoint).toHaveBeenCalledTimes(1))
  })

  it('refreshes the Cockpit after recording a baseline', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: { state: 'no_checkpoint', direction: { id: 'dir-1', name: 'X' }, current: currentState(), checkpoint: null, comparison_state: null, diff: null, history: [] },
    }))
    await screen.findByText('Record progress baseline')
    vi.mocked(api.getCockpit).mockResolvedValue(baseCockpit({
      direction: withTarget,
      target_progress: {
        state: 'available', direction: { id: 'dir-1', name: 'X' }, current: currentState(),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z' },
        comparison_state: 'normal', diff: emptyDiff(), history: [],
      },
    }))
    fireEvent.click(screen.getByText('Record progress baseline'))
    await waitFor(() => expect(api.getCockpit).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('Record new checkpoint')).toBeTruthy()
  })

  it('shows "Changes since {date}" with strengthened evidence when a comparable checkpoint exists', async () => {
    const diff = { ...emptyDiff(), evidence_strengthened: [{ concept_id: 'c2', canonical_name: 'Capital management', requirement_type: 'required', previous_status: 'not_found' as const, current_status: 'evidenced' as const }] }
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: {
        state: 'available', direction: { id: 'dir-1', name: 'X' }, current: currentState({ counts: { requirements_total: 2, required_total: 2, evidenced: 2, partial: 0, user_asserted: 0, not_found: 0, blocking_required: 0, unverified_required: 0 } }),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-02-01T00:00:00Z' },
        comparison_state: 'normal', diff, history: [],
      },
    }))
    expect(await screen.findByText(/Changes since/)).toBeTruthy()
    expect(screen.getByText(/1 Target requirement.*stronger evidence/)).toBeTruthy()
    expect(screen.getByText(/Capital management.*Not found.*Evidenced/)).toBeTruthy()
  })

  it('never hides weakened evidence', async () => {
    const diff = { ...emptyDiff(), evidence_weakened: [{ concept_id: 'c1', canonical_name: 'Reserving', requirement_type: 'required', previous_status: 'evidenced' as const, current_status: 'not_found' as const }] }
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: {
        state: 'available', direction: { id: 'dir-1', name: 'X' }, current: currentState(),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z' },
        comparison_state: 'normal', diff, history: [],
      },
    }))
    expect(await screen.findByText(/weakened or no longer supported/)).toBeTruthy()
  })

  it('suppresses evidence-diff language and shows a limited-comparison note while review/mapping is incomplete', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: {
        state: 'available',
        direction: { id: 'dir-1', name: 'X' },
        current: currentState({ review: { target_review_complete: true, target_mapping_complete: false } }),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z' },
        comparison_state: 'limited',
        diff: null,
        history: [],
      },
    }))
    const section = await findSection('Progress toward Target')
    // Current structural counts remain visible (evidenced=1, not_found=1).
    expect(within(section).getAllByText('1')).toHaveLength(2)
    // "Needs attention" messaging stays visible.
    expect(within(section).getByText(/incomplete/)).toBeTruthy()
    expect(within(section).getByText(/Progress comparison is limited/)).toBeTruthy()
    // No evidence-strengthened/weakened language, and no diff heading.
    expect(within(section).queryByText(/stronger evidence/)).toBeNull()
    expect(within(section).queryByText(/weakened or no longer supported/)).toBeNull()
    expect(within(section).queryByText(/Changes since/)).toBeNull()
    // Recording a new checkpoint is still offered.
    expect(within(section).getByText('Record new checkpoint')).toBeTruthy()
  })

  it('shows Target-definition changes separately from evidence progress', async () => {
    const diff = { ...emptyDiff(), target_definition_changed: { requirements_added: [{ concept_id: 'c3', canonical_name: 'New skill', requirement_type: 'required' }], requirements_removed: [], requirement_type_changed: [] } }
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: {
        state: 'available', direction: { id: 'dir-1', name: 'X' }, current: currentState(),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z' },
        comparison_state: 'normal', diff, history: [],
      },
    }))
    expect(await screen.findByText('The Target definition changed since your last checkpoint.')).toBeTruthy()
    expect(screen.getByText(/Added: New skill/)).toBeTruthy()
  })

  it('a new Target with no comparable checkpoint shows "no checkpoint", not a stale comparison', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: { state: 'no_checkpoint', direction: { id: 'dir-1', name: 'X' }, current: currentState(), checkpoint: null, comparison_state: null, diff: null, history: [] },
    }))
    expect(await screen.findByText('Record progress baseline')).toBeTruthy()
    expect(screen.queryByText(/Changes since/)).toBeNull()
  })

  it('optional label input is passed through to the checkpoint POST', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: {
        state: 'available', direction: { id: 'dir-1', name: 'X' }, current: currentState(),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z' },
        comparison_state: 'normal', diff: emptyDiff(), history: [],
      },
    }))
    const input = await screen.findByLabelText('Optional label')
    fireEvent.change(input, { target: { value: 'After Q3 applications' } })
    fireEvent.click(screen.getByText('Record new checkpoint'))
    await waitFor(() => expect(api.recordProgressCheckpoint).toHaveBeenCalledWith('After Q3 applications'))
  })

  it('shows bounded checkpoint history on request, never by default', async () => {
    await renderHome(baseCockpit({
      direction: withTarget,
      target_progress: {
        state: 'available', direction: { id: 'dir-1', name: 'X' }, current: currentState(),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z' },
        comparison_state: 'normal', diff: emptyDiff(),
        history: [{ id: 'cp-1', checkpoint_type: 'baseline', label: 'First look', created_at: '2026-01-01T00:00:00Z', target_role_instance_id: 'target-1', target_title: 'Head of Capital', counts: { evidenced: 1, requirements_total: 2 } }],
      },
    }))
    await screen.findByText('Progress toward Target')
    expect(screen.queryByText(/First look/)).toBeNull()
    fireEvent.click(screen.getByText('Show checkpoint history'))
    expect(await screen.findByText(/First look/)).toBeTruthy()
  })
})

describe('Home — Opportunities (build §19/§20/§29)', () => {
  it('shows an honest empty state and points to Add a posting when there are none', async () => {
    await renderHome(baseCockpit())
    expect(await screen.findByText('No current roles captured yet.')).toBeTruthy()
    expect(screen.getByText('Add posting').closest('a')?.getAttribute('href')).toBe('/import')
  })

  it('shows recent opportunities with relationship labels, no desirability ranking', async () => {
    await renderHome(baseCockpit({
      opportunities: {
        state: 'available',
        items: [{
          role_instance_id: 'r1', title: 'Head of Reserving', organisation: 'Acme', location: 'London', posting_date: '2026-01-05',
          relationship: { state: 'potential_step', label: 'Potential stepping stone', reason: 'Evidence supports 1/2 requirements.' },
          target_gaps_involved: ['Capital management'], review_caveat: null, has_application: false,
        }],
      },
    }))
    expect(await screen.findByText('Head of Reserving')).toBeTruthy()
    expect(screen.getByText(/Potential stepping stone/)).toBeTruthy()
  })

  it('shows no fabricated relationship when there is no Target', async () => {
    await renderHome(baseCockpit({
      opportunities: {
        state: 'available',
        items: [{ role_instance_id: 'r1', title: 'Head of Reserving', organisation: null, location: null, posting_date: null, relationship: null, target_gaps_involved: [], review_caveat: null, has_application: false }],
      },
    }))
    expect(await screen.findByText('Head of Reserving')).toBeTruthy()
    expect(screen.queryByText('Potential stepping stone')).toBeNull()
  })
})

describe('Home — Applications in motion (build §21/§22/§29)', () => {
  it('shows an honest empty state pointing to Opportunities', async () => {
    await renderHome(baseCockpit())
    expect(await screen.findByText('No applications yet')).toBeTruthy()
  })

  it('shows active applications, the next interview, and recent outcomes', async () => {
    await renderHome(baseCockpit({
      applications: {
        state: 'available', active_count: 1, by_status: { preparing: 1 },
        active_items: [{ id: 'app-1', role_instance_id: 'r1', status: 'preparing', role: { title: 'Head of Capital', organisation: 'Acme' }, latest_event: null, next_interview: null }],
        next_interview: { application_id: 'app-1', role_title: 'Head of Capital', organisation: 'Acme', event_at: '2026-03-01T10:00:00Z', label: null },
        recent_outcomes: [{ id: 'e1', event_type: 'rejected', event_at: '2026-02-01T00:00:00Z', application_id: 'app-2', role: { title: 'Risk Lead', organisation: null }, has_notes: true }],
      },
    }))
    const section = await findSection('Applications in motion')
    expect(within(section).getAllByText('Head of Capital').length).toBeGreaterThan(0)
    expect(within(section).getByText(/Next interview/)).toBeTruthy()
    expect(within(section).getByText(/rejected/)).toBeTruthy()
  })

  it('never shows a success-rate or conversion statistic', async () => {
    await renderHome(baseCockpit({
      applications: { state: 'available', active_count: 3, by_status: { preparing: 3 }, active_items: [], next_interview: null, recent_outcomes: [] },
    }))
    await screen.findByText('Applications in motion')
    expect(screen.queryByText(/success rate|conversion/i)).toBeNull()
  })
})

describe('Home — Learning & evidence (build §23/§29)', () => {
  it('shows an honest empty state', async () => {
    await renderHome(baseCockpit())
    expect(await screen.findByText('Application reflections and evidence examples will appear here when you record them.')).toBeTruthy()
  })

  it('shows learning items with queue status', async () => {
    await renderHome(baseCockpit({
      learning: {
        state: 'available',
        items: [{
          source_type: 'note', source_id: 'note-1', application_id: 'app-1', role: { title: 'Head of Capital', organisation: 'Acme' },
          date: '2026-01-10T00:00:00Z', text_preview: 'I led the reserving process.', concept_id: 'c2', concept_canonical_name: 'Capital management',
          note_type: 'evidence_example', event_type: null, queue_status: 'queued_pending', is_current_target_requirement: true, current_target_evidence_status: 'not_found',
        }],
      },
    }))
    expect(await screen.findByText('I led the reserving process.')).toBeTruthy()
    expect(screen.getByText('Queued for Profile360 review')).toBeTruthy()
  })
})

describe('Home — Market context (build §25/§29)', () => {
  it('reuses Phase 8 semantics and shows representativeness honestly', async () => {
    await renderHome(baseCockpit({
      market_context: {
        state: 'available', archetype_concept_id: 'a1', canonical_name: 'Reserving Actuary', status: 'active',
        seniority_band: null, typical_market: null, assigned_posting_count: 12, reviewed_requirement_posting_count: 8,
        known_posting_date_count: 12, unknown_posting_date_count: 0, latest_known_posting_date: '2026-02-01',
        distinct_country_count: 2, countries: ['United Kingdom', 'Ireland'], demand_derivation_available: true,
        compensation_benchmark_available: true, economics_freshness: { state: 'fresh', fresh: true, reason: null },
        evidence_depth: { state: 'supported', reason: '12 supporting posting(s) in this scope.' },
        representativeness: { known: false, reason: 'This is a user-collected corpus with no known sampling frame.' },
      },
    }))
    expect(await screen.findByText('Reserving Actuary')).toBeTruthy()
    expect(screen.getByText(/no known sampling frame/)).toBeTruthy()
  })
})

describe('Home — Useful next actions (build §26/§27/§29)', () => {
  it('shows deterministic, bounded next actions — never an AI verdict or score', async () => {
    await renderHome(baseCockpit({
      next_actions: [
        { code: 'select_direction', title: 'Define or select a Career Direction', reason: 'No Career Direction is currently selected.', href: '/future' },
      ],
    }))
    const section = await findSection('Useful next actions')
    expect(within(section).getByText('Define or select a Career Direction').closest('a')?.getAttribute('href')).toBe('/future')
    expect(screen.queryByText(/best next move/i)).toBeNull()
  })
})

describe('Home — no overall score anywhere', () => {
  it('never renders a career score, readiness percentage, or health metric', async () => {
    await renderHome(baseCockpit({
      direction: withDirection({}, { id: 'target-1', title: 'Head of Capital', organisation: null }),
      target_progress: {
        state: 'available', direction: { id: 'dir-1', name: 'X' }, current: currentState(),
        checkpoint: { id: 'cp-1', career_direction_id: 'dir-1', target_role_instance_id: 'target-1', checkpoint_type: 'baseline', label: null, state_schema_version: 1, state: currentState(), source_revision: {}, created_at: '2026-01-01T00:00:00Z' },
        comparison_state: 'normal', diff: emptyDiff(), history: [],
      },
    }))
    await screen.findByText('Progress toward Target')
    expect(screen.queryByText(/career health|readiness|overall score|\d+%/i)).toBeNull()
  })
})

describe('Home — section failure isolation (build §37)', () => {
  it('an unavailable section shows its own message without blanking the rest of the page', async () => {
    await renderHome(baseCockpit({
      market_context: { state: 'unavailable', reason: 'archetype lookup failed' },
    }))
    expect(await screen.findByText(/Could not load market context/)).toBeTruthy()
    expect(screen.getByText('Applications in motion')).toBeTruthy()
    expect(screen.getByText('Opportunities around this direction')).toBeTruthy()
  })

  it('a whole-page load failure shows a clear error, not a blank page', async () => {
    vi.mocked(api.getCockpit).mockRejectedValue(new Error('cockpit down'))
    render(<MemoryRouter><Home /></MemoryRouter>)
    expect(await screen.findByText(/Could not load your Career Cockpit/)).toBeTruthy()
  })
})
