import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Pathways from './Pathways'
import {
  api,
  type ArchetypeContextResponse,
  type PathwaysResult,
  type PersonalComparison,
  type ResolvedCompensation,
} from '../lib/api'

// The UI's job here is to keep four things visibly distinct: which basis a
// number has, whether a personal comparison is valid, what is structurally
// evidenced, and what the system deliberately does not estimate. These tests
// assert exactly those, not layout.

vi.mock('../lib/api', () => ({
  api: {
    listTargets: vi.fn(),
    getPathways: vi.fn(),
    getArchetypeContext: vi.fn(),
    generateArchetypeContext: vi.fn(),
  },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const marketEstimate: ResolvedCompensation = {
  basis: 'market_estimate',
  basis_label: 'Market estimate',
  currency: 'GBP',
  amount_min: 120000,
  amount_reference: 132000,
  amount_max: 145000,
  component: 'base',
  pay_period: 'annual',
  employment_basis: null,
  market: { id: 'm1', label: 'United Kingdom' },
  period: { start: '2000-01-01', end: '2026-09-16' },
  as_of: '2026-09-16',
  archetype: { id: 'a1', name: 'Head of Capital archetype' },
  evidence: { n_observations: 12, n_posting_stated: 12, n_survey_sources: 0 },
  reference_source: 'posting',
  evidence_quality: 'moderate',
  reason: 'No compensation is stated on this role; using the archetype benchmark.',
  trace: {},
}

const insufficient: ResolvedCompensation = {
  ...marketEstimate,
  basis: 'insufficient_evidence',
  basis_label: 'Insufficient compensation evidence',
  currency: null,
  amount_min: null,
  amount_reference: null,
  amount_max: null,
  market: null,
  archetype: null,
  evidence_quality: 'insufficient',
  reason: 'No accepted compensation evidence qualifies as a benchmark for this archetype yet.',
}

const comparable: PersonalComparison = {
  comparable: true,
  reason: null,
  baseline: {
    observation_id: 'o1',
    component: 'annual_base',
    amount: 105000,
    source_amount: 105000,
    currency: 'GBP',
    unit: 'annual',
    employment_basis: 'paye',
    evidence_status: 'current',
    evidence_period: 'from 2025-07-01',
    label: 'Current earnings',
    planning_equivalent: null,
  },
  uses_planning_equivalent: false,
  difference_min: 15000,
  difference_reference: 27000,
  difference_max: 40000,
  limitations: ['Base pay only — bonus, pension and benefits are not included on either side.'],
}

const notComparable: PersonalComparison = {
  comparable: false,
  reason: 'This opportunity is quoted in GBP; your accepted evidence is in EUR. Amounts are never converted.',
  baseline: null,
  uses_planning_equivalent: false,
  difference_min: null,
  difference_reference: null,
  difference_max: null,
}

function transition(overrides: Partial<PathwaysResult['direct_route']['transition']> = {}) {
  return {
    blocking_required_gaps: 1,
    blocking_required_gap_names: ['Capital management'],
    unverified_required_gaps: 0,
    unverified_required_gap_names: [],
    evidence_coverage: 0.5,
    evidenced_requirements: 1,
    requirements_total: 2,
    target_gaps_addressed: [],
    development_actions: {
      open: 0,
      done: 0,
      earliest_planned_start: null,
      estimated_effort_hours: null,
      actions_with_estimated_effort: 0,
    },
    not_estimated: [
      'No learning duration is estimated — this system has no evidence of how long a capability takes to acquire.',
      'No hiring or success probability is estimated.',
      'No universal learning-difficulty rating is applied to any capability.',
    ],
    ...overrides,
  }
}

function result(overrides: Partial<PathwaysResult> = {}): PathwaysResult {
  return {
    target: {
      id: 't1',
      title: 'Head of Capital',
      organisation: 'An insurer',
      instance_type: 'user_defined_target',
      archetype: { id: 'a1', name: 'Head of Capital archetype', status: 'active' },
    },
    market_context: {
      market_id: 'm1',
      market_label: 'United Kingdom',
      market_code: 'country-united-kingdom',
      currency: 'GBP',
      selected_by: 'target_archetype_benchmark',
    },
    available_market_contexts: [
      {
        market_id: 'm1',
        market_label: 'United Kingdom',
        market_code: 'country-united-kingdom',
        currency: 'GBP',
        accepted_observations: 12,
      },
    ],
    personal_earnings: {
      status: 'current',
      as_of: '2026-09-16',
      baselines: [
        {
          observation_id: 'o1',
          episode_id: null,
          source_kind: 'payslip',
          employment_basis: 'paye',
          employment_basis_equivalent: 'permanent',
          component: 'annual_base',
          amount: 105000,
          currency: 'GBP',
          unit: 'annual',
          quantity: null,
          effective_from: '2025-07-01',
          period_start: '2025-07-01',
          period_end: null,
          pay_date: null,
          evidence_period: 'from 2025-07-01',
          evidence_status: 'current',
          notes: null,
          uncertainty: null,
          planning_equivalent: null,
          label: 'Current earnings',
          other_evidence_in_group: [],
          group: { currency: 'GBP', employment_basis: 'paye' },
        },
      ],
      other_components: [],
      currencies: ['GBP'],
      planning_assumption: { contract_billable_days_per_year: null, note: null, updated_at: null },
      notes: [],
      fingerprint: 'fp',
    },
    direct_route: {
      kind: 'direct',
      role_instance_id: 't1',
      title: 'Head of Capital',
      organisation: 'An insurer',
      archetype: { id: 'a1', name: 'Head of Capital archetype' },
      state: 'blocking_gaps',
      state_reason: '1 required capability/capabilities have no supporting evidence.',
      fit: {
        evidenced_requirements: 1,
        requirements_total: 2,
        evidence_coverage: 0.5,
        blocking_required_gaps: ['Capital management'],
        unverified_required_gaps: [],
        review_complete: true,
        unreviewed_requirement_claims: 0,
        mapping_complete: true,
        unmapped_requirements: 0,
      },
      compensation: marketEstimate,
      personal_comparison: comparable,
      transition: transition(),
    },
    intermediate_archetypes: [
      {
        kind: 'intermediate_archetype',
        archetype_concept_id: 'a2',
        archetype_name: 'Capital Analyst archetype',
        seniority_band: 'mid',
        typical_market: null,
        state: 'useful_intermediate',
        state_reason: 'This archetype involves 1 of the target’s outstanding requirement(s).',
        supporting_posting_ids: ['p1', 'p2'],
        supporting_posting_count: 2,
        supporting_postings: [
          {
            id: 'p1',
            title: 'Capital Analyst at Alpha',
            organisation: 'Alpha',
            posting_date: '2025-02-01',
            career_track: null,
            assessment: 'potential_step',
            explanation: 'Evidence supports 1/2 requirements.',
            evidenced_requirements: 1,
            requirements_total: 2,
            evidence_coverage: 0.5,
            target_gaps_addressed: ['Capital management'],
            missing_required: [],
            unverified_required: [],
            pending_requirements: 0,
            similarity_to_target: 0.7,
            similarity_to_profile: 0.6,
          },
          {
            id: 'p2',
            title: 'Capital Analyst at Beta',
            organisation: 'Beta',
            posting_date: '2025-03-01',
            career_track: null,
            assessment: 'potential_step',
            explanation: 'Evidence supports 1/2 requirements.',
            evidenced_requirements: 1,
            requirements_total: 2,
            evidence_coverage: 0.5,
            target_gaps_addressed: ['Capital management'],
            missing_required: [],
            unverified_required: [],
            pending_requirements: 0,
            similarity_to_target: 0.65,
            similarity_to_profile: 0.55,
          },
        ],
        fit: {
          best_evidence_coverage: 0.5,
          blocking_required_gaps: [],
          unverified_required_gaps: [],
          unreviewed_requirement_claims: 0,
          review_complete: true,
        },
        target_gaps_addressed: ['Capital management'],
        compensation: { ...marketEstimate, amount_reference: 110000, amount_min: 100000, amount_max: 120000 },
        personal_comparison: comparable,
        transition: transition({ target_gaps_addressed: ['Capital management'] }),
        day_in_the_life_available: false,
        evidence_quality: 'moderate',
      },
    ],
    unclassified_supporting_postings: [],
    gap_value: [
      {
        concept_id: 'c1',
        canonical_name: 'Capital management',
        type_code: 'capability',
        evidence_status: 'not_found',
        target_relevance: {
          required_by_target: true,
          requirement_type: 'required',
          addresses_target_gaps: 1,
          current_evidence_status: 'not_found',
          intermediate_archetypes_involving_it: [
            { archetype_concept_id: 'a2', archetype_name: 'Capital Analyst archetype' },
          ],
        },
        market_option_value: {
          available: true,
          archetypes_unlocked: 2,
          archetypes_improved: 1,
          roles_unlocked: 3,
          roles_improved: 2,
          highest_qualifying_reference_compensation: 145000,
          currency: 'GBP',
          delta_vs_best_currently_reachable: 40000,
          n_compensation_observations: 14,
          rank: 1,
          as_of: '2026-09-16',
          scope_note: 'Base annual pay only — the one canonical component gap value is computed on.',
        },
        your_context: { ...comparable, difference_reference: 40000 },
        evidence_quality: 'moderate',
      },
    ],
    gates: {
      target_requirements_reviewed: true,
      target_mapping_complete: true,
      target_has_requirements: true,
      target_archetype_assigned: true,
      compensation_context_available: true,
      target_compensation_available: true,
      personal_earnings_available: true,
      candidate_review_complete: true,
    },
    incomplete: [],
    candidates_assessed: 12,
    distinct_concepts: 8,
    method: {
      route_depth: 'direct, plus one intermediate archetype',
      route_depth_limitation:
        'This version supports exactly two route shapes: you to the target directly, and you to the target via one intermediate archetype. Multi-step chains are not searched.',
      intermediate_basis: 'An intermediate archetype is a reviewed archetype that at least one posting belongs to.',
      ranking: 'Routes are ordered by how many of the target’s outstanding required gaps they address.',
      compensation: 'Every figure carries its own basis.',
      not_a_prediction: 'A role that involves a capability is an opportunity to develop it, not proof you will acquire it.',
    },
    metrics: {
      cache_hit: false,
      candidates: 12,
      archetypes_assessed: 1,
      distinct_concepts: 8,
      concepts_evaluated: 8,
      elapsed_ms: 42,
    },
    ...overrides,
  }
}

function renderPathways() {
  return render(
    <MemoryRouter initialEntries={['/pathways/t1']}>
      <Routes>
        <Route path="/pathways/:id" element={<Pathways />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Pathways', () => {
  it('opens on current earnings, the selected target, its archetype, market and direct fit', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    // Both the page header and the direct route's own comparison say
    // "Current earnings" — the opening state and the per-node economics.
    expect((await screen.findAllByText('Current earnings')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/£105,000/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Direct: Head of Capital/)).toBeTruthy()
    expect(screen.getAllByText(/Head of Capital archetype/).length).toBeGreaterThan(0)
    expect(screen.getByText('Automatic — United Kingdom (GBP)')).toBeTruthy()
    expect(screen.getByText('Required capabilities with no evidence')).toBeTruthy()
  })

  it('shows the direct route and one-step intermediate archetypes with their supporting postings', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    expect(await screen.findByText(/Direct: Head of Capital/)).toBeTruthy()
    expect(screen.getByText(/Via: Capital Analyst archetype/)).toBeTruthy()
    expect(screen.getByText('One-step intermediate archetypes (1)')).toBeTruthy()

    // The supporting postings are inspectable underneath the archetype.
    fireEvent.click(screen.getByText('Supporting postings (2)'))
    expect(screen.getByText('Capital Analyst at Alpha')).toBeTruthy()
    expect(screen.getByText('Capital Analyst at Beta')).toBeTruthy()
  })

  it('labels a market estimate as such and never as an advert salary', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    expect((await screen.findAllByText('Market estimate')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Advert salary')).toBeNull()
  })

  it('exposes Economics, Fit, Transition and Day in the life for each node', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    await screen.findByText(/Direct: Head of Capital/)
    for (const label of ['Economics', 'Fit', 'Transition', 'Day in the life']) {
      expect(screen.getAllByRole('tab', { name: label }).length).toBe(2) // direct + one intermediate
    }

    // Economics is the opening perspective and shows the personal difference.
    expect(screen.getAllByText('Potential base-pay difference').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/\+£15,000 to \+£40,000/).length).toBeGreaterThan(0)
  })

  it('shows structural fit and the blocking gap under Fit', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    await screen.findByText(/Direct: Head of Capital/)
    fireEvent.click(screen.getAllByRole('tab', { name: 'Fit' })[0])

    expect(screen.getByText(/1 of 2 reviewed requirements evidenced/)).toBeTruthy()
    expect(screen.getByText('Blocking gaps')).toBeTruthy()
    expect(screen.getAllByText('Capital management').length).toBeGreaterThan(0)
  })

  it('states what the transition view deliberately does not estimate', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    await screen.findByText(/Direct: Head of Capital/)
    fireEvent.click(screen.getAllByRole('tab', { name: 'Transition' })[0])
    fireEvent.click(screen.getAllByText('What this does not estimate')[0])

    expect(screen.getByText(/No learning duration is estimated/)).toBeTruthy()
    expect(screen.getByText(/No hiring or success probability is estimated/)).toBeTruthy()
  })

  it('generates archetype Day in the Life only on an explicit click', async () => {
    const context: ArchetypeContextResponse = {
      archetype_concept_id: 'a2',
      archetype_name: 'Capital Analyst archetype',
      enrichment: null,
      synthesis_note: 'Archetype context is a synthesis across the evidence available for this archetype.',
    }
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    vi.mocked(api.getArchetypeContext).mockResolvedValue(context)
    renderPathways()

    await screen.findByText(/Via: Capital Analyst archetype/)
    fireEvent.click(screen.getAllByRole('tab', { name: 'Day in the life' })[1])

    await screen.findByText('Generate archetype context')
    // Reading the tab must not have generated anything.
    expect(api.generateArchetypeContext).not.toHaveBeenCalled()
  })

  it('separates target relevance from market option value for each gap', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    expect(await screen.findByText('Target relevance')).toBeTruthy()
    expect(screen.getByText('Market option value')).toBeTruthy()
    expect(screen.getByText('Your context')).toBeTruthy()
    expect(screen.getByText(/2 archetypes unlocked, 1 improved/)).toBeTruthy()
    expect(screen.getByText(/Highest qualifying reference compensation: £145,000/)).toBeTruthy()
  })

  it('shows insufficient compensation as its own state, never as a zero', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const withoutCompensation = result()
    withoutCompensation.direct_route.compensation = insufficient
    withoutCompensation.direct_route.personal_comparison = {
      comparable: false,
      reason: 'There is no compensation figure for this opportunity to compare against.',
      baseline: null,
      uses_planning_equivalent: false,
      difference_min: null,
      difference_reference: null,
      difference_max: null,
    }
    vi.mocked(api.getPathways).mockResolvedValue(withoutCompensation)
    renderPathways()

    expect((await screen.findAllByText('Insufficient evidence')).length).toBeGreaterThan(0)
    expect(screen.getByText(/No accepted compensation evidence qualifies/)).toBeTruthy()
    expect(screen.queryByText('£0')).toBeNull()
    expect(screen.getByText(/no compensation figure for this opportunity/)).toBeTruthy()
  })

  it('reports an incomplete gate rather than a confident answer', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const gated = result({ incomplete: ['target_requirements_reviewed'] })
    gated.gates.target_requirements_reviewed = false
    gated.direct_route.state = 'review_incomplete'
    gated.direct_route.state_reason = '2 requirement claim(s) on this target are still unreviewed.'
    vi.mocked(api.getPathways).mockResolvedValue(gated)
    renderPathways()

    expect(await screen.findByText('Some inputs are incomplete')).toBeTruthy()
    expect(screen.getByText('target requirements reviewed')).toBeTruthy()
    expect(screen.getByText('Requirement review incomplete')).toBeTruthy()
  })

  it('explains that a comparison is not comparable rather than showing a difference', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const mismatched = result()
    mismatched.direct_route.personal_comparison = notComparable
    vi.mocked(api.getPathways).mockResolvedValue(mismatched)
    renderPathways()

    await screen.findByText(/Direct: Head of Capital/)
    expect(screen.getAllByText('Not comparable.').length).toBeGreaterThan(0)
    expect(screen.getByText(/Amounts are never converted/)).toBeTruthy()
  })

  it('surfaces useful postings that have no reviewed archetype instead of dropping them', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const withOrphans = result({
      intermediate_archetypes: [],
      unclassified_supporting_postings: [
        { id: 'p9', title: 'Unclassified capital role', organisation: null, target_gaps_addressed: ['Capital management'] },
      ],
    })
    vi.mocked(api.getPathways).mockResolvedValue(withOrphans)
    renderPathways()

    expect(await screen.findByText('Useful postings with no reviewed archetype (1)')).toBeTruthy()
    fireEvent.click(screen.getByText('Useful postings with no reviewed archetype (1)'))
    expect(screen.getByText('Unclassified capital role')).toBeTruthy()
  })

  it('states the one-intermediate-hop limitation in the product, not only in the code', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    fireEvent.click(await screen.findByText('Method and limitations'))
    expect(screen.getByText(/Multi-step chains are not searched/)).toBeTruthy()
    expect(screen.getByText(/not proof you will acquire it/)).toBeTruthy()
  })

  it('re-requests pathways when the market context is changed', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways()

    await screen.findByText(/Direct: Head of Capital/)
    fireEvent.change(screen.getByLabelText('Market and currency'), { target: { value: 'm1::GBP' } })

    await waitFor(() =>
      expect(api.getPathways).toHaveBeenLastCalledWith('t1', { market_id: 'm1', currency: 'GBP' }),
    )
  })
})
