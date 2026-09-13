import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ComparisonItem, type DevelopmentAction } from '../lib/api'

export default function ComparisonActions({ item, roleId, actions, onChanged }: {
  item: ComparisonItem; roleId: string; actions: DevelopmentAction[]; onChanged: () => Promise<void>
}) {
  const [note, setNote] = useState(item.person_side.assertion?.note ?? '')
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const act = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await operation()
      setMessage(success)
      try { await onChanged() }
      catch { setError('The change was saved, but the view could not refresh. Reload the page before repeating the action.') }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const asserted = item.status === 'user_asserted' || !!item.person_side.assertion
  return <div style={{ marginTop: 12 }}>
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    <div className="actions">
      <Link to="/profile360">Review supporting evidence</Link>
      <button onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide next steps' : 'Record an example or plan an action'}</button>
    </div>
    {open && <fieldset disabled={busy} className="form-stack" style={{ marginTop: 12 }}>
      <legend>Next steps for {item.concept.canonical_name}</legend>
      <label>Example or notes<textarea rows={3} value={note} maxLength={10000} onChange={e => setNote(e.target.value)} placeholder="What did you do, where, and what was the outcome?" /></label>
      <p className="muted">An example is a personal assertion until supporting evidence is reviewed. Assertions apply to this concept across all roles.</p>
      <div className="actions">
        {!asserted && item.status !== 'evidenced' && <button onClick={() => act(() => api.assertCapability(item.concept.id, note.trim() || undefined), 'Personal assertion saved.')}>I have done this</button>}
        {asserted && <>
          <button onClick={() => act(() => api.assertCapability(item.concept.id, note.trim() || undefined), 'Example saved.')}>Save example</button>
          <button onClick={() => act(() => api.retractAssertion(item.concept.id), 'Assertion removed from all role comparisons.')}>Undo assertion</button>
          <button onClick={() => act(async () => {
            await api.assertCapability(item.concept.id, note.trim() || undefined)
            await api.promoteAssertion(item.concept.id)
          }, 'Sent to the profile evidence review queue.')}>Send example for evidence review</button>
        </>}
      </div>
      <label>Development action<input value={title} maxLength={500} onChange={e => setTitle(e.target.value)} placeholder="For example: complete a project and document the result" /></label>
      <label>Target date (optional)<input type="date" value={due} onChange={e => setDue(e.target.value)} /></label>
      <button disabled={busy || !title.trim()} onClick={() => act(async () => {
        await api.createDevelopmentAction(roleId, { concept_id: item.concept.id, title: title.trim(), note, due_date: due || null })
        setTitle(''); setDue('')
      }, 'Development action saved.')}>Save development action</button>
      <p className="muted">Completing an action does not change your evidence status.</p>
    </fieldset>}
    {actions.length > 0 && <ul className="action-list">{actions.map(action => <li key={action.id}>
      <strong>{action.title}</strong> · {action.status === 'done' ? 'Done' : 'Open'}{action.due_date ? ' · Due ' + action.due_date : ''}
      {action.note && <p>{action.note}</p>}
      <div className="actions">
        <button disabled={busy} onClick={() => act(() => api.updateDevelopmentAction(roleId, action.id, action.status === 'done' ? 'open' : 'done'), 'Action updated.')}>{action.status === 'done' ? 'Reopen action' : 'Mark done'}</button>
        <button disabled={busy} onClick={() => { if (confirm('Delete this development action?')) void act(() => api.deleteDevelopmentAction(roleId, action.id), 'Action deleted.') }}>Delete action</button>
      </div>
    </li>)}</ul>}
  </div>
}
