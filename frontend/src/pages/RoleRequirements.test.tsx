import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RoleRequirements from './RoleRequirements'
import { api, type Concept, type RequirementClaim, type RequirementReviewSummary } from '../lib/api'

vi.mock('../lib/api', () => ({ api: {
  listRequirements: vi.fn(), extractRequirements: vi.fn(), acceptRequirement: vi.fn(),
  rejectRequirement: vi.fn(), reopenRequirement: vi.fn(), editRequirement: vi.fn(),
  addRequirement: vi.fn(), listConcepts: vi.fn(), proposeRoleMetadata: vi.fn(), updateRoleMetadata: vi.fn(),
} }))

afterEach(() => { cleanup(); vi.resetAllMocks() })

function claim(overrides: Partial<RequirementClaim> = {}): RequirementClaim {
  return {
    id: 'claim-1', requirement_type: 'required', importance: 3, basis: 'stated',
    evidence_span: 'strong Python skills', review_status: 'unreviewed', created_at: '2026-01-01T00:00:00Z',
    extraction_run_id: 'run-1', superseded_by: null, concept_id: 'python', canonical_name: 'Python',
    type_code: 'tool', document_id: 'doc-1', document_title: 'Posting', document_provenance: 'original',
    ...overrides,
  }
}

function reviewSummary(overrides: Partial<RequirementReviewSummary> = {}): RequirementReviewSummary {
  return { accepted: 0, unreviewed: 0, rejected: 0, unresolved_proposals: 0, extraction_attempted: false, complete: true, ...overrides }
}

function renderPage(roleId = 'role-1') {
  return render(
    <MemoryRouter initialEntries={[`/role-instances/${roleId}/requirements`]}>
      <Routes><Route path="/role-instances/:id/requirements" element={<RoleRequirements />} /></Routes>
    </MemoryRouter>,
  )
}

describe('unreviewed requirement row', () => {
  it('offers Accept, Edit & accept and Reject, and requests the current list without history', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim()], review_summary: reviewSummary({ accepted: 0, unreviewed: 1, rejected: 0, complete: false }) })
    renderPage('role-1')
    await screen.findByText('Python')
    expect(screen.getByText('Accept')).toBeTruthy()
    expect(screen.getByText('Edit & accept')).toBeTruthy()
    expect(screen.getByText('Reject')).toBeTruthy()
    expect(api.listRequirements).toHaveBeenCalledWith('role-1')
  })

  it('accept and reject update the status summary counts', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim()], review_summary: reviewSummary({ accepted: 0, unreviewed: 1, rejected: 0, complete: false }) })
    vi.mocked(api.acceptRequirement).mockResolvedValue(claim({ review_status: 'accepted' }))
    renderPage()
    await screen.findByText('Python')
    expect(screen.getByText('1')).toBeTruthy() // "need review" count
    fireEvent.click(screen.getByText('Accept'))
    await waitFor(() => expect(api.acceptRequirement).toHaveBeenCalledWith('role-1', 'claim-1'))
    // Two matches once the claim's own status badge also reads "accepted",
    // alongside the summary card's "accepted" label.
    await waitFor(() => expect(screen.getAllByText('accepted')).toHaveLength(2))
    expect(screen.getByText('0 requirement(s) still need review.')).toBeTruthy()
  })
})

describe('accepted and rejected rows', () => {
  it('an accepted row offers Edit and Reject, but not Accept', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim({ review_status: 'accepted' })], review_summary: reviewSummary({ accepted: 1, unreviewed: 0, rejected: 0, complete: true }) })
    renderPage()
    await screen.findByText('Python')
    expect(screen.getByText('Edit')).toBeTruthy()
    expect(screen.getByText('Reject')).toBeTruthy()
    expect(screen.queryByText('Accept')).toBeNull()
  })

  it('rejecting an accepted row un-accepts it via the same reject action', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim({ review_status: 'accepted' })], review_summary: reviewSummary({ accepted: 1, unreviewed: 0, rejected: 0, complete: true }) })
    vi.mocked(api.rejectRequirement).mockResolvedValue(claim({ id: 'claim-2', review_status: 'rejected' }))
    renderPage()
    fireEvent.click(await screen.findByText('Reject'))
    await waitFor(() => expect(api.rejectRequirement).toHaveBeenCalledWith('role-1', 'claim-1'))
    await screen.findByText('Reopen for review')
  })

  it('a rejected row can be reopened for review', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim({ review_status: 'rejected' })], review_summary: reviewSummary({ accepted: 0, unreviewed: 0, rejected: 1, complete: true }) })
    vi.mocked(api.reopenRequirement).mockResolvedValue(claim({ review_status: 'unreviewed' }))
    renderPage()
    fireEvent.click(await screen.findByText('Reopen for review'))
    await waitFor(() => expect(api.reopenRequirement).toHaveBeenCalledWith('role-1', 'claim-1'))
    await screen.findByText('Accept')
  })
})

describe('edit & accept', () => {
  it('lets you remap the concept, change type/basis/importance/span, and saves via editRequirement', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim()], review_summary: reviewSummary({ accepted: 0, unreviewed: 1, rejected: 0, complete: false }) })
    vi.mocked(api.listConcepts).mockResolvedValue([{ id: 'sql', canonical_name: 'SQL', type_code: 'tool', status: 'active' } as Concept])
    vi.mocked(api.editRequirement).mockResolvedValue(claim({
      id: 'claim-2', concept_id: 'sql', canonical_name: 'SQL', requirement_type: 'preferred',
      basis: 'inferred', evidence_span: null, importance: 5, review_status: 'accepted',
    }))
    renderPage()
    fireEvent.click(await screen.findByText('Edit & accept'))

    // Concept type is explained as read-only/global, with a route out to Vocabulary.
    expect(screen.getByText('Concept type comes from the accepted vocabulary.')).toBeTruthy()
    const vocabLink = screen.getByText('Open in Vocabulary') as HTMLAnchorElement
    expect(vocabLink.getAttribute('href')).toBe('/vocabulary?focusConceptName=Python')

    fireEvent.change(screen.getByLabelText('Search active vocabulary concepts'), { target: { value: 'SQL' } })
    fireEvent.click(await screen.findByText('SQL'))

    fireEvent.change(screen.getByLabelText('Requirement type'), { target: { value: 'preferred' } })
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: 'inferred' } })
    fireEvent.change(screen.getByLabelText('Importance (1–5, optional)'), { target: { value: '5' } })

    fireEvent.click(screen.getByText('Save & accept'))

    await waitFor(() => expect(api.editRequirement).toHaveBeenCalledWith('role-1', 'claim-1', {
      concept_id: 'sql', requirement_type: 'preferred', basis: 'inferred', importance: 5, evidence_span: null,
    }))
    await screen.findByText('SQL')
  })

  it('an edit form drops the evidence span once basis is no longer stated/implied', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim()], review_summary: reviewSummary({ accepted: 0, unreviewed: 1, rejected: 0, complete: false }) })
    renderPage()
    fireEvent.click(await screen.findByText('Edit & accept'))
    expect(screen.getByLabelText('Evidence span (must be an exact quote from the source document)')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Basis'), { target: { value: 'user_asserted' } })
    expect(screen.queryByLabelText('Evidence span (must be an exact quote from the source document)')).toBeNull()
  })

  it('a failed save preserves the edited draft and the error is recoverable on retry', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim()], review_summary: reviewSummary({ accepted: 0, unreviewed: 1, rejected: 0, complete: false }) })
    vi.mocked(api.editRequirement).mockRejectedValueOnce(new Error('Save failed'))
      .mockResolvedValueOnce(claim({ review_status: 'accepted', requirement_type: 'preferred' }))
    renderPage()
    fireEvent.click(await screen.findByText('Edit & accept'))
    fireEvent.change(screen.getByLabelText('Requirement type'), { target: { value: 'preferred' } })
    fireEvent.click(screen.getByText('Save & accept'))
    await screen.findByText('Save failed')
    expect((screen.getByLabelText('Requirement type') as HTMLSelectElement).value).toBe('preferred')
    fireEvent.click(screen.getByText('Save & accept'))
    await waitFor(() => expect(api.editRequirement).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getAllByText('accepted')).toHaveLength(2))
  })

  it('an already-accepted row can be edited via the same supersession path', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim({ review_status: 'accepted' })], review_summary: reviewSummary({ accepted: 1, unreviewed: 0, rejected: 0, complete: true }) })
    vi.mocked(api.editRequirement).mockResolvedValue(claim({ id: 'claim-3', importance: 4, review_status: 'accepted' }))
    renderPage()
    fireEvent.click(await screen.findByText('Edit'))
    fireEvent.change(screen.getByLabelText('Importance (1–5, optional)'), { target: { value: '4' } })
    fireEvent.click(screen.getByText('Save correction'))
    await waitFor(() => expect(api.editRequirement).toHaveBeenCalledWith('role-1', 'claim-1', expect.objectContaining({ importance: 4 })))
  })

  it('debounces vocabulary search instead of issuing one request per keystroke', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim()], review_summary: reviewSummary({ accepted: 0, unreviewed: 1, rejected: 0, complete: false }) })
    vi.mocked(api.listConcepts).mockResolvedValue([{ id: 'sql', canonical_name: 'SQL', type_code: 'tool', status: 'active' } as Concept])
    renderPage()
    fireEvent.click(await screen.findByText('Edit & accept'))
    const input = screen.getByLabelText('Search active vocabulary concepts')
    fireEvent.change(input, { target: { value: 'S' } })
    fireEvent.change(input, { target: { value: 'SQ' } })
    fireEvent.change(input, { target: { value: 'SQL' } })
    expect(api.listConcepts).not.toHaveBeenCalled()
    await waitFor(() => expect(api.listConcepts).toHaveBeenCalledTimes(1))
    expect(api.listConcepts).toHaveBeenCalledWith({ q: 'SQL', status: 'active' })
  })
})

describe('add requirement', () => {
  it('creates a source-backed accepted claim through the manual add form', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [], review_summary: reviewSummary({ accepted: 0, unreviewed: 0, rejected: 0, complete: true }) })
    vi.mocked(api.listConcepts).mockResolvedValue([{ id: 'sql', canonical_name: 'SQL', type_code: 'tool', status: 'active' } as Concept])
    vi.mocked(api.addRequirement).mockResolvedValue(claim({ id: 'claim-4', concept_id: 'sql', canonical_name: 'SQL', review_status: 'accepted' }))
    renderPage()
    await screen.findByText('No requirement claims yet — run extraction above, add one manually below, or check the Vocabulary page for unresolved proposals it may have created.')
    fireEvent.click(screen.getByText('Add requirement'))
    fireEvent.change(screen.getByLabelText('Search active vocabulary concepts'), { target: { value: 'SQL' } })
    fireEvent.click(await screen.findByText('SQL'))
    fireEvent.change(screen.getByLabelText('Evidence span (exact quote from the source document)'), { target: { value: 'proficiency in SQL' } })
    fireEvent.click(screen.getByText('Save requirement'))
    await waitFor(() => expect(api.addRequirement).toHaveBeenCalledWith('role-1', {
      concept_id: 'sql', requirement_type: 'required', importance: null, evidence_span: 'proficiency in SQL',
    }))
    await screen.findAllByText('SQL')
  })
})

describe('review-incomplete warning', () => {
  it('warns before Continue to comparison while requirements remain unreviewed', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim()], review_summary: reviewSummary({ accepted: 0, unreviewed: 1, rejected: 0, complete: false }) })
    renderPage()
    await screen.findByText('Python')
    expect(screen.getByText('Continue to comparison (requirement review incomplete)')).toBeTruthy()
    expect(screen.getByText(/Requirement review is incomplete/)).toBeTruthy()
  })

  it('does not warn once every current requirement has been reviewed', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({ items: [claim({ review_status: 'accepted' })], review_summary: reviewSummary({ accepted: 1, unreviewed: 0, rejected: 0, complete: true }) })
    renderPage()
    await screen.findByText('Python')
    expect(screen.getByText('Continue to comparison')).toBeTruthy()
    expect(screen.queryByText(/Requirement review is incomplete/)).toBeNull()
  })

  it('warns when unresolved vocabulary proposals remain, even with every claim accepted', async () => {
    vi.mocked(api.listRequirements).mockResolvedValue({
      items: [claim({ review_status: 'accepted' })],
      review_summary: reviewSummary({ accepted: 1, unreviewed: 0, rejected: 0, unresolved_proposals: 2, complete: false }),
    })
    renderPage()
    await screen.findByText('Python')
    expect(screen.getByText(/2 extracted terms could not be matched to the vocabulary/)).toBeTruthy()
    expect(screen.getByText('Continue to comparison (requirement review incomplete)')).toBeTruthy()
  })
})
