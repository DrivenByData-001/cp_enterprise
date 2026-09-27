import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { api, type Role } from '../lib/api'
import RoleEdit from './RoleEdit'

vi.mock('../lib/api', () => ({ api: { getRole: vi.fn(), updateTarget: vi.fn(), resolveTargetRequirements: vi.fn() } }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('preserves an imagined target and requirement priority when editing without legacy JSON', async () => {
  vi.mocked(api.getRole).mockResolvedValue({ id: 'target', node_type: 'target_imagined', title: 'Risk lead',
    raw_json: null, summary: 'Existing summary', career_track: 'Risk', seniority_level: 'Senior',
    grounding_note: 'Existing assumptions', feasibility_note: 'Existing feasibility', is_plausible: false,
    skills: [{ name: 'Python', requirement_type: 'preferred', resolved_concept_id: 'python', category: 'tool', importance: 3 }],
  } as Role)
  vi.mocked(api.updateTarget).mockResolvedValue({ id: 'target', status: 'updated' })
  vi.mocked(api.resolveTargetRequirements).mockResolvedValue([{ name: 'Python', concept_id: 'python', canonical_name: 'Python', mapping_status: 'mapped' }])
  render(<MemoryRouter initialEntries={['/roles/target/edit']}><Routes>
    <Route path="/roles/:id/edit" element={<RoleEdit />} />
    <Route path="/targets/:id" element={<p>Saved target</p>} />
  </Routes></MemoryRouter>)
  await screen.findByText('Review and edit your target')
  fireEvent.change(screen.getByLabelText('Target title'), { target: { value: 'Edited title' } })
  fireEvent.click(screen.getByText('Save target changes'))
  await waitFor(() => expect(api.updateTarget).toHaveBeenCalledWith('target', expect.objectContaining({
    target: expect.objectContaining({ title: 'Edited title', is_imagined: true, summary: 'Existing summary',
      career_track: 'Risk', seniority_level: 'Senior', grounding_note: 'Existing assumptions', is_plausible: false }),
    skills: [expect.objectContaining({ name: 'Python', requirement_type: 'preferred', concept_id: 'python', importance: 3 })],
  })))
  // Phase 1 cleanup: a target's own detail page is /targets/:id, not
  // /roles/:id, so its primary-nav context stays Explore my future rather
  // than flipping to Opportunities.
  await screen.findByText('Saved target')
})
