import { useEffect, useState } from 'react'
import { api } from '../lib/api'

export default function Profile() {
  const [text, setText] = useState('')
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .getProfile()
      .then((p) => {
        if (p) {
          setText(p.narrative_text)
          setSavedAt(p.created_at)
        }
      })
      .finally(() => setLoading(false))
  }, [])

  const save = async () => {
    setBusy(true)
    try {
      await api.updateProfile(text)
      setSavedAt(new Date().toISOString())
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>Your profile</h1>
      <p className="secondary">
        A short narrative of where you are right now — background, current role, strengths, what you're working
        towards. This gets embedded and compared against every captured role. Update it whenever your position
        changes; each save keeps a timestamped snapshot.
      </p>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="card">
          <textarea
            rows={14}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="e.g. Actuarial analyst with 4 years in general insurance pricing, strong in Python and GLMs, currently 2 exams from IFoA fellowship, looking to move towards senior pricing or a data science-adjacent role…"
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
            <span className="muted" style={{ fontSize: 12 }}>
              {savedAt ? `Last saved ${new Date(savedAt).toLocaleString()}` : 'Not saved yet'}
            </span>
            <button className="primary" onClick={save} disabled={busy || !text.trim()}>
              {busy ? 'Saving…' : 'Save & re-embed'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
