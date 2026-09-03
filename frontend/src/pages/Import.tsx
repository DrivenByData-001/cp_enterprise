import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

type Result = { ok: boolean; message: string }

function SourceAwareIngest() {
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const pdfInput = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const goToRequirements = (id: number) => setTimeout(() => navigate(`/role-instances/${id}/requirements`), 500)

  const submitText = async () => {
    setBusy(true)
    setResult(null)
    try {
      const res = await api.ingestText({ text, title: title.trim() || null, source_url: sourceUrl.trim() || null })
      setResult({ ok: true, message: `Captured as document #${res.document_id}, role instance #${res.id}.` })
      setText('')
      setTitle('')
      setSourceUrl('')
      goToRequirements(res.id)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const submitPdf = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy(true)
    setResult(null)
    try {
      const res = await api.ingestPdf(files[0], { title: title.trim() || null, source_url: sourceUrl.trim() || null })
      setResult({ ok: true, message: `Captured as document #${res.document_id}, role instance #${res.id}.` })
      goToRequirements(res.id)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
      if (pdfInput.current) pdfInput.current.value = ''
    }
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>Source-aware ingest → requirement claims</h3>
      <p className="secondary" style={{ marginTop: 0, fontSize: 13 }}>
        Captures the raw text as an immutable source document first, then lets you extract reviewable requirement
        claims against the canonical vocabulary — separately, on the next page. Nothing here is auto-accepted.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional — derived from the text if blank)" />
        <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="Source URL (optional)" />
      </div>
      <textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste raw posting text…" />
      <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="primary" onClick={submitText} disabled={busy || !text.trim()}>
          {busy ? 'Capturing…' : 'Capture text'}
        </button>
        <span className="muted" style={{ fontSize: 12 }}>or</span>
        <input ref={pdfInput} type="file" accept="application/pdf" onChange={(e) => submitPdf(e.target.files)} disabled={busy} />
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 6, marginBottom: 0 }}>
        Selectable-text PDFs only for now — image-only/scanned PDFs need OCR, not yet supported.
      </p>
      {result && (
        <p style={{ marginTop: 12, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>
      )}
    </div>
  )
}

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
      setResult({ ok: true, message: `AI extracted and imported role (id ${res.id}), via ${res.run.model}.` })
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

      <SourceAwareIngest />

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>AI extraction (legacy flat fields)</h3>
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
