import { render, screen, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it } from 'vitest'
import type { ComparisonItem } from '../lib/api'
import ComparisonActions from './ComparisonActions'

afterEach(cleanup)
const item = { concept: { id: 'concept-1', canonical_name: 'Governance', type_code: 'tool' }, status: 'not_found',
  role_side: { requirement_claim_id: null, requirement_type: 'required', basis: 'user_asserted', review_status: 'accepted', evidence_span: null, document: null },
  person_side: { assertion: null, mappings: [], component_of: [], coverage: null } } as ComparisonItem

it('keeps the supporting evidence drill-down inside the owning application', () => {
  render(<MemoryRouter initialEntries={['/comparison/role-1?application=app-1']}><ComparisonActions item={item} roleId="role-1" actions={[]} onChanged={async () => {}} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: 'Review supporting evidence' }).getAttribute('href')).toBe('/applications/app-1/prepare/concept-1')
})

it('retains ordinary profile navigation outside an application', () => {
  render(<MemoryRouter initialEntries={['/comparison/role-1']}><ComparisonActions item={item} roleId="role-1" actions={[]} onChanged={async () => {}} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: 'Review supporting evidence' }).getAttribute('href')).toBe('/profile360')
})
