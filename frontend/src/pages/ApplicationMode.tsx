import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { applicationMode, type Disposition, type EvidenceDecision, type ModeItem } from '../lib/applicationMode'
import './ApplicationMode.css'
import ApplicationClaimReview from '../components/ApplicationClaimReview'
import ApplicationOpportunity from '../components/ApplicationOpportunity'
import ApplicationRequirements from '../components/ApplicationRequirements'
import EvidenceDiscovery from '../components/EvidenceDiscovery'
import { applicationProcess, stageLabels, stateLabels, type ProcessData, type Stage } from '../lib/applicationProcess'

const LABELS: Record<Disposition, string> = { covered: 'Covered', partial: 'Partial evidence', investigate: 'Needs investigation', gap: 'Genuine gap acknowledged' }
const message = (e: unknown) => e instanceof Error ? e.message : String(e)
type Save = () => Promise<boolean>

function EvidenceEditor({ applicationId, item, register, reload }: { applicationId: string; item: ModeItem; register: (save: Save) => void; reload: () => Promise<void> }) {
  const draftKey = `application-evidence:${applicationId}:${item.concept.id}`
  const [recovered] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem(draftKey) ?? 'null') }
    catch { return null }
  })
  const initial = (): EvidenceDecision => {
    try {
      const saved = recovered
      if (saved?.decision && typeof saved.decision.rationale === 'string') return saved.decision
    } catch { /* Local recovery is best effort; accepted work is on the server. */ }
    return item.decision ?? { disposition: 'investigate', selected_refs: [], rationale: '', revision: 0 }
  }
  const [decision, setDecision] = useState<EvidenceDecision>(initial)
  const [sources, setSources] = useState<ModeItem['sources']>(recovered?.sources ?? item.sources)
  const [dirty, setDirty] = useState(() => sessionStorage.getItem(draftKey) !== null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [claimReview, setClaimReview] = useState<{ claimId?: string } | null>(null)
  const [acceptedMessage, setAcceptedMessage] = useState(false)
  const noteKey = `${draftKey}:note`
  const recoveredNote = (() => { try { return JSON.parse(sessionStorage.getItem(noteKey) ?? '{}') } catch { return {} } })()
  const [note, setNote] = useState<string>(recoveredNote.text ?? '')
  const [noteId, setNoteId] = useState<string | null>(recoveredNote.id ?? null)
  const [queued, setQueued] = useState<boolean>(recoveredNote.queued ?? false)
  useEffect(() => { sessionStorage.setItem(noteKey, JSON.stringify({ text: note, id: noteId, queued })) }, [noteKey, note, noteId, queued])
  const pending = useRef<Promise<boolean> | null>(null)
  const update = (next: EvidenceDecision) => {
    setDecision(next); setDirty(true); setSaved(false)
    sessionStorage.setItem(draftKey, JSON.stringify({ decision: next, sources, requirement_fingerprint: recovered?.requirement_fingerprint ?? item.requirement_fingerprint }))
  }
  const save: Save = async () => {
    if (pending.current) return pending.current
    if (claimReview) { setError('Close the career fact review before leaving this requirement. Its draft is kept in this browser.'); return false }
    if (busy) return false
    if (!dirty && !item.stale && item.decision && (!note.trim() || noteId)) return true
    setBusy(true); setError(null)
    pending.current = (async () => {
      try {
        if (note.trim() && !noteId) {
          const created = await api.createApplicationNote(applicationId, { concept_id: item.concept.id, note_type: 'evidence_example', note_text: note })
          setNoteId(created.id)
          sessionStorage.setItem(noteKey, JSON.stringify({ text: note, id: created.id, queued: false }))
        }
        await applicationMode.save(applicationId, item.concept.id, { ...decision,
          source_revisions: Object.fromEntries(sources.map(s => [s.ref, s.source_revision])),
          requirement_fingerprint: recovered?.requirement_fingerprint ?? item.requirement_fingerprint })
        sessionStorage.removeItem(draftKey); setDirty(false); setSaved(true)
        await reload()
        return true
      } catch (e) { setError(message(e)); return false }
      finally { setBusy(false); pending.current = null }
    })()
    return pending.current
  }
  useEffect(() => { register(save) })
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty || (note.trim() && !noteId)) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty, note, noteId])
  const search = async () => {
    setSearching(true); setError(null)
    try {
      const result = await applicationMode.search(applicationId, item.concept.id, query)
      setSources(prev => [...new Map([...prev, ...result.sources].map(s => [s.ref, s])).values()])
      if (!result.sources.length) setError('No career claims matched that search. Try another phrase or record a new example below.')
    } catch (e) { setError(message(e)) } finally { setSearching(false) }
  }
  const queueNote = async () => {
    setBusy(true); setError(null)
    try {
      const id = noteId ?? (await api.createApplicationNote(applicationId, { concept_id: item.concept.id, note_type: 'evidence_example', note_text: note })).id
      setNoteId(id)
      await api.promoteApplicationNote(applicationId, id)
      setQueued(true); setNote('')
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  return <section className="mode-editor" aria-labelledby="requirement-title">
    <p className="eyebrow">Build your evidence case</p>
    <h2 id="requirement-title">{item.concept.canonical_name}</h2>
    <p className="secondary">{item.role_side.requirement_type === 'required' ? 'Required by the role' : 'Role requirement'}</p>
    {item.role_side.evidence_span && <blockquote>{item.role_side.evidence_span}</blockquote>}
    {item.stale && <p role="status" className="mode-notice">{item.attention_reason}</p>}
    <p>Select the career facts you want to use for this application. Review what each source actually supports.</p>
    {error && <div role="alert" className="mode-notice">{error}<p>Your changes are retained.</p><button onClick={() => { sessionStorage.removeItem(draftKey); window.location.reload() }}>Discard local review changes & reload latest review</button></div>}
    <fieldset disabled={busy} className="mode-fields">
      <legend>Supporting evidence</legend>
      {sources.length === 0 && <p className="secondary">No matching evidence yet. Search your career claims or record an example below.</p>}
      {sources.map(source => <article className="mode-source" key={source.ref}>
        <label><input type="checkbox" checked={decision.selected_refs.includes(source.ref)} onChange={e => update({ ...decision, selected_refs: e.target.checked ? [...decision.selected_refs, source.ref] : decision.selected_refs.filter(r => r !== source.ref) })} /> Use this evidence</label>
        <details><summary>{source.label}</summary><p className="mode-source-text">{source.content}</p><small>Source: Profile360 · {source.kind === 'profile_claim' ? 'Career claim' : 'Capability'}</small></details>
        {source.kind === 'profile_claim' && <button disabled={!!claimReview} onClick={() => setClaimReview({ claimId: source.ref.split(':')[1] })}>Correct career claim</button>}
      </article>)}
      {decision.selected_refs.filter(ref => !sources.some(s => s.ref === ref)).map(ref => <label key={ref}><input type="checkbox" checked onChange={() => update({ ...decision, selected_refs: decision.selected_refs.filter(r => r !== ref) })} /> Unavailable evidence — uncheck to remove it from this application</label>)}
      <div className="mode-search"><label>Find another career example<input value={query} onChange={e => setQuery(e.target.value)} placeholder="Try a project, responsibility or skill" /></label><button onClick={search} disabled={searching || query.trim().length < 2}>{searching ? 'Searching…' : 'Search career claims'}</button></div>
      <label>Your assessment<select value={decision.disposition} onChange={e => update({ ...decision, disposition: e.target.value as Disposition })}>{Object.entries(LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>How will you address this requirement?<textarea rows={4} value={decision.rationale} onChange={e => update({ ...decision, rationale: e.target.value })} placeholder="Explain your choice, any limits, or how you will address a gap." /></label>
      <p className="secondary">An acknowledged gap does not stop you applying. Citation checks do not replace your review of the facts.</p>
      <button className="primary" onClick={save}>{busy ? 'Saving…' : 'Save evidence review'}</button>
      <span role="status">{dirty ? 'Unsaved review — recovered in this browser until saved.' : saved || item.decision ? 'Review saved' : 'Not reviewed yet'}</span>
    </fieldset>
    <button disabled={busy || !!claimReview} onClick={() => setClaimReview({})}>Add reviewed career claim</button>
    {acceptedMessage && <p role="status">Accepted into Profile360 and selected here. Review your assessment and save this application’s evidence review.</p>}
    {claimReview && <ApplicationClaimReview key={claimReview.claimId ?? 'new'} applicationId={applicationId} conceptId={item.concept.id} claimId={claimReview.claimId} onClose={() => setClaimReview(null)} onAccepted={source => {
      const nextSources = [...new Map([...sources, source].map(s => [s.ref, s])).values()]
      const next = { ...decision, selected_refs: [...new Set([...decision.selected_refs, source.ref])], disposition: decision.disposition === 'gap' ? 'investigate' as const : decision.disposition }
      setSources(nextSources); setDecision(next); setDirty(true); setSaved(false); setClaimReview(null)
      setAcceptedMessage(true)
      sessionStorage.setItem(draftKey, JSON.stringify({ decision: next, sources: nextSources, requirement_fingerprint: recovered?.requirement_fingerprint ?? item.requirement_fingerprint }))
    }} />}
    <details className="mode-new-evidence"><summary>Remembered another example?</summary>
      <p>Review your career fact before sending it to Profile360. It remains an application note while Profile360 reviews the addition; queued material is not accepted evidence.</p>
      <label>Career example<textarea rows={4} disabled={busy || !!noteId} value={note} onChange={e => setNote(e.target.value)} /></label>
      <button disabled={busy || queued || (!note.trim() && !noteId)} onClick={queueNote}>{noteId ? 'Send saved example to Profile360' : 'Save example & send for Profile360 review'}</button>
      {queued && <p role="status">Saved and queued for Profile360 review. Once accepted, search for the career claim above and select it here.</p>}
    </details>
  </section>
}

export default function ApplicationMode() {
  const { id = '', stage: stageParam, entity } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [data, setData] = useState<ProcessData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const save = useRef<Save>(async () => true)
  const base = `/applications/${id}/prepare`
  const known = !stageParam || Object.hasOwn(stageLabels, stageParam)
  const stage: Stage = known ? (stageParam as Stage || 'overview') : 'evidence'
  const conceptId = known ? entity : stageParam
  const path = (s: Stage, concept?: string | null) => s === 'overview' ? base : `${base}/${s}${concept ? `/${concept}` : ''}`
  const reload = useCallback(async () => { setData(await applicationProcess.get(id)) }, [id])
  useEffect(() => {
    let current = true
    setData(null); setError(null)
    applicationProcess.get(id).then(next => { if (current) {
      setData(next)
      if (!known) navigate(`${base}/evidence/${stageParam}`, { replace: true })
      else if (!stageParam && params.get('resume') === '1') {
        const resumed = next.resume.stage === 'overview' ? next.next_stage : next.resume.stage
        navigate(`${base}/${resumed}${resumed === 'evidence' && next.resume.concept_id ? `/${next.resume.concept_id}` : ''}`, { replace: true })
      }
    } }).catch(e => { if (current) setError(message(e)) })
    return () => { current = false }
  }, [id, base, navigate, known, stageParam, params])
  const item = stage === 'evidence' ? data?.items.find(i => i.concept.id === conceptId) : undefined
  const go = async (nextStage: Stage, concept: string | null = null, exit?: string) => {
    if (busy) return
    setBusy(true); setError(null)
    try {
      const editable = stage === 'opportunity' || stage === 'requirements' || !!item
      if (editable && !await save.current()) return
      await applicationProcess.resume(id, exit ? stage : nextStage, exit ? item?.concept.id ?? null : concept)
      save.current = async () => true
      navigate(exit ?? path(nextStage, concept))
      await reload()
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  const attention = data?.items.filter(i => !i.decision || i.stale || i.decision.disposition === 'investigate') ?? []
  return <div className="application-mode">
    <header className="mode-header"><div><p className="eyebrow">Application Mode · {stageLabels[stage]}</p><h1>{data?.role.title ?? 'Your application'}</h1><p>{data?.role.organisation || 'Employer not recorded'}</p>{data && <p className="secondary">{stage === 'overview' ? data.application.status : stateLabels[data.stages[stage]]}</p>}</div><button disabled={busy || !data} onClick={() => go(stage, null, '/applications')}>{busy ? 'Saving…' : 'Save & exit'}</button></header>
    {error && <div role="alert" className="mode-notice">{error}<button onClick={() => reload().then(() => setError(null)).catch(e => setError(message(e)))}>Retry</button></div>}
    {!data && !error && <p role="status">Loading your application…</p>}
    {data && <div className="mode-layout"><aside className="mode-rail">
      <label className="mode-mobile-stages">Application stage<select disabled={busy} value={stage} onChange={e => go(e.target.value as Stage)}>{Object.entries(stageLabels).map(([value, label]) => <option key={value} value={value}>{label}{value !== 'overview' ? ` · ${stateLabels[data.stages[value as Exclude<Stage, 'overview'>]]}` : ''}</option>)}</select></label>
      <nav aria-label="Application stages">{Object.entries(stageLabels).map(([value, label]) => <button disabled={busy} aria-current={value === stage ? 'step' : undefined} key={value} onClick={() => go(value as Stage)}><span>{label}</span>{value !== 'overview' && <small>{stateLabels[data.stages[value as Exclude<Stage, 'overview'>]]}</small>}</button>)}</nav>
      <div className="mode-exit">{Object.entries(data.downstream ?? {}).map(([kind, state]) => <p key={kind}>{({ positioning: 'Positioning', cv: 'CV', cover_letter: 'Cover letter', supporting_statement: 'Supporting statement' } as Record<string, string>)[kind] ?? kind}: {stateLabels[state]}</p>)}<p>Positioning, Documents, Review & submit, and Interviews are available in the existing workspace during migration.</p><button disabled={busy} onClick={() => go(stage, null, `/applications/${id}`)}>Save & leave mode for workspace</button></div>
    </aside>
    {stage === 'overview' && <section className="mode-editor"><h2>Prepare your application</h2><p>Work through the stages in the order that helps you. Saved progress and your resume location are separate.</p><div className="mode-task-list">{(['opportunity', 'requirements', 'evidence'] as const).map(s => <button key={s} disabled={busy} onClick={() => go(s)}><strong>{stageLabels[s]}</strong><span>{stateLabels[data.stages[s]]}{s === 'evidence' ? ` · ${attention.length} requirements need attention` : ''}</span></button>)}</div><button className="primary" disabled={busy} onClick={() => go(data.next_stage)}>Continue: {stageLabels[data.next_stage]}</button>{data.preparation.updated_at && <p className="secondary">Last opportunity/checkpoint save: {new Date(data.preparation.updated_at).toLocaleString()}</p>}</section>}
    {stage === 'opportunity' && <ApplicationOpportunity key={`${id}:${data.preparation.revision}`} id={id} data={data} register={fn => { save.current = fn }} onSaved={setData} />}
    {stage === 'requirements' && <ApplicationRequirements key={id} id={id} data={data} register={fn => { save.current = fn }} reload={reload} />}
    {stage === 'evidence' && <div><button disabled={busy} onClick={() => go('evidence')}>Evidence overview</button>
      {item ? <EvidenceEditor key={`${id}:${item.concept.id}:${item.decision?.revision ?? 0}`} applicationId={id} item={item} register={fn => { save.current = fn }} reload={reload} /> : <section className="mode-editor"><h2>Build your evidence case</h2>{conceptId && <p role="alert">This requirement is no longer available. Select another requirement below.</p>}
        {data.stages.requirements !== 'complete' && <p className="mode-notice">Confirm the current requirement set in Requirements. Evidence progress is provisional until then.</p>}
        <p>{Object.entries(LABELS).map(([key, label]) => `${data.items.filter(i => i.decision?.disposition === key && !i.stale).length} ${label.toLowerCase()}`).join(' · ')}</p>
        {!data.items.length && <p>No confirmed requirements yet. Review Requirements to build this list.</p>}
        {!!data.items.length && <EvidenceDiscovery applicationId={id} onChanged={reload} />}
        {[['Needs attention', attention], ['Reviewed', data.items.filter(i => !attention.includes(i))]] .map(([label, items]) => <section key={label as string}><h3>{label as string}</h3><div className="mode-task-list">{(items as ModeItem[]).map(i => <button key={i.concept.id} disabled={busy} onClick={() => go('evidence', i.concept.id)}><strong>{i.concept.canonical_name}</strong><span>{i.stale ? 'Needs attention — sources changed' : i.decision ? LABELS[i.decision.disposition] : 'Start review'} →</span></button>)}</div></section>)}
      </section>}
    </div>}
    </div>}
    {!data && error && <Link to="/applications">Return to Applications</Link>}
  </div>
}
