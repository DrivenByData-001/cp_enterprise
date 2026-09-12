import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Import from './Import'
import { api } from '../lib/api'

// Source-aware ingest cleanup, problems #1-#3: uploading an exact duplicate
// must warn before completion/navigation instead of silently capturing a
// second role, a whitespace-normalised ("possible") duplicate must be
// visibly distinguished from an exact one, and "Capture anyway" must still
// go through.

vi.mock('../lib/api', () => ({
  api: {
    checkDuplicate: vi.fn(),
    ingestText: vi.fn(),
    extractPdfText: vi.fn(),
    importPostingNative: vi.fn(),
    importBulk: vi.fn(),
  },
}))

function renderImport() {
  return render(
    <MemoryRouter>
      <Import />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function pasteAndCapture(text: string) {
  const textarea = screen.getByPlaceholderText('Paste raw posting text…')
  fireEvent.change(textarea, { target: { value: text } })
  fireEvent.click(screen.getByText('Capture text'))
}

describe('Import — exact duplicate warning', () => {
  it('warns instead of capturing, and offers Open existing / Capture anyway', async () => {
    vi.mocked(api.checkDuplicate).mockResolvedValue({
      exact_duplicate: {
        document_id: 'doc-1', role_instance_id: 'role-1', title: 'Life Actuarial Manager',
        organisation: 'Forvis Mazars Ireland', posting_date: '2025-06-13',
      },
      possible_duplicate: null,
    })

    renderImport()
    pasteAndCapture('Life Actuarial Manager at Forvis Mazars in Ireland.')

    await waitFor(() => expect(screen.getByText('This document appears to have already been captured.')).toBeTruthy())
    expect(screen.getByText('Forvis Mazars Ireland')).toBeTruthy()
    expect(screen.getByText('Open existing')).toBeTruthy()
    expect(screen.getByText('Capture anyway')).toBeTruthy()
    expect(api.ingestText).not.toHaveBeenCalled()
  })

  it('captures anyway when the user explicitly chooses to', async () => {
    vi.mocked(api.checkDuplicate).mockResolvedValue({
      exact_duplicate: { document_id: 'doc-1', role_instance_id: 'role-1', title: 'X', organisation: null, posting_date: null },
      possible_duplicate: null,
    })
    vi.mocked(api.ingestText).mockResolvedValue({ id: 'role-2', document_id: 'doc-2', duplicate_of_document_id: 'doc-1', duplicate: { exact_duplicate: null, possible_duplicate: null }, status: 'ingested' })

    renderImport()
    pasteAndCapture('Some duplicated posting text.')
    await waitFor(() => expect(screen.getByText('Capture anyway')).toBeTruthy())

    fireEvent.click(screen.getByText('Capture anyway'))
    await waitFor(() => expect(api.ingestText).toHaveBeenCalledTimes(1))
    expect(api.ingestText).toHaveBeenCalledWith(expect.objectContaining({ text: 'Some duplicated posting text.' }))
  })
})

describe('Import — possible (normalised) duplicate warning', () => {
  it('is visibly distinguished from an exact duplicate', async () => {
    vi.mocked(api.checkDuplicate).mockResolvedValue({
      exact_duplicate: null,
      possible_duplicate: {
        document_id: 'doc-1', role_instance_id: 'role-1', title: 'Life Actuarial Manager',
        organisation: 'Forvis Mazars Ireland', posting_date: '2025-06-13',
      },
    })

    renderImport()
    pasteAndCapture('Life Actuarial Manager at Forvis Mazars in Ireland (reflowed).')

    await waitFor(() => expect(screen.getByText('This source appears to already exist.')).toBeTruthy())
    expect(screen.queryByText('This document appears to have already been captured.')).toBeFalsy()
    expect(api.ingestText).not.toHaveBeenCalled()
  })
})

describe('Import — no duplicate found', () => {
  it('captures directly with no warning shown', async () => {
    vi.mocked(api.checkDuplicate).mockResolvedValue({ exact_duplicate: null, possible_duplicate: null })
    vi.mocked(api.ingestText).mockResolvedValue({ id: 'role-3', document_id: 'doc-3', duplicate_of_document_id: null, duplicate: { exact_duplicate: null, possible_duplicate: null }, status: 'ingested' })

    renderImport()
    pasteAndCapture('A brand new posting nobody has seen.')

    await waitFor(() => expect(api.ingestText).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('This document appears to have already been captured.')).toBeFalsy()
    expect(screen.queryByText('This source appears to already exist.')).toBeFalsy()
  })
})
