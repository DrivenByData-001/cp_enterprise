import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type TargetDraft } from '../lib/api'
import TargetDraftEditor from '../components/TargetDraftEditor'

export default function AddTarget() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [organisation, setOrganisation] = useState('')
  const [imagined, setImagined] = useState(false)
  const [description, setDescription] = useState('')
  const [support, setSupport] = useState('')
  const [draft, setDraft] = useState<TargetDraft | null>(null)
  const [json, setJson] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const manualDraft = (): TargetDraft => ({ metadata: { source: 'user_defined', notes_for_user: support || null },
    target: { title: title.trim(), organisation: organisation || null, is_imagined: imagined, description,
      typical_tasks: [], skill_decomposition: [], technical_subjects: [] }, skills: [] })
  const generate = async () => {
    setBusy(true); setError(null)
    try {
      const result = await api.previewTarget({ title: title.trim(), organisation: organisation || null, is_imagined: imagined, description, supporting_material: support })
      if (!result.proposal || result.status === 'failed') throw new Error(result.error ?? 'Could not generate a draft. Retry or continue manually.')
      setDraft(result.proposal)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const save = async () => {
    if (!draft) return
    setBusy(true); setError(null)
    try {
      const res = await api.importTarget(draft)
      navigate(`/targets/${res.id}`)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <div>
    <Link to="/targets">← Back to targets</Link>
    <h1>Add a target</h1>
    <p>Describe a role you want to explore, review its tasks and requirements, then save it.</p>
    {error && <p role="alert">{error} Your input is preserved.</p>}
    {!draft ? <>
      <fieldset className="form-stack card" disabled={busy}>
        <legend>Describe your target</legend>
        <div className="form-grid">
          <label>Target title<input value={title} maxLength={300} onChange={e => setTitle(e.target.value)} required /></label>
          <label>Organisation (optional)<input value={organisation} maxLength={300} onChange={e => setOrganisation(e.target.value)} /></label>
          <label>Role type<select value={imagined ? 'imagined' : 'real'} onChange={e => setImagined(e.target.value === 'imagined')}><option value="real">Real role</option><option value="imagined">Imagined role</option></select></label>
        </div>
        <label>Description<textarea rows={4} maxLength={10000} value={description} onChange={e => setDescription(e.target.value)} /></label>
        <label>Supporting material (optional)<textarea rows={6} maxLength={40000} value={support} onChange={e => setSupport(e.target.value)} /></label>
        <p className="muted">AI generation sends these fields to your configured provider and records the draft for review. No target is added until you save.</p>
        <div className="actions"><button className="primary" disabled={!title.trim() || busy} onClick={generate}>{busy ? 'Generating draft…' : 'Generate a draft with AI'}</button>
          <button disabled={!title.trim() || busy} onClick={() => { setError(null); setDraft(manualDraft()) }}>Continue manually</button></div>
      </fieldset>
      <details className="card" style={{ marginTop: 16 }}><summary>Advanced: import existing target JSON</summary>
        <label>Target JSON<textarea rows={8} value={json} onChange={e => setJson(e.target.value)} disabled={busy} /></label>
        <button disabled={busy || !json.trim()} onClick={async () => {
          setBusy(true); setError(null)
          try { const res = await api.importTarget(JSON.parse(json)); navigate(`/targets/${res.id}`) }
          catch (e) { setError(e instanceof Error ? e.message : String(e)) }
          finally { setBusy(false) }
        }}>Import JSON</button>
      </details>
    </> : <>
      <TargetDraftEditor value={draft} onChange={setDraft} disabled={busy} />
      <p className="secondary">Review inferred tasks, feasibility and requirements before saving. These describe the role; they do not establish your ability.</p>
      <div className="actions"><button className="primary" disabled={busy || !draft.target.title.trim()} onClick={save}>{busy ? 'Saving…' : 'Save target'}</button>
        <button disabled={busy} onClick={() => setDraft(null)}>Back to description</button></div>
    </>}
  </div>
}
