import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import Pathways from './Pathways'
import {
  api,
  type ArchetypeContextResponse,
  type ArchetypeEvidence,
  type OpportunityAlignment,
  type PathwaysResult,
  type PersonalComparison,
  type ResolvedCompensation,
  type Role,
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
    getPlanningAssumptions: vi.fn(),
    savePlanningAssumptions: vi.fn(),
    rebuildEconomics: vi.fn(),
    getCareerAlignment: vi.fn(),
  },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const marketEstimate: ResolvedCompensation = {
  basis: 'market_estimate',
  basis_label: 'Market estimate',
  component_label: 'base salary',
  supplementary: [],
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
          episode_title: null,
          episode_organisation: null,
          episode_end_date: null,
          episode_status: null,
          evidence_status_reason: null,
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
      market_evidence: null,
      state: 'blocking_gaps',
      state_reason: '1 required capability/capabilities have no supporting evidence.',
      fit: {
        evidenced_requirements: 1,
        requirements_total: 2,
        evidence_coverage: 0.5,
        blocking_required_gaps: ['Capital management'],
        unverified_required_gaps: [],
        review_complete: true,
        review_blockers: [],
        unreviewed_requirement_claims: 0,
        unresolved_vocabulary_proposals: 0,
        concepts_needing_reextraction: 0,
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
        market_evidence: null,
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
            review_complete: true,
            review_blockers: [],
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
            review_complete: true,
            review_blockers: [],
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
          review_blockers: [],
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
      derived_economics_fresh: true,
    },
    economics_freshness: {
      state: 'fresh',
      fresh: true,
      stale_inputs: [],
      reason: null,
      last_rebuilt_at: '2026-09-16T08:00:00Z',
      engine_version: 'economics-engine-v1',
    },
    representativeness: { known: false, reason: 'No known sampling frame.' },
    review_blockers: { target: [], supporting_candidates: [] },
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

function renderPathways(initialPath = '/pathways/t1') {
  // The planning-assumption editor loads on mount; without a resolved value
  // its promise chain throws and takes the whole page down.
  if (vi.mocked(api.getPlanningAssumptions).mock.results.length === 0) {
    vi.mocked(api.getPlanningAssumptions).mockResolvedValue({
      contract_billable_days_per_year: null,
      note: null,
      updated_at: null,
    })
  }
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/pathways" element={<Pathways />} />
        <Route path="/pathways/:id" element={<Pathways />} />
      </Routes>
    </MemoryRouter>,
  )
}

function testTarget(overrides: Partial<Role> = {}): Role {
  return {
    id: 't1', node_type: 'target_real', title: 'Head of Capital', organisation: 'An insurer', location: null,
    country: null, remote_type: null, employment_type: null, posting_date: null, captured_at: null,
    career_track: null, seniority_level: null, salary_min: null, salary_max: null, currency: null, summary: null,
    description: null, requirements: null, responsibilities: null, key_skills_summary: null, top_adjacent_roles: null,
    extraction_status: null, extraction_notes: null, similarity: null, url: null,
    ...overrides,
  }
}

const ALIGNMENT_METHOD = {
  what_this_is: 'test', not_a_recommendation: 'test', involvement_not_acquisition: 'test',
  relationship_caveat: 'Independent facts, not verdicts.', semantic_similarity: 'test', compensation: 'test',
}

function opportunityAlignment(overrides: Partial<OpportunityAlignment> = {}): OpportunityAlignment {
  return {
    state: 'target_available',
    direction: null,
    target: { id: 't1', title: 'Head of Capital', organisation: 'An insurer', archetype_concept_id: null },
    opportunity: { id: 'opp-1', title: 'Capital Analyst at Alpha', organisation: 'Alpha', archetype_concept_id: null },
    relationship: { state: 'potential_step', label: 'Potential stepping stone', reason: 'Evidence supports 1/2 requirements.' },
    you_to_opportunity: {
      counts: { evidenced: 1, partial: 0, user_asserted: 0, not_found: 0 }, requirements_reviewed: 1,
      legacy_requirement_count: 0, review_summary: { accepted: 1, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: true, needs_reextraction: 0, complete: true },
      review_blockers: [], blocking_gaps: [], unverified_required: [], embedding_similarity: null,
    },
    opportunity_to_target: {
      target_gaps_involved: [{ concept_id: 'g1', canonical_name: 'Capital management', target_requirement_type: 'required', opportunity_requirement_type: 'preferred', person_evidence_status: 'not_found', target_requirement_source: 'claim', opportunity_requirement_source: 'claim' }],
      target_gaps_not_touched: [], additional_opportunity_demands: [], legacy_requirements_involved: 0,
      is_potential_step: true, candidate_required_gaps: 0, candidate_missing_required: [], candidate_unverified_required: [],
      target_required_evidence_gaps: 1, candidate_review_complete: true, candidate_review_blockers: [],
      target_review_complete: true, target_review_blockers: [], target_mapping_complete: true, target_mapping_unresolved: 0,
    },
    direction_constraints: [],
    direction_dimensions: [],
    archetype_relationship: { opportunity_archetype: null, target_archetype: { id: 'a1', canonical_name: 'Head of Capital archetype', status: 'active' }, direction_archetype: null, same_as_target_archetype: false, same_as_direction_archetype: false, note: 'No reviewed archetype is assigned to this posting.' },
    economics: {
      opportunity: insufficient, vs_personal_earnings: { comparable: false, reason: 'no evidence', baseline: null, uses_planning_equivalent: false, difference_min: null, difference_reference: null, difference_max: null },
      target: marketEstimate,
    },
    review: { opportunity: { complete: true, blockers: [], legacy_requirement_count: 0 }, target: { complete: true, blockers: [], mapping_complete: true, mapping_unresolved: 0 } },
    semantic_similarity: { to_target: null, to_profile: null },
    method: ALIGNMENT_METHOD,
    ...overrides,
  }
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

  it('shows market evidence for the direct route and each intermediate node without a route-confidence score (docs/39 build §24)', async () => {
    const evidence: ArchetypeEvidence = {
      archetype_concept_id: 'a2',
      canonical_name: 'Capital Analyst archetype',
      status: 'active',
      seniority_band: 'mid',
      typical_market: null,
      assigned_posting_count: 2,
      reviewed_requirement_posting_count: 1,
      known_posting_date_count: 2,
      unknown_posting_date_count: 0,
      latest_known_posting_date: '2025-02-01',
      distinct_country_count: 1,
      countries: ['United Kingdom'],
      demand_derivation_available: false,
      compensation_benchmark_available: true,
      economics_freshness: { state: 'fresh', fresh: true, reason: null },
      evidence_depth: { state: 'thin', reason: 'Only 2 supporting posting(s) in this scope.' },
    }
    const withEvidence = result()
    withEvidence.direct_route.market_evidence = null
    withEvidence.intermediate_archetypes[0].market_evidence = evidence
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(withEvidence)
    renderPathways()

    await screen.findByText(/Direct: Head of Capital/)
    fireEvent.click(screen.getAllByRole('tab', { name: 'Fit' })[1])

    expect(screen.getByText('Market evidence')).toBeTruthy()
    expect(screen.getByText(/Thin evidence/i)).toBeTruthy()
    // No numeric route-confidence score anywhere on the node.
    expect(screen.queryByText(/route confidence/i)).toBeNull()
    expect(screen.queryByText(/%\s*confidence/i)).toBeNull()
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

describe('Pathways — review findings', () => {
  it('names every part of the review gate, not just unreviewed claims', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const gated = result({ incomplete: ['target_requirements_reviewed'] })
    gated.gates.target_requirements_reviewed = false
    gated.direct_route.state = 'review_incomplete'
    gated.direct_route.state_reason =
      'Requirement review on this target is not complete (1 vocabulary proposals still unresolved).'
    gated.direct_route.fit.review_complete = false
    gated.direct_route.fit.unreviewed_requirement_claims = 0
    gated.direct_route.fit.unresolved_vocabulary_proposals = 1
    gated.direct_route.fit.review_blockers = [
      { kind: 'unresolved_proposals', label: 'vocabulary proposals still unresolved', count: 1 },
    ]
    vi.mocked(api.getPathways).mockResolvedValue(gated)
    renderPathways()

    await screen.findByText(/Direct: Head of Capital/)
    expect(screen.getByText('Requirement review incomplete')).toBeTruthy()
    // Zero unreviewed claims, yet review is not complete — the whole point.
    expect(screen.getByText(/1 vocabulary proposals still unresolved/)).toBeTruthy()

    fireEvent.click(screen.getAllByRole('tab', { name: 'Fit' })[0])
    expect(screen.getByText(/Requirement review: 1 vocabulary proposals still unresolved/)).toBeTruthy()
  })

  it('warns that derived economics are out of date rather than showing them as current', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const stale = result({ incomplete: ['derived_economics_fresh'] })
    stale.gates.derived_economics_fresh = false
    stale.economics_freshness = {
      state: 'stale',
      fresh: false,
      stale_inputs: ['economics'],
      reason:
        'Derived economics were last rebuilt on 2026-09-10, and since then compensation evidence or market ' +
        'definitions have changed. Figures derived from them are withheld rather than shown as current.',
      last_rebuilt_at: '2026-09-10T00:00:00Z',
      engine_version: 'economics-engine-v1',
    }
    vi.mocked(api.getPathways).mockResolvedValue(stale)
    renderPathways()

    expect(await screen.findByText('Market benchmarks are out of date')).toBeTruthy()
    expect(screen.getByText(/withheld rather than shown as current/)).toBeTruthy()
    expect(screen.getByText('Rebuild economics')).toBeTruthy()
  })

  it('rebuilds economics and reloads on request', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const stale = result()
    stale.gates.derived_economics_fresh = false
    stale.economics_freshness = {
      state: 'never_rebuilt',
      fresh: false,
      stale_inputs: [],
      reason: 'The derived economics tables have never been rebuilt.',
      last_rebuilt_at: null,
      engine_version: null,
    }
    vi.mocked(api.getPathways).mockResolvedValue(stale)
    vi.mocked(api.rebuildEconomics).mockResolvedValue({
      engine_version: 'economics-engine-v1',
    } as never)
    renderPathways()

    expect(await screen.findByText('Market benchmarks have never been built')).toBeTruthy()
    fireEvent.click(screen.getByText('Rebuild economics'))
    await waitFor(() => expect(api.rebuildEconomics).toHaveBeenCalled())
  })

  it('lets the contract billable-days assumption be set from Pathways', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const contracting = result()
    contracting.personal_earnings.baselines[0] = {
      ...contracting.personal_earnings.baselines[0],
      component: 'day_rate',
      unit: 'daily',
      amount: 650,
      employment_basis: 'contract',
    }
    vi.mocked(api.getPathways).mockResolvedValue(contracting)
    vi.mocked(api.getPlanningAssumptions).mockResolvedValue({
      contract_billable_days_per_year: null,
      note: null,
      updated_at: null,
    })
    vi.mocked(api.savePlanningAssumptions).mockResolvedValue({
      contract_billable_days_per_year: 215,
      note: null,
      updated_at: '2026-09-16T09:00:00Z',
    })
    renderPathways()

    expect(await screen.findByText('Not set — day rates are not annualised')).toBeTruthy()
    fireEvent.click(screen.getByText('Set assumption'))
    fireEvent.change(screen.getByLabelText('Billable days per year'), { target: { value: '215' } })

    // The arithmetic is shown before saving, labelled as a planning figure.
    expect(screen.getByText(/£650 × 215 days = £139,750/)).toBeTruthy()
    expect(screen.getByText('This is a planning equivalent, not salary.')).toBeTruthy()

    fireEvent.click(screen.getByText('Save'))
    await waitFor(() =>
      expect(api.savePlanningAssumptions).toHaveBeenCalledWith({
        contract_billable_days_per_year: 215,
        note: null,
      }),
    )
  })

  it('explains why a historic salary is not reported as current earnings', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const historic = result()
    historic.personal_earnings.status = 'historical'
    historic.personal_earnings.baselines[0] = {
      ...historic.personal_earnings.baselines[0],
      evidence_status: 'historical',
      label: 'Latest known earnings',
      episode_title: 'Actuarial Manager',
      episode_end_date: '2022-06-30',
      evidence_status_reason: 'Actuarial Manager ended on 2022-06-30, so this pay is no longer in force.',
    }
    vi.mocked(api.getPathways).mockResolvedValue(historic)
    renderPathways()

    expect(await screen.findByText('Latest known earnings')).toBeTruthy()
    expect(screen.getByText(/ended on 2022-06-30, so this pay is no longer in force/)).toBeTruthy()
  })

  it('withholds a stale gap-value market option rather than reporting it', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    const stale = result()
    stale.gap_value[0].market_option_value = {
      available: false,
      reason: 'Derived economics were last rebuilt on 2026-09-10 and are out of date.',
      withheld_as_stale: true,
    }
    vi.mocked(api.getPathways).mockResolvedValue(stale)
    renderPathways()

    expect(await screen.findByText('Market option value')).toBeTruthy()
    expect(screen.getByText(/out of date/)).toBeTruthy()
    expect(screen.queryByText(/Highest qualifying reference compensation/)).toBeNull()
  })
})

// Phase 7 (docs/38 build §19): the target-selector URL-sync fix + regression.
describe('Pathways — target selector reflects the URL (build §19)', () => {
  it('visibly selects the URL target once the target list loads', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([testTarget({ id: 't1', title: 'Head of Capital' }), testTarget({ id: 't2', title: 'Head of Risk' })])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways('/pathways/t1')

    const select = (await screen.findByLabelText('Target')) as HTMLSelectElement
    await waitFor(() => expect(select.value).toBe('t1'))
  })

  it('updates the selector when the route param changes without unmounting (browser back/forward)', async () => {
    // A same-tree navigation (imperative navigate, or browser back/forward)
    // never unmounts Pathways — the same regression class RoleDetail's own
    // A->B stale-response test guards against.
    vi.mocked(api.listTargets).mockResolvedValue([testTarget({ id: 't1', title: 'Head of Capital' }), testTarget({ id: 't2', title: 'Head of Risk' })])
    vi.mocked(api.getPathways).mockImplementation((targetId: string) =>
      Promise.resolve(result({ target: { ...result().target, id: targetId, title: targetId === 't2' ? 'Head of Risk' : 'Head of Capital' } })),
    )

    function Nav() {
      const navigate = useNavigate()
      return (
        <button type="button" onClick={() => navigate('/pathways/t2')}>
          Go to t2
        </button>
      )
    }
    render(
      <MemoryRouter initialEntries={['/pathways/t1']}>
        <Nav />
        <Routes>
          <Route path="/pathways/:id" element={<Pathways />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect((screen.getByLabelText('Target') as HTMLSelectElement).value).toBe('t1'))

    fireEvent.click(screen.getByText('Go to t2'))
    await waitFor(() => expect((screen.getByLabelText('Target') as HTMLSelectElement).value).toBe('t2'))
  })

  it('navigates to the new route when the selector is changed', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([testTarget({ id: 't1', title: 'Head of Capital' }), testTarget({ id: 't2', title: 'Head of Risk' })])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways('/pathways/t1')

    const select = await screen.findByLabelText('Target')
    fireEvent.change(select, { target: { value: 't2' } })
    await waitFor(() => expect(api.getPathways).toHaveBeenCalledWith('t2', expect.anything()))
  })

  it('shows an honest not-found state for a missing/deleted target id, never another real target', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([testTarget({ id: 't1', title: 'Head of Capital' })])
    vi.mocked(api.getPathways).mockRejectedValue(new Error('404 not found'))
    renderPathways('/pathways/deleted-id')

    expect(await screen.findByText(/This target could not be found/)).toBeTruthy()
    // The select falls back to the placeholder — it never silently shows
    // t1 (a real target) as though it were the selected one.
    const select = screen.getByLabelText('Target') as HTMLSelectElement
    expect(select.value).toBe('')
  })
})

describe('Pathways — specific-opportunity overlay (build §17/§18)', () => {
  it('shows nothing extra when no opportunity_id is present', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    renderPathways('/pathways/t1')

    await screen.findByText(/Direct: Head of Capital/)
    expect(screen.queryByRole('heading', { name: "This opportunity's route to the target" })).toBeNull()
    expect(api.getCareerAlignment).not.toHaveBeenCalled()
  })

  it('shows the relationship, gap movement and economics for the specific opportunity', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    vi.mocked(api.getCareerAlignment).mockResolvedValue(opportunityAlignment())
    renderPathways('/pathways/t1?opportunity_id=opp-1')

    const heading = await screen.findByRole('heading', { name: "This opportunity's route to the target" })
    const overlay = heading.closest('section') as HTMLElement
    expect(within(overlay).getByText('Capital Analyst at Alpha')).toBeTruthy()
    expect(within(overlay).getByText('Potential stepping stone')).toBeTruthy()
    expect(within(overlay).getByText('Capital management')).toBeTruthy()
    expect(api.getCareerAlignment).toHaveBeenCalledWith('opp-1', { target_id: 't1' })
  })

  it('never inserts the opportunity into an archetype route card', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    vi.mocked(api.getCareerAlignment).mockResolvedValue(opportunityAlignment())
    renderPathways('/pathways/t1?opportunity_id=opp-1')

    await screen.findByRole('heading', { name: "This opportunity's route to the target" })
    // The overlay is its own card; the existing archetype route section is untouched.
    expect(screen.getByText('One-step intermediate archetypes (1)')).toBeTruthy()
    expect(screen.getByText(/Via: Capital Analyst archetype/)).toBeTruthy()
  })

  it('an overlay failure never blanks the existing Direct/Intermediate route sections', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    vi.mocked(api.getCareerAlignment).mockRejectedValue(new Error('alignment service unavailable'))
    renderPathways('/pathways/t1?opportunity_id=opp-1')

    expect(await screen.findByText(/Couldn't load this opportunity's alignment/)).toBeTruthy()
    expect(screen.getByText(/Direct: Head of Capital/)).toBeTruthy()
    expect(screen.getByText(/Via: Capital Analyst archetype/)).toBeTruthy()
  })

  it('a Pathways route-section failure never blanks the opportunity overlay', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockRejectedValue(new Error('pathways engine unavailable'))
    vi.mocked(api.getCareerAlignment).mockResolvedValue(opportunityAlignment())
    renderPathways('/pathways/t1?opportunity_id=opp-1')

    expect(await screen.findByText('Potential stepping stone')).toBeTruthy()
  })

  it('route depth stays direct + one intermediate — no N-hop UI appears', async () => {
    vi.mocked(api.listTargets).mockResolvedValue([])
    vi.mocked(api.getPathways).mockResolvedValue(result())
    vi.mocked(api.getCareerAlignment).mockResolvedValue(opportunityAlignment())
    renderPathways('/pathways/t1?opportunity_id=opp-1')

    await screen.findByRole('heading', { name: "This opportunity's route to the target" })
    expect(screen.getByText('One-step intermediate archetypes (1)')).toBeTruthy()
    expect(screen.queryByText(/two-step|multi-step archetype|second intermediate/i)).toBeNull()
  })
})
