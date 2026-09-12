import { useEffect, useState } from 'react'
import type { ConceptType, PriorityBand } from '../../lib/api'
import { DEFAULT_MAP_FILTERS, type VocabMapFilterState } from './vocabConstants'

const SEARCH_DEBOUNCE_MS = 350

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3, fontSize: 12 }}>
      <span className="secondary" style={{ fontSize: 11 }}>
        {label}
      </span>
      {children}
    </label>
  )
}

export default function VocabularyMapControls({
  filters,
  conceptTypes,
  onChange,
}: {
  filters: VocabMapFilterState
  conceptTypes: ConceptType[]
  onChange: (f: VocabMapFilterState) => void
}) {
  // Search is debounced client-side before it ever reaches the server-side
  // `q` filter (brief §7/§29.3: "Do not refetch on every keystroke").
  const [searchDraft, setSearchDraft] = useState(filters.q)
  useEffect(() => setSearchDraft(filters.q), [filters.q])
  useEffect(() => {
    const t = setTimeout(() => {
      if (searchDraft !== filters.q) onChange({ ...filters, q: searchDraft })
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft])

  const set = (patch: Partial<VocabMapFilterState>) => onChange({ ...filters, ...patch })

  return (
    <div className="card" style={{ marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
      <Field label="View">
        <select
          value={filters.status}
          onChange={(e) => {
            const status = e.target.value as VocabMapFilterState['status']
            set({ status, group_by: status === 'accepted' ? 'type' : status === 'pending' ? 'priority' : filters.group_by })
          }}
        >
          <option value="pending">Pending</option>
          <option value="accepted">Accepted</option>
          <option value="combined">Combined</option>
        </select>
      </Field>
      <Field label="Group by">
        <select value={filters.group_by} onChange={(e) => set({ group_by: e.target.value as VocabMapFilterState['group_by'] })}>
          <option value="priority">Priority</option>
          <option value="type">Concept type</option>
          <option value="none">None</option>
        </select>
      </Field>
      {filters.status !== 'accepted' && (
        <Field label="Priority">
          <select value={filters.band ?? ''} onChange={(e) => set({ band: (e.target.value || undefined) as PriorityBand | undefined })}>
            <option value="">All</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
            <option value="sparse">Sparse</option>
          </select>
        </Field>
      )}
      {filters.status !== 'pending' && (
        <Field label="Type">
          <select value={filters.type_code ?? ''} onChange={(e) => set({ type_code: e.target.value || undefined })}>
            <option value="">All types</option>
            {conceptTypes.map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Search">
        <input
          value={searchDraft}
          onChange={(e) => setSearchDraft(e.target.value)}
          placeholder="stakeholder…"
          style={{ minWidth: 180 }}
        />
      </Field>
      <Field label="Min roles">
        <input
          type="number"
          min={0}
          value={filters.min_role_count ?? ''}
          onChange={(e) => set({ min_role_count: e.target.value ? Number(e.target.value) : undefined })}
          style={{ width: 80 }}
        />
      </Field>
      <Field label="Min obs.">
        <input
          type="number"
          min={0}
          value={filters.min_observation_count ?? ''}
          onChange={(e) => set({ min_observation_count: e.target.value ? Number(e.target.value) : undefined })}
          style={{ width: 80 }}
        />
      </Field>
      <Field label="Limit">
        <select value={filters.limit} onChange={(e) => set({ limit: Number(e.target.value) })}>
          <option value={100}>100</option>
          <option value={200}>200</option>
          <option value={300}>300</option>
          <option value={500}>500</option>
        </select>
      </Field>
      <button
        onClick={() => {
          setSearchDraft('')
          onChange(DEFAULT_MAP_FILTERS)
        }}
      >
        Reset
      </button>
    </div>
  )
}
