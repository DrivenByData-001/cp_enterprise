import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type CareerDirection, type TargetDraft } from '../lib/api'
import TargetDraftEditor from '../components/TargetDraftEditor'

// Phase 6 (docs/37 build §21): `?direction_id=` prefills this form from a
// Career Direction's own dimensions/constraints/summary — sensibly, never
// silently — the user can edit every field before saving. A Target is never
// created merely because a direction exists; this page still only saves on
// the existing explicit "Save target" action, and the direction is linked
// back only after that save succeeds.
function describeDirection(d: CareerDirection): { description: string; support: string } {
  const dimensionLines = d.dimensions
    .map((dim) => `- ${dim.dimension_code}: ${dim.desired_direction} (importance ${dim.importance}/3)${dim.note ? ` — ${dim.note}` : ''}`)
    .join('\n')
  const c = d.constraints
  const constraintLines = [
    c.locations.length ? `Locations: ${c.locations.join(', ')}` : '',
    c.remote_types.length ? `Working mode: ${c.remote_types.join(', ')}` : '',
    c.employment_types.length ? `Employment type: ${c.employment_types.join(', ')}` : '',
    c.seniority_levels.length ? `Seniority: ${c.seniority_levels.join(', ')}` : '',
    c.compensation_floor
      ? `Compensation floor: ${c.compensation_floor.amount.toLocaleString()} ${c.compensation_floor.currency} (${c.compensation_floor.pay_period}, ${c.compensation_floor.employment_basis})`
      : '',
    c.other.length ? `Other context: ${c.other.join('; ')}` : '',
  ].filter(Boolean).join('\n')

  return {
    description: [d.summary, dimensionLines ? `Desired properties:\n${dimensionLines}` : ''].filter(Boolean).join('\n\n'),
    support: [constraintLines, `Originating Career Direction: ${d.name}`].filter(Boolean).join('\n\n'),
  }
}

export default function AddTarget() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const directionId = params.get('direction_id')
  const [direction, setDirection] = useState<CareerDirection | null>(null)
  const [directionError, setDirectionError] = useState<string | null>(null)

  const [title, setTitle] = useState('')
  const [organisation, setOrganisation] = useState('')
  const [imagined, setImagined] = useState(false)
  const [description, setDescription] = useState('')
  const [support, setSupport] = useState('')
  const [draft, setDraft] = useState<TargetDraft | null>(null)
  const [json, setJson] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!directionId) return
    let current = true
    api.getCareerDirection(directionId)
      .then((d) => {
        if (!current) return
        setDirection(d)
        setTitle(d.name)
        setImagined(true) // default to imagined unless anchored to a specific real role (build §21) — never known here
        const { description: desc, support: sup } = describeDirection(d)
        setDescription(desc)
        setSupport(sup)
      })
      .catch((e) => { if (current) setDirectionError(e instanceof Error ? e.message : String(e)) })
    return () => { current = false }
  }, [directionId])
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
      if (directionId) {
        try {
          await api.updateCareerDirection(directionId, { target_role_instance_id: res.id })
        } catch {
          // The Target itself saved fine — linking can still be done manually
          // from the Direction detail page ("Link an existing Target"), so a
          // linking failure must never block the save the user just made.
        }
      }
      navigate(`/targets/${res.id}`)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <div>
    <Link to={directionId ? `/future/directions/${directionId}` : '/targets'}>← Back to {directionId ? 'direction' : 'targets'}</Link>
    <h1>Add a target</h1>
    {directionId && (
      directionError
        ? <p role="alert">Could not load the Career Direction to prefill from: {directionError}</p>
        : direction && <p className="secondary">Prefilled from your Career Direction "{direction.name}" — edit anything below before saving.</p>
    )}
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
