import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RoleEdit from './RoleEdit'
import { api, type Role } from '../lib/api'

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
  },
}))

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
