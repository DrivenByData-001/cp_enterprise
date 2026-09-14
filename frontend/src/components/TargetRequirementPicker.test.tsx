import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type TargetDraft, type Concept } from '../lib/api'
import TargetDraftEditor from './TargetDraftEditor'

vi.mock('../lib/api', () => ({ api: { resolveTargetRequirements: vi.fn(), listConcepts: vi.fn() } }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const initial: TargetDraft = { metadata: {}, target: { title: 'Target', is_imagined: false,
  typical_tasks: [], skill_decomposition: [], technical_subjects: [] }, skills: [{ name: 'CM', requirement_type: 'required' }] }
function Editor() {
  const [value, setValue] = useState(initial)
  return <><TargetDraftEditor value={value} onChange={setValue} /><output data-testid="draft">{JSON.stringify(value.skills)}</output></>
}

it('shows canonical resolution and confirms reviewed mappings for different wording', async () => {
  vi.mocked(api.resolveTargetRequirements).mockImplementation(async skills => [{ name: skills[0].name,
    concept_id: skills[0].concept_id ?? null, canonical_name: skills[0].concept_id ? 'Capital Modelling' : null,
    mapping_status: skills[0].concept_id ? 'mapped' : 'unmapped' }])
  vi.mocked(api.listConcepts).mockResolvedValue([{ id: 'capital', canonical_name: 'Capital Modelling' } as Concept])
  render(<Editor />)
  await screen.findByText('Unmapped — excluded from analysis; needs review.')
  fireEvent.change(screen.getByLabelText('Search vocabulary for CM'), { target: { value: 'Capital' } })
  fireEvent.click(await screen.findByText('Map to Capital Modelling'))
  await screen.findByText('Mapped → Capital Modelling')
  expect(screen.getByTestId('draft').textContent).toContain('"mapping_reviewed":true')
  expect(screen.getByTestId('draft').textContent).toContain('"concept_id":"capital"')
  fireEvent.change(screen.getByLabelText('Requirement 1'), { target: { value: 'Different work' } })
  await screen.findByText('Unmapped — excluded from analysis; needs review.')
  expect(screen.getByTestId('draft').textContent).toContain('"concept_id":null')
  expect(screen.getByTestId('draft').textContent).toContain('"mapping_reviewed":false')
})

it('shows aliases as their canonical vocabulary name and permits explicit unmapped choice', async () => {
  vi.mocked(api.resolveTargetRequirements).mockImplementation(async skills => [{ name: 'CM',
    concept_id: skills[0].mapping_reviewed ? null : 'capital', canonical_name: 'Capital Modelling',
    mapping_status: skills[0].mapping_reviewed ? 'unmapped' : 'mapped' }])
  render(<Editor />)
  await screen.findByText('Mapped → Capital Modelling')
  fireEvent.click(screen.getByText('Leave unmapped'))
  await screen.findByText('Unmapped — excluded from analysis; needs review.')
  expect(screen.getByTestId('draft').textContent).toContain('"mapping_reviewed":true')
})

it('reports a failed resolution and supports retry without losing wording', async () => {
  vi.mocked(api.resolveTargetRequirements).mockRejectedValueOnce(new Error('Offline')).mockResolvedValueOnce([
    { name: 'CM', concept_id: 'capital', canonical_name: 'Capital Modelling', mapping_status: 'mapped' },
  ])
  render(<Editor />)
  await screen.findByRole('alert')
  expect((screen.getByLabelText('Requirement 1') as HTMLInputElement).value).toBe('CM')
  fireEvent.click(screen.getByText('Retry mapping check'))
  await screen.findByText('Mapped → Capital Modelling')
})

it('ignores obsolete resolution responses after requirement wording changes', async () => {
  let resolveOld!: (value: Awaited<ReturnType<typeof api.resolveTargetRequirements>>) => void
  vi.mocked(api.resolveTargetRequirements).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
    .mockResolvedValue([{ name: 'New', concept_id: null, canonical_name: null, mapping_status: 'unmapped' }])
  render(<Editor />)
  await waitFor(() => expect(api.resolveTargetRequirements).toHaveBeenCalledTimes(1))
  fireEvent.change(screen.getByLabelText('Requirement 1'), { target: { value: 'New' } })
  await screen.findByText('Unmapped — excluded from analysis; needs review.')
  resolveOld([{ name: 'CM', concept_id: 'capital', canonical_name: 'Capital Modelling', mapping_status: 'mapped' }])
  await waitFor(() => expect(screen.queryByText('Mapped → Capital Modelling')).toBeNull())
})
