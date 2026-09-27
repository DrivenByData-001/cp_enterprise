import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RoleEdit from './RoleEdit'
import { api, type Role, type RoleCompensationResponse } from '../lib/api'

// Source-aware ingest cleanup, problem #7: a role with no `raw_json` must
// never crash/blank the Edit page (RoleEdit.tsx used to do
// `JSON.stringify(r.raw_json, null, 2)` unconditionally — `undefined` in,
// `undefined` out, and the disabled-button check `!text.trim()` then threw
// on render). This covers both branches: no raw_json -> metadata editor,
// real raw_json -> the legacy JSON overwrite editor keeps working.

vi.mock('../lib/api', () => ({
  api: {
    getRole: vi.fn(),
    updateRole: vi.fn(),
    updateTarget: vi.fn(),
    updateRoleMetadata: vi.fn(),
    getRoleCompensation: vi.fn(),
    acceptManualRoleCompensation: vi.fn(),
    correctManualRoleCompensation: vi.fn(),
    rejectRoleCompensation: vi.fn(),
    resolveTargetRequirements: vi.fn(),
  },
}))

// A posting always renders ManualCompensationSection, which fetches
// observations on mount — give every test a harmless empty default so
// tests unrelated to compensation don't each have to know about it.
beforeEach(() => {
  vi.mocked(api.getRoleCompensation).mockResolvedValue({
    role_instance_id: 'role-1',
    compensation: { basis: 'insufficient_evidence' },
    personal_comparison: { comparable: false },
    personal_earnings: {},
    observations: [],
  } as unknown as RoleCompensationResponse)
})

function baseRole(overrides: Partial<Role> = {}): Role {
  return {
    id: 'role-1',
    node_type: 'posting',
    title: 'Life Actuarial Manager',
    organisation: null,
    location: null,
    country: null,
    remote_type: null,
    employment_type: null,
    posting_date: null,
    captured_at: null,
    career_track: null,
    seniority_level: null,
    salary_min: null,
    salary_max: null,
    currency: null,
    summary: null,
    description: null,
    requirements: null,
    responsibilities: null,
    key_skills_summary: null,
    top_adjacent_roles: null,
    extraction_status: null,
    extraction_notes: null,
    similarity: null,
    url: null,
    ...overrides,
  }
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/roles/:id/edit" element={<RoleEdit />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('RoleEdit — source-aware role with no raw_json', () => {
  it('does not crash and renders a metadata editor instead of the JSON textarea', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))

    renderAt('/roles/role-1/edit')

    await waitFor(() => expect(screen.getByRole('heading', { name: /Life Actuarial Manager/ })).toBeTruthy())
    // The metadata form's labelled fields are present…
    expect(screen.getByText('Employer / Organisation')).toBeTruthy()
    expect(screen.getByText('Posting date')).toBeTruthy()
    // …and the legacy full-JSON textarea is not.
    expect(screen.queryByText(/paste the complete object/i)).toBeFalsy()
  })

  it('saves edited fields through the metadata PATCH endpoint, not the legacy JSON PUT', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined, organisation: null }))
    vi.mocked(api.updateRoleMetadata).mockResolvedValue(baseRole({ organisation: 'Forvis Mazars Ireland' }))

    renderAt('/roles/role-1/edit')
    await waitFor(() => expect(screen.getByText('Employer / Organisation')).toBeTruthy())

    const orgInput = screen.getByText('Employer / Organisation').querySelector('input')!
    fireEvent.change(orgInput, { target: { value: 'Forvis Mazars Ireland' } })
    fireEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(api.updateRoleMetadata).toHaveBeenCalledWith('role-1', expect.objectContaining({ organisation: 'Forvis Mazars Ireland' })))
    expect(api.updateRole).not.toHaveBeenCalled()
  })

  it('never substitutes a posting date the user did not enter', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined, posting_date: null }))
    vi.mocked(api.updateRoleMetadata).mockResolvedValue(baseRole())

    renderAt('/roles/role-1/edit')
    await waitFor(() => expect(screen.getByText('Employer / Organisation')).toBeTruthy())
    fireEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(api.updateRoleMetadata).toHaveBeenCalled())
    expect(api.updateRoleMetadata).toHaveBeenCalledWith('role-1', expect.objectContaining({ posting_date: null }))
  })
})

describe('RoleEdit — legacy role with real raw_json', () => {
  it('keeps the full-JSON overwrite editor working', async () => {
    const raw = { job: { title: 'Legacy posting' } }
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: raw }))
    vi.mocked(api.updateRole).mockResolvedValue({ id: 'role-1', status: 'updated' })

    renderAt('/roles/role-1/edit')
    await waitFor(() => expect(screen.getByText(/paste the complete object/i)).toBeTruthy())

    fireEvent.click(screen.getByText('Save changes'))
    await waitFor(() => expect(api.updateRole).toHaveBeenCalledWith('role-1', raw))
    expect(api.updateRoleMetadata).not.toHaveBeenCalled()
  })
})

describe('RoleEdit — Add/update compensation (Phase 3 addendum)', () => {
  it('renders for a posting and submits a new manual figure', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))
    vi.mocked(api.acceptManualRoleCompensation).mockResolvedValue({
      id: 'obs-1', created: true, status: 'accepted', market_id: 'market-1', market_unassigned_reason: null,
      basis: 'posting_stated', review_status: 'accepted', superseded_observation_ids: [],
    })

    renderAt('/roles/role-1/edit')
    await screen.findByText('Add/update compensation')

    fireEvent.change(screen.getByLabelText('Minimum amount'), { target: { value: '120000' } })
    fireEvent.change(screen.getByLabelText('Maximum amount'), { target: { value: '145000' } })
    fireEvent.change(screen.getByLabelText('Currency'), { target: { value: 'gbp' } })
    fireEvent.change(screen.getByLabelText('Source or evidence note'), { target: { value: 'Told by recruiter on a call.' } })
    fireEvent.click(screen.getByText('Add compensation'))

    await waitFor(() =>
      expect(api.acceptManualRoleCompensation).toHaveBeenCalledWith('role-1', {
        component: 'base', pay_period: 'annual', employment_basis: null, currency: 'GBP',
        amount_min: 120000, amount_max: 145000, bonus_pct: null, note: 'Told by recruiter on a call.',
      }),
    )
  })

  it('never writes to the legacy salary fields — only calls the manual compensation endpoint', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))
    vi.mocked(api.acceptManualRoleCompensation).mockResolvedValue({
      id: 'obs-1', created: true, status: 'accepted', market_id: null, market_unassigned_reason: 'x',
      basis: 'posting_stated', review_status: 'accepted', superseded_observation_ids: [],
    })

    renderAt('/roles/role-1/edit')
    await screen.findByText('Add/update compensation')
    fireEvent.change(screen.getByLabelText('Minimum amount'), { target: { value: '100000' } })
    fireEvent.click(screen.getByText('Add compensation'))

    await waitFor(() => expect(api.acceptManualRoleCompensation).toHaveBeenCalled())
    expect(api.updateRoleMetadata).not.toHaveBeenCalled()
    expect(api.updateRole).not.toHaveBeenCalled()
  })

  it('shows a bonus % field instead of amount fields for the bonus component', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))
    renderAt('/roles/role-1/edit')
    await screen.findByText('Add/update compensation')

    fireEvent.change(screen.getByLabelText('Component'), { target: { value: 'bonus_pct' } })
    expect(screen.getByLabelText('Bonus percentage')).toBeTruthy()
    expect(screen.queryByLabelText('Minimum amount')).toBeNull()
  })

  it('lists existing manual (quote-less) observations with Edit/Reject, and separates source-quoted ones', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))
    vi.mocked(api.getRoleCompensation).mockResolvedValue({
      role_instance_id: 'role-1',
      compensation: { basis: 'advert_stated' },
      personal_comparison: { comparable: false },
      personal_earnings: {},
      observations: [
        {
          id: 'manual-1', basis: 'posting_stated', review_status: 'accepted', component: 'base', pay_period: 'annual',
          employment_basis: null, currency: 'GBP', amount_min: 120000, amount_mid: null, amount_max: 145000,
          bonus_pct: null, evidence_span: null, observed_at: '2026-01-01', source_note: 'Recruiter call.',
          market_id: null, market_label: null, created_at: '2026-01-01T00:00:00Z', reviewed_at: '2026-01-01T00:00:00Z',
        },
        {
          id: 'quoted-1', basis: 'posting_stated', review_status: 'accepted', component: 'base', pay_period: 'annual',
          employment_basis: null, currency: 'GBP', amount_min: 100000, amount_mid: null, amount_max: 110000,
          bonus_pct: null, evidence_span: '£100,000 - £110,000 per annum', observed_at: '2026-01-01', source_note: null,
          market_id: null, market_label: null, created_at: '2026-01-01T00:00:00Z', reviewed_at: '2026-01-01T00:00:00Z',
        },
      ],
    } as unknown as RoleCompensationResponse)

    renderAt('/roles/role-1/edit')
    await screen.findByText('Recruiter call.')
    expect(screen.getByText('Edit')).toBeTruthy()
    expect(screen.getByText('Reject')).toBeTruthy()
    expect(screen.getByText(/1 source-quoted compensation figure/)).toBeTruthy()
    // The quoted observation's own amounts are not shown as an editable row.
    expect(screen.queryByText('100000 – 110000 GBP')).toBeNull()
  })

  it('edits an existing manual observation via the correction endpoint', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))
    vi.mocked(api.getRoleCompensation).mockResolvedValue({
      role_instance_id: 'role-1',
      compensation: { basis: 'advert_stated' },
      personal_comparison: { comparable: false },
      personal_earnings: {},
      observations: [
        {
          id: 'manual-1', basis: 'posting_stated', review_status: 'accepted', component: 'base', pay_period: 'annual',
          employment_basis: null, currency: 'GBP', amount_min: 120000, amount_mid: null, amount_max: 145000,
          bonus_pct: null, evidence_span: null, observed_at: '2026-01-01', source_note: 'Recruiter call.',
          market_id: null, market_label: null, created_at: '2026-01-01T00:00:00Z', reviewed_at: '2026-01-01T00:00:00Z',
        },
      ],
    } as unknown as RoleCompensationResponse)
    vi.mocked(api.correctManualRoleCompensation).mockResolvedValue({
      id: 'manual-2', status: 'corrected', market_id: null, market_unassigned_reason: null,
      basis: 'posting_stated', review_status: 'accepted', superseded_observation_ids: ['manual-1'],
      corrected_from_observation_id: 'manual-1',
    })

    renderAt('/roles/role-1/edit')
    await screen.findByText('Recruiter call.')
    fireEvent.click(screen.getByText('Edit'))
    expect(screen.getByText('Save correction')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Maximum amount'), { target: { value: '150000' } })
    fireEvent.click(screen.getByText('Save correction'))

    await waitFor(() =>
      expect(api.correctManualRoleCompensation).toHaveBeenCalledWith('role-1', 'manual-1', expect.objectContaining({
        amount_min: 120000, amount_max: 150000, note: 'Recruiter call.',
      })),
    )
  })

  it('rejects an existing manual observation', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))
    vi.mocked(api.getRoleCompensation).mockResolvedValue({
      role_instance_id: 'role-1',
      compensation: { basis: 'advert_stated' },
      personal_comparison: { comparable: false },
      personal_earnings: {},
      observations: [
        {
          id: 'manual-1', basis: 'posting_stated', review_status: 'accepted', component: 'base', pay_period: 'annual',
          employment_basis: null, currency: 'GBP', amount_min: 120000, amount_mid: null, amount_max: 145000,
          bonus_pct: null, evidence_span: null, observed_at: '2026-01-01', source_note: null,
          market_id: null, market_label: null, created_at: '2026-01-01T00:00:00Z', reviewed_at: '2026-01-01T00:00:00Z',
        },
      ],
    } as unknown as RoleCompensationResponse)
    vi.mocked(api.rejectRoleCompensation).mockResolvedValue({
      id: 'manual-1', status: 'rejected', review_status: 'rejected', superseded_observation_ids: [],
    })

    renderAt('/roles/role-1/edit')
    await screen.findByRole('button', { name: 'Reject' })
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))

    await waitFor(() => expect(api.rejectRoleCompensation).toHaveBeenCalledWith('role-1', 'manual-1'))
  })

  it('shows a focused error without crashing the rest of the page on a validation failure', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ raw_json: undefined }))
    vi.mocked(api.acceptManualRoleCompensation).mockRejectedValue(new Error('400: amount_min cannot be greater than amount_max'))

    renderAt('/roles/role-1/edit')
    await screen.findByText('Add/update compensation')
    fireEvent.change(screen.getByLabelText('Minimum amount'), { target: { value: '150000' } })
    fireEvent.change(screen.getByLabelText('Maximum amount'), { target: { value: '100000' } })
    fireEvent.click(screen.getByText('Add compensation'))

    expect(await screen.findByText(/amount_min cannot be greater than amount_max/)).toBeTruthy()
    expect(screen.getByText('Add/update compensation')).toBeTruthy()
  })

  it('does not render for a target', async () => {
    vi.mocked(api.getRole).mockResolvedValue(baseRole({ node_type: 'target_real', raw_json: null }))
    renderAt('/roles/role-1/edit')
    await waitFor(() => expect(screen.getByLabelText('Target title')).toBeTruthy())
    expect(screen.queryByText('Add/update compensation')).toBeNull()
    expect(api.getRoleCompensation).not.toHaveBeenCalled()
  })
})
