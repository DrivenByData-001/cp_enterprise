import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Vocabulary from './Vocabulary'
import { api, type Role, type VocabClusterSummary } from '../lib/api'

vi.mock('../lib/api', () => ({ api: {
  listConceptTypes: vi.fn(), getRole: vi.fn(), listVocabClusters: vi.fn(),
  getVocabProgress: vi.fn(), getVocabMethodology: vi.fn(), listConcepts: vi.fn(),
} }))
// Exercise the real queue refresh after a curation action without duplicating
// the accept/merge/split form tests here.
vi.mock('../components/vocabulary/ClusterActions', () => ({
  ClusterActionsPanel: ({ onChanged }: { onChanged: () => Promise<void> }) => <button onClick={onChanged}>Finish resolution</button>,
}))
afterEach(() => { cleanup(); vi.resetAllMocks() })
beforeEach(() => {
  vi.mocked(api.listConceptTypes).mockResolvedValue([])
  vi.mocked(api.getRole).mockResolvedValue({ id: 'aspida', title: 'Actuary, EBS Valuation', organisation: 'Aspida Re' } as Role)
  vi.mocked(api.listVocabClusters).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, status: 'pending', sort: 'priority' })
  vi.mocked(api.getVocabProgress).mockResolvedValue(null as never)
  vi.mocked(api.getVocabMethodology).mockResolvedValue(null as never)
  vi.mocked(api.listConcepts).mockResolvedValue([])
})
function open(url = '/vocabulary?role_id=aspida&application=app-1') {
  return render(<MemoryRouter initialEntries={[url]}><Vocabulary /></MemoryRouter>)
}

it('scopes the first request and filter changes to the role, with a contextual return link', async () => {
  open()
  await screen.findByText('Actuary, EBS Valuation · Aspida Re')
  await waitFor(() => expect(api.listVocabClusters).toHaveBeenCalled())
  expect(screen.getByText('Back to role requirements').getAttribute('href')).toBe('/role-instances/aspida/requirements?application=app-1')
  expect(api.getVocabProgress).not.toHaveBeenCalled()
  expect(api.listConcepts).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'Map' })).toBeNull()
  fireEvent.change(screen.getByPlaceholderText('Search surface form / label…'), { target: { value: 'SBA' } })
  await waitFor(() => expect(api.listVocabClusters).toHaveBeenLastCalledWith(expect.objectContaining({ role_id: 'aspida', status: 'pending', q: 'SBA' })))
  expect(vi.mocked(api.listVocabClusters).mock.calls.every(([filter]) => filter?.role_id === 'aspida')).toBe(true)
})

it('retains role scope when opening a single extracted term', async () => {
  open('/vocabulary?role_id=aspida&status=pending&q=US%20CFT')
  await waitFor(() => expect(api.listVocabClusters).toHaveBeenLastCalledWith(expect.objectContaining({ role_id: 'aspida', q: 'US CFT', status: 'pending' })))
  expect(vi.mocked(api.listVocabClusters).mock.calls.every(([filter]) => filter?.role_id === 'aspida')).toBe(true)
})

it('keeps the role scope after resolution and shows a return path when its queue is empty', async () => {
  vi.mocked(api.listVocabClusters).mockResolvedValueOnce({ items: [{ cluster_key: 'sba', suggested_canonical_label: 'Scenario-Based Approach', status: 'pending', surface_forms: ['sba'], priority_score: null, example_roles: [], flags: [] } as unknown as VocabClusterSummary], total: 1, limit: 20, offset: 0, status: 'pending', sort: 'priority' })
  open()
  fireEvent.click(await screen.findByText('Finish resolution'))
  await screen.findByText(/No pending vocabulary matches this role/)
  expect(api.listVocabClusters).toHaveBeenLastCalledWith(expect.objectContaining({ role_id: 'aspida' }))
  expect(screen.getByText('Back to role requirements')).toBeTruthy()
  expect(api.getVocabProgress).not.toHaveBeenCalled()
})

it('does not report an empty completed queue when loading fails', async () => {
  vi.mocked(api.listVocabClusters).mockRejectedValue(new Error('Role not found'))
  open()
  await screen.findByText('Error: Role not found')
  expect(screen.queryByText(/No pending vocabulary matches/)).toBeNull()
})

it('preserves the global vocabulary entry point', async () => {
  open('/vocabulary')
  await waitFor(() => expect(api.getVocabProgress).toHaveBeenCalled())
  expect(screen.getByRole('button', { name: 'Map' })).toBeTruthy()
  expect(api.listVocabClusters).toHaveBeenCalledWith(expect.objectContaining({ status: 'pending', role_id: undefined }))
  expect(screen.queryByText('Back to role requirements')).toBeNull()
})
