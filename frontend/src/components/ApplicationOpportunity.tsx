import { useEffect, useState } from 'react'
import RoleMetadataForm from './RoleMetadataForm'
import { type RoleMetadataInput } from '../lib/api'
import { applicationProcess, type PackageItem, type ProcessData, type StageSave } from '../lib/applicationProcess'

const kinds: Record<PackageItem['kind'], string> = { cv: 'CV', cover_letter: 'Cover letter', supporting_statement: 'Supporting statement', question: 'Application question', portfolio: 'Portfolio / work sample', other: 'Other attachment' }

export default function ApplicationOpportunity({ id, data, register, onSaved }: { id: string; data: ProcessData; register: (save: StageSave) => void; onSaved: (data: ProcessData) => void }) {
  const key = `application-opportunity:${id}`
  const [recovered] = useState(() => { try { return JSON.parse(sessionStorage.getItem(key) ?? 'null') } catch { return null } })
  const [metadata, setMetadata] = useState<RoleMetadataInput>(() => recovered?.metadata ?? Object.fromEntries(['title', 'organisation', 'location', 'country', 'remote_type', 'employment_type', 'seniority_level', 'posting_date'].map(k => [k, data.role[k as keyof typeof data.role] ?? null])))
  const [deadline, setDeadline] = useState<string>(recovered?.deadline ?? data.preparation.deadline ?? '')
  const [items, setItems] = useState<PackageItem[]>(recovered?.items ?? data.preparation.package_items)
  const [dirty, setDirty] = useState(!!recovered)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  useEffect(() => { if (dirty) sessionStorage.setItem(key, JSON.stringify({ metadata, deadline, items, revision: recovered?.revision ?? data.preparation.revision, target_revision: recovered?.target_revision ?? data.target_revision })) }, [dirty, metadata, deadline, items, key, data, recovered])
  useEffect(() => { const guard = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = '' } }; window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard) }, [dirty])
  const save = async (confirm = false) => {
    if (busy) return false
    if (!dirty && !confirm) return true
    setBusy(true); setError(null)
    try {
      const next = await applicationProcess.opportunity(id, { revision: recovered?.revision ?? data.preparation.revision, target_revision: recovered?.target_revision ?? data.target_revision, metadata, deadline: deadline || null, package_items: items, confirm })
      sessionStorage.removeItem(key); setDirty(false); setNotice(confirm ? 'Opportunity confirmed' : 'Changes saved'); onSaved(next)
      return true
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); return false }
    finally { setBusy(false) }
  }
  useEffect(() => { register(() => save()) })
  const changeItem = (index: number, change: Partial<PackageItem>) => { setItems(prev => prev.map((item, i) => i === index ? { ...item, ...change } : item)); setDirty(true) }
  return <section className="mode-editor"><h2>Understand the opportunity</h2>
    <p>Check the target and what you need to submit. Corrections update the shared role; application materials and deadline belong to this application.</p>
    {data.role.url && /^https?:\/\//i.test(data.role.url) && <a href={data.role.url} target="_blank" rel="noreferrer">Open original posting ↗</a>}
    <details><summary>Posting text</summary><p className="mode-source-text">{data.role.source_document_text || [data.role.description, data.role.requirements, data.role.responsibilities].filter(Boolean).join('\n\n') || 'No posting text is available. Add reviewed requirements from the captured source where available.'}</p></details>
    {data.role.salary_min != null && <p>Advertised compensation: {data.role.currency} {data.role.salary_min}{data.role.salary_max != null ? `–${data.role.salary_max}` : ''}</p>}
    <p>Role classification: {data.role.archetype?.archetype_name ?? 'Not classified'}</p>
    {error && <div role="alert"><p>{error} Your changes are retained.</p><button onClick={() => { sessionStorage.removeItem(key); window.location.reload() }}>Discard draft and reload current opportunity</button></div>}
    <fieldset disabled={busy} className="mode-fields"><legend>Target details</legend>
      <RoleMetadataForm value={metadata} onChange={next => { setMetadata(next); setDirty(true) }} disabled={busy} />
      <label>Application deadline, if known<input type="date" value={deadline} onChange={e => { setDeadline(e.target.value); setDirty(true) }} /></label>
      <h3>Required application package</h3><p>Record the materials requested in the posting. An empty list means no package items have been specified; it does not imply a CV is required.</p>
      <button onClick={() => {
        const text = [data.role.source_document_text, data.role.description, data.role.requirements].filter(Boolean).join(' ')
        const suggestions = (['cv', 'cover_letter', 'supporting_statement', 'portfolio'] as const).filter(kind => ({ cv: /\b(cv|resume|curriculum vitae)\b/i, cover_letter: /cover(ing)? letter/i, supporting_statement: /supporting statement/i, portfolio: /portfolio|work samples?/i })[kind].test(text) && !items.some(i => i.kind === kind))
        setItems(prev => [...prev, ...suggestions.map(kind => ({ id: crypto.randomUUID(), kind, label: kinds[kind], required: false, word_limit: null, instructions: 'Mentioned in the posting. Review whether this is required and add any limits.' }))]); setDirty(true)
        setNotice(suggestions.length ? 'Suggestions added for your review; mark required only after checking the posting.' : 'No additional package mentions found. Add materials manually.')
      }}>Suggest materials mentioned in posting</button>
      {notice && <p role="status">{notice}</p>}
      {items.map((item, index) => <article className="mode-source mode-fields" key={item.id}>
        <label>Material type<select value={item.kind} onChange={e => changeItem(index, { kind: e.target.value as PackageItem['kind'] })}>{Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Material name<input value={item.label} onChange={e => changeItem(index, { label: e.target.value })} maxLength={200} /></label>
        <label><input type="checkbox" checked={item.required} onChange={e => changeItem(index, { required: e.target.checked })} /> Required</label>
        <label>Word limit, if specified<input type="number" min={1} max={100000} value={item.word_limit ?? ''} onChange={e => changeItem(index, { word_limit: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Instructions or question<textarea value={item.instructions} onChange={e => changeItem(index, { instructions: e.target.value })} maxLength={10000} /></label>
        <button onClick={() => { setItems(prev => prev.filter((_, i) => i !== index)); setDirty(true) }}>Remove material</button>
      </article>)}
      <button onClick={() => { setItems(prev => [...prev, { id: crypto.randomUUID(), kind: 'cv', label: 'CV', required: true, word_limit: null, instructions: '' }]); setDirty(true) }}>Add package item</button>
      <div className="actions"><button onClick={() => save()}>Save opportunity changes</button><button className="primary" onClick={() => save(true)}>Confirm opportunity & package</button></div>
    </fieldset><p role="status">{dirty ? 'Unsaved changes — recoverable in this browser' : notice || 'Saved opportunity details'}</p>
  </section>
}
