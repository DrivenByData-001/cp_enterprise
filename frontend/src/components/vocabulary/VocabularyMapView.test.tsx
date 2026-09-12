import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import VocabularyMapView from './VocabularyMapView'
import { api } from '../../lib/api'

// State-transition coverage for the vocab-graph-II brief's fullscreen/details/
// selection/filter-preservation requirements (§30). Real React Flow
// rendering (semantic zoom bands, labels, tooltips, fit-view) is deliberately
// out of scope here — no dedicated frontend test framework existed before
// this pass, and jsdom does not exercise React Flow's canvas measurement
// meaningfully, so that behaviour is covered by browser smoke testing
// instead (docs/25-vocabulary-map.md).

vi.mock('../../lib/api', () => ({
  api: {
    getVocabularyGraph: vi.fn(),
    getVocabularySimilarityFocus: vi.fn(),
  },
}))

// A minimal stand-in for the real graph canvas: one button per selectable
// node, reporting clicks back through the exact same `onSelect` contract the
// real VocabularyGraph uses. This isolates VocabularyMapView/Workspace/
// Overlay's own state logic from React Flow's rendering entirely.
vi.mock('./VocabularyGraph', () => ({
  default: ({ nodes, selectedId, onSelect }: { nodes: { id: string; kind: string; label: string }[]; selectedId: string | null; onSelect: (n: unknown) => void }) => (
    <div data-testid="mock-graph">
      {nodes
        .filter((n) => n.kind !== 'group')
        .map((n) => (
          <button key={n.id} data-testid={`node-${n.id}`} data-selected={n.id === selectedId} onClick={() => onSelect(n)}>
            {n.label}
          </button>
        ))}
    </div>
  ),
}))

const CONCEPT_NODE = {
  id: 'concept:c1',
  kind: 'concept' as const,
  label: 'Python',
  type_code: 'tool',
  status: 'active',
  alias_count: 1,
  target: { type: 'concept' as const, id: 'c1' },
}

const BASE_RESPONSE = {
  meta: { total_nodes: 1, returned_nodes: 2, returned_edges: 0, truncated: false },
  nodes: [{ id: 'root', kind: 'group' as const, label: 'All', count: 1 }, CONCEPT_NODE],
  edges: [],
}

const FOCUS_RESPONSE = {
  meta: { total_nodes: 1, returned_nodes: 1, returned_edges: 0, truncated: false, mode: 'similarity_focus' as const },
  nodes: [{ ...CONCEPT_NODE, focus: true }],
  edges: [],
}

function renderMapView() {
  return render(<VocabularyMapView conceptTypes={[]} onOpenClusterReview={() => {}} />)
}

async function selectConceptNode() {
  const node = await screen.findByTestId('node-concept:c1')
  fireEvent.click(node)
}

function openFullscreen() {
  fireEvent.click(screen.getByRole('button', { name: /open vocabulary map in fullscreen/i }))
}

beforeEach(() => {
  vi.mocked(api.getVocabularyGraph).mockResolvedValue(BASE_RESPONSE)
  vi.mocked(api.getVocabularySimilarityFocus).mockResolvedValue(FOCUS_RESPONSE)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('VocabularyMapView fullscreen', () => {
  it('shows a fullscreen control and opens an in-app dialog, not a new window', async () => {
    renderMapView()
    await screen.findByTestId('mock-graph')
    openFullscreen()
    expect(await screen.findByRole('dialog', { name: /fullscreen/i })).toBeTruthy()
  })

  it('closes via the Exit button', async () => {
    renderMapView()
    await screen.findByTestId('mock-graph')
    openFullscreen()
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: /exit fullscreen/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('closes via Escape', async () => {
    renderMapView()
    await screen.findByTestId('mock-graph')
    openFullscreen()
    await screen.findByRole('dialog')
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('preserves the selected node across entering and exiting fullscreen', async () => {
    renderMapView()
    await selectConceptNode()
    const dialog = await (async () => {
      openFullscreen()
      return screen.findByRole('dialog')
    })()
    // "Type: tool" only renders in the details panel for a selected concept
    // node — unambiguous, unlike the node's own label text (which the mock
    // graph button and the legend can also both contain).
    expect(within(dialog).getByText('Type: tool')).toBeTruthy()

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByText('Type: tool')).toBeTruthy()
  })

  it('never issues an extra graph fetch purely from toggling fullscreen', async () => {
    renderMapView()
    await screen.findByTestId('mock-graph')
    const callsBefore = vi.mocked(api.getVocabularyGraph).mock.calls.length

    openFullscreen()
    await screen.findByRole('dialog')
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    expect(vi.mocked(api.getVocabularyGraph).mock.calls.length).toBe(callsBefore)
  })

  it('does not automatically raise the node limit on entry, and keeps an explicit change after exit', async () => {
    renderMapView()
    await screen.findByTestId('mock-graph')

    openFullscreen()
    const dialog = await screen.findByRole('dialog')
    const limitSelect = within(dialog).getByLabelText('Limit') as HTMLSelectElement
    expect(limitSelect.value).toBe('300') // DEFAULT_MAP_FILTERS.limit — untouched by entering fullscreen

    fireEvent.change(limitSelect, { target: { value: '500' } })
    await waitFor(() => expect(vi.mocked(api.getVocabularyGraph)).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 500 })))

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect((screen.getByLabelText('Limit') as HTMLSelectElement).value).toBe('500')
  })

  it('collapses and reopens the details panel without losing the selection', async () => {
    renderMapView()
    await selectConceptNode()
    openFullscreen()
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Type: tool')).toBeTruthy()

    fireEvent.click(within(dialog).getByRole('button', { name: /hide details/i }))
    expect(within(dialog).queryByText('Type: tool')).toBeNull()

    fireEvent.click(within(dialog).getByRole('button', { name: /show details/i }))
    expect(within(dialog).getByText('Type: tool')).toBeTruthy()
  })

  it('preserves similarity-focus mode across the fullscreen toggle', async () => {
    renderMapView()
    await selectConceptNode()
    fireEvent.click(screen.getByRole('button', { name: /view similarity neighbourhood/i }))
    await screen.findByText(/similarity focus/i)

    openFullscreen()
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/similarity focus/i)).toBeTruthy()

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByText(/similarity focus/i)).toBeTruthy()
  })
})
