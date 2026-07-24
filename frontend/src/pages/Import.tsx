import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

type Result = { ok: boolean; message: string }

export default function Import() {
  const [text, setText] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const submitPaste = async () => {
    setBusy(true)
    setResult(null)
    try {
      const parsed = JSON.parse(text)
      const res = await api.importPosting(parsed)
      setResult({ ok: true, message: `Imported "${parsed.job?.title ?? 'role'}" (id ${res.id}).` })
      setText('')
      setTimeout(() => navigate(`/roles/${res.id}`), 700)
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
        Give the extraction prompt (<code>prompts/extract_job_posting.md</code>) and a posting URL to Claude or
        ChatGPT. Paste the JSON it returns below, or drop one/many JSON files.
      </p>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Paste JSON</h3>
        <textarea
          rows={12}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste the extracted JSON object here…"
          style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
        />
        <div style={{ marginTop: 8 }}>
          <button className="primary" onClick={submitPaste} disabled={busy || !text.trim()}>
            {busy ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Or upload file(s)</h3>
        <input
          ref={fileInput}
          type="file"
          accept="application/json"
          multiple
          onChange={(e) => submitFiles(e.target.files)}
        />
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          Select multiple .json files to bulk-import a folder of already-captured postings.
        </p>
      </div>

      {result && (
        <p style={{ marginTop: 16, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>
      )}
    </div>
  )
}
