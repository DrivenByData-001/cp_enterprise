import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ArchetypeSkillsRadar from './ArchetypeSkillsRadar'
import { api, type ArchetypeSkillProfile } from '../lib/api'

vi.mock('../lib/api', () => ({ api: { listArchetypes: vi.fn(), getArchetypeSkillProfile: vi.fn(), saveArchetypeSkillProfile: vi.fn() } }))
vi.mock('recharts', () => ({ ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, RadarChart: () => <div>Radar rendered</div>, Radar: () => null, PolarAngleAxis: () => null, PolarGrid: () => null, PolarRadiusAxis: () => null, Tooltip: () => null, Legend: () => null }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const profile: ArchetypeSkillProfile = {
  archetype_id: 'a', source: 'suggested', sample_role_count: 4, unavailable_skill_ids: [],
  axes: ['Pricing', 'Modelling', 'Communication'].map((name, i) => ({ id: `${i}`, canonical_name: name, type_code: 'method', role_count: 3, status: 'evidenced', coverage: 100, target: 100 })),
  candidates: Array.from({ length: 9 }, (_, i) => ({ id: `${i}`, canonical_name: `Skill ${i}`, type_code: 'method', role_count: 2 })),
}
function setup(data = profile) {
  vi.mocked(api.listArchetypes).mockResolvedValue([{ id: 'a', canonical_name: 'Actuary' }] as never)
  vi.mocked(api.getArchetypeSkillProfile).mockResolvedValue(data)
  render(<MemoryRouter><ArchetypeSkillsRadar /></MemoryRouter>)
}
it('shows the target, evidence scale and accessible skill table', async () => {
  setup()
  expect(await screen.findByText('Radar rendered')).toBeTruthy()
  expect(screen.getByRole('table').textContent).toContain('Pricing')
  expect(screen.getByText(/not proficiency/)).toBeTruthy()
})
it('limits the skillset to eight and saves the selected skills', async () => {
  setup()
  fireEvent.click(await screen.findByRole('button', { name: 'Edit target skillset' }))
  for (let i = 3; i < 8; i++) fireEvent.click(screen.getByLabelText(new RegExp(`Skill ${i} `)))
  expect((screen.getByLabelText(/Skill 8 /) as HTMLInputElement).disabled).toBe(true)
  vi.mocked(api.saveArchetypeSkillProfile).mockResolvedValue({ ...profile, source: 'saved' })
  fireEvent.click(screen.getByRole('button', { name: 'Save target skillset' }))
  await waitFor(() => expect(api.saveArchetypeSkillProfile).toHaveBeenCalledWith('a', ['0', '1', '2', '3', '4', '5', '6', '7']))
  expect(await screen.findByText('Target skillset saved for this archetype.')).toBeTruthy()
})
it('does not imply zero coverage for an unconfigured assessment', async () => {
  setup({ ...profile, axes: profile.axes.map(axis => ({ ...axis, status: 'unconfigured', coverage: null })) })
  expect(await screen.findByText(/Configure the missing/)).toBeTruthy()
  expect(screen.queryByText('Radar rendered')).toBeNull()
})
it('requires three skills before saving', async () => {
  setup({ ...profile, axes: [] })
  fireEvent.click(await screen.findByRole('button', { name: 'Edit target skillset' }))
  expect((screen.getByRole('button', { name: 'Save target skillset' }) as HTMLButtonElement).disabled).toBe(true)
})
