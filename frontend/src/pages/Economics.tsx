import { useEffect, useState } from 'react'
import {
  api,
  type Archetype,
  type ArchetypeCompRow,
  type ArchetypeDemandRow,
  type ArchetypeDetail,
  type CompensationObservation,
  type GapValueContext,
  type GapValueRow,
  type Market,
  type MarketSurveyDocument,
  type MarketSurveyDocumentDetail,
  type Phase4Readiness,
  type TitleGroup,
} from '../lib/api'

// Money formatting has no existing precedent elsewhere in this app
// (RoleDetail.tsx prints raw numbers) — introduced here since Economics is
// the first page whose whole point is showing compensation figures
// legibly. Never more precise than the underlying evidence: always rounded
// to the nearest whole unit, never a decimal a source didn't state.
function formatMoney(amount: number | null | undefined, currency: string): string {
  if (amount === null || amount === undefined) return '—'
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount)
  } catch {
    return `${Math.round(amount).toLocaleString()} ${currency}`
  }
}

function EvidenceQualityBadge({ quality }: { quality: string }) {
  const colors: Record<string, string> = {
    good: 'var(--good)',
    moderate: 'var(--good)',
    thin: 'var(--warning)',
    insufficient: 'var(--text-muted)',
  }
  return (
    <span
      style={{
        fontSize: 11,
        padding: '2px 8px',
        borderRadius: 10,
        border: `1px solid ${colors[quality] || 'var(--text-muted)'}`,
        color: colors[quality] || 'var(--text-muted)',
      }}
    >
      {quality === 'insufficient' ? 'insufficient compensation evidence' : `${quality} evidence`}
    </span>
  )
}

function Badge({ children, tone = 'muted' }: { children: React.ReactNode; tone?: 'muted' | 'good' | 'warning' }) {
  const color = tone === 'good' ? 'var(--good)' : tone === 'warning' ? 'var(--warning)' : 'var(--text-muted)'
  return <span style={{ fontSize: 11, color, border: `1px solid ${color}`, borderRadius: 8, padding: '1px 6px' }}>{children}</span>
}

// --- Gap Value tab -----------------------------------------------------------

function GapValueTab() {
  const [contexts, setContexts] = useState<GapValueContext[] | null>(null)
  const [marketId, setMarketId] = useState('')
  const [currency, setCurrency] = useState('')
  const [rows, setRows] = useState<GapValueRow[] | null>(null)
  const [archetypeNames, setArchetypeNames] = useState<Record<string, string>>({})
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listGapValueContexts()
      .then((cs) => {
        setContexts(cs)
        if (cs.length > 0) {
          setMarketId(cs[0].market_id)
          setCurrency(cs[0].currency)
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
    api.listArchetypes('all').then((archs) => {
      setArchetypeNames(Object.fromEntries(archs.map((a) => [a.id, a.canonical_name])))
    })
  }, [])

  useEffect(() => {
    if (!marketId || !currency) return
    api
      .listGapValue(marketId, currency)
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marketId, currency])

  if (contexts === null) return <p className="muted">Loading…</p>
  if (contexts.length === 0) {
    return (
      <div className="card">
        <p className="muted">
          No compensation evidence yet — Gap Value needs at least one accepted compensation observation in some
          market/currency before it has anything to rank. Backfill posting compensation or ingest a market report
          in the Market Data tab, then rebuild from Benchmarks / Readiness.
        </p>
      </div>
    )
  }

  return (
    <div>
      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', margin: '12px 0' }}>
        <label>
          Market / currency:{' '}
          <select
            value={`${marketId}|${currency}`}
            onChange={(e) => {
              const [m, c] = e.target.value.split('|')
              setMarketId(m)
              setCurrency(c)
            }}
          >
            {contexts.map((c) => (
              <option key={`${c.market_id}|${c.currency}`} value={`${c.market_id}|${c.currency}`}>
                {c.market_label} — {c.currency}
              </option>
            ))}
          </select>
        </label>
        {rows && rows.length > 0 && (
          <span className="muted" style={{ fontSize: 12 }}>
            through {rows[0].period_end} · engine {rows[0].engine_version}
          </span>
        )}
      </div>

      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="muted">No capability gap has structural impact in this market/currency yet.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {rows.map((row) => (
            <div key={row.capability_concept_id} className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <div>
                  <strong>
                    #{row.rank} {row.canonical_name}
                  </strong>
                </div>
                <EvidenceQualityBadge quality={row.evidence_quality} />
              </div>
              <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 13 }}>
                <span>
                  <strong>{row.archetypes_unlocked}</strong> archetype{row.archetypes_unlocked === 1 ? '' : 's'} unlocked
                </span>
                <span>
                  <strong>{row.archetypes_improved}</strong> improved
                </span>
                <span className="muted">
                  {row.roles_unlocked} role(s) unlocked, {row.roles_improved} improved
                </span>
              </div>
              <div style={{ marginTop: 8 }}>
                {row.reference_comp_unlocked !== null ? (
                  <span>
                    Reference comp unlocked: <strong>{formatMoney(row.reference_comp_unlocked, row.currency)}</strong>
                    {row.comp_delta_vs_best_current_reachable !== null && (
                      <span style={{ marginLeft: 8, color: row.comp_delta_vs_best_current_reachable >= 0 ? 'var(--good)' : 'var(--critical)' }}>
                        {row.comp_delta_vs_best_current_reachable >= 0 ? '+' : ''}
                        {formatMoney(row.comp_delta_vs_best_current_reachable, row.currency)} vs best currently reachable
                      </span>
                    )}
                    <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
                      ({row.n_comp_observations} observations)
                    </span>
                  </span>
                ) : (
                  <span className="muted">No qualifying compensation reference for the unlocked archetype(s) yet.</span>
                )}
              </div>
              <button
                type="button"
                className="secondary"
                style={{ marginTop: 8 }}
                onClick={() => setExpanded(expanded === row.capability_concept_id ? null : row.capability_concept_id)}
              >
                {expanded === row.capability_concept_id ? 'Hide trace' : 'Show trace'}
              </button>
              {expanded === row.capability_concept_id && (
                <div style={{ marginTop: 8, fontSize: 13, borderTop: '1px solid var(--surface-1)', paddingTop: 8 }}>
                  <p className="muted" style={{ margin: '4px 0' }}>
                    Best currently-reachable benchmark: {formatMoney(row.trace.best_current_reachable ?? null, row.currency)}
                  </p>
                  <p style={{ margin: '4px 0' }}>
                    Unlocked archetypes:{' '}
                    {(row.trace.unlocked_archetype_ids || []).map((id) => archetypeNames[id] || id).join(', ') || 'none'}
                  </p>
                  <p style={{ margin: '4px 0' }}>
                    Improved archetypes:{' '}
                    {(row.trace.improved_archetype_ids || []).map((id) => archetypeNames[id] || id).join(', ') || 'none'}
                  </p>
                  <p className="muted" style={{ margin: '4px 0' }}>
                    Counterfactual only — this does not mean the capability has been evidenced.
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// --- Archetypes tab -----------------------------------------------------------

function TitleGroupCard({ group, archetypes, onDone }: { group: TitleGroup; archetypes: Archetype[]; onDone: () => void }) {
  const [selected, setSelected] = useState<Set<string>>(new Set(group.roles.map((r) => r.id)))
  const [newName, setNewName] = useState(group.sample_title || '')
  const [existingId, setExistingId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggle = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  const createNew = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.createArchetype({ canonical_name: newName, role_instance_ids: [...selected] })
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const assignExisting = async () => {
    if (!existingId) return
    setBusy(true)
    setError(null)
    try {
      await api.assignRolesToArchetype(existingId, [...selected])
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <strong>{group.sample_title}</strong>
        <span className="muted">{group.role_count} role(s)</span>
      </div>
      <div style={{ margin: '8px 0', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {group.roles.map((r) => (
          <label key={r.id} style={{ fontSize: 13 }}>
            <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)} /> {r.title}
            {r.organisation ? ` — ${r.organisation}` : ''}
            {r.country ? ` (${r.country})` : ''}
          </label>
        ))}
      </div>
      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New archetype name" style={{ minWidth: 200 }} />
        <button type="button" disabled={busy || selected.size === 0 || !newName.trim()} onClick={createNew}>
          Create archetype from selection
        </button>
        <span className="muted">or</span>
        <select value={existingId} onChange={(e) => setExistingId(e.target.value)}>
          <option value="">Assign to existing archetype…</option>
          {archetypes.map((a) => (
            <option key={a.id} value={a.id}>
              {a.canonical_name}
            </option>
          ))}
        </select>
        <button type="button" disabled={busy || selected.size === 0 || !existingId} onClick={assignExisting}>
          Assign
        </button>
      </div>
    </div>
  )
}

function ArchetypeRow({ archetype, onChanged }: { archetype: Archetype; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<ArchetypeDetail | null>(null)
  const [demand, setDemand] = useState<ArchetypeDemandRow[] | null>(null)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({
    canonical_name: archetype.canonical_name,
    seniority_band: archetype.seniority_band || '',
    typical_market: archetype.typical_market || '',
    notes: archetype.notes || '',
  })

  const load = () => {
    api.getArchetype(archetype.id).then(setDetail)
    api.getArchetypeDemand(archetype.id).then(setDemand)
  }

  useEffect(() => {
    if (open && !detail) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const save = async () => {
    await api.updateArchetype(archetype.id, form)
    setEditing(false)
    onChanged()
    load()
  }

  const toggleDeprecated = async () => {
    await api.updateArchetype(archetype.id, { status: archetype.status === 'active' ? 'deprecated' : 'active' })
    onChanged()
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', cursor: 'pointer' }} onClick={() => setOpen(!open)}>
        <strong>{archetype.canonical_name}</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {archetype.role_count} role(s) · {archetype.accepted_compensation_observation_count} accepted comp observation(s)
          {archetype.status !== 'active' && <Badge tone="warning">{archetype.status}</Badge>}
        </span>
      </div>
      {open && (
        <div style={{ marginTop: 8, borderTop: '1px solid var(--surface-1)', paddingTop: 8 }}>
          {editing ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <input value={form.canonical_name} onChange={(e) => setForm({ ...form, canonical_name: e.target.value })} placeholder="Canonical name" />
              <input value={form.seniority_band} onChange={(e) => setForm({ ...form, seniority_band: e.target.value })} placeholder="Seniority band" />
              <input value={form.typical_market} onChange={(e) => setForm({ ...form, typical_market: e.target.value })} placeholder="Typical market" />
              <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="Notes" />
              <div style={{ display: 'flex', gap: 8 }}>
                <button type="button" className="primary" onClick={save}>
                  Save
                </button>
                <button type="button" onClick={() => setEditing(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div>
              <p className="muted" style={{ margin: '2px 0' }}>
                Seniority: {archetype.seniority_band || '—'} · Typical market: {archetype.typical_market || '—'}
              </p>
              {archetype.notes && <p style={{ margin: '2px 0' }}>{archetype.notes}</p>}
              <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                <button type="button" className="secondary" onClick={() => setEditing(true)}>
                  Edit
                </button>
                <button type="button" className="secondary" onClick={toggleDeprecated}>
                  {archetype.status === 'active' ? 'Deprecate' : 'Reactivate'}
                </button>
              </div>
            </div>
          )}

          {detail && (
            <div style={{ marginTop: 10 }}>
              <p style={{ fontSize: 13, fontWeight: 600, margin: '4px 0' }}>Assigned roles</p>
              {detail.roles.length === 0 ? (
                <p className="muted">None yet.</p>
              ) : (
                <ul style={{ fontSize: 13, margin: '4px 0' }}>
                  {detail.roles.map((r) => (
                    <li key={r.id}>
                      {r.title}
                      {r.organisation ? ` — ${r.organisation}` : ''}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {demand && demand.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <p style={{ fontSize: 13, fontWeight: 600, margin: '4px 0' }}>Capability demand (rebuild economics for latest)</p>
              <ul style={{ fontSize: 13, margin: '4px 0' }}>
                {demand.slice(0, 8).map((d) => (
                  <li key={d.capability_concept_id}>
                    {d.canonical_name} — {d.roles_demanding_capability}/{d.roles_in_archetype} roles (
                    {d.required_count} required)
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ArchetypesTab() {
  const [groups, setGroups] = useState<TitleGroup[] | null>(null)
  const [archetypes, setArchetypes] = useState<Archetype[] | null>(null)
  const [statusFilter, setStatusFilter] = useState('active')
  const [refreshSignal, setRefreshSignal] = useState(0)

  const reload = () => {
    api.listArchetypeTitleGroups().then(setGroups)
    api.listArchetypes(statusFilter).then(setArchetypes)
  }

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, refreshSignal])

  return (
    <div>
      <section>
        <h3 style={{ fontSize: 14 }}>Unassigned observed roles by title</h3>
        {groups === null ? (
          <p className="muted">Loading…</p>
        ) : groups.length === 0 ? (
          <p className="muted">Every observed posting is assigned to an archetype.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {groups.slice(0, 25).map((g) => (
              <TitleGroupCard key={g.normalized_title} group={g} archetypes={archetypes || []} onDone={() => setRefreshSignal((s) => s + 1)} />
            ))}
            {groups.length > 25 && <p className="muted">{groups.length - 25} more group(s) not shown.</p>}
          </div>
        )}
      </section>

      <section style={{ marginTop: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ fontSize: 14 }}>Archetypes</h3>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="active">Active</option>
            <option value="all">All</option>
            <option value="deprecated">Deprecated</option>
          </select>
        </div>
        {archetypes === null ? (
          <p className="muted">Loading…</p>
        ) : archetypes.length === 0 ? (
          <p className="muted">No archetypes yet — create one from a title group above.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
            {archetypes.map((a) => (
              <ArchetypeRow key={a.id} archetype={a} onChanged={() => setRefreshSignal((s) => s + 1)} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

// --- Market Data tab -----------------------------------------------------------

function DraftObservationRow({ obs, onChanged }: { obs: CompensationObservation; onChanged: () => void }) {
  const [markets, setMarkets] = useState<Market[]>([])
  const [archetypes, setArchetypes] = useState<Archetype[]>([])
  const [marketId, setMarketId] = useState(obs.market_id || '')
  const [archetypeId, setArchetypeId] = useState(obs.archetype_concept_id || '')

  useEffect(() => {
    api.listMarkets('all').then(setMarkets)
    api.listArchetypes('active').then(setArchetypes)
  }, [])

  const assign = async () => {
    const payload: Record<string, string> = {}
    if (marketId) payload.market_id = marketId
    if (archetypeId) payload.archetype_concept_id = archetypeId
    if (Object.keys(payload).length > 0) await api.correctCompensationObservation(obs.id, payload)
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <strong>{obs.raw_role_label || 'Unlabelled row'}</strong>
        <span className="muted">
          {formatMoney(obs.amount_mid ?? obs.amount_min ?? obs.amount_max, obs.currency)} · {obs.component}/{obs.pay_period}
          {obs.reported_sample_size ? ` · n=${obs.reported_sample_size}` : ' · sample size not reported'}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={marketId} onChange={(e) => setMarketId(e.target.value)}>
          <option value="">Assign market…</option>
          {markets.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
        <select value={archetypeId} onChange={(e) => setArchetypeId(e.target.value)}>
          <option value="">Assign archetype…</option>
          {archetypes.map((a) => (
            <option key={a.id} value={a.id}>
              {a.canonical_name}
            </option>
          ))}
        </select>
        <button type="button" className="secondary" onClick={assign}>
          Save assignment
        </button>
        <button
          type="button"
          className="primary"
          onClick={async () => {
            await assign()
            await api.reviewCompensationObservation(obs.id, 'accept')
            onChanged()
          }}
        >
          Accept
        </button>
        <button
          type="button"
          onClick={async () => {
            await api.reviewCompensationObservation(obs.id, 'reject')
            onChanged()
          }}
        >
          Reject
        </button>
      </div>
    </div>
  )
}

function MarketDataDocumentDetailView({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const [doc, setDoc] = useState<MarketSurveyDocumentDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)

  const load = () => api.getMarketDataDocument(documentId).then(setDoc)

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId])

  const extract = async () => {
    setExtracting(true)
    setError(null)
    try {
      await api.extractMarketData(documentId)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setExtracting(false)
    }
  }

  if (!doc) return <p className="muted">Loading…</p>
  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <strong>{doc.title || doc.source_payload.report_title || 'Untitled report'}</strong>
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="muted" style={{ fontSize: 12 }}>
        {doc.source} · captured {doc.captured_at || doc.created_at}
      </p>
      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      <button type="button" disabled={extracting} onClick={extract}>
        {doc.extraction_runs.length > 0 ? 'Re-run extraction' : 'Run AI extraction'}
      </button>
      {doc.extraction_runs.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          Last run: {doc.extraction_runs[0].status}
        </p>
      )}
      <p style={{ fontSize: 13, fontWeight: 600, marginTop: 12 }}>Extracted observations ({doc.observations.length})</p>
      {doc.observations.map((o) => (
        <p key={o.id} className="muted" style={{ fontSize: 12, margin: '2px 0' }}>
          {o.raw_role_label || '—'}: {formatMoney(o.amount_mid ?? o.amount_min, o.currency)} [{o.review_status}]
        </p>
      ))}
    </div>
  )
}

function MarketDataTab() {
  const [documents, setDocuments] = useState<MarketSurveyDocument[] | null>(null)
  const [drafts, setDrafts] = useState<CompensationObservation[] | null>(null)
  const [openDocId, setOpenDocId] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [publisher, setPublisher] = useState('')
  const [reportTitle, setReportTitle] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reload = () => {
    api.listMarketDataDocuments().then(setDocuments)
    api.listDraftCompensationObservations('unreviewed').then(setDrafts)
  }

  useEffect(reload, [])

  const ingest = async () => {
    if (!text.trim()) return
    setError(null)
    try {
      await api.ingestMarketDataText({ text, publisher: publisher || undefined, report_title: reportTitle || undefined })
      setText('')
      setPublisher('')
      setReportTitle('')
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const uploadPdf = async (file: File) => {
    setError(null)
    try {
      await api.ingestMarketDataPdf(file, { publisher: publisher || undefined, report_title: reportTitle || undefined })
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div>
      <section className="card">
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Capture a salary report</h3>
        {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <input value={publisher} onChange={(e) => setPublisher(e.target.value)} placeholder="Publisher" />
          <input value={reportTitle} onChange={(e) => setReportTitle(e.target.value)} placeholder="Report title" />
        </div>
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste raw report text…" rows={4} style={{ width: '100%' }} />
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button type="button" className="primary" onClick={ingest}>
            Save as document
          </button>
          <label className="secondary" style={{ display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
            Upload PDF
            <input
              type="file"
              accept="application/pdf"
              style={{ display: 'none' }}
              onChange={(e) => e.target.files?.[0] && uploadPdf(e.target.files[0])}
            />
          </label>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>
          Stored immutably first. AI extraction is a separate, explicit step — viewing a report never calls it.
        </p>
      </section>

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 14 }}>Source documents</h3>
        {documents === null ? (
          <p className="muted">Loading…</p>
        ) : documents.length === 0 ? (
          <p className="muted">No market survey documents captured yet.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {documents.map((d) => (
              <div key={d.id} className="card" style={{ cursor: 'pointer' }} onClick={() => setOpenDocId(d.id)}>
                <strong>{d.title || d.source_payload.report_title || 'Untitled report'}</strong>
                <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
                  {d.source} · {d.created_at}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {openDocId && (
        <div style={{ marginTop: 16 }}>
          <MarketDataDocumentDetailView
            documentId={openDocId}
            onClose={() => {
              setOpenDocId(null)
              reload()
            }}
          />
        </div>
      )}

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 14 }}>Unreviewed draft observations</h3>
        {drafts === null ? (
          <p className="muted">Loading…</p>
        ) : drafts.length === 0 ? (
          <p className="muted">Nothing pending review.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {drafts.map((o) => (
              <DraftObservationRow key={o.id} obs={o} onChanged={reload} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

// --- Benchmarks / Readiness tab ------------------------------------------------

function BenchmarksTab() {
  const [readiness, setReadiness] = useState<Phase4Readiness | null>(null)
  const [comp, setComp] = useState<ArchetypeCompRow[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const reload = () => {
    api.getPhase4Readiness().then(setReadiness)
    api.listArchetypeComp().then(setComp)
  }

  useEffect(reload, [])

  const rebuild = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const result = await api.rebuildEconomics()
      setMessage(
        `Rebuilt: ${result.archetype_demand.computed} demand rows, ${result.archetype_comp.computed} comp rows, ${result.gap_value.computed} gap-value rows across ${result.gap_value.buckets} context(s).`,
      )
      reload()
    } finally {
      setBusy(false)
    }
  }

  const backfill = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const result = await api.backfillCompensation()
      setMessage(`Backfill: ${result.observations_created} created, ${result.observations_already_present} already present.`)
      reload()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <section className="card">
        <h3 style={{ fontSize: 14, marginTop: 0 }}>Phase 4 readiness</h3>
        {readiness === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 13 }}>
            <div>
              Capability agreement:{' '}
              {readiness.capability_agreement.measured ? (
                <strong>{((readiness.capability_agreement.value || 0) * 100).toFixed(0)}%</strong>
              ) : (
                <Badge tone="warning">Not yet evaluated</Badge>
              )}
            </div>
            <div>
              Compensation sample:{' '}
              <Badge tone={readiness.compensation_sample_sufficiency === 'sufficient' ? 'good' : 'warning'}>
                {readiness.compensation_sample_sufficiency}
              </Badge>
            </div>
            <div>Active archetypes: {readiness.total_active_archetypes}</div>
            <div>Archetypes with assigned roles: {readiness.archetypes_with_assigned_roles}</div>
            <div>Accepted compensation observations: {readiness.accepted_compensation_observations}</div>
            <div>
              Stated postings: {readiness.accepted_posting_stated_observations} · Surveys: {readiness.accepted_survey_observations}
            </div>
          </div>
        )}
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Informational only — the Economics page stays usable below the readiness gate; economic outputs are
          experimental until capability agreement has actually been measured.
        </p>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button type="button" disabled={busy} onClick={backfill}>
            Backfill posting compensation
          </button>
          <button type="button" className="primary" disabled={busy} onClick={rebuild}>
            Rebuild economics
          </button>
        </div>
        {message && <p className="muted">{message}</p>}
      </section>

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 14 }}>Archetype compensation summaries</h3>
        {comp === null ? (
          <p className="muted">Loading…</p>
        ) : comp.length === 0 ? (
          <p className="muted">Nothing computed yet — rebuild economics once compensation evidence exists.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {comp.map((row) => (
              <div key={`${row.archetype_concept_id}|${row.market_id}|${row.component}|${row.pay_period}`} className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <strong>
                    {row.archetype_name} — {row.market_label}
                  </strong>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {row.component}/{row.pay_period}
                  </span>
                </div>
                <p style={{ margin: '4px 0', fontSize: 13 }}>
                  {row.reference_comp !== null ? (
                    <>
                      Reference: <strong>{formatMoney(row.reference_comp, row.currency)}</strong> ({row.reference_source})
                    </>
                  ) : (
                    <span className="muted">No qualifying benchmark yet</span>
                  )}
                </p>
                <p className="muted" style={{ margin: '2px 0', fontSize: 12 }}>
                  {row.n_posting_stated} stated postings, {row.n_posting_estimated} estimated, {row.n_survey_sources} survey source(s)
                  {row.posting_p50 !== null && <> · median advertised-range midpoint {formatMoney(row.posting_p50, row.currency)}</>}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

// --- Page ----------------------------------------------------------------------

type EconTab = 'gap_value' | 'archetypes' | 'market_data' | 'benchmarks'

export default function Economics() {
  const [tab, setTab] = useState<EconTab>('gap_value')

  return (
    <div>
      <h1 style={{ fontSize: 18 }}>Economics</h1>
      <p className="muted" style={{ fontSize: 13 }}>
        Evidence-first market economics. "Current benchmark" always means the best compensation reference among
        structurally reachable roles/archetypes — never your actual salary, which this app does not track (see
        docs/24).
      </p>
      <div style={{ display: 'flex', gap: 8, margin: '16px 0' }}>
        <button type="button" className={tab === 'gap_value' ? 'primary' : ''} onClick={() => setTab('gap_value')}>
          Gap Value
        </button>
        <button type="button" className={tab === 'archetypes' ? 'primary' : ''} onClick={() => setTab('archetypes')}>
          Archetypes
        </button>
        <button type="button" className={tab === 'market_data' ? 'primary' : ''} onClick={() => setTab('market_data')}>
          Market Data
        </button>
        <button type="button" className={tab === 'benchmarks' ? 'primary' : ''} onClick={() => setTab('benchmarks')}>
          Benchmarks / Readiness
        </button>
      </div>
      {tab === 'gap_value' && <GapValueTab />}
      {tab === 'archetypes' && <ArchetypesTab />}
      {tab === 'market_data' && <MarketDataTab />}
      {tab === 'benchmarks' && <BenchmarksTab />}
    </div>
  )
}
