import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type DuplicateCheckResult } from '../lib/api'
import ImportSteps from '../components/ImportSteps'

type BulkResult = { file: string; status: string; id?: string; error?: string }

export default function Import() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [source, setSource] = useState('user_paste')
  const [title, setTitle] = useState('')
  const [organisation, setOrganisation] = useState('')
  const [location, setLocation] = useState('')
  const [country, setCountry] = useState('')
  const [postingDate, setPostingDate] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [duplicate, setDuplicate] = useState<DuplicateCheckResult | null>(null)
  const [bulkResults, setBulkResults] = useState<BulkResult[]>([])
  const [failedFiles, setFailedFiles] = useState<File[]>([])
  const fileInput = useRef<HTMLInputElement>(null)
  const pdfInput = useRef<HTMLInputElement>(null)
  const match = duplicate?.exact_duplicate ?? duplicate?.possible_duplicate

  const capture = async (force = false) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      if (!force) {
        const check = await api.checkDuplicate(text, 'posting')
        if (check.exact_duplicate || check.possible_duplicate) { setDuplicate(check); return }
      }
      const res = await api.ingestText({ text, source, title: title.trim() || null,
        organisation: organisation.trim() || null, location: location.trim() || null,
        country: country.trim() || null, posting_date: postingDate || null, source_url: sourceUrl.trim() || null })
      navigate(`/role-instances/${res.id}/requirements?step=details`)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const previewPdf = async (file?: File) => {
    if (!file) return
    setBusy(true); setError(null); setMessage(null)
    try {
      const res = await api.extractPdfText(file)
      setText(res.text); setSource('pdf')
      setMessage('PDF text is ready to review. Nothing is saved yet.')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false); if (pdfInput.current) pdfInput.current.value = '' }
  }
  const importFiles = async (files: File[] | FileList | null, retry = false) => {
    if (!files?.length) return
    const submitted = Array.from(files)
    setBusy(true); setError(null); setMessage(null)
    try {
      const res = await api.importBulk(submitted)
      setFailedFiles(submitted.filter((_, i) => res.results[i]?.status !== 'imported'))
      setBulkResults(previous => retry ? [...previous.filter(r => r.status === 'imported'), ...res.results] : res.results)
      setMessage('Import finished. Review the result for each file below.')
    } catch (e) {
      setFailedFiles([])
      setError(`${e instanceof Error ? e.message : String(e)} Import outcome could not be confirmed. Check your roles before resubmitting to avoid duplicates.`)
    } finally { setBusy(false); if (fileInput.current) fileInput.current.value = '' }
  }
  const importNative = async () => {
    setBusy(true); setError(null)
    try {
      const res = await api.importPostingNative({ text, source_url: sourceUrl || null, known_posting_date: postingDate || null })
      navigate(`/roles/${res.id}`)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  return <div>
    <h1>Import a role</h1>
    <ImportSteps step={0} />
    <p>Add the source, review the details and requirements, then compare the role with your evidence.</p>
    {error && <p role="alert">{error} Your input is preserved.</p>}
    {message && <p role="status">{message}</p>}
    {match && <div className="card" role="alert">
      <strong>{duplicate?.exact_duplicate ? 'This document appears to have already been captured.' : 'This source appears to already exist.'}</strong>
      <p>{match.title ?? 'Untitled'}</p><p>{match.organisation ?? 'Unknown org'}</p>
      <p>Posting date: {match.posting_date ?? 'unknown'}</p>
      <p>{duplicate?.exact_duplicate ? 'The exact content is already stored.' : 'The text matches after whitespace differences are ignored.'}</p>
      <div className="actions">
        {match.role_instance_id && <button disabled={busy} onClick={() => navigate(`/roles/${match.role_instance_id}`)}>Open existing</button>}
        <button disabled={busy} onClick={() => capture(true)}>Save anyway</button>
        <button disabled={busy} onClick={() => setDuplicate(null)}>Cancel</button>
      </div>
    </div>}
    <fieldset disabled={busy || !!duplicate} className="form-stack card">
      <legend>Add a posting</legend>
      <div className="form-grid">
        <label>Title (optional)<input value={title} onChange={e => setTitle(e.target.value)} /></label>
        <label>Employer (optional)<input value={organisation} onChange={e => setOrganisation(e.target.value)} /></label>
        <label>Location (optional)<input value={location} onChange={e => setLocation(e.target.value)} /></label>
        <label>Country (optional)<input value={country} onChange={e => setCountry(e.target.value)} /></label>
        <label>Posting date (optional)<input type="date" value={postingDate} onChange={e => setPostingDate(e.target.value)} /></label>
        <label>Source URL (optional)<input type="url" value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} /></label>
      </div>
      <label>{source === 'pdf' ? 'Extracted PDF text (preview)' : 'Posting text'}
        <textarea rows={10} value={text} readOnly={source === 'pdf'} onChange={e => setText(e.target.value)} placeholder="Paste raw posting text…" />
      </label>
      {source === 'pdf' && <button onClick={() => { setSource('user_paste'); setText('') }}>Use pasted text instead</button>}
      <label>Upload a PDF<input ref={pdfInput} type="file" accept="application/pdf" onChange={e => previewPdf(e.target.files?.[0])} /></label>
      <p className="muted">Selectable-text PDFs only. Scanned PDFs need OCR before import. Leave unknown posting dates blank.</p>
      <p className="muted" style={{ margin: '4px 0' }}>Nothing is saved until you choose Save posting.</p>
      <button className="primary" disabled={busy || !text.trim()} onClick={() => capture()}>{busy ? 'Saving…' : 'Save posting & continue'}</button>
    </fieldset>
    <details className="card" style={{ marginTop: 16 }}>
      <summary>Advanced imports</summary>
      <p>For previously extracted files or the original one-step import workflow.</p>
      <label>JSON files<input ref={fileInput} type="file" multiple accept="application/json" disabled={busy} onChange={e => importFiles(e.target.files)} /></label>
      <p className="secondary">One-step AI import uses the posting text and source details above, saves the extraction immediately, and opens the role for review.</p>
      <button disabled={busy || !!duplicate || !text.trim() || source === 'pdf'} onClick={importNative}>One-step AI import</button>
    </details>
    {bulkResults.length > 0 && <section className="card" aria-label="Import results" style={{ marginTop: 16 }}>
      <h2>Import results</h2>
      <ul>{bulkResults.map((r, i) => <li key={i}><strong>{r.file}</strong>: {r.status === 'imported' ? 'Imported' : r.error ?? 'Failed'}{' '}
        {r.id && <Link to={`/roles/${r.id}`}>Open role</Link>}</li>)}</ul>
      {failedFiles.length > 0 && <button disabled={busy} onClick={() => importFiles(failedFiles, true)}>Retry failed files only ({failedFiles.length})</button>}
    </section>}
  </div>
}
