import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

type Result = { ok: boolean; message: string }

export default function AddTarget() {
  const [text, setText] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  const submit = async () => {
    setBusy(true)
    setResult(null)
    try {
      const parsed = JSON.parse(text)
      const res = await api.importTarget(parsed)
      setResult({ ok: true, message: `Added "${parsed.target?.title ?? 'target'}" (id ${res.id}).` })
      setText('')
      setTimeout(() => navigate(`/roles/${res.id}`), 700)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <Link to="/targets" className="muted" style={{ fontSize: 13 }}>
        ← Back to targets
      </Link>

      <h1 style={{ fontSize: 22, marginTop: 12 }}>Add a target</h1>
      <p className="secondary">
        Give <code>prompts/decompose_target_role.md</code> to Claude or ChatGPT along with a role — real (a
        specific posting/title/organisation) or imagined (a hypothetical role you're picturing). Paste any
        supporting material you've gathered (postings, profiles, articles) into the prompt too — the AI will
        decompose it into typical tasks, a skill breakdown with concrete examples, the technical subjects to study,
        and an honest feasibility assessment, grounding an imagined role in the nearest real analogues. Paste the
        resulting JSON below.
      </p>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Paste JSON</h3>
        <textarea
          rows={12}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste the decomposed target JSON object here…"
          style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
        />
        <div style={{ marginTop: 8 }}>
          <button className="primary" onClick={submit} disabled={busy || !text.trim()}>
            {busy ? 'Adding…' : 'Add target'}
          </button>
        </div>
      </div>

      {result && (
        <p style={{ marginTop: 16, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>
      )}
    </div>
  )
}
