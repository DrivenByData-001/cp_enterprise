import { useEffect, useState } from 'react'
import {
  api,
  type AutonomyLevel,
  type Capability,
  type CapabilityInput,
  type CapabilitySummary,
  type Concept,
  type DepthLevel,
  type Necessity,
  type RebuildSummary,
} from '../lib/api'

const DEPTH_LEVELS: DepthLevel[] = ['exposed', 'applied', 'owned', 'set_standard']
const AUTONOMY_LEVELS: AutonomyLevel[] = ['assisted', 'independent', 'directed_others', 'accountable']
const NECESSITY_LEVELS: Necessity[] = ['core', 'supporting', 'contextual']

const STATUS_COLOR: Record<string, string> = {
  evidenced: 'var(--good)',
  partial: 'var(--warning)',
  user_asserted: 'var(--series-1)',
  not_found: 'var(--muted, #888)',
}

const EMPTY: CapabilityInput = {
  canonical_name: '',
  definition: '',
  demonstration_standard: '',
  min_depth: 'owned',
  min_autonomy: null,
  requires_all_core: true,
  min_core_required: null,
  economic_salience: null,
  notes: '',
}

function AddCapabilityForm({ onAdded }: { onAdded: (id: string) => Promise<void> }) {
  const [form, setForm] = useState<CapabilityInput>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  const submit = async () => {
    if (!form.canonical_name.trim() || !form.demonstration_standard.trim()) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.createCapability({
        ...form,
        canonical_name: form.canonical_name.trim(),
        definition: form.definition?.trim() || undefined,
        notes: form.notes?.trim() || undefined,
      })
      setForm(EMPTY)
      setOpen(false)
      await onAdded(result.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button className="primary" style={{ width: '100%' }} onClick={() => setOpen(true)}>
        + New capability
      </button>
    )
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Canonical name</span>
        <input value={form.canonical_name} onChange={(e) => setForm({ ...form, canonical_name: e.target.value })} placeholder="e.g. Lead a reserving process" />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Definition (optional)</span>
        <input value={form.definition ?? ''} onChange={(e) => setForm({ ...form, definition: e.target.value })} />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Demonstration standard — what would have to be true for this to count?</span>
        <textarea rows={3} value={form.demonstration_standard} onChange={(e) => setForm({ ...form, demonstration_standard: e.target.value })} />
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Minimum depth</span>
          <select value={form.min_depth} onChange={(e) => setForm({ ...form, min_depth: e.target.value as DepthLevel })}>
            {DEPTH_LEVELS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Minimum autonomy</span>
          <select value={form.min_autonomy ?? ''} onChange={(e) => setForm({ ...form, min_autonomy: (e.target.value || null) as AutonomyLevel | null })}>
            <option value="">(none required)</option>
            {AUTONOMY_LEVELS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
        <input type="checkbox" checked={form.requires_all_core} onChange={(e) => setForm({ ...form, requires_all_core: e.target.checked })} />
        <span className="secondary">Requires every core component</span>
      </label>
      {!form.requires_all_core && (
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Minimum core components required (blank = engine default of 1)</span>
          <input
            type="number"
            min={0}
            value={form.min_core_required ?? ''}
            onChange={(e) => setForm({ ...form, min_core_required: e.target.value === '' ? null : Number(e.target.value) })}
          />
        </label>
      )}
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Economic salience (catalogue metadata only — never affects coverage)</span>
        <select value={form.economic_salience ?? ''} onChange={(e) => setForm({ ...form, economic_salience: (e.target.value || null) as CapabilityInput['economic_salience'] })}>
          <option value="">(unset)</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Notes</span>
        <textarea rows={2} value={form.notes ?? ''} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </label>
      {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="primary" disabled={busy || !form.canonical_name.trim() || !form.demonstration_standard.trim()} onClick={submit}>
          {busy ? 'Creating…' : 'Create capability'}
        </button>
        <button onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function ComponentBrowser({ capabilityId, existingIds, onAdded }: { capabilityId: string; existingIds: Set<string>; onAdded: () => Promise<void> }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Concept[]>([])
  const [necessity, setNecessity] = useState<Necessity>('core')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults([])
      return
    }
    const handle = setTimeout(() => {
      api
        .listConcepts({ q: q.trim() })
        .then((concepts) => setResults(concepts.filter((c) => c.type_code !== 'capability' && c.type_code !== 'role_archetype')))
        .catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(handle)
  }, [q])

  const add = async (conceptId: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.addComponent(capabilityId, { concept_id: conceptId, necessity })
      setQ('')
      setResults([])
      await onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', gap: 6 }}>
        <input placeholder="Search atomic concepts…" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
        <select value={necessity} onChange={(e) => setNecessity(e.target.value as Necessity)}>
          {NECESSITY_LEVELS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </div>
      {error && <p style={{ color: 'var(--critical)', fontSize: 12, margin: 0 }}>{error}</p>}
      {results.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 160, overflowY: 'auto' }}>
          {results.map((c) => (
            <button
              key={c.id}
              disabled={busy || existingIds.has(c.id)}
              onClick={() => add(c.id)}
              style={{ textAlign: 'left', fontSize: 13 }}
            >
              {c.canonical_name} <span className="muted">({c.type_code})</span>
              {existingIds.has(c.id) ? ' — already a component' : ''}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function ComponentList({ capabilityId, components, onChanged }: { capabilityId: string; components: Capability['components']; onChanged: () => Promise<void> }) {
  const groups: { necessity: Necessity; label: string }[] = [
    { necessity: 'core', label: 'Core' },
    { necessity: 'supporting', label: 'Supporting' },
    { necessity: 'contextual', label: 'Contextual' },
  ]

  const changeNecessity = async (edgeId: string, necessity: Necessity) => {
    await api.updateComponent(capabilityId, edgeId, necessity)
    await onChanged()
  }
  const remove = async (edgeId: string) => {
    await api.removeComponent(capabilityId, edgeId)
    await onChanged()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {groups.map(({ necessity, label }) => (
        <div key={necessity}>
          <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 4 }}>
            {label} ({components[necessity].length})
          </div>
          {components[necessity].length === 0 && <p className="muted" style={{ fontSize: 13, margin: 0 }}>None yet.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {components[necessity].map((edge) => (
              <div key={edge.edge_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13 }}>
                <span>
                  {edge.canonical_name} <span className="muted">({edge.type_code})</span>
                </span>
                <div style={{ display: 'flex', gap: 4 }}>
                  <select value={edge.necessity} onChange={(e) => changeNecessity(edge.edge_id, e.target.value as Necessity)}>
                    {NECESSITY_LEVELS.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                  <button onClick={() => remove(edge.edge_id)}>Remove</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// docs/18 §10: bootstrap-proposed component_of edges (necessity core/
// supporting/contextual, direction atom -> capability — see
// app/vocabulary_bootstrap.py) sit in `concept_edge.status='proposed'`,
// completely invisible to the coverage/fit engine until reviewed. This is
// the review surface — accept moves one edge into the same accepted list
// ComponentList renders above; reject discards it. Never auto-applied.
function ProposedComponentList({
  capabilityId,
  components,
  onChanged,
}: {
  capabilityId: string
  components: Capability['components_proposed']
  onChanged: () => Promise<void>
}) {
  const groups: { necessity: Necessity; label: string }[] = [
    { necessity: 'core', label: 'Core' },
    { necessity: 'supporting', label: 'Supporting' },
    { necessity: 'contextual', label: 'Contextual' },
  ]
  const total = components.core.length + components.supporting.length + components.contextual.length
  if (total === 0) return null

  const review = async (edgeId: string, action: 'accept' | 'reject') => {
    await api.reviewComponent(capabilityId, edgeId, action)
    await onChanged()
  }

  return (
    <div>
      <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
        Proposed components — from the vocabulary bootstrap, awaiting review
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {groups.map(
          ({ necessity, label }) =>
            components[necessity].length > 0 && (
              <div key={necessity}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                  {label} ({components[necessity].length})
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {components[necessity].map((edge) => (
                    <div key={edge.edge_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13 }}>
                      <span>
                        {edge.canonical_name} <span className="muted">({edge.type_code})</span>
                      </span>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button onClick={() => review(edge.edge_id, 'accept')}>Accept</button>
                        <button onClick={() => review(edge.edge_id, 'reject')}>Reject</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ),
        )}
      </div>
    </div>
  )
}

function MergeControl({ capabilityId, onMerged }: { capabilityId: string; onMerged: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<CapabilitySummary[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    api
      .listCapabilities({ status: 'active', q: q.trim() || undefined })
      .then((rows) => setResults(rows.filter((r) => r.id !== capabilityId)))
      .catch(() => setResults([]))
  }, [open, q, capabilityId])

  const merge = async (targetId: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.mergeCapability(capabilityId, targetId)
      await onMerged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} title="Merge this proposed capability into an existing one">
        Merge into…
      </button>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <input placeholder="Search capabilities to merge into…" value={q} onChange={(e) => setQ(e.target.value)} />
      {error && <p style={{ color: 'var(--critical)', fontSize: 12, margin: 0 }}>{error}</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 160, overflowY: 'auto' }}>
        {results.map((r) => (
          <button key={r.id} disabled={busy} onClick={() => merge(r.id)} style={{ textAlign: 'left', fontSize: 13 }}>
            {r.canonical_name}
          </button>
        ))}
      </div>
      <button onClick={() => setOpen(false)} disabled={busy} style={{ alignSelf: 'flex-start' }}>
        Cancel
      </button>
    </div>
  )
}

function CapabilityDetail({ id, onListChanged }: { id: string; onListChanged: () => Promise<void> }) {
  const [capability, setCapability] = useState<Capability | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Partial<CapabilityInput>>({})
  const [error, setError] = useState<string | null>(null)

  const reload = () =>
    api.getCapability(id).then((c) => {
      setCapability(c)
      setDraft({
        canonical_name: c.canonical_name,
        definition: c.definition ?? '',
        demonstration_standard: c.demonstration_standard,
        min_depth: c.min_depth,
        min_autonomy: c.min_autonomy,
        requires_all_core: c.requires_all_core,
        min_core_required: c.min_core_required,
        economic_salience: c.economic_salience,
        notes: c.notes ?? '',
      })
    })

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    setEditing(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const save = async () => {
    try {
      await api.updateCapability(id, draft)
      setEditing(false)
      await reload()
      await onListChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const toggleStatus = async () => {
    if (!capability) return
    await api.updateCapability(id, { status: capability.status === 'active' ? 'deprecated' : 'active' })
    await reload()
    await onListChanged()
  }

  const acceptProposed = async () => {
    await api.updateCapability(id, { status: 'active' })
    await reload()
    await onListChanged()
  }
  const rejectProposed = async () => {
    await api.updateCapability(id, { status: 'rejected' })
    await reload()
    await onListChanged()
  }

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!capability) return <p className="muted">Loading…</p>

  const existingIds = new Set([
    ...capability.components.core.map((c) => c.concept_id),
    ...capability.components.supporting.map((c) => c.concept_id),
    ...capability.components.contextual.map((c) => c.concept_id),
  ])

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700 }}>{capability.canonical_name}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {capability.status} · min depth {capability.min_depth}
            {capability.min_autonomy ? ` · min autonomy ${capability.min_autonomy}` : ''}
          </div>
        </div>
        {capability.coverage && (
          <span style={{ fontWeight: 600, fontSize: 13, color: STATUS_COLOR[capability.coverage.status] }}>
            {capability.coverage.status.replace('_', ' ')}
          </span>
        )}
      </div>

      {capability.status === 'proposed' && (
        <div className="card" style={{ background: 'rgba(120,120,255,0.06)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <p className="secondary" style={{ margin: 0, fontSize: 13 }}>
            Proposed by the vocabulary bootstrap from corpus co-occurrence — not yet part of the catalogue. Review
            the demonstration standard and components below before accepting.
          </p>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="primary" onClick={acceptProposed}>
              Accept into catalogue
            </button>
            <button onClick={rejectProposed}>Reject</button>
            <MergeControl capabilityId={id} onMerged={onListChanged} />
          </div>
        </div>
      )}

      {!editing ? (
        <>
          <p className="secondary" style={{ margin: 0, fontSize: 13 }}>
            {capability.demonstration_standard}
          </p>
          {capability.definition && <p className="muted" style={{ margin: 0, fontSize: 13 }}>{capability.definition}</p>}
          {capability.notes && (
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              Notes: {capability.notes}
            </p>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={() => setEditing(true)}>Edit</button>
            <button onClick={toggleStatus}>{capability.status === 'active' ? 'Deactivate' : 'Reactivate'}</button>
          </div>
        </>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input value={draft.canonical_name ?? ''} onChange={(e) => setDraft({ ...draft, canonical_name: e.target.value })} />
          <input placeholder="definition" value={draft.definition ?? ''} onChange={(e) => setDraft({ ...draft, definition: e.target.value })} />
          <textarea rows={3} value={draft.demonstration_standard ?? ''} onChange={(e) => setDraft({ ...draft, demonstration_standard: e.target.value })} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <select value={draft.min_depth} onChange={(e) => setDraft({ ...draft, min_depth: e.target.value as DepthLevel })}>
              {DEPTH_LEVELS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            <select value={draft.min_autonomy ?? ''} onChange={(e) => setDraft({ ...draft, min_autonomy: (e.target.value || null) as AutonomyLevel | null })}>
              <option value="">(none required)</option>
              {AUTONOMY_LEVELS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={!!draft.requires_all_core} onChange={(e) => setDraft({ ...draft, requires_all_core: e.target.checked })} />
            <span className="secondary">Requires every core component</span>
          </label>
          {!draft.requires_all_core && (
            <input
              type="number"
              min={0}
              placeholder="min core components required"
              value={draft.min_core_required ?? ''}
              onChange={(e) => setDraft({ ...draft, min_core_required: e.target.value === '' ? null : Number(e.target.value) })}
            />
          )}
          <textarea rows={2} placeholder="notes" value={draft.notes ?? ''} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
          {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="primary" onClick={save}>
              Save
            </button>
            <button onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div>
        <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
          Components
        </div>
        <ComponentList capabilityId={id} components={capability.components} onChanged={reload} />
        <div style={{ marginTop: 10 }}>
          <ComponentBrowser capabilityId={id} existingIds={existingIds} onAdded={reload} />
        </div>
        <div style={{ marginTop: 14 }}>
          <ProposedComponentList capabilityId={id} components={capability.components_proposed} onChanged={reload} />
        </div>
      </div>

      {capability.coverage && (
        <div>
          <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 4 }}>
            Evidence summary
          </div>
          <p className="secondary" style={{ fontSize: 13, margin: 0 }}>
            {capability.coverage.trace.status_reason.message}
          </p>
        </div>
      )}
    </div>
  )
}

export default function Capabilities() {
  const [capabilities, setCapabilities] = useState<CapabilitySummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('active')
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [rebuildBusy, setRebuildBusy] = useState(false)
  const [rebuildResult, setRebuildResult] = useState<RebuildSummary | null>(null)

  const reload = () => api.listCapabilities({ status: statusFilter, q: q || undefined }).then(setCapabilities)

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, q])

  const rebuild = async () => {
    setRebuildBusy(true)
    try {
      const result = await api.rebuildCapabilities()
      setRebuildResult(result)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRebuildBusy(false)
    }
  }

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Capability catalogue</h1>
      <p className="secondary" style={{ marginTop: 4 }}>
        Curated, economically meaningful units of "what a person can do" — deliberately not renamed skills. Each one
        composes from core/supporting/contextual atomic concepts and carries its own demonstration standard.
      </p>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '16px 0' }}>
        <input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="active">Active</option>
          <option value="proposed">Proposed</option>
          <option value="deprecated">Deprecated</option>
        </select>
        <button onClick={rebuild} disabled={rebuildBusy}>
          {rebuildBusy ? 'Rebuilding…' : 'Rebuild coverage + role fit'}
        </button>
      </div>
      {rebuildResult && (
        <p className="muted" style={{ fontSize: 12 }}>
          Engine {rebuildResult.engine_version}: {rebuildResult.capability_coverage.computed} capabilities,{' '}
          {rebuildResult.role_fit.computed} roles recomputed.
        </p>
      )}
      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <AddCapabilityForm
            onAdded={async (id) => {
              await reload()
              setSelectedId(id)
            }}
          />
          {capabilities.length === 0 && <p className="muted">No capabilities yet.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {capabilities.map((c) => (
              <div
                key={c.id}
                className="card"
                onClick={() => setSelectedId(c.id)}
                style={{ cursor: 'pointer', borderColor: selectedId === c.id ? 'var(--series-1)' : undefined }}
              >
                <div style={{ fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
                  {c.canonical_name}
                  {c.status === 'proposed' && (
                    <span className="muted" style={{ fontSize: 10, border: '1px solid var(--border)', borderRadius: 999, padding: '0 6px' }}>
                      proposed
                    </span>
                  )}
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  {c.core_component_count} core · {c.supporting_component_count} supporting · {c.contextual_component_count} contextual
                  {c.proposed_component_count > 0 ? ` · ${c.proposed_component_count} proposed` : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          {selectedId ? (
            <CapabilityDetail id={selectedId} onListChanged={reload} />
          ) : (
            <p className="muted">Select a capability on the left, or create a new one.</p>
          )}
        </div>
      </div>
    </div>
  )
}
