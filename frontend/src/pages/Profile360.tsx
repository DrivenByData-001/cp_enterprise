import { useEffect, useState } from 'react'
import {
  api, type ConceptType, type Profile360Mapping, type Profile360MappingState, type Profile360Row,
  type Profile360Workbench, type VocabularySearchHit,
} from '../lib/api'

type Kind = 'claim' | 'capability'

const fmtSim = (s: number | null) => (s === null ? null : s.toFixed(2))

function Workbench({ kind, rowId, onChanged }: { kind: Kind; rowId: string; onChanged: () => void }) {
  const [wb, setWb] = useState<Profile360Workbench | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<VocabularySearchHit[] | null>(null)
  const [proposing, setProposing] = useState(false)
  const [types, setTypes] = useState<ConceptType[]>([])
  const [draft, setDraft] = useState({ canonical_name: '', type_code: '', definition: '' })

  const reload = () => api.getProfile360Workbench(kind, rowId).then(setWb)

  useEffect(() => {
    reload().catch((e) => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, rowId])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await reload()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const mapTo = (conceptId: string) => act(() => api.addProfile360Mapping(kind, rowId, conceptId))
  const review = (id: string, action: 'accept' | 'reject') => act(() => api.reviewProfile360Mapping(id, kind, action))

  const suggest = () =>
    act(async () => {
      const result = kind === 'claim' ? await api.mapProfile360Claim(rowId) : await api.mapProfile360Capability(rowId)
      if (result.status === 'failed') setMessage(`AI mapping failed: ${result.error}`)
      else if (result.reason === 'no_candidates_available')
        setMessage('No canonical vocabulary candidates exist yet for this item — search the vocabulary or suggest new vocabulary.')
      else if (result.reason === 'recommended_previously_rejected')
        setMessage('The AI recommended a mapping you previously rejected — it was not re-opened.')
      else if (result.proposal) setMessage('The AI found no adequate existing concept and drafted new vocabulary for your review.')
      else if (result.no_adequate_concept) setMessage('The AI found no adequate existing concept.')
      else if (!result.mapped) setMessage('The AI was not confident in any candidate — choose one below, search, or suggest new vocabulary.')
      else setMessage(null)
    })

  const search = async () => {
    if (!query.trim()) return
    setError(null)
    try {
      setHits(await api.searchProfile360Vocabulary(query.trim(), kind))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const openProposal = () => {
    setProposing(true)
    setDraft((d) => ({ ...d, canonical_name: d.canonical_name || query }))
    if (kind === 'claim' && types.length === 0) api.listConceptTypes().then(setTypes).catch(() => {})
  }

  const submitProposal = () =>
    act(async () => {
      await api.proposeProfile360Vocabulary(kind, rowId, {
        canonical_name: draft.canonical_name.trim(),
        type_code: kind === 'capability' ? 'capability' : draft.type_code || undefined,
        definition: draft.definition.trim() || undefined,
      })
      setProposing(false)
      setDraft({ canonical_name: '', type_code: '', definition: '' })
      setMessage('Vocabulary suggestion submitted for review in Vocabulary — it is not canonical until accepted.')
    })

  if (!wb) return <p className="muted">{error ?? 'Loading…'}</p>

  const mappedIds = new Set(wb.mappings.filter((m) => m.review_status !== 'rejected').map((m) => m.concept_id))

  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border, #ddd)', fontSize: 13 }}>
      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}

      <h3 style={{ fontSize: 13, margin: '0 0 4px' }}>Existing mappings</h3>
      {wb.mappings.length === 0 && <p className="muted" style={{ margin: 0 }}>None yet.</p>}
      {wb.mappings.map((m) => (
        <div key={m.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
          <div>
            <strong>{m.canonical_name}</strong> <span className="muted">({m.type_code} · {m.mapping_basis} · {m.review_status})</span>
            {m.definition && <div className="muted" style={{ fontSize: 12 }}>{m.definition}</div>}
          </div>
          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            {m.review_status === 'unreviewed' && <button disabled={busy} onClick={() => review(m.id, 'accept')}>Accept</button>}
            {m.review_status !== 'rejected' && (
              <button disabled={busy} onClick={() => review(m.id, 'reject')}>{m.review_status === 'accepted' ? 'Unmap' : 'Reject'}</button>
            )}
          </div>
        </div>
      ))}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '12px 0 4px' }}>
        <h3 style={{ fontSize: 13, margin: 0 }}>Suggested mappings</h3>
        <button disabled={busy} onClick={suggest}>{busy ? 'Working…' : 'Suggest mapping (AI)'}</button>
      </div>
      {message && <p className="muted" style={{ margin: '0 0 4px' }}>{message}</p>}
      {wb.latest_run?.status === 'failed' && <p className="muted">The last AI run failed: {wb.latest_run.error}</p>}
      {wb.candidates.length === 0 && (
        <p className="muted" style={{ margin: 0 }}>
          No candidates yet — run “Suggest mapping (AI)” to retrieve the closest vocabulary concepts, or search the vocabulary below.
        </p>
      )}
      {wb.ai_outcome === 'declined_all_candidates' && wb.candidates.length > 0 && (
        <p className="muted" style={{ margin: '0 0 4px' }}>
          The AI did not recommend any of these{wb.latest_run?.reasoning ? `: ${wb.latest_run.reasoning}` : '.'}
        </p>
      )}
      <ol style={{ paddingLeft: 20, margin: 0 }}>
        {wb.candidates.map((c) => (
          <li key={c.concept_id} style={{ marginBottom: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <div>
                <strong>{c.canonical_name}</strong> <span className="muted">({c.type_code}{fmtSim(c.similarity) ? ` · similarity ${fmtSim(c.similarity)}` : ''})</span>
                {c.ai_selected && <span style={{ marginLeft: 6, fontWeight: 600 }}>[AI recommendation]</span>}
                <div className="muted" style={{ fontSize: 12 }}>{c.definition ?? 'No definition recorded.'}</div>
              </div>
              <div style={{ flexShrink: 0 }}>
                {c.mapping_status === 'accepted' ? (
                  <span className="muted">Mapped</span>
                ) : c.mapping_status === 'unreviewed' && c.mapping_id ? (
                  <button disabled={busy} onClick={() => review(c.mapping_id as string, 'accept')}>Accept</button>
                ) : (
                  <button disabled={busy} onClick={() => mapTo(c.concept_id)}>
                    Map to this{c.mapping_status === 'rejected' ? ' (previously rejected)' : ''}
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>

      <h3 style={{ fontSize: 13, margin: '12px 0 4px' }}>Search vocabulary</h3>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          aria-label="Search vocabulary" placeholder="Search vocabulary…" value={query}
          onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()}
        />
        <button onClick={search}>Search</button>
      </div>
      {hits !== null && hits.length === 0 && <p className="muted">No matching vocabulary.</p>}
      {hits?.map((h) => (
        <div key={h.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 4 }}>
          <div>
            <strong>{h.canonical_name}</strong> <span className="muted">({h.type_code})</span>
            <div className="muted" style={{ fontSize: 12 }}>{h.definition ?? 'No definition recorded.'}</div>
          </div>
          {mappedIds.has(h.id) ? <span className="muted">Mapped</span> : <button disabled={busy} onClick={() => mapTo(h.id)}>Map to this</button>}
        </div>
      ))}

      <div style={{ marginTop: 12 }}>
        {!proposing ? (
          <button onClick={openProposal}>None of these — suggest new vocabulary…</button>
        ) : (
          <div className="card">
            <strong>Suggest new vocabulary</strong>
            <p className="muted" style={{ margin: '2px 0 6px' }}>
              Goes to the Vocabulary review queue; nothing becomes canonical until a human accepts it, and the mapping is
              created for review once it is.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <input aria-label="Canonical name" placeholder="Canonical name" value={draft.canonical_name}
                onChange={(e) => setDraft({ ...draft, canonical_name: e.target.value })} />
              {kind === 'claim' && (
                <select aria-label="Type" value={draft.type_code} onChange={(e) => setDraft({ ...draft, type_code: e.target.value })}>
                  <option value="">Type…</option>
                  {types.map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
                </select>
              )}
              <textarea aria-label="Definition" placeholder="Definition" value={draft.definition}
                onChange={(e) => setDraft({ ...draft, definition: e.target.value })} />
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="primary" disabled={busy || !draft.canonical_name.trim() || (kind === 'claim' && !draft.type_code)} onClick={submitProposal}>
                  Submit suggestion
                </button>
                <button onClick={() => setProposing(false)}>Cancel</button>
              </div>
            </div>
          </div>
        )}
      </div>

      {wb.proposals.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <h3 style={{ fontSize: 13, margin: '0 0 4px' }}>Vocabulary suggestions from this item</h3>
          {wb.proposals.map((p) => (
            <div key={p.id} style={{ marginBottom: 4 }}>
              <strong>{p.surface_form}</strong>{' '}
              <span className="muted">({p.suggested_type ?? 'no type'} · {p.origin === 'ai' ? 'AI draft' : 'you'} · {p.status}
                {p.resolved_canonical_name ? ` → ${p.resolved_canonical_name}` : ''})</span>
              {p.suggested_definition && <div className="muted" style={{ fontSize: 12 }}>{p.suggested_definition}</div>}
              {p.nearest_canonical_name && (
                <div className="muted" style={{ fontSize: 12 }}>
                  Nearest existing: {p.nearest_canonical_name}{fmtSim(p.nearest_similarity) ? ` (similarity ${fmtSim(p.nearest_similarity)})` : ''}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const STATE_LABEL: Record<string, string> = { unmapped: 'Unmapped', pending: 'Pending review', mapped: 'Mapped' }

function RowCard({
  row, kind, open, onToggle, onChanged,
}: { row: Profile360Row; kind: Kind; open: boolean; onToggle: () => void; onChanged: () => void }) {
  const rowId = String(row.id)
  const state = row._mapping_state
  const counts = row._mapping_counts

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ fontSize: 13 }}>{row._display}</div>
          <div className="muted" style={{ fontSize: 11 }}>
            {state && <>{STATE_LABEL[state]}{counts && counts.accepted + counts.unreviewed > 0 ? ` (${counts.accepted} accepted, ${counts.unreviewed} pending)` : ''} · </>}
            id: {rowId}
          </div>
        </div>
        <button onClick={onToggle} aria-expanded={open}>{open ? 'Close' : 'Curate mappings'}</button>
      </div>
      {open && <Workbench kind={kind} rowId={rowId} onChanged={onChanged} />}
    </div>
  )
}

function MappingQueue({ kind, onReviewed }: { kind: Kind; onReviewed: () => void }) {
  const [mappings, setMappings] = useState<Profile360Mapping[]>([])
  const [error, setError] = useState<string | null>(null)

  const reload = () => api.listProfile360Mappings(kind, 'unreviewed').then(setMappings)

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  const review = async (id: string, action: 'accept' | 'reject') => {
    await api.reviewProfile360Mapping(id, kind, action)
    await reload()
    onReviewed() // accept/reject changes which rows count as unmapped
  }

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (mappings.length === 0) return <p className="muted">Nothing pending review.</p>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {mappings.map((m) => (
        <div key={m.id} className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 13 }}>
            {m._display ?? m.profile360_id} → <strong>{m.canonical_name}</strong>{' '}
            <span className="muted">({m.type_code}, {m.mapping_basis})</span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={() => review(m.id, 'accept')}>Accept</button>
            <button onClick={() => review(m.id, 'reject')}>Reject</button>
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Profile360() {
  const [tab, setTab] = useState<Kind>('claim')
  const [rows, setRows] = useState<Profile360Row[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [queueVersion, setQueueVersion] = useState(0)
  const [unmappedVersion, setUnmappedVersion] = useState(0)
  // Rows being curated stay listed (and open) even after their mapping state
  // moves them out of the current filter, so more mappings can be added.
  const [openRows, setOpenRows] = useState<Record<string, Profile360Row>>({})
  const [state, setState] = useState<Exclude<Profile360MappingState, 'all'> | 'all'>('unmapped')

  const load = () =>
    tab === 'claim' ? api.listProfile360Claims(50, 0, state) : api.listProfile360Capabilities(50, 0, state)

  useEffect(() => {
    // Only the first load of a tab shows the full-page spinner; refreshes
    // after map/review swap the list in place so the queue keeps its state.
    let cancelled = false
    setError(null)
    load()
      .then((r) => !cancelled && setRows(r))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, state, unmappedVersion])

  const switchTab = (next: Kind) => {
    if (next === tab) return
    setOpenRows({})
    setLoading(true)
    setTab(next)
  }

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>profile360 mapping</h1>
      <p className="secondary">
        Maps person-side evidence — read-only from the authoritative profile360 store, never copied here — onto the
        canonical jobber vocabulary. The AI only recommends — you choose among the candidates it considered, search the full
        vocabulary, or suggest new vocabulary. Nothing is accepted or made canonical automatically.
      </p>

      <div style={{ display: 'flex', gap: 8, margin: '16px 0' }}>
        <button className={tab === 'claim' ? 'primary' : ''} onClick={() => switchTab('claim')}>
          Claims
        </button>
        <button className={tab === 'capability' ? 'primary' : ''} onClick={() => switchTab('capability')}>
          Capabilities
        </button>
      </div>

      {error && (
        <p style={{ color: 'var(--critical)' }}>
          {error}
          {error.includes('503') && ' — profile360 is not reachable from this environment (see docs/14 §2/§5).'}
        </p>
      )}
      {loading && <p className="muted">Loading…</p>}

      {!loading && !error && (
        <>
          <section style={{ marginBottom: 24 }}>
            <h2 style={{ fontSize: 16 }}>{tab === 'claim' ? 'Claims' : 'Capabilities'}</h2>
            <div style={{ display: 'flex', gap: 6, margin: '8px 0' }} role="group" aria-label="Mapping state">
              {(['unmapped', 'pending', 'mapped', 'all'] as const).map((st) => (
                <button key={st} className={state === st ? 'primary' : ''} aria-pressed={state === st} onClick={() => { setOpenRows({}); setState(st) }}>
                  {st === 'pending' ? 'Pending review' : st[0].toUpperCase() + st.slice(1)}
                </button>
              ))}
            </div>
            {rows.length === 0 && Object.keys(openRows).length === 0 && <p className="muted">Nothing here.</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {[...rows, ...Object.values(openRows).filter((o) => !rows.some((r) => r.id === o.id))].map((r) => (
                <RowCard key={String(r.id)} row={r} kind={tab} open={String(r.id) in openRows}
                  onToggle={() => setOpenRows((cur) => {
                    const next = { ...cur }
                    if (String(r.id) in next) delete next[String(r.id)]
                    else next[String(r.id)] = r
                    return next
                  })}
                  onChanged={() => {
                    setQueueVersion((v) => v + 1)
                    setUnmappedVersion((v) => v + 1)
                  }} />
              ))}
            </div>
          </section>

          <section>
            <h2 style={{ fontSize: 16 }}>Review queue</h2>
            <MappingQueue key={`${tab}-${queueVersion}`} kind={tab} onReviewed={() => setUnmappedVersion((v) => v + 1)} />
          </section>
        </>
      )}
    </div>
  )
}
