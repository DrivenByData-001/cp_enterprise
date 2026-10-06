import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Concept, type ConceptType, type RequirementClaim } from '../lib/api'
import { applicationProcess, type ProcessData, type ScopedProposal, type StageSave } from '../lib/applicationProcess'
import { RequirementCard, AddRequirementForm, ConceptPicker } from '../pages/RoleRequirements'

function VocabularyIssue({ id, proposal, done, busy, onBusyChange }: { id: string; proposal: ScopedProposal; done: () => Promise<void>; busy: boolean; onBusyChange: (busy: boolean) => void }) {
  const key = `application-vocabulary:${id}:${proposal.id}`
  const [recovered] = useState(() => { try { return JSON.parse(sessionStorage.getItem(key) ?? 'null') } catch { return null } })
  const [action, setAction] = useState<'accept_alias' | 'accept_new' | 'reject'>(recovered?.action ?? 'accept_alias')
  const [concept, setConcept] = useState<Concept | null>(recovered?.concept ?? null)
  const [types, setTypes] = useState<ConceptType[]>([])
  const [name, setName] = useState(recovered?.name ?? proposal.surface_form)
  const [type, setType] = useState(recovered?.type ?? proposal.suggested_type ?? '')
  const [definition, setDefinition] = useState(recovered?.definition ?? '')
  useEffect(() => { sessionStorage.setItem(key, JSON.stringify({ action, concept, name, type, definition, revision: recovered?.revision ?? proposal.revision })) }, [key, action, concept, name, type, definition, recovered, proposal.revision])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.listConceptTypes().then(setTypes).catch(e => setError(String(e))) }, [])
  const resolve = async () => {
    onBusyChange(true); setError(null)
    try {
      await applicationProcess.resolve(id, { ...proposal, revision: recovered?.revision ?? proposal.revision }, { action, concept_id: concept?.id, canonical_name: name, type_code: type, definition })
      sessionStorage.removeItem(key)
      await done()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { onBusyChange(false) }
  }
  return <article className="mode-source"><h4>Resolve “{proposal.surface_form}”</h4>
    {proposal.role_evidence_span && <blockquote>{proposal.role_evidence_span}</blockquote>}
    <p>Choices are kept in this browser until explicitly confirmed. This resolves this shared vocabulary term wherever it occurs. Any resulting role requirement still needs your review.</p>
    {error && <p role="alert">{error} <button disabled={busy} onClick={() => { sessionStorage.removeItem(key); window.location.reload() }}>Discard vocabulary draft and reload</button></p>}
    <fieldset disabled={busy} className="mode-fields"><legend>Vocabulary decision</legend>
      <label>Resolution<select value={action} onChange={e => setAction(e.target.value as typeof action)}><option value="accept_alias">Use an existing concept</option><option value="accept_new">Create a reviewed concept</option><option value="reject">Reject this vocabulary proposal</option></select></label>
      {action === 'accept_alias' && <><ConceptPicker scoped onSelect={setConcept} />{concept && <p>Selected: {concept.canonical_name}</p>}</>}
      {action === 'accept_new' && <><label>Canonical name<input value={name} onChange={e => setName(e.target.value)} /></label><label>Concept type<select value={type} onChange={e => setType(e.target.value)}><option value="">Choose type</option>{types.map(t => <option key={t.code} value={t.code}>{t.label}</option>)}</select></label><label>Definition<textarea value={definition} onChange={e => setDefinition(e.target.value)} /></label></>}
      <button disabled={busy || (action === 'accept_alias' && !concept) || (action === 'accept_new' && (!name.trim() || !type))} onClick={resolve}>Confirm vocabulary resolution</button>
    </fieldset>
  </article>
}

export default function ApplicationRequirements({ id, data, register, reload }: { id: string; data: ProcessData; register: (save: StageSave) => void; reload: () => Promise<void> }) {
  const [claims, setClaims] = useState<RequirementClaim[]>([])
  const [proposals, setProposals] = useState<ScopedProposal[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(() => !!sessionStorage.getItem(`requirement-add:${data.role.id}`))
  const [error, setError] = useState<string | null>(null)
  const saves = useRef(new Map<string, StageSave>())
  const registerSave = useCallback((key: string, save: StageSave | null) => { if (save) saves.current.set(key, save); else saves.current.delete(key) }, [])
  const load = useCallback(async () => {
    const [requirements, vocabulary] = await Promise.all([api.listRequirements(data.role.id), applicationProcess.proposals(id)])
    setClaims(requirements.items); setProposals(vocabulary.items)
  }, [id, data.role.id])
  useEffect(() => { load().catch(e => setError(String(e))).finally(() => setLoading(false)) }, [load])
  const reloadAll = async () => { await load(); await reload() }
  const flush = async () => {
    if (busy || loading) return false
    for (const save of [...saves.current.values()]) if (!await save()) return false
    return true
  }
  useEffect(() => { register(flush) })
  const run = async (action: () => Promise<unknown>) => {
    if (!await flush()) return
    setBusy(true); setError(null)
    try { await action(); await reloadAll() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <section className="mode-editor"><h2>Review the employer’s requirements</h2>
    <p>Accept, refine or reject each requirement. Only confirmed requirements appear in Evidence. Changes preserve your previous work and may require fresh review downstream.</p>
    {data.legacy_requirement_count > 0 && <p className="mode-notice">{data.legacy_requirement_count} legacy requirement signals are excluded from the new Evidence stage. Extract and review the posting, or add a supported requirement below. Earlier evidence decisions are retained.</p>}
    {!!data.role.legacy_skills?.length && <details><summary>Inspect legacy signals (not confirmed requirements)</summary><ul>{data.role.legacy_skills.map((skill, i) => <li key={i}>{skill.name}{skill.requirement_type ? ` · ${skill.requirement_type}` : ''}</li>)}</ul></details>}
    {error && <div role="alert">{error}<button onClick={() => reloadAll().then(() => setError(null)).catch(e => setError(String(e)))}>Reload requirements</button></div>}
    <button disabled={busy || loading} onClick={() => run(async () => { const result = await api.extractRequirements(data.role.id); if (result.status === 'failed') throw new Error(result.error || 'Extraction failed. Your existing requirements are preserved.') })}>{busy ? 'Working…' : 'Extract requirements with AI'}</button>
    {loading && <p role="status">Loading requirements…</p>}
    {claims.map(claim => <RequirementCard key={claim.id} claim={claim} roleId={data.role.id} scoped busy={busy} onBusyChange={setBusy} onError={setError} onUpdated={(oldId, updated) => { setClaims(rows => rows.map(r => r.id === oldId ? updated : r)); void reload().catch(e => setError(String(e))) }} registerSave={registerSave} />)}
    {!loading && !claims.length && <p>No reviewed requirement set yet. Extract from the posting or add a requirement supported by its text.</p>}
    {adding ? <AddRequirementForm roleId={data.role.id} busy={busy} onBusyChange={setBusy} onAdded={claim => { setClaims(prev => [...prev, claim]); setAdding(false); void reload().catch(e => setError(String(e))) }} onCancel={() => setAdding(false)} registerSave={registerSave} /> : <button disabled={busy} onClick={() => setAdding(true)}>Add requirement</button>}
    {proposals.length > 0 && <section aria-label="Vocabulary for this role"><h3>Vocabulary needing resolution</h3>{proposals.map(p => <VocabularyIssue key={p.id} id={id} proposal={p} done={reloadAll} busy={busy} onBusyChange={setBusy} />)}</section>}
    {data.review_summary.needs_reextraction > 0 && <p className="mode-notice">Some resolved terms need another extraction before their requirements can be reviewed.</p>}
    <p>{data.review_summary.unreviewed} requirements need review; {proposals.length} vocabulary proposals remain.</p>
    <button className="primary" disabled={busy || loading} onClick={() => run(async () => { const latest = await applicationProcess.get(id); await applicationProcess.confirmRequirements(id, latest.requirements_fingerprint) })}>Confirm requirement set</button>
  </section>
}
