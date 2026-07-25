import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Role } from '../lib/api'

type Result = { ok: boolean; message: string }

export default function RoleEdit() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [role, setRole] = useState<Role | null>(null)
  const [text, setText] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api
      .getRole(Number(id))
      .then((r) => {
        setRole(r)
        setText(JSON.stringify(r.raw_json, null, 2))
      })
      .catch((e) => setLoadError(String(e)))
  }, [id])

  const isTarget = role?.node_type !== 'posting'
  const promptName = isTarget ? 'prompts/decompose_target_role.md' : 'prompts/extract_job_posting.md'

  const submit = async () => {
    if (!role) return
    setBusy(true)
    setResult(null)
    try {
      const parsed = JSON.parse(text)
      if (isTarget) {
        await api.updateTarget(role.id, parsed)
      } else {
        await api.updateRole(role.id, parsed)
      }
      setResult({ ok: true, message: 'Saved. Re-embedded from the updated content.' })
      setTimeout(() => navigate(`/roles/${role.id}`), 700)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  if (loadError) return <p style={{ color: 'var(--critical)' }}>{loadError}</p>
  if (!role) return <p className="muted">Loading…</p>

  return (
    <div>
      <Link to={`/roles/${role.id}`} className="muted" style={{ fontSize: 13 }}>
        ← Back to {role.title}
      </Link>

      <h1 style={{ fontSize: 22, marginTop: 12 }}>Edit "{role.title}"</h1>
      <p className="secondary">
        To add more info to this {isTarget ? 'target' : 'posting'} (e.g. you found extra detail, or want to
        re-research it with new supporting material), go back to Claude/ChatGPT with{' '}
        <code>{promptName}</code>, get an updated JSON, and paste the full object below. This replaces the stored
        fields and skills for this entry and re-embeds it — it's a full overwrite, not a merge, so paste the
        complete object each time.
      </p>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Updated JSON</h3>
        <textarea
          rows={18}
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
        />
        <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
          <button className="primary" onClick={submit} disabled={busy || !text.trim()}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
          <button onClick={() => navigate(`/roles/${role.id}`)} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>

      {result && (
        <p style={{ marginTop: 16, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>
      )}
    </div>
  )
}
