import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ApplicationMode from './ApplicationMode'
import { applicationMode } from '../lib/applicationMode'
import { applicationProcess, type ProcessData } from '../lib/applicationProcess'

vi.mock('../lib/applicationMode', () => ({ applicationMode: { save: vi.fn(), search: vi.fn() } }))
vi.mock('../components/EvidenceDiscovery', () => ({ default: () => null }))
vi.mock('../lib/applicationProcess', async importOriginal => ({
  ...await importOriginal<typeof import('../lib/applicationProcess')>(),
  applicationProcess: { get: vi.fn(), resume: vi.fn() },
}))

const decision = { disposition: 'partial' as const, selected_refs: [], rationale: 'Existing assessment', revision: 1 }

function openReview(saved = false, stale = false) {
  const data = {
    application: { id: 'app', status: 'preparing' }, role: { title: 'Actuary' },
    preparation: { revision: 0, deadline: null, package_items: [] },
    stages: { opportunity: 'complete', requirements: 'complete', evidence: 'in_progress' },
    resume: { stage: 'evidence', concept_id: 'concept' }, next_stage: 'evidence',
    items: [{ concept: { id: 'concept', canonical_name: 'Analysis', type_code: 'capability' },
      role_side: { requirement_type: 'required' }, sources: [], decision: saved ? decision : null,
      stale, attention_reason: stale ? 'Sources changed' : null, requirement_fingerprint: 'fp' }],
  } as unknown as ProcessData
  vi.mocked(applicationProcess.get).mockResolvedValue(data)
  vi.mocked(applicationProcess.resume).mockResolvedValue({})
  vi.mocked(applicationMode.save).mockResolvedValue(decision)
  render(<MemoryRouter initialEntries={['/applications/app/prepare/evidence/concept']}><Routes>
    <Route path="/applications/:id/prepare/:stage/:entity?" element={<ApplicationMode />} />
    <Route path="/applications" element={<h1>Applications list</h1>} />
    <Route path="/applications/:id" element={<h1>Application workspace</h1>} />
  </Routes></MemoryRouter>)
}

beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear() })
afterEach(cleanup)

describe('Leaving an evidence review', () => {
  it.each([[false, false], [true, false], [true, true]])(
    'exits an untouched review without writing evidence (saved=%s, stale=%s)', async (saved, stale) => {
      openReview(saved, stale)
      await screen.findByRole('heading', { name: 'Analysis' })
      fireEvent.click(screen.getByRole('button', { name: 'Exit' }))
      await screen.findByRole('heading', { name: 'Applications list' })
      expect(applicationMode.save).not.toHaveBeenCalled()
      expect(applicationProcess.get).toHaveBeenCalledTimes(1)
    })

  it('returns to the workspace from an untouched stale review without reconfirming it', async () => {
    openReview(true, true)
    await screen.findByRole('heading', { name: 'Analysis' })
    fireEvent.click(screen.getByRole('button', { name: 'Application summary' }))
    await screen.findByRole('heading', { name: 'Application workspace' })
    expect(applicationMode.save).not.toHaveBeenCalled()
  })

  it('still explicitly saves an untouched new review when requested', async () => {
    openReview()
    fireEvent.click(await screen.findByRole('button', { name: 'Save evidence review' }))
    await waitFor(() => expect(applicationMode.save).toHaveBeenCalledTimes(1))
  })

  it('disables saving an unchanged review and enables it when edited', async () => {
    openReview(true)
    const button = await screen.findByRole('button', { name: 'Save evidence review' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(applicationMode.save).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('How will you address this requirement?'), { target: { value: 'Updated assessment' } })
    expect(button.disabled).toBe(false)
    fireEvent.click(button)
    await waitFor(() => expect(applicationMode.save).toHaveBeenCalledWith('app', 'concept', expect.objectContaining({ rationale: 'Updated assessment' })))
    await waitFor(() => expect(button.disabled).toBe(true))
  })

  it('allows explicitly reconfirming a stale review', async () => {
    openReview(true, true)
    const button = await screen.findByRole('button', { name: 'Save evidence review' }) as HTMLButtonElement
    expect(button.disabled).toBe(false)
    fireEvent.click(button)
    await waitFor(() => expect(applicationMode.save).toHaveBeenCalledTimes(1))
  })

  it('enables saving when a new career example is entered', async () => {
    openReview(true)
    const button = await screen.findByRole('button', { name: 'Save evidence review' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Career example'), { target: { value: 'Another example' } })
    expect(button.disabled).toBe(false)
  })

  it('keeps genuine edits on screen when saving them fails during exit', async () => {
    openReview(true)
    vi.mocked(applicationMode.save).mockRejectedValue(new Error('Save unavailable'))
    fireEvent.change(await screen.findByLabelText('How will you address this requirement?'), { target: { value: 'New assessment' } })
    fireEvent.click(screen.getByRole('button', { name: 'Exit' }))
    await screen.findByText('Save unavailable')
    expect(screen.queryByRole('heading', { name: 'Applications list' })).toBeNull()
    expect(sessionStorage.getItem('application-evidence:app:concept')).toContain('New assessment')
  })

  it('saves recovered edits before leaving', async () => {
    sessionStorage.setItem('application-evidence:app:concept', JSON.stringify({ decision: { ...decision, rationale: 'Recovered change' }, sources: [], requirement_fingerprint: 'fp' }))
    openReview(true)
    await screen.findByRole('heading', { name: 'Analysis' })
    fireEvent.click(screen.getByRole('button', { name: 'Exit' }))
    await screen.findByRole('heading', { name: 'Applications list' })
    expect(applicationMode.save).toHaveBeenCalledWith('app', 'concept', expect.objectContaining({ rationale: 'Recovered change' }))
  })
})
