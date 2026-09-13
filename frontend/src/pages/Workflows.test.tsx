import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import Dashboard from './Dashboard'
import Import from './Import'
import AddTarget from './AddTarget'
import RoleRequirements from './RoleRequirements'
import ComparisonActions from '../components/ComparisonActions'
import { api, type Role, type RoleListResponse, type TargetDraft, type ComparisonItem } from '../lib/api'

vi.mock('../lib/api', () => ({ api: {
  listRoles: vi.fn(), getFacets: vi.fn(), checkDuplicate: vi.fn(), ingestText: vi.fn(),
  extractPdfText: vi.fn(), importBulk: vi.fn(), importPostingNative: vi.fn(),
  previewTarget: vi.fn(), importTarget: vi.fn(), assertCapability: vi.fn(), retractAssertion: vi.fn(),
  promoteAssertion: vi.fn(), createDevelopmentAction: vi.fn(), updateDevelopmentAction: vi.fn(),
  deleteDevelopmentAction: vi.fn(), getRole: vi.fn(), listRequirements: vi.fn(),
  reviewRequirement: vi.fn(), proposeRoleMetadata: vi.fn(), updateRoleMetadata: vi.fn(),
} }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
function Location() { return <output aria-label="Location">{useLocation().pathname + useLocation().search}</output> }
const list = (title?: string): RoleListResponse => ({ items: title ? [{ id: title, title, similarity: null } as Role] : [], total: title ? 1 : 0, offset: 0, limit: 20, period: 'all', year_range: { min: 2008, max: 2026 } })
const wrap = (element: React.ReactNode, url = '/') => render(<MemoryRouter initialEntries={[url]}>{element}<Location /></MemoryRouter>)
const draft: TargetDraft = { metadata: {}, target: { title: 'Risk lead', is_imagined: true, typical_tasks: ['Review risk'], skill_decomposition: [], technical_subjects: [] }, skills: [] }

describe('Dashboard recovery and navigation', () => {
  it('clears the concept when switching facet category and preserves URL filters', async () => {
    vi.mocked(api.listRoles).mockResolvedValue(list())
    vi.mocked(api.getFacets).mockResolvedValue([{ id: 'python', canonical_name: 'Python', role_count: 1 }])
    wrap(<Dashboard />, '/?facet=tool&concept=python&offset=20&period=all')
    await waitFor(() => expect(api.listRoles).toHaveBeenCalledWith(expect.objectContaining({ concept_id: 'python', offset: 20 })))
    fireEvent.change(screen.getByLabelText('Filter category'), { target: { value: 'domain' } })
    await waitFor(() => expect(api.listRoles).toHaveBeenLastCalledWith(expect.objectContaining({ concept_id: undefined, offset: 0 })))
    expect(screen.getByLabelText('Location').textContent).not.toContain('concept=')
    expect(screen.getByText('No roles captured yet.')).toBeTruthy()
  })
  it('ignores older responses arriving after the current filter response', async () => {
    let finish!: (r: RoleListResponse) => void
    vi.mocked(api.listRoles).mockImplementationOnce(() => new Promise(resolve => { finish = resolve })).mockResolvedValue(list('Current result'))
    wrap(<Dashboard />)
    fireEvent.change(screen.getByLabelText('Career track'), { target: { value: 'risk' } })
    await screen.findByText('Current result')
    await act(async () => finish(list('Obsolete result')))
    expect(screen.queryByText('Obsolete result')).toBeNull()
    expect(screen.getByText('Current result')).toBeTruthy()
  })
  it('distinguishes no matches and clears errors on successful retry', async () => {
    vi.mocked(api.listRoles).mockRejectedValueOnce(new Error('Network failed')).mockResolvedValue(list())
    wrap(<Dashboard />)
    await screen.findByText('Error: Network failed')
    fireEvent.click(screen.getByText('Retry loading roles'))
    await screen.findByText('No roles match these filters.')
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(screen.getByText('Clear filters and show all years'))
    await screen.findByText('No roles captured yet.')
  })
})

describe('Import preview and retry', () => {
  it('previews PDF text without capturing or checking duplicates until confirmation', async () => {
    vi.mocked(api.extractPdfText).mockResolvedValue({ text: 'Extracted advert' })
    vi.mocked(api.checkDuplicate).mockResolvedValue({ exact_duplicate: null, possible_duplicate: null })
    vi.mocked(api.ingestText).mockResolvedValue({ id: 'role', document_id: 'doc', duplicate_of_document_id: null, duplicate: { exact_duplicate: null, possible_duplicate: null }, status: 'ingested' })
    wrap(<Import />)
    fireEvent.change(screen.getByLabelText('Upload a PDF'), { target: { files: [new File(['pdf'], 'role.pdf', { type: 'application/pdf' })] } })
    await screen.findByDisplayValue('Extracted advert')
    expect(api.ingestText).not.toHaveBeenCalled()
    expect(api.checkDuplicate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Capture text'))
    await waitFor(() => expect(api.ingestText).toHaveBeenCalledWith(expect.objectContaining({ text: 'Extracted advert', source: 'pdf' })))
    await waitFor(() => expect(screen.getByLabelText('Location').textContent).toBe('/role-instances/role/requirements?step=details'))
  })
  it('shows per-file failures and retries only the failed File object', async () => {
    const first = new File(['{}'], 'same.json'), second = new File(['{}'], 'same.json')
    vi.mocked(api.importBulk).mockResolvedValueOnce({ results: [{ file: 'same.json', id: 'ok', status: 'imported' }, { file: 'same.json', status: 'error', error: 'Title missing' }] })
      .mockResolvedValueOnce({ results: [{ file: 'same.json', id: 'retry', status: 'imported' }] })
    wrap(<Import />)
    fireEvent.click(screen.getByText('Advanced imports'))
    fireEvent.change(screen.getByLabelText('JSON files'), { target: { files: [first, second] } })
    await screen.findByText(/Title missing/)
    fireEvent.click(screen.getByText('Retry failed files only (1)'))
    await waitFor(() => expect(api.importBulk).toHaveBeenLastCalledWith([second]))
    await waitFor(() => expect(screen.queryByText(/Title missing/)).toBeNull())
    expect(screen.getAllByText('Open role')).toHaveLength(2)
  })
})

describe('Target creation', () => {
  it('keeps AI drafts editable and does not import until Save target', async () => {
    vi.mocked(api.previewTarget).mockResolvedValue({ status: 'ok', proposal: draft, error: null, extraction_run_id: 'run' })
    vi.mocked(api.importTarget).mockResolvedValue({ id: 'target', status: 'imported' })
    wrap(<AddTarget />)
    fireEvent.change(screen.getByLabelText('Target title'), { target: { value: 'Risk lead' } })
    fireEvent.click(screen.getByText('Generate a draft with AI'))
    await screen.findByText('Review and edit your target')
    expect(api.importTarget).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Target title'), { target: { value: 'Edited title' } })
    fireEvent.click(screen.getByText('Save target'))
    await waitFor(() => expect(api.importTarget).toHaveBeenCalledWith(expect.objectContaining({ target: expect.objectContaining({ title: 'Edited title' }) })))
  })
  it('supports manual creation after AI failure without losing the description', async () => {
    vi.mocked(api.previewTarget).mockRejectedValue(new Error('AI unavailable'))
    wrap(<AddTarget />)
    fireEvent.change(screen.getByLabelText('Target title'), { target: { value: 'Risk lead' } })
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'My description' } })
    fireEvent.click(screen.getByText('Generate a draft with AI'))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByText('Continue manually'))
    expect((screen.getByLabelText('Description') as HTMLTextAreaElement).value).toBe('My description')
  })
})

describe('Requirement review', () => {
  it('shows failed acceptance and allows retry without losing the claim', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue([{ id: 'claim', canonical_name: 'Python', concept_id: 'python', type_code: 'tool', requirement_type: 'required', basis: 'stated', review_status: 'unreviewed', evidence_span: 'Python', importance: 3 } as Awaited<ReturnType<typeof api.listRequirements>>[number]])
    vi.mocked(api.reviewRequirement).mockRejectedValueOnce(new Error('Review failed')).mockResolvedValueOnce({ id: 'claim', review_status: 'accepted' })
    render(<MemoryRouter initialEntries={['/role-instances/role/requirements']}><Routes><Route path="/role-instances/:id/requirements" element={<RoleRequirements />} /></Routes></MemoryRouter>)
    fireEvent.click(await screen.findByText('Accept'))
    await screen.findByText('Review failed')
    fireEvent.click(screen.getByText('Accept'))
    await screen.findByText('accepted')
    expect(api.reviewRequirement).toHaveBeenCalledTimes(2)
  })
})

describe('Comparison next steps', () => {
  const item = { concept: { id: 'python', canonical_name: 'Python', type_code: 'tool' }, status: 'not_found', person_side: { assertion: null } } as ComparisonItem
  it('preserves the example after an assertion failure and saves it on retry', async () => {
    vi.mocked(api.assertCapability).mockRejectedValueOnce(new Error('Save failed')).mockResolvedValueOnce({ id: 'a', status: 'asserted' })
    const changed = vi.fn().mockResolvedValue(undefined)
    wrap(<ComparisonActions item={item} roleId="role" actions={[]} onChanged={changed} />)
    fireEvent.click(screen.getByText('Record an example or plan an action'))
    fireEvent.change(screen.getByLabelText('Example or notes'), { target: { value: 'Built a pricing model' } })
    fireEvent.click(screen.getByText('I have done this'))
    await screen.findByText('Save failed')
    expect((screen.getByLabelText('Example or notes') as HTMLTextAreaElement).value).toBe('Built a pricing model')
    fireEvent.click(screen.getByText('I have done this'))
    await waitFor(() => expect(changed).toHaveBeenCalledOnce())
    expect(api.assertCapability).toHaveBeenLastCalledWith('python', 'Built a pricing model')
  })
  it('can retract assertions and saves development actions separately', async () => {
    vi.mocked(api.retractAssertion).mockResolvedValue({ status: 'retracted' })
    vi.mocked(api.createDevelopmentAction).mockResolvedValue({ id: 'a', concept_id: 'python', role_instance_id: 'role', title: 'Build project', note: '', due_date: null, status: 'open' })
    wrap(<ComparisonActions item={{ ...item, status: 'user_asserted' }} roleId="role" actions={[]} onChanged={vi.fn().mockResolvedValue(undefined)} />)
    fireEvent.click(screen.getByText('Record an example or plan an action'))
    fireEvent.click(screen.getByText('Undo assertion'))
    await waitFor(() => expect(api.retractAssertion).toHaveBeenCalledWith('python'))
    await screen.findByText('Assertion removed from all role comparisons.')
    fireEvent.change(screen.getByLabelText('Development action'), { target: { value: 'Build project' } })
    fireEvent.click(screen.getByText('Save development action'))
    await waitFor(() => expect(api.createDevelopmentAction).toHaveBeenCalledWith('role', expect.objectContaining({ title: 'Build project' })))
    expect(api.assertCapability).not.toHaveBeenCalled()
  })
})
