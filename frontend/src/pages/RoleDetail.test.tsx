import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RoleDetail from './RoleDetail'
import {
  api,
  type ComparisonItem,
  type ComparisonResult,
  type Role,
  type RoleCompensationResponse,
} from '../lib/api'

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

function renderPosting(role: Role = basePosting, compensation: RoleCompensationResponse = NO_COMPENSATION, comparison: ComparisonResult = emptyComparison()) {
  vi.mocked(api.getRole).mockResolvedValue(role)
  vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: role.id, enrichment: null })
  vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation)
  vi.mocked(api.compareRole).mockResolvedValue(comparison)
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
    expect(screen.getByText(/No career direction selected yet/)).toBeTruthy()
    // No fit/readiness/hiring score is introduced anywhere on the page.
    expect(screen.queryByText(/fit score/i)).toBeNull()
    expect(screen.queryByText(/readiness/i)).toBeNull()
    expect(screen.queryByText(/hiring/i)).toBeNull()
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
    await screen.findByRole('heading', { name: 'Decision summary' })
    expect(screen.getByText('Not yet extracted.')).toBeTruthy()
    expect(screen.getByText("Requirements haven't been extracted from this posting yet.")).toBeTruthy()
    expect(screen.getByText('No reviewed requirements to compare yet.')).toBeTruthy()
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

  it('does not add an application call-to-action', async () => {
    renderPosting()
    await screen.findByRole('heading', { name: 'Next actions' })
    expect(screen.queryByText(/i want to apply/i)).toBeNull()
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

  it('still renders target content and never fetches the comparison engine', async () => {
    renderTarget()
    expect(await screen.findByRole('heading', { level: 1, name: 'Head of Risk' })).toBeTruthy()
    expect(screen.getByText(/Target · real/)).toBeTruthy()
    expect(api.compareRole).not.toHaveBeenCalled()
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
