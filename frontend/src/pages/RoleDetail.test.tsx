import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import RoleDetail from './RoleDetail'
import {
  api,
  type CareerDirection,
  type ComparisonItem,
  type ComparisonResult,
  type OpportunityAlignment,
  type Role,
  type RoleCompensationResponse,
} from '../lib/api'

function testDirection(overrides: Partial<CareerDirection> = {}): CareerDirection {
  return {
    id: 'dir-1', name: 'Technical actuarial leadership', summary: '', state: 'selected', origin: 'user',
    dimensions: [], constraints: { locations: [], remote_types: [], employment_types: [], seniority_levels: [], compensation_floor: null, other: [] },
    target: null, archetype: null, source_discovery_run_id: null, source_candidate_id: null,
    selected_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const ALIGNMENT_METHOD = {
  what_this_is: 'test', not_a_recommendation: 'test', involvement_not_acquisition: 'test',
  relationship_caveat: 'Same destination family, potential step and no identified target progress are each independent facts, not verdicts.',
  semantic_similarity: 'test', compensation: 'test',
}

function noDirectionAlignment(roleId = 'role'): OpportunityAlignment {
  return {
    state: 'no_selected_direction',
    direction: null,
    target: null,
    opportunity: { id: roleId, title: 'Head of Capital', organisation: 'An insurer', archetype_concept_id: null },
    message: 'Select a Career Direction to evaluate this opportunity against it.',
    method: ALIGNMENT_METHOD,
  }
}

function targetAvailableAlignment(overrides: Partial<OpportunityAlignment> = {}): OpportunityAlignment {
  return {
    state: 'target_available',
    direction: testDirection({ target: { id: 'target-1', title: 'Head of Risk', organisation: null } }),
    target: { id: 'target-1', title: 'Head of Risk', organisation: null, archetype_concept_id: null },
    opportunity: { id: 'role', title: 'Head of Capital', organisation: 'An insurer', archetype_concept_id: null },
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
    archetype_relationship: { opportunity_archetype: null, target_archetype: null, direction_archetype: null, same_as_target_archetype: false, same_as_direction_archetype: false, note: 'No reviewed archetype is assigned to this posting.' },
    economics: {
      opportunity: NO_COMPENSATION_FIGURE, vs_personal_earnings: { comparable: false, reason: 'no evidence', baseline: null, uses_planning_equivalent: false, difference_min: null, difference_reference: null, difference_max: null },
      target: null,
    },
    review: { opportunity: { complete: true, blockers: [], legacy_requirement_count: 0 }, target: { complete: true, blockers: [], mapping_complete: true, mapping_unresolved: 0 } },
    semantic_similarity: { to_target: null, to_profile: null },
    method: ALIGNMENT_METHOD,
    ...overrides,
  }
}

const NO_COMPENSATION_FIGURE = {
  basis: 'insufficient_evidence' as const, basis_label: 'Insufficient compensation evidence', component_label: null,
  supplementary: [], currency: null, amount_min: null, amount_reference: null, amount_max: null, component: null,
  pay_period: null, employment_basis: null, market: null, period: null, as_of: null, archetype: null,
  evidence: { n_observations: 0, n_posting_stated: 0, n_survey_sources: 0 }, reference_source: null,
  evidence_quality: 'insufficient' as const, reason: 'No compensation evidence.', trace: {},
}

// Phase 2 (docs/33): the Opportunity Decision Workspace. These tests focus on
// what the acceptance criteria actually care about — the summary-first
// structure, honest incomplete-review handling, evidence/economics states,
// failure isolation, and that targets keep their own separate presentation —
// not the underlying engines themselves, which have their own test suites.

vi.mock('../lib/api', () => ({
  api: {
    getRole: vi.fn(),
    getRoleContext: vi.fn(),
    getRoleCompensation: vi.fn(),
    compareRole: vi.fn(),
    proposeRoleCompensation: vi.fn(),
    acceptRoleCompensation: vi.fn(),
    getArchetypeCatalogue: vi.fn(),
    proposeRoleArchetype: vi.fn(),
    setRoleArchetype: vi.fn(),
    correctRoleCompensation: vi.fn(),
    rejectRoleCompensation: vi.fn(),
    reacceptRoleCompensation: vi.fn(),
    deleteRole: vi.fn(),
    createOrReopenApplication: vi.fn(),
    getCareerAlignment: vi.fn(),
  },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const basePosting: Role = {
  id: 'role',
  node_type: 'posting',
  title: 'Head of Capital',
  organisation: 'An insurer',
  location: 'London',
  country: 'United Kingdom',
  remote_type: 'hybrid',
  employment_type: 'permanent',
  posting_date: '2026-01-15',
  captured_at: '2026-01-16T00:00:00Z',
  career_track: null,
  seniority_level: null,
  salary_min: null,
  salary_max: null,
  currency: null,
  summary: null,
  description: 'Lead the capital function.',
  requirements: null,
  responsibilities: null,
  key_skills_summary: null,
  top_adjacent_roles: null,
  extraction_status: 'ok',
  extraction_notes: null,
  similarity: 0.42,
  url: 'https://example.com/job/1',
  skills: [
    { name: 'Solvency II', category: null, importance: null, requirement_type: 'required', resolved_concept_id: 'c1' },
    { name: 'Python', category: null, importance: null, requirement_type: 'preferred', resolved_concept_id: 'c2' },
  ],
  legacy_skills: [],
  requirement_review: {
    accepted: 2,
    unreviewed: 0,
    rejected: 0,
    unresolved_proposals: 0,
    extraction_attempted: true,
    needs_reextraction: 0,
    complete: true,
  },
}

const NO_COMPENSATION: RoleCompensationResponse = {
  role_instance_id: 'role',
  compensation: {
    basis: 'insufficient_evidence',
    basis_label: 'Insufficient compensation evidence',
    component_label: null,
    supplementary: [],
    currency: null,
    amount_min: null,
    amount_reference: null,
    amount_max: null,
    component: null,
    pay_period: null,
    employment_basis: null,
    market: null,
    period: null,
    as_of: null,
    archetype: null,
    evidence: { n_observations: 0, n_posting_stated: 0, n_survey_sources: 0 },
    reference_source: null,
    evidence_quality: 'insufficient',
    reason: 'No compensation is stated on this role, and it has no reviewed archetype.',
    trace: {},
  },
  personal_comparison: {
    comparable: false,
    reason: 'There is no compensation figure for this opportunity to compare against.',
    baseline: null,
    uses_planning_equivalent: false,
    difference_min: null,
    difference_reference: null,
    difference_max: null,
  },
  personal_earnings: {
    status: 'unavailable',
    as_of: '2026-09-16',
    baselines: [],
    other_components: [],
    currencies: [],
    planning_assumption: { contract_billable_days_per_year: null, note: null, updated_at: null },
    notes: [],
    fingerprint: 'none',
  },
  observations: [],
}

function advertCompensation(): RoleCompensationResponse {
  return {
    ...NO_COMPENSATION,
    compensation: {
      ...NO_COMPENSATION.compensation,
      basis: 'advert_stated',
      basis_label: 'Advert salary',
      component_label: 'base salary',
      currency: 'GBP',
      amount_min: 120000,
      amount_reference: 132500,
      amount_max: 145000,
      component: 'base',
      pay_period: 'annual',
      evidence_quality: 'good',
      reason: 'Compensation stated on this posting, reviewed against a verbatim quote from the source document.',
    },
  }
}

function makeItem(overrides: Partial<ComparisonItem> = {}): ComparisonItem {
  return {
    concept: { id: 'c1', canonical_name: 'Solvency II', type_code: 'skill' },
    status: 'evidenced',
    role_side: {
      requirement_claim_id: 'rc1',
      requirement_type: 'required',
      basis: 'stated',
      review_status: 'accepted',
      evidence_span: null,
      document: null,
    },
    person_side: { mappings: [], assertion: null, component_of: [], coverage: null },
    ...overrides,
  }
}

function emptyComparison(overrides: Partial<ComparisonResult> = {}): ComparisonResult {
  return {
    role: { id: 'role', title: 'Head of Capital', kind: 'posting' },
    items: [],
    counts: { evidenced: 0, partial: 0, user_asserted: 0, not_found: 0 },
    blocking_gaps: [],
    unverified_required: [],
    fit_score: null,
    embedding_similarity: null,
    engine_version: 'test',
    review_summary: basePosting.requirement_review!,
    ...overrides,
  }
}

function renderPosting(
  role: Role = basePosting, compensation: RoleCompensationResponse = NO_COMPENSATION, comparison: ComparisonResult = emptyComparison(),
  alignment: OpportunityAlignment = noDirectionAlignment(role.id),
) {
  vi.mocked(api.getRole).mockResolvedValue(role)
  vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: role.id, enrichment: null })
  vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation)
  vi.mocked(api.compareRole).mockResolvedValue(comparison)
  vi.mocked(api.getCareerAlignment).mockResolvedValue(alignment)
  return render(
    <MemoryRouter initialEntries={[`/roles/${role.id}`]}>
      <Routes>
        <Route path="/roles/:id" element={<RoleDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Opportunity Decision Workspace — posting', () => {
  it('renders the decision summary with requirements, evidence, economics and career-direction tiles', async () => {
    renderPosting()
    expect(await screen.findByRole('heading', { name: 'Decision summary' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Requirements' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Economics' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Career direction' })).toBeTruthy()
    expect(await screen.findByText('Select a Career Direction to evaluate this opportunity against it.')).toBeTruthy()
    // No fit/readiness/hiring/alignment score is introduced anywhere on the page.
    expect(screen.queryByText(/fit score/i)).toBeNull()
    expect(screen.queryByText(/readiness/i)).toBeNull()
    expect(screen.queryByText(/hiring/i)).toBeNull()
  })

  it('shows the relationship state and involved-gaps summary for a full alignment (build §8/§15)', async () => {
    renderPosting(basePosting, NO_COMPENSATION, emptyComparison(), targetAvailableAlignment())
    await screen.findByRole('heading', { name: 'Decision summary' })
    expect(await screen.findByText('Technical actuarial leadership')).toBeTruthy()
    expect(screen.getByText(/Current direction:/)).toBeTruthy()
    expect(screen.getByText('Potential stepping stone')).toBeTruthy()
    expect(screen.getByText('Involves 1 of your 1 outstanding Target requirement.')).toBeTruthy()
    expect(screen.getByText('Open direction').closest('a')?.getAttribute('href')).toBe('/future/directions/dir-1')
    // No composite score/percentage anywhere.
    expect(screen.queryByText(/fit score|readiness|hiring probability|offer probability/i)).toBeNull()
  })

  it('shows the fuller You -> this opportunity -> Target section, with a Pathways deep link', async () => {
    renderPosting(basePosting, NO_COMPENSATION, emptyComparison(), targetAvailableAlignment())
    expect(await screen.findByRole('heading', { name: 'You → this opportunity → Target' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'You → Opportunity' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Opportunity → Target' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Why this relationship' })).toBeTruthy()
    expect(screen.getByText('Capital management')).toBeTruthy()
    const pathwaysLink = screen.getByText('View this opportunity in Pathways').closest('a')
    expect(pathwaysLink?.getAttribute('href')).toBe('/pathways/target-1?opportunity_id=role')
  })

  it('shows Phase 8 market evidence context without changing the relationship state (docs/39 build §23)', async () => {
    const alignment = targetAvailableAlignment({
      market_evidence_context: {
        available: true,
        archetype_concept_id: 'arch-1',
        canonical_name: 'Senior Actuary',
        status: 'active',
        seniority_band: 'senior',
        typical_market: 'Ireland',
        assigned_posting_count: 4,
        reviewed_requirement_posting_count: 3,
        known_posting_date_count: 4,
        unknown_posting_date_count: 0,
        latest_known_posting_date: '2024-06-01',
        distinct_country_count: 1,
        countries: ['Ireland'],
        demand_derivation_available: true,
        compensation_benchmark_available: true,
        economics_freshness: { state: 'fresh', fresh: true, reason: null },
        evidence_depth: { state: 'thin', reason: 'Only 4 supporting posting(s) in this scope.' },
      },
    })
    renderPosting(basePosting, NO_COMPENSATION, emptyComparison(), alignment)
    expect(await screen.findByRole('heading', { name: 'Market evidence for this pattern' })).toBeTruthy()
    expect(screen.getByText('Senior Actuary')).toBeTruthy()
    expect(screen.getByText(/Thin evidence/i)).toBeTruthy()
    // The relationship state is exactly what targetAvailableAlignment() already asserts elsewhere — unaffected by coverage.
    expect(screen.getByText('Potential stepping stone')).toBeTruthy()
  })

  it('shows the "no reviewed archetype" message when the opportunity has none (docs/39 build §23)', async () => {
    const alignment = targetAvailableAlignment({
      market_evidence_context: {
        available: false,
        message: 'No reviewed archetype assignment — market-pattern support is unavailable for this role.',
      },
    })
    renderPosting(basePosting, NO_COMPENSATION, emptyComparison(), alignment)
    await screen.findByRole('heading', { name: 'You → this opportunity → Target' })
    expect(screen.getByText('No reviewed archetype assignment — market-pattern support is unavailable for this role.')).toBeTruthy()
  })

  it('never shows direction constraints/dimensions cards when there are none to show', async () => {
    renderPosting(basePosting, NO_COMPENSATION, emptyComparison(), targetAvailableAlignment())
    await screen.findByRole('heading', { name: 'You → this opportunity → Target' })
    expect(screen.queryByRole('heading', { name: 'Direction constraints' })).toBeNull()
  })

  it('shows an honest partial state for a direction with no linked Target, without inventing route analysis', async () => {
    const alignment: OpportunityAlignment = {
      state: 'direction_without_target',
      direction: testDirection(),
      target: null,
      opportunity: { id: 'role', title: 'Head of Capital', organisation: 'An insurer', archetype_concept_id: null },
      message: 'This direction has no linked Target yet, so structural Opportunity -> Target analysis is not available. Direction constraints can still be checked directly.',
      direction_constraints: [
        { constraint: 'locations', label: 'Location', status: 'matches', observed_value: 'London', desired_value: ['London'], reason: '"London" matches one of your stated preferences.' },
      ],
      direction_dimensions: [],
      archetype_relationship: { opportunity_archetype: null, target_archetype: null, direction_archetype: null, same_as_target_archetype: false, same_as_direction_archetype: false, note: 'No reviewed archetype is assigned to this posting.' },
      method: ALIGNMENT_METHOD,
    }
    renderPosting(basePosting, NO_COMPENSATION, emptyComparison(), alignment)
    expect(await screen.findByText(/No concrete Target linked/)).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Direction constraints' })).toBeTruthy()
    expect(screen.getByText('Location')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Opportunity → Target' })).toBeNull()
  })

  it('shows insufficient-target-evidence honestly while still showing independent facts', async () => {
    const alignment: OpportunityAlignment = {
      state: 'insufficient_target_evidence',
      direction: testDirection({ target: { id: 'target-1', title: 'Head of Risk', organisation: null } }),
      target: { id: 'target-1', title: 'Head of Risk', organisation: null, archetype_concept_id: null },
      opportunity: { id: 'role', title: 'Head of Capital', organisation: 'An insurer', archetype_concept_id: null },
      message: 'This target has no reviewed, mapped requirements yet, so a structural Opportunity -> Target comparison is not available. The facts below do not depend on that gap.',
      you_to_opportunity: {
        counts: { evidenced: 1, partial: 0, user_asserted: 0, not_found: 0 }, requirements_reviewed: 1,
        legacy_requirement_count: 0, review_summary: { accepted: 1, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: true, needs_reextraction: 0, complete: true },
        review_blockers: [], blocking_gaps: [], unverified_required: [], embedding_similarity: null,
      },
      economics: { opportunity: NO_COMPENSATION_FIGURE, vs_personal_earnings: { comparable: false, reason: 'no evidence', baseline: null, uses_planning_equivalent: false, difference_min: null, difference_reference: null, difference_max: null }, target: null },
      method: ALIGNMENT_METHOD,
    }
    renderPosting(basePosting, NO_COMPENSATION, emptyComparison(), alignment)
    expect(await screen.findByText(/no reviewed, mapped requirements yet/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'You → Opportunity' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Opportunity → Target' })).toBeNull()
  })

  it('degrades to a focused local error if the alignment fetch fails, without blanking the rest of the page', async () => {
    vi.mocked(api.getRole).mockResolvedValue(basePosting)
    vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: basePosting.id, enrichment: null })
    vi.mocked(api.getRoleCompensation).mockResolvedValue(NO_COMPENSATION)
    vi.mocked(api.compareRole).mockResolvedValue(emptyComparison())
    vi.mocked(api.getCareerAlignment).mockRejectedValue(new Error('down'))
    render(
      <MemoryRouter initialEntries={['/roles/role']}>
        <Routes>
          <Route path="/roles/:id" element={<RoleDetail />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Decision summary' })
    expect(await screen.findByText("Career alignment couldn't be loaded.")).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Requirements' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Economics' })).toBeTruthy()
  })

  it('retries the alignment fetch alone without reloading the rest of the page', async () => {
    vi.mocked(api.getRole).mockResolvedValue(basePosting)
    vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: basePosting.id, enrichment: null })
    vi.mocked(api.getRoleCompensation).mockResolvedValue(NO_COMPENSATION)
    vi.mocked(api.compareRole).mockResolvedValue(emptyComparison())
    vi.mocked(api.getCareerAlignment).mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce(noDirectionAlignment())
    render(
      <MemoryRouter initialEntries={['/roles/role']}>
        <Routes>
          <Route path="/roles/:id" element={<RoleDetail />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByText("Career alignment couldn't be loaded.")
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Select a Career Direction to evaluate this opportunity against it.')).toBeTruthy()
    expect(api.getRole).toHaveBeenCalledTimes(1)
  })

  it('shows a complete requirement review without treating it as a problem', async () => {
    renderPosting()
    await screen.findByRole('heading', { name: 'Decision summary' })
    expect(screen.getByText('2 reviewed requirements')).toBeTruthy()
    expect(screen.getByText('Review complete.')).toBeTruthy()
    expect(screen.queryByText(/Requirements review pending/)).toBeNull()
  })

  it('shows an incomplete requirement review honestly, without making the page unusable', async () => {
    renderPosting({
      ...basePosting,
      requirement_review: { accepted: 2, unreviewed: 1, rejected: 0, unresolved_proposals: 1, extraction_attempted: true, needs_reextraction: 0, complete: false },
    })
    await screen.findByRole('heading', { name: 'Decision summary' })
    expect(screen.getByText('2 reviewed requirements')).toBeTruthy()
    expect(screen.getByText('1 item still need review')).toBeTruthy()
    expect(screen.getByText('1 unresolved vocabulary term')).toBeTruthy()
    // The pending notice appears (inside "What this role asks for") and the
    // rest of the page — economics, next actions — is still fully present.
    expect(await screen.findByText(/Requirements review pending/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Next actions' })).toBeTruthy()
  })

  it('labels a legacy-only role honestly instead of upgrading it into a reviewed requirement', async () => {
    renderPosting({
      ...basePosting,
      skills: [],
      legacy_skills: [{ name: 'Excel', category: null, importance: null, requirement_type: 'required', resolved_concept_id: null }],
      requirement_review: { accepted: 0, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: true, needs_reextraction: 0, complete: true },
    })
    await screen.findByRole('heading', { name: 'What this role asks for' })
    expect(screen.getByText(/No reviewed requirements yet/)).toBeTruthy()
    expect(screen.getByText(/1 legacy, unreviewed skill/)).toBeTruthy()
  })

  it('treats a never-extracted, newly saved posting as "not yet extracted", not as zero coverage', async () => {
    renderPosting(
      {
        ...basePosting,
        skills: [],
        legacy_skills: [],
        description: null,
        requirements: null,
        responsibilities: null,
        source_document_text: 'Original captured advert text goes here.',
        requirement_review: { accepted: 0, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: false, needs_reextraction: 0, complete: true },
      },
      NO_COMPENSATION,
      emptyComparison({ review_summary: { accepted: 0, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: false, needs_reextraction: 0, complete: true } }),
    )
    // "No reviewed requirements to compare yet." depends on the comparison
    // fetch, the second of two async waves (role, then compensation/
    // comparison) — awaiting it also guarantees the first-wave, role-derived
    // content below has settled, so those can be asserted synchronously.
    await screen.findByText('No reviewed requirements to compare yet.')
    expect(screen.getByText('Not yet extracted.')).toBeTruthy()
    expect(screen.getByText("Requirements haven't been extracted from this posting yet.")).toBeTruthy()
    expect(screen.getByText('Captured source text')).toBeTruthy()
    expect(screen.getByText('Original captured advert text goes here.')).toBeTruthy()
  })

  it('shows evidenced/partial/asserted/not-found counts from the structural comparison engine', async () => {
    const comparison = emptyComparison({
      items: [makeItem({ status: 'evidenced' }), makeItem({ status: 'partial' }), makeItem({ status: 'not_found' })],
      counts: { evidenced: 1, partial: 1, user_asserted: 0, not_found: 1 },
    })
    renderPosting(basePosting, NO_COMPENSATION, comparison)
    const evidenceTile = (await screen.findByRole('heading', { name: 'Evidence' })).closest('section') as HTMLElement
    await within(evidenceTile).findByText('evidenced')
    expect(within(evidenceTile).getAllByText('1')).toHaveLength(3) // evidenced, partial, not_found
    expect(within(evidenceTile).getByText('evidenced')).toBeTruthy()
    expect(within(evidenceTile).getByText('partially evidenced')).toBeTruthy()
    expect(within(evidenceTile).getByText('no evidence found')).toBeTruthy()
    expect(within(evidenceTile).getByText(/No accepted evidence found does not mean you lack the capability/)).toBeTruthy()
  })

  it('distinguishes a blocking required gap from an unverified one', async () => {
    const comparison = emptyComparison({
      items: [makeItem({ status: 'not_found' })],
      counts: { evidenced: 0, partial: 0, user_asserted: 0, not_found: 1 },
      blocking_gaps: [{ id: 'c9', canonical_name: 'Actuarial modelling', type_code: 'skill' }],
      unverified_required: [{ id: 'c8', canonical_name: 'Stakeholder management', type_code: 'skill', status: 'partial' }],
    })
    renderPosting(basePosting, NO_COMPENSATION, comparison)
    expect(await screen.findByText(/1 required blocking gap/)).toBeTruthy()
    expect(screen.getByText(/Actuarial modelling/)).toBeTruthy()
    expect(screen.getByText(/1 required capability not fully verified/)).toBeTruthy()
  })

  it('shows an available compensation figure with its basis', async () => {
    renderPosting(basePosting, advertCompensation())
    expect(await screen.findByText('Advert salary')).toBeTruthy()
    expect(screen.getByText('£120,000 – £145,000')).toBeTruthy()
  })

  it('shows insufficient compensation evidence as its own honest state', async () => {
    renderPosting()
    expect(await screen.findByText('Insufficient evidence')).toBeTruthy()
    expect(screen.getByText(/no reviewed archetype/)).toBeTruthy()
  })

  it('keeps the rest of the page usable when the comparison request fails', async () => {
    vi.mocked(api.getRole).mockResolvedValue(basePosting)
    vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: 'role', enrichment: null })
    vi.mocked(api.getRoleCompensation).mockResolvedValue(NO_COMPENSATION)
    vi.mocked(api.compareRole).mockRejectedValue(new Error('comparison service unavailable'))
    vi.mocked(api.getCareerAlignment).mockResolvedValue(noDirectionAlignment())
    render(
      <MemoryRouter initialEntries={['/roles/role']}>
        <Routes>
          <Route path="/roles/:id" element={<RoleDetail />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { level: 1, name: 'Head of Capital' })).toBeTruthy()
    expect(await screen.findByText("Evidence comparison couldn't be loaded.")).toBeTruthy()
    // The rest of the decision summary is unaffected by the comparison failure.
    expect(screen.getByText('2 reviewed requirements')).toBeTruthy()
    expect(await screen.findByText('Insufficient evidence')).toBeTruthy()
  })

  it('links to Review requirements and the detailed Comparison page', async () => {
    renderPosting()
    await screen.findByRole('heading', { name: 'Decision summary' })
    const requirementsLinks = screen.getAllByRole('link', { name: /review requirements/i })
    expect(requirementsLinks.some((l) => l.getAttribute('href') === '/role-instances/role/requirements')).toBe(true)
    const comparisonLinks = screen.getAllByRole('link', { name: /review evidence in detail/i })
    expect(comparisonLinks.some((l) => l.getAttribute('href') === '/comparison/role')).toBe(true)
  })

})

describe('Opportunity → Application (docs/34 §7)', () => {
  function renderPostingWithApplicationsRoute() {
    vi.mocked(api.getRole).mockResolvedValue(basePosting)
    vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: basePosting.id, enrichment: null })
    vi.mocked(api.getRoleCompensation).mockResolvedValue(NO_COMPENSATION)
    vi.mocked(api.compareRole).mockResolvedValue(emptyComparison())
    vi.mocked(api.getCareerAlignment).mockResolvedValue(noDirectionAlignment())
    return render(
      <MemoryRouter initialEntries={['/roles/role']}>
        <Routes>
          <Route path="/roles/:id" element={<RoleDetail />} />
          <Route path="/applications/:id" element={<p>Application workspace</p>} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('shows a prominent primary "I want to apply" action', async () => {
    renderPosting()
    const button = await screen.findByRole('button', { name: 'I want to apply' })
    expect(button.className).toContain('primary')
  })

  it('creates/reopens the application and navigates to its workspace on click', async () => {
    vi.mocked(api.createOrReopenApplication).mockResolvedValue({
      id: 'app-1', role_instance_id: 'role', status: 'preparing', created_at: 't', updated_at: 't', created: true,
    })
    renderPostingWithApplicationsRoute()
    const button = await screen.findByRole('button', { name: 'I want to apply' })
    fireEvent.click(button)
    expect(await screen.findByText('Application workspace')).toBeTruthy()
    expect(api.createOrReopenApplication).toHaveBeenCalledWith('role')
  })

  it('reopens (rather than duplicates) an existing active application the same way', async () => {
    vi.mocked(api.createOrReopenApplication).mockResolvedValue({
      id: 'existing-app', role_instance_id: 'role', status: 'submitted', created_at: 't', updated_at: 't', created: false,
    })
    renderPostingWithApplicationsRoute()
    const button = await screen.findByRole('button', { name: 'I want to apply' })
    fireEvent.click(button)
    expect(await screen.findByText('Application workspace')).toBeTruthy()
    expect(api.createOrReopenApplication).toHaveBeenCalledTimes(1)
  })

  it('prevents double-submit while the request is in flight', async () => {
    let resolveCall: (v: { id: string; role_instance_id: string; status: 'preparing'; created_at: string; updated_at: string; created: boolean }) => void = () => {}
    vi.mocked(api.createOrReopenApplication).mockReturnValue(
      new Promise((resolve) => { resolveCall = resolve }),
    )
    renderPostingWithApplicationsRoute()
    const button = await screen.findByRole('button', { name: 'I want to apply' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(api.createOrReopenApplication).toHaveBeenCalledTimes(1)
    resolveCall({ id: 'app-1', role_instance_id: 'role', status: 'preparing', created_at: 't', updated_at: 't', created: true })
    await screen.findByText('Application workspace')
  })

  it('shows a focused error without breaking the rest of the page when the call fails', async () => {
    vi.mocked(api.createOrReopenApplication).mockRejectedValue(new Error('network down'))
    renderPostingWithApplicationsRoute()
    const button = await screen.findByRole('button', { name: 'I want to apply' })
    fireEvent.click(button)
    expect(await screen.findByText(/Couldn't open the application/)).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Decision summary' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'I want to apply' })).toBeTruthy()
  })
})

describe('Opportunity Decision Workspace — navigation and accessibility', () => {
  it('renders next-action navigation as real links, never a Link wrapping a button', async () => {
    renderPosting()
    const nextActions = (await screen.findByRole('heading', { name: 'Next actions' })).closest('section') as HTMLElement
    const links = within(nextActions).getAllByRole('link')
    expect(links.length).toBeGreaterThan(0)
    for (const link of links) {
      expect(link.querySelector('button')).toBeNull()
    }
  })

  it('gives every summary section a meaningful heading', async () => {
    renderPosting({
      ...basePosting,
      requirement_review: { accepted: 2, unreviewed: 1, rejected: 0, unresolved_proposals: 0, extraction_attempted: true, needs_reextraction: 0, complete: false },
    })
    expect(await screen.findByRole('heading', { name: 'Decision summary' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'What this role asks for' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Next actions' })).toBeTruthy()
  })

  it('reports a failed comparison with alert semantics', async () => {
    vi.mocked(api.getRole).mockResolvedValue(basePosting)
    vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: 'role', enrichment: null })
    vi.mocked(api.getRoleCompensation).mockResolvedValue(NO_COMPENSATION)
    vi.mocked(api.compareRole).mockRejectedValue(new Error('boom'))
    vi.mocked(api.getCareerAlignment).mockResolvedValue(noDirectionAlignment())
    render(
      <MemoryRouter initialEntries={['/roles/role']}>
        <Routes>
          <Route path="/roles/:id" element={<RoleDetail />} />
        </Routes>
      </MemoryRouter>,
    )
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/couldn't be loaded/)
  })
})

describe('Target regression', () => {
  const baseTarget: Role = {
    ...basePosting,
    id: 'target-1',
    node_type: 'target_real',
    title: 'Head of Risk',
    organisation: null,
    requirement_review: { accepted: 0, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: false, needs_reextraction: 0, complete: true },
  }

  function renderTarget(role: Role = baseTarget) {
    vi.mocked(api.getRole).mockResolvedValue(role)
    vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: role.id, enrichment: null })
    vi.mocked(api.getRoleCompensation).mockResolvedValue(NO_COMPENSATION)
    return render(
      <MemoryRouter initialEntries={[`/targets/${role.id}`]}>
        <Routes>
          <Route path="/targets/:id" element={<RoleDetail />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('still renders target content and never fetches the comparison or alignment engines', async () => {
    renderTarget()
    expect(await screen.findByRole('heading', { level: 1, name: 'Head of Risk' })).toBeTruthy()
    expect(screen.getByText(/Target · real/)).toBeTruthy()
    expect(api.compareRole).not.toHaveBeenCalled()
    expect(api.getCareerAlignment).not.toHaveBeenCalled()
  })

  it('never shows opportunity-only decision or application language', async () => {
    renderTarget()
    await screen.findByRole('heading', { level: 1, name: 'Head of Risk' })
    expect(screen.queryByText('Decision summary')).toBeNull()
    expect(screen.queryByText('What this role asks for')).toBeNull()
    expect(screen.queryByText(/current vacancy/i)).toBeNull()
    expect(screen.queryByText('Correct role details')).toBeNull()
    expect(screen.queryByText(/i want to apply/i)).toBeNull()
  })

  it('keeps Back pointed at targets, not opportunities', async () => {
    renderTarget()
    await screen.findByRole('heading', { level: 1, name: 'Head of Risk' })
    const back = screen.getByText(/← Back to/).closest('a') as HTMLAnchorElement
    expect(back.getAttribute('href')).toBe('/targets')
  })

  it('keeps its own Requirements/Compare/Edit next actions untouched', async () => {
    renderTarget()
    const nextActions = (await screen.findByRole('heading', { name: 'Next actions' })).closest('section') as HTMLElement
    expect(within(nextActions).getByText('Requirements')).toBeTruthy()
    expect(within(nextActions).getByText('Compare')).toBeTruthy()
    expect(within(nextActions).getByText('Edit')).toBeTruthy()
    for (const link of within(nextActions).getAllByRole('link')) {
      expect(link.querySelector('button')).toBeNull()
    }
  })
})

describe('makes no AI call merely by opening the page', () => {
  it('never calls a propose/generate endpoint on load', async () => {
    renderPosting(basePosting, advertCompensation())
    await waitFor(() => expect(api.compareRole).toHaveBeenCalled())
    expect(api.proposeRoleCompensation).not.toHaveBeenCalled()
    expect(api.proposeRoleArchetype).not.toHaveBeenCalled()
  })
})

describe('Cross-role navigation never leaks stale state', () => {
  // Navigating /roles/A -> /roles/B re-renders the same mounted RoleDetail
  // with a new :id param — React Router does not unmount it. A's comparison
  // and compensation must disappear the instant B is requested (never shown
  // for B while it loads), and a slow A response that resolves *after* B's
  // own response must never be allowed to overwrite it.
  function renderWithNavigator(initial: string) {
    function Nav() {
      const navigate = useNavigate()
      return (
        <button type="button" onClick={() => navigate('/roles/B')}>
          Go to B
        </button>
      )
    }
    return render(
      <MemoryRouter initialEntries={[initial]}>
        <Nav />
        <Routes>
          <Route path="/roles/:id" element={<RoleDetail />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('clears A\'s comparison/compensation on navigation and ignores A\'s late, out-of-order response', async () => {
    const roleA: Role = { ...basePosting, id: 'A', title: 'Role A' }
    const roleB: Role = { ...basePosting, id: 'B', title: 'Role B' }

    // Deferred promises so the test controls exactly when each role's
    // compensation/comparison resolves — including resolving A's (the
    // stale, superseded request) *after* B's own response has already
    // landed, which is the out-of-order case the fix must survive.
    let resolveCompA!: (v: RoleCompensationResponse) => void
    let resolveCompB!: (v: RoleCompensationResponse) => void
    let resolveCmpA!: (v: ComparisonResult) => void
    let resolveCmpB!: (v: ComparisonResult) => void
    let resolveAlignA!: (v: OpportunityAlignment) => void
    let resolveAlignB!: (v: OpportunityAlignment) => void

    vi.mocked(api.getRole).mockImplementation((id: string) => Promise.resolve(id === 'A' ? roleA : roleB))
    vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: 'x', enrichment: null })
    vi.mocked(api.getRoleCompensation).mockImplementation(
      (id: string) =>
        new Promise((resolve) => {
          if (id === 'A') resolveCompA = resolve
          else resolveCompB = resolve
        }),
    )
    vi.mocked(api.compareRole).mockImplementation(
      (id: string) =>
        new Promise((resolve) => {
          if (id === 'A') resolveCmpA = resolve
          else resolveCmpB = resolve
        }),
    )
    // Phase 7: the alignment fetch must carry the exact same stale-response
    // protection as compensation/comparison above (build §32).
    vi.mocked(api.getCareerAlignment).mockImplementation(
      (id: string) =>
        new Promise((resolve) => {
          if (id === 'A') resolveAlignA = resolve
          else resolveAlignB = resolve
        }),
    )

    renderWithNavigator('/roles/A')
    await screen.findByRole('heading', { level: 1, name: 'Role A' })

    // Navigate away before A's own compensation/comparison ever resolve.
    fireEvent.click(screen.getByText('Go to B'))
    await screen.findByRole('heading', { level: 1, name: 'Role B' })

    // B is showing, but its own requests are still pending too — both
    // tiles must show their own loading state, never A's data (there is
    // none to show yet, since A's requests never resolved).
    const economicsTile = screen.getByRole('heading', { name: 'Economics' }).closest('section') as HTMLElement
    const evidenceTile = screen.getByRole('heading', { name: 'Evidence' }).closest('section') as HTMLElement
    expect(within(economicsTile).getByText('Loading…')).toBeTruthy()
    expect(within(evidenceTile).getByText('Loading…')).toBeTruthy()

    // B's own responses land first.
    await waitFor(() => {
      expect(resolveCompB).toBeTypeOf('function')
      expect(resolveCmpB).toBeTypeOf('function')
      expect(resolveAlignB).toBeTypeOf('function')
    })
    resolveCompB(advertCompensation())
    resolveCmpB(
      emptyComparison({
        items: [makeItem({ status: 'evidenced' })],
        counts: { evidenced: 1, partial: 0, user_asserted: 0, not_found: 0 },
      }),
    )
    resolveAlignB(targetAvailableAlignment({
      opportunity: { id: 'B', title: 'Role B', organisation: null, archetype_concept_id: null },
      economics: undefined,
    }))
    await screen.findByText('Advert salary')
    await within(evidenceTile).findByText('evidenced')
    await screen.findByText('Potential stepping stone')

    // A's slow, now-stale responses finally arrive — they must be ignored.
    resolveCompA(NO_COMPENSATION)
    resolveCmpA(emptyComparison())
    resolveAlignA(noDirectionAlignment('A'))
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(screen.getByRole('heading', { level: 1, name: 'Role B' })).toBeTruthy()
    expect(screen.getByText('Advert salary')).toBeTruthy()
    expect(screen.queryByText('Insufficient evidence')).toBeNull()
    expect(within(evidenceTile).getByText('evidenced')).toBeTruthy()
    // A's alignment ("no direction selected") must never overwrite B's.
    expect(screen.getByText('Potential stepping stone')).toBeTruthy()
    expect(screen.queryByText('Select a Career Direction to evaluate this opportunity against it.')).toBeNull()
    expect(within(evidenceTile).queryByText('No reviewed requirements to compare yet.')).toBeNull()
  })
})
