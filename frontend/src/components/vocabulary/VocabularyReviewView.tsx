import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import ConceptDetailsDrawer from '../ConceptDetailsDrawer'
import {
  api,
  type AcceptedVocabularyOverview,
  type BatchAcceptItemInput,
  type BatchPreviewResult,
  type Concept,
  type ConceptInput,
  type ConceptType,
  type VocabClusterFilters,
  type VocabClusterSummary,
  type VocabMethodology,
  type VocabProgress,
} from '../../lib/api'
import { Badge, FlagBadges, PriorityBandBadge } from './shared'
import { ClusterActionsPanel } from './ClusterActions'

const PAGE_SIZE = 20

// --- progress / empty-vocabulary messaging (brief §10/§11) -----------------

function ProgressPanel({ progress }: { progress: VocabProgress | null }) {
  if (!progress) return null
  const mappedTotal = progress.observations_mapped + progress.observations_unresolved
  const mappedPct = mappedTotal > 0 ? Math.round((progress.observations_mapped / mappedTotal) * 100) : 0
  return (
    <div className="card" style={{ marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {!progress.canonical_vocabulary_curated && (
        <p style={{ margin: 0, fontSize: 13 }}>
          <strong>Canonical vocabulary is not yet curated.</strong> 0 concepts have been accepted yet — this is not the
          same as "no match found" elsewhere in this app (Comparison, profile360 mapping): those views have nothing to
          match against yet, which says nothing about the strength of any evidence. Accepting clusters below moves this
          from "not yet curated" toward normal mapping behaviour.
        </p>
      )}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', fontSize: 13 }}>
        <span>
          <strong>{progress.total_clusters}</strong> <span className="muted">clusters total</span>
        </span>
        <span>
          <strong>{progress.pending_clusters}</strong> <span className="muted">pending</span>
        </span>
        <span>
          <strong>{progress.accepted_clusters}</strong> <span className="muted">accepted</span>
        </span>
        <span>
          <strong>{progress.rejected_clusters}</strong> <span className="muted">rejected</span>
        </span>
        <span>
          <strong style={{ color: 'var(--good)' }}>{progress.high_priority_pending_clusters}</strong>{' '}
          <span className="muted">high-priority pending</span>
        </span>
        <span>
          <strong>{progress.accepted_concepts}</strong> <span className="muted">canonical concepts</span>
        </span>
      </div>
      <div style={{ fontSize: 12 }} className="muted">
        {progress.observations_mapped} of {mappedTotal} role-skill observations mapped to canonical concepts ({mappedPct}
        %). Some observations may legitimately remain unresolved or rejected — 100% mapping is not the goal.
      </div>
    </div>
  )
}

function MethodologyPanel({ methodology }: { methodology: VocabMethodology | null }) {
  const [open, setOpen] = useState(false)
  if (!methodology) return null
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <button onClick={() => setOpen((o) => !o)} style={{ fontSize: 13 }}>
        {open ? 'Hide' : 'How is priority decided?'}
      </button>
      {open && (
        <pre
          style={{
            whiteSpace: 'pre-wrap',
            fontFamily: 'inherit',
            fontSize: 12.5,
            marginTop: 10,
            color: 'var(--text-secondary)',
          }}
        >
          {methodology.text}
        </pre>
      )}
    </div>
  )
}

// --- filter bar (brief §3) --------------------------------------------------

function FilterBar({ filters, onChange }: { filters: VocabClusterFilters; onChange: (f: VocabClusterFilters) => void }) {
  const set = (patch: Partial<VocabClusterFilters>) => onChange({ ...filters, ...patch })
  return (
    <div className="card" style={{ marginBottom: 16, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <select value={filters.status ?? 'pending'} onChange={(e) => set({ status: e.target.value as VocabClusterFilters['status'] })}>
        <option value="pending">Pending</option>
        <option value="accepted">Accepted</option>
        <option value="rejected">Rejected</option>
        <option value="all">All</option>
      </select>
      <input
        placeholder="Search surface form / label…"
        value={filters.q ?? ''}
        onChange={(e) => set({ q: e.target.value })}
        style={{ minWidth: 200 }}
      />
      {filters.status !== 'accepted' && filters.status !== 'rejected' && (
        <>
          <select value={filters.band ?? ''} onChange={(e) => set({ band: (e.target.value || undefined) as VocabClusterFilters['band'] })}>
            <option value="">All bands</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
            <option value="sparse">Sparse</option>
          </select>
          <input
            type="number"
            min={0}
            placeholder="Min roles"
            value={filters.min_role_count ?? ''}
            onChange={(e) => set({ min_role_count: e.target.value ? Number(e.target.value) : undefined })}
            style={{ width: 90 }}
          />
          <input
            type="number"
            min={0}
            placeholder="Min obs."
            value={filters.min_observation_count ?? ''}
            onChange={(e) => set({ min_observation_count: e.target.value ? Number(e.target.value) : undefined })}
            style={{ width: 90 }}
          />
          <input
            placeholder="Country"
            value={filters.country ?? ''}
            onChange={(e) => set({ country: e.target.value || undefined })}
            style={{ width: 100 }}
          />
          <input
            placeholder="Seniority"
            value={filters.seniority ?? ''}
            onChange={(e) => set({ seniority: e.target.value || undefined })}
            style={{ width: 100 }}
          />
          <select value={filters.sort ?? 'priority'} onChange={(e) => set({ sort: e.target.value as VocabClusterFilters['sort'] })}>
            <option value="priority">Sort: priority (default)</option>
            <option value="occurrence">Sort: observation count</option>
            <option value="role_count">Sort: role count</option>
            <option value="recent">Sort: most recent</option>
            <option value="alphabetical">Sort: alphabetical</option>
          </select>
        </>
      )}
    </div>
  )
}

// --- review card (brief §4) -------------------------------------------------

function ClusterCard({
  cluster,
  conceptTypes,
  selected,
  onToggleSelect,
  onChanged,
  onOpenDetails,
}: {
  cluster: VocabClusterSummary
  conceptTypes: ConceptType[]
  selected: boolean
  onToggleSelect: (() => void) | null
  onChanged: () => Promise<void>
  onOpenDetails: (conceptId: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const isPending = cluster.status === 'pending'

  const yearSpan =
    cluster.distinct_years && cluster.distinct_years.length > 0
      ? cluster.distinct_years.length === 1
        ? String(cluster.distinct_years[0])
        : `${cluster.distinct_years[0]}–${cluster.distinct_years[cluster.distinct_years.length - 1]} (${cluster.distinct_years.length} yrs)`
      : '—'

  const visibleRoles = expanded ? cluster.example_roles : cluster.example_roles?.slice(0, 3)

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          {onToggleSelect && isPending && (
            <input type="checkbox" checked={selected} onChange={onToggleSelect} style={{ marginTop: 4 }} />
          )}
          <div>
            <div style={{ fontWeight: 600, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              {cluster.suggested_canonical_label}
              <PriorityBandBadge band={cluster.priority_band} />
              {!isPending && <Badge color="var(--text-muted)">{cluster.status.replace('_', ' ')}</Badge>}
              {!isPending && cluster.resolved_concept_id && (
                <button style={{ fontSize: 12, padding: '2px 10px' }} onClick={() => onOpenDetails(cluster.resolved_concept_id!)}>
                  Details
                </button>
              )}
            </div>
            {cluster.surface_forms.length > 1 && (
              <div className="muted" style={{ fontSize: 12 }}>
                aliases: {cluster.surface_forms.filter((s) => s !== cluster.suggested_canonical_label).join(', ')}
              </div>
            )}
            <div className="secondary" style={{ fontSize: 13, display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 2 }}>
              {isPending ? (
                <>
                  <span>{cluster.role_count} role{cluster.role_count === 1 ? '' : 's'}</span>
                  <span>{cluster.observation_count} observation{cluster.observation_count === 1 ? '' : 's'}</span>
                  <span>{yearSpan}</span>
                  {cluster.countries && cluster.countries.length > 0 && <span>{cluster.countries.join(', ')}</span>}
                  {cluster.seniority_levels && cluster.seniority_levels.length > 0 && <span>{cluster.seniority_levels.join(', ')}</span>}
                  {cluster.priority_score !== null && <span className="muted">score {cluster.priority_score.toFixed(2)}</span>}
                </>
              ) : (
                <span>
                  {cluster.resolved_canonical_name ? `→ ${cluster.resolved_canonical_name}` : ''}
                  {cluster.resolved_at ? ` · ${new Date(cluster.resolved_at).toLocaleDateString()}` : ''}
                </span>
              )}
            </div>
            {isPending && <FlagBadges flags={cluster.flags} />}
          </div>
        </div>
      </div>

      {isPending && (cluster.example_roles?.length ?? 0) > 0 && (
        <div style={{ fontSize: 12 }}>
          <span className="muted">example roles: </span>
          {visibleRoles?.map((r, i) => (
            <span key={r.id}>
              {i > 0 && ', '}
              <Link to={`/roles/${r.id}`}>{r.title ?? r.id}</Link>
            </span>
          ))}
        </div>
      )}

      {isPending && (
        <button onClick={() => setExpanded((e) => !e)} style={{ alignSelf: 'flex-start', fontSize: 12, padding: '3px 10px' }}>
          {expanded ? 'Show less' : 'Show more evidence'}
        </button>
      )}
      {expanded && isPending && (
        <div className="muted" style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span>Cluster key: {cluster.cluster_key}</span>
          <span>Surface forms: {cluster.surface_forms.join(', ')}</span>
          <span>First observed: {cluster.first_observed ?? '—'} · Last observed: {cluster.last_observed ?? '—'}</span>
          {cluster.career_tracks && cluster.career_tracks.length > 0 && <span>Career tracks: {cluster.career_tracks.join(', ')}</span>}
          {cluster.nearest_concept_id && cluster.nearest_similarity !== null && (
            <span>Nearest existing concept similarity: {Math.round((cluster.nearest_similarity ?? 0) * 100)}%</span>
          )}
        </div>
      )}

      {isPending && <ClusterActionsPanel cluster={cluster} conceptTypes={conceptTypes} onChanged={onChanged} />}
    </div>
  )
}

// --- batch review (brief §6) -------------------------------------------------

function BatchAcceptModal({
  clusters,
  conceptTypes,
  onClose,
  onDone,
}: {
  clusters: VocabClusterSummary[]
  conceptTypes: ConceptType[]
  onClose: () => void
  onDone: () => Promise<void>
}) {
  const [rows, setRows] = useState<BatchAcceptItemInput[]>(
    clusters.map((c) => ({ cluster_key: c.cluster_key, canonical_name: c.suggested_canonical_label, type_code: c.suggested_type ?? '' })),
  )
  const [preview, setPreview] = useState<BatchPreviewResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const allValid = rows.every((r) => r.canonical_name?.trim() && r.type_code)

  const updateRow = (i: number, patch: Partial<BatchAcceptItemInput>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))

  const review = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.previewVocabBatch({ action: 'accept', items: rows })
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
      await api.executeVocabBatch({ action: 'accept', items: rows })
      await onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex',
        alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20,
      }}
    >
      <div className="card" style={{ maxWidth: 640, width: '100%', maxHeight: '85vh', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Batch accept {clusters.length} cluster{clusters.length === 1 ? '' : 's'}</h2>

        {!preview && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {rows.map((row, i) => (
              <div key={row.cluster_key} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <input value={row.canonical_name ?? ''} onChange={(e) => updateRow(i, { canonical_name: e.target.value })} />
                <select value={row.type_code ?? ''} onChange={(e) => updateRow(i, { type_code: e.target.value })}>
                  <option value="">choose type…</option>
                  {conceptTypes.map((t) => (
                    <option key={t.code} value={t.code}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        )}

        {preview && (
          <div style={{ fontSize: 14, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span>{preview.clusters_ready} of {preview.clusters_selected} clusters ready to accept</span>
            <span>{preview.resulting_concepts} new canonical concepts will be created</span>
            <span>~{preview.aliases_estimate} aliases will be created</span>
            <span>{preview.observations_affected} role-skill observations will be mapped</span>
            {preview.clusters_not_pending.length > 0 && (
              <span style={{ color: 'var(--warning)' }}>
                {preview.clusters_not_pending.length} cluster(s) are no longer pending and will be skipped.
              </span>
            )}
          </div>
        )}

        {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
          {!preview ? (
            <button className="primary" disabled={!allValid || busy} onClick={review}>
              {busy ? 'Checking…' : 'Review'}
            </button>
          ) : (
            <button className="primary" disabled={busy} onClick={confirm}>
              {busy ? 'Accepting…' : `Confirm accept (${preview.clusters_ready})`}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function BatchRejectModal({ clusterKeys, onClose, onDone }: { clusterKeys: string[]; onClose: () => void; onDone: () => Promise<void> }) {
  const [preview, setPreview] = useState<BatchPreviewResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .previewVocabBatch({ action: 'reject', items: clusterKeys.map((cluster_key) => ({ cluster_key })) })
      .then(setPreview)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.executeVocabBatch({ action: 'reject', items: clusterKeys.map((cluster_key) => ({ cluster_key })) })
      await onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20 }}>
      <div className="card" style={{ maxWidth: 480, width: '100%', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Batch reject {clusterKeys.length} cluster{clusterKeys.length === 1 ? '' : 's'}</h2>
        {preview && (
          <p style={{ fontSize: 14, margin: 0 }}>
            {preview.clusters_ready} of {preview.clusters_selected} clusters are still pending and will be rejected. No
            concepts or aliases are ever created by a reject.
          </p>
        )}
        {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="primary" disabled={busy || !preview} onClick={confirm}>
            {busy ? 'Rejecting…' : `Confirm reject (${preview?.clusters_ready ?? '…'})`}
          </button>
        </div>
      </div>
    </div>
  )
}

// --- concept browser (pre-existing, unrelated to cluster curation) ---------

const EMPTY_CONCEPT: ConceptInput = { type_code: '', canonical_name: '', definition: '' }

function AddConceptForm({ conceptTypes, onAdded }: { conceptTypes: ConceptType[]; onAdded: () => Promise<void> }) {
  const [form, setForm] = useState<ConceptInput>({ ...EMPTY_CONCEPT, type_code: conceptTypes[0]?.code ?? '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!form.canonical_name.trim() || !form.type_code) return
    setBusy(true)
    setError(null)
    try {
      await api.createConcept({ ...form, canonical_name: form.canonical_name.trim() })
      setForm({ ...EMPTY_CONCEPT, type_code: form.type_code })
      await onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Type</span>
          <select value={form.type_code} onChange={(e) => setForm({ ...form, type_code: e.target.value })}>
            {conceptTypes.map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Canonical name</span>
          <input
            value={form.canonical_name}
            onChange={(e) => setForm({ ...form, canonical_name: e.target.value })}
            placeholder="e.g. Chain ladder"
          />
        </label>
      </div>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Definition (optional)</span>
        <input value={form.definition ?? ''} onChange={(e) => setForm({ ...form, definition: e.target.value })} />
      </label>
      {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
      <div>
        <button className="primary" onClick={submit} disabled={busy || !form.canonical_name.trim()}>
          {busy ? 'Adding…' : 'Add concept'}
        </button>
      </div>
    </div>
  )
}

export interface ConceptBrowserFocus {
  typeCode?: string
  conceptIds?: string[]
  label?: string
}

function ConceptBrowser({
  conceptTypes,
  refreshSignal,
  onOpenDetails,
  focus,
  onClearFocus,
}: {
  conceptTypes: ConceptType[]
  refreshSignal: number
  onOpenDetails: (conceptId: string) => void
  focus?: ConceptBrowserFocus | null
  onClearFocus?: () => void
}) {
  const [browseType, setBrowseType] = useState('')
  const [concepts, setConcepts] = useState<Concept[]>([])

  useEffect(() => {
    if (focus) setBrowseType(focus.typeCode ?? '')
  }, [focus])

  const reload = () => api.listConcepts({ type_code: browseType || undefined }).then(setConcepts)

  useEffect(() => {
    reload().catch(() => setConcepts([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browseType, refreshSignal])

  const visibleConcepts = focus?.conceptIds ? concepts.filter((c) => focus.conceptIds!.includes(c.id)) : concepts

  return (
    <section style={{ marginTop: 28 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <h2 style={{ fontSize: 16, margin: 0 }}>Concepts</h2>
        <select
          value={browseType}
          onChange={(e) => {
            setBrowseType(e.target.value)
            onClearFocus?.()
          }}
        >
          <option value="">All types</option>
          {conceptTypes.map((t) => (
            <option key={t.code} value={t.code}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      {focus?.conceptIds && (
        <p className="muted" style={{ fontSize: 12 }}>
          Showing {visibleConcepts.length} concept(s){focus.label ? ` — ${focus.label}` : ''}.{' '}
          <button style={{ fontSize: 12, padding: '1px 8px' }} onClick={onClearFocus}>
            Clear filter
          </button>
        </p>
      )}
      <div style={{ marginBottom: 12 }}>
        <AddConceptForm conceptTypes={conceptTypes} onAdded={reload} />
      </div>
      {visibleConcepts.length === 0 && <p className="muted">No concepts match.</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {visibleConcepts.map((c) => (
          <div key={c.id} className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
            <div>
              <div style={{ fontWeight: 600 }}>{c.canonical_name}</div>
              {c.definition && (
                <div className="secondary" style={{ fontSize: 13 }}>
                  {c.definition}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
              <span className="muted" style={{ fontSize: 12 }}>
                {c.type_code}
              </span>
              <button style={{ fontSize: 12, padding: '2px 10px' }} onClick={() => onOpenDetails(c.id)}>
                Details
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

// --- Accepted Vocabulary overview (Phase 4 prompt §14) ----------------------

function AcceptedVocabularyOverviewPanel({
  refreshSignal,
  onFocus,
}: {
  refreshSignal: number
  onFocus: (focus: ConceptBrowserFocus) => void
}) {
  const [overview, setOverview] = useState<AcceptedVocabularyOverview | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getAcceptedVocabularyOverview()
      .then(setOverview)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [refreshSignal])

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!overview) return <p className="muted">Loading accepted vocabulary overview…</p>

  const countButtonStyle: React.CSSProperties = {
    display: 'block',
    fontSize: 12,
    padding: '2px 8px',
    background: 'none',
    border: '1px solid var(--text-muted)',
    borderRadius: 8,
    cursor: 'pointer',
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2 style={{ fontSize: 15, margin: 0 }}>Accepted Vocabulary overview</h2>
        <span className="muted" style={{ fontSize: 12 }}>
          {overview.total_active_concepts} active concepts · {overview.alias_count} alias(es)
        </span>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
        {overview.by_type.map((row) => (
          <button
            key={row.type_code}
            type="button"
            style={countButtonStyle}
            onClick={() => onFocus({ typeCode: row.type_code, label: row.type_code })}
          >
            {row.type_code}: <strong>{row.count}</strong>
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
        <button
          type="button"
          style={countButtonStyle}
          onClick={() => onFocus({ conceptIds: overview.missing_definition.concept_ids, label: 'missing a definition' })}
          disabled={overview.missing_definition.count === 0}
        >
          Missing definition: <strong>{overview.missing_definition.count}</strong>
        </button>
        <button
          type="button"
          style={countButtonStyle}
          onClick={() => onFocus({ conceptIds: overview.no_active_dossier.concept_ids, label: 'no active Concept Dossier' })}
          disabled={overview.no_active_dossier.count === 0}
        >
          No active dossier: <strong>{overview.no_active_dossier.count}</strong>
        </button>
        <button
          type="button"
          style={countButtonStyle}
          onClick={() =>
            onFocus({ conceptIds: overview.dossier_no_related_concepts.concept_ids, label: "dossier has no related concepts" })
          }
          disabled={overview.dossier_no_related_concepts.count === 0}
        >
          Dossier w/ no related concepts: <strong>{overview.dossier_no_related_concepts.count}</strong>
        </button>
      </div>
      {overview.recent_concepts.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <p className="muted" style={{ fontSize: 12, margin: '0 0 4px' }}>
            Recently accepted/reviewed
          </p>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {overview.recent_concepts.map((c) => (
              <span key={c.id} style={{ fontSize: 12 }} className="muted">
                {c.canonical_name} ({c.type_code})
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// --- main view ---------------------------------------------------------------

export default function VocabularyReviewView({
  conceptTypes,
  focusRequest,
  onFocusHandled,
}: {
  conceptTypes: ConceptType[]
  /** Set by the Map tab's "Open cluster review" action (status: 'pending') or
   * a deep link from elsewhere in the app pointing at an already-accepted
   * concept (status: 'accepted', e.g. requirement review's "concept type is
   * global — open it here" link) — filters straight to it without
   * duplicating the queue's own filter/search logic. */
  focusRequest?: { q: string; status: 'pending' | 'accepted' } | null
  onFocusHandled?: () => void
}) {
  const [progress, setProgress] = useState<VocabProgress | null>(null)
  const [methodology, setMethodology] = useState<VocabMethodology | null>(null)
  const [filters, setFilters] = useState<VocabClusterFilters>({ status: 'pending', sort: 'priority' })
  const [offset, setOffset] = useState(0)
  const [clusters, setClusters] = useState<VocabClusterSummary[]>([])
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [batchModal, setBatchModal] = useState<'accept' | 'reject' | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [drawerConceptId, setDrawerConceptId] = useState<string | null>(null)
  const [conceptRefreshSignal, setConceptRefreshSignal] = useState(0)
  const [browserFocus, setBrowserFocus] = useState<ConceptBrowserFocus | null>(null)

  const reloadClusters = () =>
    api.listVocabClusters({ ...filters, limit: PAGE_SIZE, offset }).then((res) => {
      setClusters(res.items)
      setTotal(res.total)
    })
  const reloadProgress = () => api.getVocabProgress().then(setProgress)

  useEffect(() => {
    setLoading(true)
    Promise.all([api.getVocabMethodology(), reloadProgress(), reloadClusters()])
      .then(([meth]) => setMethodology(meth))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!focusRequest) return
    setFilters((f) => ({ ...f, status: focusRequest.status, q: focusRequest.q, sort: 'priority' }))
    onFocusHandled?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequest])

  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.status, filters.q, filters.band, filters.min_role_count, filters.min_observation_count, filters.country, filters.seniority, filters.sort])

  useEffect(() => {
    setLoading(true)
    reloadClusters()
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, offset])

  const refreshAfterAction = async () => {
    setSelected(new Set())
    await Promise.all([reloadClusters(), reloadProgress()])
  }

  const toggleSelect = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const selectableOnPage = useMemo(() => clusters.filter((c) => c.status === 'pending'), [clusters])
  const allPageSelected = selectableOnPage.length > 0 && selectableOnPage.every((c) => selected.has(c.cluster_key))
  const toggleSelectPage = () =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (allPageSelected) selectableOnPage.forEach((c) => next.delete(c.cluster_key))
      else selectableOnPage.forEach((c) => next.add(c.cluster_key))
      return next
    })

  const selectedClusters = clusters.filter((c) => selected.has(c.cluster_key))
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + PAGE_SIZE, total)

  return (
    <div>
      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}

      <ProgressPanel progress={progress} />
      <MethodologyPanel methodology={methodology} />
      <FilterBar filters={filters} onChange={setFilters} />

      {filters.status === 'accepted' && (
        <AcceptedVocabularyOverviewPanel
          refreshSignal={conceptRefreshSignal}
          onFocus={(focus) => setBrowserFocus(focus)}
        />
      )}

      {loading && <p className="muted">Loading…</p>}
      {!loading && clusters.length === 0 && <p className="muted">Nothing matches the current filters.</p>}

      {filters.status === 'pending' && selectableOnPage.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 13 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={allPageSelected} onChange={toggleSelectPage} />
            <span className="muted">Select all on this page</span>
          </label>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: selected.size > 0 ? 72 : 16 }}>
        {clusters.map((c) => (
          <ClusterCard
            key={c.cluster_key}
            cluster={c}
            conceptTypes={conceptTypes}
            selected={selected.has(c.cluster_key)}
            onToggleSelect={filters.status === 'pending' ? () => toggleSelect(c.cluster_key) : null}
            onChanged={refreshAfterAction}
            onOpenDetails={setDrawerConceptId}
          />
        ))}
      </div>

      {total > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <span className="muted" style={{ fontSize: 13 }}>
            {pageStart}–{pageEnd} of {total}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <button disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </button>
          </div>
        </div>
      )}

      {selected.size > 0 && (
        <div
          className="card"
          style={{
            position: 'fixed', bottom: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 40,
            display: 'flex', gap: 12, alignItems: 'center', boxShadow: '0 4px 20px rgba(0,0,0,0.25)',
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600 }}>{selected.size} selected</span>
          <button className="primary" onClick={() => setBatchModal('accept')}>
            Batch accept…
          </button>
          <button onClick={() => setBatchModal('reject')}>Batch reject…</button>
          <button onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}

      {batchModal === 'accept' && (
        <BatchAcceptModal
          clusters={selectedClusters}
          conceptTypes={conceptTypes}
          onClose={() => setBatchModal(null)}
          onDone={async () => {
            setBatchModal(null)
            await refreshAfterAction()
          }}
        />
      )}
      {batchModal === 'reject' && (
        <BatchRejectModal
          clusterKeys={[...selected]}
          onClose={() => setBatchModal(null)}
          onDone={async () => {
            setBatchModal(null)
            await refreshAfterAction()
          }}
        />
      )}

      <ConceptBrowser
        conceptTypes={conceptTypes}
        refreshSignal={conceptRefreshSignal}
        onOpenDetails={setDrawerConceptId}
        focus={browserFocus}
        onClearFocus={() => setBrowserFocus(null)}
      />

      {drawerConceptId && (
        <ConceptDetailsDrawer
          conceptId={drawerConceptId}
          conceptTypes={conceptTypes}
          onClose={() => setDrawerConceptId(null)}
          onConceptChanged={() => {
            setConceptRefreshSignal((n) => n + 1)
            reloadClusters()
            reloadProgress()
          }}
          onNavigate={setDrawerConceptId}
        />
      )}
    </div>
  )
}
