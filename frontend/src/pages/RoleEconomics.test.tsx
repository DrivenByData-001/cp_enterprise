import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RoleDetail from './RoleDetail'
import { api, type Role, type RoleCompensationResponse } from '../lib/api'

// Role/Target Detail's economic block (build §14). The rules these assert
// are the ones a user would be misled by if they broke: which basis a figure
// has, whether a personal comparison is valid, and that nothing calls the
// model just because the page opened.

vi.mock('../lib/api', () => ({
  api: {
    getRole: vi.fn(),
    getRoleContext: vi.fn(),
    getRoleCompensation: vi.fn(),
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

const baseRole = {
  id: 'role',
  node_type: 'posting',
  title: 'Head of Capital',
  organisation: 'An insurer',
  location: null,
  country: 'United Kingdom',
  remote_type: null,
  employment_type: null,
  posting_date: '2026-01-15',
  captured_at: null,
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
  extraction_status: null,
  extraction_notes: null,
  similarity: null,
  url: null,
  skills: [],
  legacy_skills: [],
} as Role

function compensation(overrides: Partial<RoleCompensationResponse> = {}): RoleCompensationResponse {
  return {
    role_instance_id: 'role',
    compensation: {
      basis: 'advert_stated',
      basis_label: 'Advert salary',
      component_label: 'base salary',
      supplementary: [],
      currency: 'GBP',
      amount_min: 120000,
      amount_reference: 132500,
      amount_max: 145000,
      component: 'base',
      pay_period: 'annual',
      employment_basis: null,
      market: { id: 'm1', label: 'United Kingdom' },
      period: null,
      as_of: '2026-01-15',
      archetype: null,
      evidence: { n_observations: 1, n_posting_stated: 1, n_survey_sources: 0 },
      reference_source: 'posting_stated',
      evidence_quality: 'good',
      reason: 'Compensation stated on this posting, reviewed against a verbatim quote from the source document.',
      trace: { tier: 1 },
    },
    personal_comparison: {
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
      difference_reference: 27500,
      difference_max: 40000,
      limitations: ['Base pay only — bonus, pension and benefits are not included on either side.'],
    },
    personal_earnings: {
      status: 'current',
      as_of: '2026-09-16',
      baselines: [],
      other_components: [],
      currencies: ['GBP'],
      planning_assumption: { contract_billable_days_per_year: null, note: null, updated_at: null },
      notes: [],
      fingerprint: 'fp',
    },
    observations: [
      {
        id: 'obs1',
        basis: 'posting_stated',
        review_status: 'accepted',
        component: 'base',
        pay_period: 'annual',
        employment_basis: null,
        currency: 'GBP',
        amount_min: 120000,
        amount_mid: null,
        amount_max: 145000,
        bonus_pct: null,
        evidence_span: '£120,000 - £145,000 per annum',
        observed_at: '2026-01-15',
        source_note: null,
        market_id: 'm1',
        market_label: 'United Kingdom',
        created_at: '2026-01-16',
        reviewed_at: '2026-01-16',
      },
    ],
    ...overrides,
  }
}

function renderRole(role: Role = baseRole) {
  vi.mocked(api.getRole).mockResolvedValue(role)
  vi.mocked(api.getRoleContext).mockResolvedValue({ role_instance_id: 'role', enrichment: null })
  return render(
    <MemoryRouter initialEntries={['/roles/role']}>
      <Routes>
        <Route path="/roles/:id" element={<RoleDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Role Detail — compensation', () => {
  it('labels an advert-stated figure as an advert salary', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole()

    expect(await screen.findByText('Advert salary')).toBeTruthy()
    expect(screen.getByText('£120,000 – £145,000')).toBeTruthy()
    expect(screen.queryByText('Market estimate')).toBeNull()
    expect(screen.queryByText('Legacy estimate')).toBeNull()
  })

  it('labels an archetype benchmark as a market estimate, never as an advert salary', async () => {
    const data = compensation()
    data.compensation = {
      ...data.compensation,
      basis: 'market_estimate',
      basis_label: 'Market estimate',
      archetype: { id: 'a1', name: 'Head of Capital archetype' },
      evidence_quality: 'moderate',
      reason: 'No compensation is stated on this role; using the archetype benchmark for United Kingdom.',
    }
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    expect(await screen.findByText('Market estimate')).toBeTruthy()
    expect(screen.queryByText('Advert salary')).toBeNull()
    expect(screen.getByText(/Archetype: Head of Capital archetype/)).toBeTruthy()
  })

  it('labels a legacy estimate as such and marks its evidence thin', async () => {
    const data = compensation()
    data.compensation = {
      ...data.compensation,
      basis: 'legacy_estimate',
      basis_label: 'Legacy estimate',
      evidence_quality: 'thin',
      reason: 'No stated compensation and no archetype benchmark available. This is the legacy estimate.',
    }
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    expect(await screen.findByText('Legacy estimate')).toBeTruthy()
    expect(screen.getByText(/Evidence quality:/)).toBeTruthy()
    expect(screen.getByText('Thin')).toBeTruthy()
  })

  it('shows insufficient evidence as its own state with a reason, not a number', async () => {
    const data = compensation()
    data.compensation = {
      ...data.compensation,
      basis: 'insufficient_evidence',
      basis_label: 'Insufficient compensation evidence',
      currency: null,
      amount_min: null,
      amount_reference: null,
      amount_max: null,
      evidence_quality: 'insufficient',
      reason: 'No compensation is stated on this role, and it has no reviewed archetype.',
    }
    data.personal_comparison = {
      comparable: false,
      reason: 'There is no compensation figure for this opportunity to compare against.',
      baseline: null,
      uses_planning_equivalent: false,
      difference_min: null,
      difference_reference: null,
      difference_max: null,
    }
    data.observations = []
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    expect(await screen.findByText('Insufficient evidence')).toBeTruthy()
    expect(screen.getByText(/no reviewed archetype/)).toBeTruthy()
    expect(screen.queryByText(/£0/)).toBeNull()
  })

  it('shows the personal comparison with its difference and limitations', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole()

    expect(await screen.findByText('Your comparison')).toBeTruthy()
    expect(screen.getByText('Current earnings')).toBeTruthy()
    expect(screen.getByText('£105,000')).toBeTruthy()
    expect(screen.getByText('+£15,000 to +£40,000')).toBeTruthy()
    expect(screen.getByText(/Base pay only/)).toBeTruthy()
  })

  it('explains an incomparable pair instead of showing a difference', async () => {
    const data = compensation()
    data.personal_comparison = {
      comparable: false,
      reason:
        'Your latest evidence is a contract day rate and this opportunity is an annual salary. Set a billable-days-per-year planning assumption to compare them; no annualisation is assumed.',
      baseline: null,
      uses_planning_equivalent: false,
      difference_min: null,
      difference_reference: null,
      difference_max: null,
    }
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    expect(await screen.findByText('Not comparable.')).toBeTruthy()
    expect(screen.getByText(/no annualisation is assumed/)).toBeTruthy()
    expect(screen.queryByText('Potential base-pay difference')).toBeNull()
  })

  it('flags a planning equivalent as a planning figure, never as salary', async () => {
    const data = compensation()
    data.personal_comparison = {
      ...data.personal_comparison,
      uses_planning_equivalent: true,
      baseline: {
        observation_id: 'o1',
        component: 'day_rate',
        amount: 139750,
        source_amount: 650,
        currency: 'GBP',
        unit: 'annual',
        employment_basis: 'contract',
        evidence_status: 'current',
        evidence_period: 'from 2025-04-01',
        label: 'Current earnings',
        planning_equivalent: {
          amount: 139750,
          currency: 'GBP',
          unit: 'annual',
          basis: 'planning_equivalent',
          assumption: { billable_days_per_year: 215, day_rate: 650, note: null },
          label: '650 x 215 billable days',
          caveat: 'This is a planning equivalent, not salary.',
        },
      },
    }
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    expect(await screen.findByText(/Current earnings \(planning equivalent\)/)).toBeTruthy()
    expect(screen.getByText(/This is a planning equivalent, not salary\./)).toBeTruthy()
  })

  it('lets the compensation evidence behind a figure be inspected', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    expect(screen.getByText(/“£120,000 - £145,000 per annum”/)).toBeTruthy()
  })

  it('makes no AI call merely by opening the page', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole()

    await screen.findByText('Advert salary')
    expect(api.proposeRoleCompensation).not.toHaveBeenCalled()
    expect(api.proposeRoleArchetype).not.toHaveBeenCalled()
  })

  it('extracts stated compensation only on an explicit click, and blocks an unacceptable item', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.proposeRoleCompensation).mockResolvedValue({
      status: 'ok',
      extraction_run_id: 'run',
      error: null,
      error_type: null,
      document_id: 'doc',
      provenance_quality: 'original',
      proposal: {
        no_compensation_stated: false,
        notes: null,
        items: [
          {
            amount_min: 99999,
            amount_max: null,
            bonus_pct: null,
            currency: 'GBP',
            component: 'base',
            pay_period: 'annual',
            employment_basis: null,
            evidence_span: 'a quote that is not in the advert',
            note: null,
            acceptable: false,
            problems: ['the quoted evidence span does not appear verbatim in the source document'],
          },
        ],
      },
    })
    renderRole()

    fireEvent.click(await screen.findByText('Extract stated compensation from source'))
    await screen.findByText(/does not appear verbatim/)
    const acceptButton = screen.getByText('Accept') as HTMLButtonElement
    expect(acceptButton.disabled).toBe(true)
  })
})

describe('Role Detail — archetype review', () => {
  it('shows an unclassified role as unclassified and offers a review affordance', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole({
      ...baseRole,
      archetype: {
        assigned: false,
        archetype_concept_id: null,
        archetype_name: null,
        seniority_band: null,
        typical_market: null,
        catalogue_size: 2,
        state: 'unclassified',
      },
    })

    expect(await screen.findByText(/Unclassified\./)).toBeTruthy()
    expect(screen.getByText('Review archetype')).toBeTruthy()
  })

  it('shows a reviewed assignment and offers to change it', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole({
      ...baseRole,
      archetype: {
        assigned: true,
        archetype_concept_id: 'a1',
        archetype_name: 'Senior Life Actuary',
        seniority_band: 'senior',
        typical_market: null,
        catalogue_size: 2,
        state: 'assigned',
      },
    })

    expect(await screen.findByText('Senior Life Actuary')).toBeTruthy()
    expect(screen.getByText('Change archetype')).toBeTruthy()
  })

  it('offers leave-unclassified as an explicit choice alongside the catalogue', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.getArchetypeCatalogue).mockResolvedValue([
      { id: 'a1', canonical_name: 'Senior Life Actuary', seniority_band: 'senior', typical_market: null, notes: null },
    ])
    vi.mocked(api.setRoleArchetype).mockResolvedValue({
      role_instance_id: 'role',
      archetype_concept_id: null,
      archetype_name: null,
      status: 'unclassified',
    })
    renderRole({
      ...baseRole,
      archetype: {
        assigned: true,
        archetype_concept_id: 'a1',
        archetype_name: 'Senior Life Actuary',
        seniority_band: 'senior',
        typical_market: null,
        catalogue_size: 1,
        state: 'assigned',
      },
    })

    fireEvent.click(await screen.findByText('Change archetype'))
    await screen.findByText('Leave unclassified')
    fireEvent.change(screen.getByLabelText('Choose another'), { target: { value: '' } })
    await waitFor(() => expect(api.setRoleArchetype).toHaveBeenCalledWith('role', null))
  })

  it('says archetypes are never created automatically when the catalogue is empty', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.getArchetypeCatalogue).mockResolvedValue([])
    renderRole({
      ...baseRole,
      archetype: {
        assigned: false,
        archetype_concept_id: null,
        archetype_name: null,
        seniority_band: null,
        typical_market: null,
        catalogue_size: 0,
        state: 'unclassified',
      },
    })

    fireEvent.click(await screen.findByText('Review archetype'))
    expect(await screen.findByText(/archetypes are never created automatically/)).toBeTruthy()
  })
})

describe('Role Detail — Pathways entry point', () => {
  it('offers "Explore path to this role" on a target', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole({ ...baseRole, node_type: 'target_real', id: 't1' })

    const link = (await screen.findByText('Explore path to this role')) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/pathways/t1')
  })

  it('explains that Pathways is built around a target when viewing a posting', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    renderRole()

    expect(await screen.findByText(/Pathways is built around a target/)).toBeTruthy()
  })
})

describe('Role Detail — review findings', () => {
  it('labels a total package as a package, never as a base salary', async () => {
    const data = compensation()
    data.compensation = {
      ...data.compensation,
      component: 'total_package',
      component_label: 'total package',
      reason: 'The total package stated on this posting, reviewed against a verbatim quote.',
    }
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    expect(await screen.findByText('Advert total package')).toBeTruthy()
    expect(screen.queryByText('Advert salary')).toBeNull()
  })

  it('labels a contract day rate as a day rate', async () => {
    const data = compensation()
    data.compensation = {
      ...data.compensation,
      component: 'day_rate',
      pay_period: 'daily',
      component_label: 'day rate',
      amount_min: 650,
      amount_max: 650,
      amount_reference: 650,
    }
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    expect(await screen.findByText('Advert day rate')).toBeTruthy()
  })

  it('reports a stated bonus alongside the headline, never as the headline', async () => {
    const data = compensation()
    data.compensation = {
      ...data.compensation,
      supplementary: [
        {
          observation_id: 'obs2',
          component: 'bonus_pct',
          pay_period: 'annual',
          label: 'bonus',
          amount_min: null,
          amount_max: null,
          bonus_pct: 15,
          currency: 'GBP',
          evidence_span: 'plus an annual bonus of up to 15%',
        },
      ],
    }
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    // The headline stays the base salary…
    expect(await screen.findByText('Advert salary')).toBeTruthy()
    expect(screen.getByText('£120,000 – £145,000')).toBeTruthy()
    // …and the bonus is reported as a percentage, not a currency amount.
    expect(screen.getByText('Also stated on this posting')).toBeTruthy()
    expect(screen.getByText(/bonus: 15%/)).toBeTruthy()
  })

  it('offers Edit & accept for a proposed figure', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.proposeRoleCompensation).mockResolvedValue({
      status: 'ok',
      extraction_run_id: 'run',
      error: null,
      error_type: null,
      document_id: 'doc',
      provenance_quality: 'original',
      proposal: {
        no_compensation_stated: false,
        notes: null,
        items: [
          {
            amount_min: 12000,
            amount_max: 14500,
            bonus_pct: null,
            currency: 'GBP',
            component: 'base',
            pay_period: 'annual',
            employment_basis: null,
            evidence_span: '£120,000 - £145,000 per annum',
            note: null,
            acceptable: true,
            problems: [],
          },
        ],
      },
    })
    vi.mocked(api.acceptRoleCompensation).mockResolvedValue({
      id: 'obs9',
      created: true,
      status: 'accepted',
      market_unassigned_reason: null,
      superseded_observation_ids: [],
    })
    renderRole()

    fireEvent.click(await screen.findByText('Extract stated compensation from source'))
    fireEvent.click(await screen.findByText('Edit & accept'))

    // A mis-scaled figure can be corrected before it becomes a stated fact.
    fireEvent.change(screen.getByLabelText('Minimum amount'), { target: { value: '120000' } })
    fireEvent.change(screen.getByLabelText('Maximum amount'), { target: { value: '145000' } })
    fireEvent.click(screen.getByText('Save & accept'))

    await waitFor(() =>
      expect(api.acceptRoleCompensation).toHaveBeenCalledWith(
        'role',
        expect.objectContaining({ amount_min: 120000, amount_max: 145000 }),
      ),
    )
  })

  it('corrects an accepted observation through the role-aware endpoint', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.correctRoleCompensation).mockResolvedValue({
      // A correction returns the *new* accepted observation; the corrected
      // one survives as retired history.
      id: 'obs2',
      status: 'corrected',
      review_status: 'accepted',
      corrected_from_observation_id: 'obs1',
      superseded_observation_ids: ['obs1'],
    })
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    fireEvent.click(screen.getByText('Correct'))
    fireEvent.change(screen.getByLabelText('Minimum amount for base'), { target: { value: '125000' } })
    fireEvent.click(screen.getByText('Save correction'))

    // The role-aware endpoint re-validates against the source document; the
    // generic market-data PATCH would not.
    await waitFor(() =>
      expect(api.correctRoleCompensation).toHaveBeenCalledWith('role', 'obs1', {
        amount_min: 125000,
        amount_max: 145000,
      }),
    )
  })

  it('lets a correction re-quote the passage that states the new figure', async () => {
    // A posting-stated figure has to be stated by the quote backing it, so a
    // correction to a number from elsewhere in the advert has to re-anchor.
    // Without an editable span the reviewer would get a refusal they could
    // not act on.
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.correctRoleCompensation).mockResolvedValue({
      id: 'obs2',
      status: 'corrected',
      review_status: 'accepted',
      corrected_from_observation_id: 'obs1',
      superseded_observation_ids: ['obs1'],
    })
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    fireEvent.click(screen.getByText('Correct'))

    const span = screen.getByLabelText('Evidence span for base') as HTMLTextAreaElement
    expect(span.value).toBe('£120,000 - £145,000 per annum')

    fireEvent.change(screen.getByLabelText('Minimum amount for base'), { target: { value: '125000' } })
    fireEvent.change(screen.getByLabelText('Maximum amount for base'), { target: { value: '150000' } })
    fireEvent.change(span, { target: { value: 'Exceptional candidates at £125,000 - £150,000' } })
    fireEvent.click(screen.getByText('Save correction'))

    await waitFor(() =>
      expect(api.correctRoleCompensation).toHaveBeenCalledWith('role', 'obs1', {
        amount_min: 125000,
        amount_max: 150000,
        evidence_span: 'Exceptional candidates at £125,000 - £150,000',
      }),
    )
  })

  it('surfaces the server refusal when a figure is not in its quote', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.correctRoleCompensation).mockRejectedValue(
      new Error('amount_min 999999 is not stated by the quoted evidence span, which states 120000, 145000'),
    )
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    fireEvent.click(screen.getByText('Correct'))
    fireEvent.change(screen.getByLabelText('Minimum amount for base'), { target: { value: '999999' } })
    fireEvent.click(screen.getByText('Save correction'))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('is not stated by the quoted evidence span')
  })

  it('edits a bonus as a percentage, not as min/max amounts', async () => {
    const data = compensation()
    data.observations = [
      {
        ...data.observations[0],
        id: 'obs-bonus',
        component: 'bonus_pct',
        amount_min: null,
        amount_max: null,
        bonus_pct: 15,
        evidence_span: 'plus an annual bonus of up to 15%',
      },
    ]
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    vi.mocked(api.correctRoleCompensation).mockResolvedValue({
      id: 'obs-bonus-2',
      status: 'corrected',
      review_status: 'accepted',
      corrected_from_observation_id: 'obs-bonus',
      superseded_observation_ids: ['obs-bonus'],
    })
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    fireEvent.click(screen.getByText('Correct'))

    expect(screen.queryByLabelText('Minimum amount for bonus_pct')).toBeNull()
    fireEvent.change(screen.getByLabelText('Bonus percentage for bonus_pct'), { target: { value: '20' } })
    fireEvent.click(screen.getByText('Save correction'))

    await waitFor(() =>
      expect(api.correctRoleCompensation).toHaveBeenCalledWith('role', 'obs-bonus', { bonus_pct: 20 }),
    )
  })

  it('rejects an accepted observation through the role-aware endpoint', async () => {
    vi.mocked(api.getRoleCompensation).mockResolvedValue(compensation())
    vi.mocked(api.rejectRoleCompensation).mockResolvedValue({
      id: 'obs1',
      status: 'rejected',
      review_status: 'rejected',
      superseded_observation_ids: [],
    })
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    fireEvent.click(screen.getByText('Reject'))

    await waitFor(() => expect(api.rejectRoleCompensation).toHaveBeenCalledWith('role', 'obs1'))
  })

  it('re-accepts through the superseding endpoint, not a bare status flip', async () => {
    const data = compensation()
    data.observations = [{ ...data.observations[0], review_status: 'rejected' }]
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    vi.mocked(api.reacceptRoleCompensation).mockResolvedValue({
      id: 'obs1',
      status: 'accepted',
      review_status: 'accepted',
      superseded_observation_ids: ['obs2'],
    })
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    fireEvent.click(screen.getByText('Re-accept'))

    await waitFor(() => expect(api.reacceptRoleCompensation).toHaveBeenCalledWith('role', 'obs1'))
  })

  it('offers no correction affordance for a survey row reviewed elsewhere', async () => {
    const data = compensation()
    data.observations = [{ ...data.observations[0], basis: 'survey' }]
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    expect(screen.queryByText('Correct')).toBeNull()
    expect(screen.queryByText('Reject')).toBeNull()
  })

  it('shows a retired observation as superseded rather than hiding it', async () => {
    const data = compensation()
    data.observations = [
      {
        ...data.observations[0],
        review_status: 'rejected',
        source_note: 'superseded by reviewed observation obs2',
      },
    ]
    vi.mocked(api.getRoleCompensation).mockResolvedValue(data)
    renderRole()

    fireEvent.click(await screen.findByText('Compensation evidence on this role (1)'))
    expect(screen.getByText(/superseded by reviewed observation obs2/)).toBeTruthy()
    expect(screen.getByText('Re-accept')).toBeTruthy()
  })
})
