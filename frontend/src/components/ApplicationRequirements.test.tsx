import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import ApplicationRequirements from './ApplicationRequirements'
import { api, type RequirementClaim } from '../lib/api'
import { applicationProcess, type ProcessData } from '../lib/applicationProcess'
vi.mock('../lib/api', () => ({ api: { listRequirements: vi.fn(), extractRequirements: vi.fn() } }))
vi.mock('../lib/applicationProcess', async original => ({ ...await original<typeof import('../lib/applicationProcess')>(), applicationProcess: { reconcileRequirements: vi.fn(), proposals: vi.fn(), confirmRequirements: vi.fn() } }))
vi.mock('../pages/RoleRequirements', () => ({ RequirementCard: ({ claim }: { claim: RequirementClaim }) => <article>{claim.canonical_name}</article>, AddRequirementForm: () => null, ConceptPicker: () => null }))
const claim = { id: 'r', concept_id: 'c', canonical_name: 'Task management', review_status: 'accepted' } as RequirementClaim
const data = { role: { id: 'role', legacy_skills: [{ name: 'Duplicate task signal', resolved_concept_id: 'c' }] }, stages: { requirements: 'in_progress' }, review_summary: { complete: true, unreviewed: 0 }, requirements_fingerprint: 'old', legacy_requirement_count: 10 } as unknown as ProcessData
function open() { render(<ApplicationRequirements id="app" data={data} register={() => {}} reload={async () => {}} />) }
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear()
  vi.mocked(applicationProcess.reconcileRequirements).mockResolvedValue({ claims_created: 0, process: { ...data, requirements_fingerprint: 'reconciled' } })
  vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim] } as never)
  vi.mocked(applicationProcess.proposals).mockResolvedValue({ items: [], missing_requirements: [] })
})
afterEach(cleanup)
it('reconciles on entry, hides duplicate older signals, and confirms the reviewed fingerprint', async () => {
  open()
  const button = await screen.findByRole('button', { name: 'Confirm requirement set' }) as HTMLButtonElement
  await waitFor(() => expect(button.disabled).toBe(false))
  expect(applicationProcess.reconcileRequirements).toHaveBeenCalledWith('app')
  expect(api.extractRequirements).not.toHaveBeenCalled()
  expect(screen.queryByText(/legacy requirement signals/)).toBeNull()
  expect(screen.queryByText('Duplicate task signal')).toBeNull()
  fireEvent.click(button)
  await waitFor(() => expect(applicationProcess.confirmRequirements).toHaveBeenCalledWith('app', 'reconciled'))
})
it('names automatically restored requirements and keeps confirmation disabled until review', async () => {
  vi.mocked(applicationProcess.reconcileRequirements).mockResolvedValue({ claims_created: 1, process: { ...data, review_summary: { ...data.review_summary, complete: false, unreviewed: 1 } } })
  vi.mocked(api.listRequirements).mockResolvedValue({ items: [{ ...claim, review_status: 'unreviewed' }] } as never)
  open()
  await screen.findByText(/Review “Task management”/)
  expect((screen.getByRole('button', { name: 'Confirm requirement set' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText(/requirements restored from the original extraction/)).toBeTruthy()
  expect(api.extractRequirements).not.toHaveBeenCalled()
})
it('shows the exact missing source term without a generic extraction warning', async () => {
  vi.mocked(applicationProcess.proposals).mockResolvedValue({ items: [], missing_requirements: [{ surface_form: 'managing multiple tasks', recoverable: false }] })
  open()
  await screen.findByText(/“managing multiple tasks”/)
  expect((screen.getByRole('button', { name: 'Confirm requirement set' }) as HTMLButtonElement).disabled).toBe(true)
})
it('shows a readable error and blocks confirmation when reconciliation fails', async () => {
  vi.mocked(applicationProcess.reconcileRequirements).mockRejectedValue(new Error('409 : {"detail":"The posting changed. Reload requirements."}'))
  open()
  expect((await screen.findByRole('alert')).textContent).toContain('The posting changed. Reload requirements.')
  expect(screen.getByRole('alert').textContent).not.toContain('409')
  expect(api.listRequirements).not.toHaveBeenCalled()
})
