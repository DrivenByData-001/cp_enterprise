import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Capabilities from './Capabilities'
import { api, type Capability, type UnconfiguredCapability } from '../lib/api'

vi.mock('../lib/api', () => ({ api: {
  listCapabilities: vi.fn(), listUnconfiguredCapabilities: vi.fn(),
  configureCapability: vi.fn(), createCapability: vi.fn(), getCapability: vi.fn(),
} }))
const pending: UnconfiguredCapability = {
  id: 'existing-id', canonical_name: 'Capital Modelling', definition: 'Vocabulary definition',
  status: 'active', origin: 'curator', created_at: '2026-09-16', reviewed_at: null,
}
const ready: Capability = {
  ...pending, demonstration_standard: 'Own a cycle', min_depth: 'owned', min_autonomy: null,
  requires_all_core: true, min_core_required: null, economic_salience: null, notes: null,
  core_component_count: 0, supporting_component_count: 0, contextual_component_count: 0, proposed_component_count: 0,
  components: { core: [], supporting: [], contextual: [] },
  components_proposed: { core: [], supporting: [], contextual: [] }, coverage: null,
}
beforeEach(() => {
  vi.mocked(api.listCapabilities).mockResolvedValue([])
  vi.mocked(api.listUnconfiguredCapabilities).mockResolvedValue([pending])
  vi.mocked(api.getCapability).mockResolvedValue(ready)
})
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('shows Vocabulary capabilities awaiting specification when no configured entries exist', async () => {
  render(<Capabilities />)
  await screen.findByRole('button', { name: 'Configure Capital Modelling' })
  expect(screen.getByText('1 capability concepts in Vocabulary need assessment specifications.')).toBeTruthy()
  expect(screen.getByText('No configured capabilities match this view.')).toBeTruthy()
  expect(screen.getByRole('button', { name: '+ New capability' }).className).not.toContain('primary')
})

it('configures the same concept with specification fields only and opens components', async () => {
  render(<Capabilities />)
  fireEvent.click(await screen.findByRole('button', { name: 'Configure Capital Modelling' }))
  expect((screen.getByLabelText('Canonical name') as HTMLInputElement).readOnly).toBe(true)
  expect((screen.getByLabelText('Canonical name') as HTMLInputElement).value).toBe('Capital Modelling')
  expect((screen.getByLabelText('Definition (optional)') as HTMLInputElement).value).toBe('Vocabulary definition')
  fireEvent.change(screen.getByLabelText(/Demonstration standard/), { target: { value: 'Own a cycle' } })
  fireEvent.change(screen.getByLabelText(/Economic salience/), { target: { value: 'high' } })
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Assessment notes' } })
  vi.mocked(api.configureCapability).mockResolvedValue({ id: pending.id, status: 'configured' })
  vi.mocked(api.listCapabilities).mockResolvedValue([ready])
  vi.mocked(api.listUnconfiguredCapabilities).mockResolvedValue([])
  fireEvent.click(screen.getByRole('button', { name: 'Save specification' }))
  await screen.findByText('Components')
  expect(api.configureCapability).toHaveBeenCalledWith(pending.id, {
    demonstration_standard: 'Own a cycle', min_depth: 'owned', min_autonomy: null,
    requires_all_core: true, min_core_required: null, economic_salience: 'high', notes: 'Assessment notes',
  })
  expect(api.createCapability).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'Configure Capital Modelling' })).toBeNull()
  expect(screen.getByText('Assessment-ready')).toBeTruthy()
  expect(api.getCapability).toHaveBeenCalledWith(pending.id)
})

it('retains the form and reports a duplicate-safe failure', async () => {
  render(<Capabilities />)
  fireEvent.click(await screen.findByRole('button', { name: 'Configure Capital Modelling' }))
  fireEvent.change(screen.getByLabelText(/Demonstration standard/), { target: { value: 'Own a cycle' } })
  vi.mocked(api.configureCapability).mockRejectedValue(new Error('Already configured'))
  fireEvent.click(screen.getByRole('button', { name: 'Save specification' }))
  await screen.findByText('Already configured')
  expect(screen.getByRole('button', { name: 'Save specification' })).toBeTruthy()
  expect(api.createCapability).not.toHaveBeenCalled()
})

it('keeps deliberate create-new available', async () => {
  render(<Capabilities />)
  fireEvent.click(screen.getByRole('button', { name: '+ New capability' }))
  fireEvent.change(screen.getByLabelText('Canonical name'), { target: { value: 'New capability' } })
  fireEvent.change(screen.getByLabelText(/Demonstration standard/), { target: { value: 'Own a cycle' } })
  vi.mocked(api.createCapability).mockResolvedValue({ id: 'new-id', status: 'created' })
  fireEvent.click(screen.getByRole('button', { name: 'Create capability' }))
  await waitFor(() => expect(api.createCapability).toHaveBeenCalled())
  expect(api.configureCapability).not.toHaveBeenCalled()
})

it('searches unconfigured Vocabulary capabilities through the API', async () => {
  render(<Capabilities />)
  fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'Capital' } })
  await waitFor(() => expect(api.listUnconfiguredCapabilities).toHaveBeenCalledWith('Capital'))
})

it('distinguishes an empty Vocabulary from a configured-empty catalogue', async () => {
  vi.mocked(api.listUnconfiguredCapabilities).mockResolvedValue([])
  render(<Capabilities />)
  await screen.findByText('0 capability concepts in Vocabulary need assessment specifications.')
  expect(screen.queryByRole('button', { name: 'Configure Capital Modelling' })).toBeNull()
})
