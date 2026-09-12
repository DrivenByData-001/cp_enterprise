import { useEffect, useMemo, useState } from 'react'
import { api, type ClusterSplitPreviewResult, type Concept, type ConceptType, type VocabClusterSummary } from '../../lib/api'

// Accept / Merge into… / Reject / Split — the one implementation of pending-
// cluster mutation UI, shared between the Review tab's ClusterCard and the
// Map tab's PendingClusterMapDetails (docs/25 §"Reuse of existing curation
// components"). There is exactly one place that calls
// accept/merge/reject/splitVocabCluster from the UI — neither tab forks this
// logic.

// --- merge target picker (brief §5: "search/select the target accepted concept") --

export function MergeTargetPicker({ onCancel, onMerged, clusterKey }: { onCancel: () => void; onMerged: () => Promise<void>; clusterKey: string }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Concept[]>([])
  const [selected, setSelected] = useState<Concept | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([])
      return
    }
    let cancelled = false
    api
      .listConcepts({ status: 'active', q: query.trim() })
      .then((cs) => !cancelled && setResults(cs.slice(0, 8)))
      .catch(() => !cancelled && setResults([]))
    return () => {
      cancelled = true
    }
  }, [query])

  const submit = async () => {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      await api.mergeVocabCluster({ cluster_key: clusterKey, concept_id: selected.id })
      await onMerged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <input
        autoFocus
        placeholder="Search active concepts…"
        value={selected ? selected.canonical_name : query}
        onChange={(e) => {
          setSelected(null)
          setQuery(e.target.value)
        }}
      />
      {!selected && results.length > 0 && (
        <div className="card" style={{ padding: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {results.map((c) => (
            <button key={c.id} onClick={() => setSelected(c)} style={{ textAlign: 'left', border: 'none', background: 'none' }}>
              {c.canonical_name} <span className="muted">({c.type_code})</span>
            </button>
          ))}
        </div>
      )}
      {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="primary" disabled={!selected || busy} onClick={submit}>
          {busy ? 'Merging…' : 'Merge into selected concept'}
        </button>
        <button onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// --- split cluster ------------------------------------------------------------

export function SplitClusterEditor({
  cluster,
  onCancel,
  onSplit,
}: {
  cluster: VocabClusterSummary
  onCancel: () => void
  onSplit: () => Promise<void>
}) {
  const forms = cluster.surface_forms
  const [groupOf, setGroupOf] = useState<Record<string, number>>(() => Object.fromEntries(forms.map((f, i) => [f, i])))
  const [preview, setPreview] = useState<ClusterSplitPreviewResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const groups = useMemo(() => {
    const byGroup = new Map<number, string[]>()
    for (const f of forms) {
      const g = groupOf[f] ?? 0
      byGroup.set(g, [...(byGroup.get(g) ?? []), f])
    }
    return [...byGroup.values()].filter((g) => g.length > 0)
  }, [groupOf, forms])

  const canSplit = groups.length >= 2

  const setGroup = (form: string, group: number) => {
    setGroupOf((prev) => ({ ...prev, [form]: group }))
    setPreview(null)
    setError(null)
  }

  const review = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.previewVocabSplit({
        cluster_key: cluster.cluster_key,
        groups: groups.map((surface_forms) => ({ surface_forms })),
      })
      setPreview(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.splitVocabCluster({
        cluster_key: cluster.cluster_key,
        groups: groups.map((surface_forms) => ({ surface_forms })),
      })
      await onSplit()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <p className="muted" style={{ fontSize: 12, margin: 0 }}>
        Assign each surface form to a group. Forms in the same group stay together as one pending cluster; different
        groups become independent pending clusters. Nothing is accepted, rejected, or merged by splitting — every
        resulting cluster goes through the normal review actions afterward.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {forms.map((f) => (
          <div key={f} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 13 }}>{f}</span>
            <select value={groupOf[f]} onChange={(e) => setGroup(f, Number(e.target.value))}>
              {forms.map((_, i) => (
                <option key={i} value={i}>
                  Group {i + 1}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>

      {!canSplit && (
        <p style={{ fontSize: 12, color: 'var(--warning)', margin: 0 }}>
          Put at least two surface forms in different groups to split this cluster.
        </p>
      )}

      {preview && (
        <div className="card" style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <strong>Preview — {preview.resulting_groups.length} resulting clusters</strong>
          {preview.resulting_groups.map((g) => (
            <div key={g.new_cluster_key}>
              <strong>{g.suggested_canonical_label}</strong>
              <div className="muted" style={{ fontSize: 12 }}>
                {g.surface_forms.join(', ')} — {g.role_count} role{g.role_count === 1 ? '' : 's'}, {g.observation_count}{' '}
                observation{g.observation_count === 1 ? '' : 's'}
              </div>
            </div>
          ))}
        </div>
      )}

      {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}

      <div style={{ display: 'flex', gap: 8 }}>
        {!preview ? (
          <button className="primary" disabled={!canSplit || busy} onClick={review}>
            {busy ? 'Checking…' : 'Preview split'}
          </button>
        ) : (
          <button className="primary" disabled={busy} onClick={confirm}>
            {busy ? 'Splitting…' : `Confirm split into ${preview.resulting_groups.length} clusters`}
          </button>
        )}
        <button onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// --- the shared accept/merge/reject/split action controls -------------------

export function ClusterActionsPanel({
  cluster,
  conceptTypes,
  onChanged,
}: {
  cluster: VocabClusterSummary
  conceptTypes: ConceptType[]
  onChanged: () => Promise<void>
}) {
  const [mode, setMode] = useState<'idle' | 'accept' | 'merge' | 'split'>('idle')
  const [typeCode, setTypeCode] = useState(cluster.suggested_type ?? conceptTypes[0]?.code ?? '')
  const [name, setName] = useState(cluster.suggested_canonical_label)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // The Map's details panel swaps to a different cluster without unmounting
  // this component — reset the local form state whenever the cluster
  // identity itself changes so a stale name/type never lingers.
  useEffect(() => {
    setMode('idle')
    setTypeCode(cluster.suggested_type ?? conceptTypes[0]?.code ?? '')
    setName(cluster.suggested_canonical_label)
    setError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cluster.cluster_key])

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  if (cluster.status !== 'pending') return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {mode === 'idle' && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button className="primary" onClick={() => setMode('accept')} disabled={busy}>
            Accept
          </button>
          <button onClick={() => setMode('merge')} disabled={busy}>
            Merge into…
          </button>
          <button onClick={() => run(() => api.rejectVocabCluster({ cluster_key: cluster.cluster_key }))} disabled={busy}>
            Reject
          </button>
          {cluster.surface_forms.length > 1 && (
            <button onClick={() => setMode('split')} disabled={busy}>
              Split cluster
            </button>
          )}
        </div>
      )}

      {mode === 'accept' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
              <span className="secondary">Type</span>
              <select value={typeCode} onChange={(e) => setTypeCode(e.target.value)}>
                {conceptTypes.map((t) => (
                  <option key={t.code} value={t.code}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
              <span className="secondary">Canonical name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          </div>
          {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="primary"
              disabled={busy || !name.trim() || !typeCode}
              onClick={() =>
                run(() => api.acceptVocabCluster({ cluster_key: cluster.cluster_key, type_code: typeCode, canonical_name: name.trim() }))
              }
            >
              {busy ? 'Saving…' : 'Create & accept'}
            </button>
            <button onClick={() => setMode('idle')} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {mode === 'merge' && <MergeTargetPicker clusterKey={cluster.cluster_key} onCancel={() => setMode('idle')} onMerged={onChanged} />}

      {mode === 'split' && (
        <SplitClusterEditor
          cluster={cluster}
          onCancel={() => setMode('idle')}
          onSplit={async () => {
            setMode('idle')
            await onChanged()
          }}
        />
      )}

      {error && mode === 'idle' && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
    </div>
  )
}
