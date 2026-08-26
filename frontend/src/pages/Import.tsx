import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

type Result = { ok: boolean; message: string }

export default function Import() {
  const [text, setText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [postingDate, setPostingDate] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const submitNative = async () => {
    setBusy(true)
    setResult(null)
    try {
      const res = await api.importPostingNative({
        text,
        source_url: sourceUrl.trim() || null,
        known_posting_date: postingDate || null,
      })
      setResult({ ok: true, message: `AI extracted and imported role (id ${res.id}).` })
      setText('')
      setSourceUrl('')
      setPostingDate('')
      setTimeout(() => navigate(`/roles/${res.id}`), 500)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const submitFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy(true)
    setResult(null)
    try {
      const res = await api.importBulk(Array.from(files))
      const okCount = res.results.filter((r: { status: string }) => r.status === 'imported').length
      const failCount = res.results.length - okCount
      setResult({
        ok: failCount === 0,
        message: `Imported ${okCount} of ${res.results.length} file(s).${failCount ? ' Check console for errors.' : ''}`,
      })
      if (failCount) console.warn(res.results)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>Import a role</h1>
      <p className="secondary">
        Paste the posting text and let the app extract and analyse it. The structured result is validated and stored directly.
      </p>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>AI extraction</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 8, marginBottom: 8 }}>
          <input
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            placeholder="Source URL (optional)"
          />
          <input type="date" value={postingDate} onChange={(e) => setPostingDate(e.target.value)} />
        </div>
        <textarea
          rows={16}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste the raw job posting here…"
        />
        <div style={{ marginTop: 8 }}>
          <button className="primary" onClick={submitNative} disabled={busy || !text.trim()}>
            {busy ? 'Extracting…' : 'Extract with AI & import'}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Legacy JSON import</h3>
        <input
          ref={fileInput}
          type="file"
          accept="application/json"
          multiple
          onChange={(e) => submitFiles(e.target.files)}
        />
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          Existing extracted JSON files remain supported for migration and recovery.
        </p>
      </div>

      {result && (
        <p style={{ marginTop: 16, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>
      )}
    </div>
  )
}
