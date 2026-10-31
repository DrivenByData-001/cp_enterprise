import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Concept, type ConceptType, type RequirementClaim } from '../lib/api'
import { applicationProcess, reviewError, type MissingRequirement, type ProcessData, type ScopedProposal, type StageSave, type RequirementClassification } from '../lib/applicationProcess'
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
  useEffect(() => { api.listConceptTypes().then(setTypes).catch(e => setError(reviewError(e))) }, [])
  const resolve = async () => {
    onBusyChange(true); setError(null)
    try {
      await applicationProcess.resolve(id, { ...proposal, revision: recovered?.revision ?? proposal.revision }, { action, concept_id: concept?.id, canonical_name: name, type_code: type, definition })
      sessionStorage.removeItem(key)
      await done()
    } catch (e) { setError(reviewError(e)) }
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

export default function ApplicationRequirements({ id, data, register, reload }: { id: string; data: ProcessData; register: (save: StageSave) => void; reload: (next?: ProcessData) => Promise<void> }) {
  const [current, setCurrent] = useState(data)
  useEffect(() => { setCurrent(data) }, [data])
  const [restored, setRestored] = useState(0)
  const [missing, setMissing] = useState<MissingRequirement[]>([])
  const [claims, setClaims] = useState<RequirementClaim[]>([])
  const [proposals, setProposals] = useState<ScopedProposal[]>([])
  const [classification, setClassification] = useState<RequirementClassification | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(() => !!sessionStorage.getItem(`requirement-add:${data.role.id}`))
  const [error, setError] = useState<string | null>(null)
  const saves = useRef(new Map<string, StageSave>())
  const registerSave = useCallback((key: string, save: StageSave | null) => { if (save) saves.current.set(key, save); else saves.current.delete(key) }, [])
  const load = useCallback(async () => {
    setLoading(true)
    try {
      let [requirements, vocabulary] = await Promise.all([api.listRequirements(data.role.id), applicationProcess.proposals(id)])
      let repaired: ProcessData | undefined
      // Reopening a completed set is a read, not a repair plus two full
      // application rebuilds. Recover only when the current source needs it.
      if (vocabulary.missing_requirements?.some(item => item.recoverable)) {
        const reconciled = await applicationProcess.reconcileRequirements(id)
        repaired = reconciled.process
        setCurrent(repaired)
        setRestored(count => count + reconciled.claims_created)
        ;[requirements, vocabulary] = await Promise.all([api.listRequirements(data.role.id), applicationProcess.proposals(id)])
      }
      setClaims(requirements.items); setProposals(vocabulary.items)
      setMissing(vocabulary.missing_requirements ?? [])
      setClassification(vocabulary.classification ?? null)
      return repaired
    } finally { setLoading(false) }
  }, [id, data.role.id])
  useEffect(() => { load().then(next => { if (next) return reload(next) }).catch(e => setError(reviewError(e))) }, [load, reload])
  const reloadAll = async () => { const next = await load(); await reload(next) }
  const flush = async () => {
    if (busy || loading) return false
    for (const save of [...saves.current.values()]) if (!await save()) return false
    return true
  }
  useEffect(() => { register(flush) })
  const run = async (action: () => Promise<ProcessData | void>) => {
    if (!await flush()) return
    setBusy(true); setError(null)
    try {
      const next = await action()
      if (next) { setCurrent(next); await reload(next) }
      else await reloadAll()
    }
    catch (e) { setError(reviewError(e)) }
    finally { setBusy(false) }
  }
  const outstanding = claims.filter(claim => claim.review_status === 'unreviewed')
  const accepted = claims.filter(claim => claim.review_status === 'accepted')
  const represented = new Set(claims.map(claim => claim.concept_id))
  const olderNotes = (current.role.legacy_skills ?? []).filter(skill => !skill.resolved_concept_id || !represented.has(skill.resolved_concept_id))
  const canConfirm = current.review_summary.complete && accepted.length > 0 && !outstanding.length && !proposals.length && !missing.length
  const changed = reloadAll
  return <section className="mode-editor"><h2>Review the employer’s requirements</h2>
    <p>Accept, refine or reject each requirement. Only confirmed requirements appear in Evidence. Changes preserve your previous work and may require fresh review downstream.</p>
    {restored > 0 && <p role="status">{restored} requirements restored from the original extraction. Review them below; no new extraction was needed.</p>}
    {olderNotes.length > 0 && <details><summary>Older posting notes (optional context)</summary><p>These notes have no reviewed requirement or sufficient original source information to restore one automatically. They do not block confirmation. Add a requirement only if the posting supports it.</p><ul>{olderNotes.map((skill, i) => <li key={i}>{skill.name}</li>)}</ul></details>}
    {error && <div role="alert">{error}<button onClick={() => reloadAll().then(() => setError(null)).catch(e => setError(reviewError(e)))}>Reload requirements</button></div>}
    <details><summary>Extract a new or changed posting</summary><p>Use this only if the posting changed, the extraction was incorrect, or the missing source information listed below cannot be recovered. Resolving a term does not require another extraction.</p><button disabled={busy || loading} onClick={() => run(async () => { const result = await api.extractRequirements(data.role.id); if (result.status === 'failed') throw new Error(result.error || 'Extraction failed. Your existing requirements are preserved.') })}>{busy ? 'Working…' : 'Extract requirements with AI'}</button></details>
    {loading && <p role="status">Loading requirements…</p>}
    {!loading && !claims.length && !proposals.length && !missing.length && <p>No requirements have been extracted yet. Extract the posting once, or add requirements with supporting text.</p>}
    {classification && <section aria-label="Other information from the posting">
      <p>AI has separated the posting by meaning. Only professional requirements enter vocabulary review. You can inspect the other information below; these are suggestions from the source, not confirmed changes to your profile or role details.</p>
      {(['eligibility_condition', 'role_metadata', 'irrelevant'] as const).map(category => {
        const items = classification.statements.filter(item => item.category === category)
        const label = { eligibility_condition: 'Practical eligibility conditions', role_metadata: 'Role details', irrelevant: 'Other text excluded from requirements' }[category]
        return items.length > 0 && <details key={category}><summary>{label} ({items.length})</summary>
          {items.map((item, index) => <article className="mode-source" key={index}><h4>{item.surface_form}</h4><blockquote>{item.evidence_span}</blockquote><p>{item.classification_reason}</p></article>)}
        </details>
      })}
      <p>If AI has classified a professional requirement incorrectly, use “Add requirement” below to record it with its supporting text.</p>
    </section>}
    {claims.map(claim => <RequirementCard key={claim.id} claim={claim} roleId={data.role.id} scoped busy={busy} onBusyChange={setBusy} onError={error => setError(reviewError(error))} onUpdated={(oldId, updated) => { setClaims(rows => rows.map(r => r.id === oldId ? updated : r)); void changed().catch(e => setError(reviewError(e))) }} registerSave={registerSave} />)}
    {!loading && !claims.length && <p>No reviewed requirement set yet. Extract from the posting or add a requirement supported by its text.</p>}
    {adding ? <AddRequirementForm roleId={data.role.id} busy={busy} onBusyChange={setBusy} onAdded={claim => { setClaims(prev => [...prev, claim]); setAdding(false); void changed().catch(e => setError(reviewError(e))) }} onCancel={() => setAdding(false)} registerSave={registerSave} /> : <button disabled={busy} onClick={() => setAdding(true)}>Add requirement</button>}
    {proposals.length > 0 && <section aria-label="Vocabulary for this role"><h3>Vocabulary needing resolution</h3>{proposals.map(p => <VocabularyIssue key={p.id} id={id} proposal={p} done={reloadAll} busy={busy} onBusyChange={setBusy} />)}</section>}
    {missing.length > 0 && <div className="mode-notice"><h3>Check source information</h3><ul>{missing.map((term, index) => <li key={index}>“{term.surface_form}” — the original extraction lacks current supporting information. Add the requirement with its source text, or extract again if that information cannot be recovered.</li>)}</ul></div>}
    {!loading && <section aria-label="Remaining requirement decisions"><h3>{canConfirm ? 'Ready to confirm' : 'Before confirming'}</h3>
      <ul>{outstanding.map(claim => <li key={claim.id}>Review “{claim.canonical_name}” in the requirement card above.</li>)}
      {proposals.map(p => <li key={p.id}>Choose the meaning of “{p.surface_form}” above.</li>)}</ul>
      {!accepted.length && <p>Accept at least one requirement supported by the posting.</p>}
      {canConfirm && <p>Every requirement has a decision. Confirm this set to continue to Evidence.</p>}
    </section>}
    <button className="primary" disabled={busy || loading || !!error || !canConfirm || current.stages.requirements === 'complete'} onClick={() => run(async () => {
      return applicationProcess.confirmRequirements(id, current.requirements_fingerprint)
    })}>{current.stages.requirements === 'complete' ? 'Requirement set confirmed' : 'Confirm requirement set'}</button>

  </section>
}
