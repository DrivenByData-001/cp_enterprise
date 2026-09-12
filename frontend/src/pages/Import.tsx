import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type DuplicateCheckResult, type DuplicateRoleSummary } from '../lib/api'

type Result = { ok: boolean; message: string }
type PendingCapture = { text: string; source: string }

function DuplicateWarning({
  duplicate,
  onOpenExisting,
  onCaptureAnyway,
  onCancel,
  busy,
}: {
  duplicate: DuplicateCheckResult
  onOpenExisting: (roleId: string) => void
  onCaptureAnyway: () => void
  onCancel: () => void
  busy: boolean
}) {
  const isExact = duplicate.exact_duplicate != null
  const match: DuplicateRoleSummary = (duplicate.exact_duplicate ?? duplicate.possible_duplicate)!

  return (
    <div
      className="card"
      style={{ marginBottom: 8, borderColor: isExact ? 'var(--critical)' : 'var(--warning)', background: isExact ? 'rgba(208,59,59,0.08)' : 'rgba(250,178,25,0.08)' }}
    >
      <strong>{isExact ? 'This document appears to have already been captured.' : 'This source appears to already exist.'}</strong>
      <p className="secondary" style={{ margin: '6px 0 0', fontSize: 13 }}>
        {isExact
          ? 'The exact same content is already stored as a captured role.'
          : 'The text matches an existing capture once whitespace differences (e.g. from PDF extraction) are ignored.'}
      </p>
      <div className="secondary" style={{ fontSize: 13, marginTop: 8 }}>
        <div>
          <strong>{match.title ?? 'Untitled'}</strong>
        </div>
        <div>{match.organisation ?? 'Unknown org'}</div>
        <div>Posting date: {match.posting_date ?? 'unknown'}</div>
      </div>
      <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {match.role_instance_id && (
          <button onClick={() => onOpenExisting(match.role_instance_id!)} disabled={busy}>
            Open existing
          </button>
        )}
        <button className="primary" onClick={onCaptureAnyway} disabled={busy}>
          {busy ? 'Capturing…' : 'Capture anyway'}
        </button>
        <button onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function SourceAwareIngest() {
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [organisation, setOrganisation] = useState('')
  const [location, setLocation] = useState('')
  const [country, setCountry] = useState('')
  const [postingDate, setPostingDate] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [duplicate, setDuplicate] = useState<DuplicateCheckResult | null>(null)
  const [pendingCapture, setPendingCapture] = useState<PendingCapture | null>(null)
  const pdfInput = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const goToRequirements = (id: string) => setTimeout(() => navigate(`/role-instances/${id}/requirements`), 500)

  const resetFields = () => {
    setText('')
    setTitle('')
    setOrganisation('')
    setLocation('')
    setCountry('')
    setPostingDate('')
    setSourceUrl('')
  }

  const doCapture = async (captureText: string, source: string) => {
    setBusy(true)
    setResult(null)
    try {
      const res = await api.ingestText({
        text: captureText,
        title: title.trim() || null,
        organisation: organisation.trim() || null,
        location: location.trim() || null,
        country: country.trim() || null,
        posting_date: postingDate || null,
        source_url: sourceUrl.trim() || null,
        source,
      })
      setResult({ ok: true, message: `Captured as document #${res.document_id}, role instance #${res.id}.` })
      resetFields()
      setDuplicate(null)
      setPendingCapture(null)
      goToRequirements(res.id)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const checkThenCapture = async (captureText: string, source: string) => {
    setBusy(true)
    setResult(null)
    setDuplicate(null)
    try {
      const check = await api.checkDuplicate(captureText, 'posting')
      if (check.exact_duplicate || check.possible_duplicate) {
        setDuplicate(check)
        setPendingCapture({ text: captureText, source })
        setBusy(false)
        return
      }
      await doCapture(captureText, source)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
      setBusy(false)
    }
  }

  const submitText = () => checkThenCapture(text, 'user_paste')

  const submitPdf = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy(true)
    setResult(null)
    try {
      const { text: extracted } = await api.extractPdfText(files[0])
      await checkThenCapture(extracted, 'pdf')
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
      setBusy(false)
    } finally {
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

      {duplicate && (
        <DuplicateWarning
          duplicate={duplicate}
          busy={busy}
          onOpenExisting={(roleId) => navigate(`/roles/${roleId}`)}
          onCaptureAnyway={() => pendingCapture && doCapture(pendingCapture.text, pendingCapture.source)}
          onCancel={() => {
            setDuplicate(null)
            setPendingCapture(null)
          }}
        />
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional — derived from the text if blank)" />
        <input value={organisation} onChange={(e) => setOrganisation(e.target.value)} placeholder="Employer / Organisation (optional)" />
        <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Location (optional)" />
        <input value={country} onChange={(e) => setCountry(e.target.value)} placeholder="Country (optional)" />
        <input
          type="date"
          value={postingDate}
          onChange={(e) => setPostingDate(e.target.value)}
          title="Posting date (optional — leave blank if unknown; never inferred from upload date)"
        />
        <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="Source URL (optional)" />
      </div>
      <textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste raw posting text…" />
      <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="primary" onClick={submitText} disabled={busy || !text.trim() || !!duplicate}>
          {busy ? 'Capturing…' : 'Capture text'}
        </button>
        <span className="muted" style={{ fontSize: 12 }}>or</span>
        <input ref={pdfInput} type="file" accept="application/pdf" onChange={(e) => submitPdf(e.target.files)} disabled={busy || !!duplicate} />
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
