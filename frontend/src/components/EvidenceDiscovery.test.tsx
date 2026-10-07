import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import EvidenceDiscovery from './EvidenceDiscovery'
import { req } from '../lib/api'

vi.mock('../lib/api', () => ({ req: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const finding = {
  id: 'f1', revision: 1, status: 'pending', stale: false, reviewed_payload: null,
  proposal: { rationale: 'Diagnosed a defect', limitations: 'Technical ownership only', question: 'Who reviewed the correction?', depth: 'owned', autonomy: null },
  requirement_snapshot: { canonical_name: 'Problem solving', evidence_span: 'Self-driven investigation' },
  source_snapshot: { claim: { claim_text: 'Corrected a calculation', evidence_class: 'stated', uncertainty: null },
    episode: { organisation: 'MetLife', title: 'Actuary', start_date: '2024-09-01', autonomy: 'Technical discretion' },
    evidence: [{ id: 'e1', passage: 'Recorded source passage', locator: 'source:1', document_title: 'Project notes', evidence_type: 'documented', notes: null }] },
}
function setup(overrides = {}) {
  vi.mocked(req).mockResolvedValue({ run: { status: 'complete',total_sources: 308,model: 'gpt-5.4-mini' },findings: [{ ...finding,...overrides }] })
  const onChanged = vi.fn().mockResolvedValue(undefined)
  render(<EvidenceDiscovery applicationId="a1" onChanged={onChanged} />)
  return onChanged
}
it('requires review and sends edited rationale and clarification with revision', async () => {
  const changed = setup()
  const approve = await screen.findByRole('button',{ name: 'Approve finding' }) as HTMLButtonElement
  expect(approve.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('Why this supports the requirement'),{target:{value:'Reviewed diagnosis'}})
  fireEvent.change(screen.getByLabelText(/Your clarification/),{target:{value:'Reviewed by my manager'}})
  fireEvent.click(screen.getByLabelText(/I have reviewed/))
  fireEvent.click(approve)
  await waitFor(() => expect(changed).toHaveBeenCalled())
  const call = vi.mocked(req).mock.calls.find(([,init]) => init?.method === 'POST')!
  expect(call[0]).toBe('/applications/a1/evidence-discovery/f1/review')
  expect(JSON.parse(call[1]!.body as string)).toMatchObject({ action:'approve',revision:1,rationale:'Reviewed diagnosis',clarification:'Reviewed by my manager' })
})
it('blocks approval of stale findings but permits rejection', async () => {
  setup({ stale: true })
  const approve = await screen.findByRole('button',{name:'Approve finding'}) as HTMLButtonElement
  fireEvent.click(screen.getByLabelText(/I have reviewed/))
  expect(approve.disabled).toBe(true)
  expect((screen.getByRole('button',{name:'Reject finding'}) as HTMLButtonElement).disabled).toBe(false)
})
it('saves edits without approving', async () => {
  setup()
  fireEvent.click(await screen.findByRole('button',{name:'Save edits for later'}))
  await waitFor(() => expect(vi.mocked(req).mock.calls.some(([,init]) => init?.method === 'POST')).toBe(true))
  const call = vi.mocked(req).mock.calls.find(([,init]) => init?.method === 'POST')!
  expect(JSON.parse(call[1]!.body as string).action).toBe('edit')
})
it('shows failure rather than claiming no evidence exists', async () => {
  vi.mocked(req).mockResolvedValue({run:{status:'failed',error:'Provider unavailable'},findings:[]})
  render(<EvidenceDiscovery applicationId="a1" onChanged={vi.fn()} />)
  expect(await screen.findByText(/Search failed: Provider unavailable/)).toBeTruthy()
})
