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

// Phase 3 routing cleanup (docs/34 §12): editing a target now goes through
// its own /targets/:id/edit route (App.tsx renders the same RoleEdit there),
// so a link built from a target's own id never has to fall back to
// /roles/:id/edit and its Opportunities-flavoured nav context. The old
// /roles/:id/edit deep link keeps working too — see RoleEdit.test.tsx.
it('renders and saves identically via the new /targets/:id/edit route', async () => {
  vi.mocked(api.getRole).mockResolvedValue({
    id: 'target', node_type: 'target_real', title: 'Head of Risk', organisation: null, location: null,
    country: null, remote_type: null, employment_type: null, posting_date: null, captured_at: null,
    career_track: null, seniority_level: null, salary_min: null, salary_max: null, currency: null,
    summary: null, description: null, requirements: null, responsibilities: null, key_skills_summary: null,
    top_adjacent_roles: null, extraction_status: null, extraction_notes: null, similarity: null, url: null,
    raw_json: null, grounding_note: null, feasibility_note: null, is_plausible: null, skills: [],
  } satisfies Role)
  vi.mocked(api.updateTarget).mockResolvedValue({ id: 'target', status: 'updated' })
  vi.mocked(api.resolveTargetRequirements).mockResolvedValue([])
  render(<MemoryRouter initialEntries={['/targets/target/edit']}><Routes>
    <Route path="/targets/:id/edit" element={<RoleEdit />} />
    <Route path="/targets/:id" element={<p>Saved target</p>} />
  </Routes></MemoryRouter>)
  await screen.findByText('Review and edit your target')
  fireEvent.click(screen.getByText('Save target changes'))
  await waitFor(() => expect(api.updateTarget).toHaveBeenCalledWith('target', expect.anything()))
  await screen.findByText('Saved target')
})
