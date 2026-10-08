import { useEffect, useState } from 'react'
import { req } from '../lib/api'

type Proposal = { rationale: string; limitations: string; question?: string; clarification?: string; depth: string | null; autonomy: string | null }
type Finding = {
  id: string; revision: number; status: string; stale: boolean; proposal: Proposal; reviewed_payload: Proposal | null
  requirement_snapshot: { canonical_name: string; evidence_span: string }
  source_snapshot: { claim: { claim_text: string; evidence_class: string; uncertainty: string | null }; episode: { organisation: string; title: string; start_date: string | null; autonomy: string | null } | null; evidence: { id: string; passage: string | null; locator: string | null; document_title: string | null; evidence_type: string; notes: string | null }[] }
}
type Data = { run: { status: string; completed_batches: number; total_batches: number; total_sources: number; error: string | null; model: string | null } | null; findings: Finding[] }

function FindingCard({ finding: f, base, onSaved }: { finding: Finding; base: string; onSaved: () => Promise<void> }) {
  const [draft, setDraft] = useState<Proposal>(f.reviewed_payload ?? f.proposal)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const source = f.source_snapshot
  async function review(action: string) {
    setBusy(true); setError('')
    try {
      await req(`${base}/${f.id}/review`, { method: 'POST', body: JSON.stringify({ action, revision: f.revision,
        rationale: draft.rationale, limitations: draft.limitations, clarification: draft.clarification ?? '', depth: draft.depth, autonomy: draft.autonomy }) })
      await onSaved()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  return <article className="mode-source discovery-finding">
    <h4>{f.requirement_snapshot.canonical_name} · {source.episode?.organisation ?? 'Career evidence'}</h4>
    <p className="secondary">{source.episode?.start_date ?? 'Date not recorded'} · {source.episode?.title} · {f.status}</p>
    <blockquote>{f.requirement_snapshot.evidence_span}</blockquote>
    <p className="mode-source-text">{source.claim.claim_text}</p>
    <p className="secondary">Evidence type: {source.claim.evidence_class}</p>
    {source.claim.uncertainty && <p><strong>Source limitations:</strong> {source.claim.uncertainty}</p>}
    <details><summary>Inspect supporting records ({source.evidence.length})</summary>
      {source.episode?.autonomy && <p>Recorded responsibility: {source.episode.autonomy}</p>}
      {source.evidence.map(e => <section key={e.id}><p><strong>{e.document_title ?? e.locator ?? 'Recorded evidence'}</strong> · {e.evidence_type}</p><p>{e.passage ?? 'No original passage stored; inspect the source reference.'}</p><small>{e.locator}</small>{e.notes && <p>{e.notes}</p>}</section>)}
    </details>
    {f.stale && <p role="status">Source or requirement changed. Reject this finding and search again for a current proposal.</p>}
    {f.status === 'pending' ? <fieldset disabled={busy} className="mode-fields">
      <legend>Review proposed connection</legend>
      <label>Why this supports the requirement<textarea value={draft.rationale} onChange={e => setDraft({ ...draft, rationale: e.target.value })} /></label>
      <label>Limits or remaining gaps<textarea value={draft.limitations} onChange={e => setDraft({ ...draft, limitations: e.target.value })} /></label>
      {f.proposal.question && <p><strong>Clarification:</strong> {f.proposal.question}</p>}
      <label>Your clarification (optional; saved as your own statement on approval)<textarea value={draft.clarification ?? ''} onChange={e => setDraft({ ...draft, clarification: e.target.value })} /></label>
      <label>Demonstrated depth<select value={draft.depth ?? ''} onChange={e => setDraft({ ...draft, depth: e.target.value || null })}>
        <option value="">Unknown / not established</option>{['exposed', 'applied', 'owned', 'set_standard'].map(v => <option key={v} value={v}>{v.replace('_', ' ')}</option>)}
      </select></label>
      <label>Demonstrated independence<select value={draft.autonomy ?? ''} onChange={e => setDraft({ ...draft, autonomy: e.target.value || null })}>
        <option value="">Unknown / not established</option>{['assisted', 'independent', 'directed_others', 'accountable'].map(v => <option key={v} value={v}>{v.replace('_', ' ')}</option>)}
      </select></label>
      <p>Approval links this existing career claim to the capability across applications and records these evidence-specific assessments. It does not automatically mark this application requirement covered.</p>
      <label className="discovery-check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I have reviewed the source, proposed connection and responsibility levels.</label>
      <div className="mode-search"><button disabled={f.stale || !confirmed || !draft.rationale.trim()} onClick={() => review('approve')}>Approve finding</button><button disabled={f.stale || !draft.rationale.trim()} onClick={() => review('edit')}>Save edits for later</button><button onClick={() => review('reject')}>Reject finding</button></div>
    </fieldset> : <><p>{draft.rationale}</p><p>{draft.limitations}</p></>}
    {error && <p role="alert">{error} Your draft is retained here.</p>}
  </article>
}

export default function EvidenceDiscovery({ applicationId, onChanged }: { applicationId: string; onChanged: () => Promise<void> }) {
  const base = `/applications/${applicationId}/evidence-discovery`
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState(false)
  const load = async () => { setData(await req<Data>(base)) }
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const next = await req<Data>(base)
        if (!cancelled) { setData(next); if (next.run?.status === 'running') timer = setTimeout(poll, 3000) }
      } catch (e) { if (!cancelled) setError(String(e)) }
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [base, busy])
  async function start() {
    setBusy(true); setError('')
    try { await req(base, { method: 'POST' }); await load() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function saved() { await load(); await onChanged() }
  const pending = data?.findings.filter(f => f.status === 'pending') ?? []
  return <section aria-label="Discover supporting evidence">
    <h3>Find supporting evidence</h3>
    <p>AI searches your Profile360 career claims and their supporting records against the reviewed role requirements, starting with recent work. It explains each proposed connection and remaining gaps. Approve, edit or reject the findings; nothing is accepted automatically.</p>
    <button onClick={start} disabled={busy || data?.run?.status === 'running'}>{busy || data?.run?.status === 'running' ? 'Searching career records…' : 'Find supporting evidence'}</button>
    {error && <p role="alert">{error}<button onClick={() => load().then(() => setError('')).catch(e => setError(String(e)))}>Reload findings</button></p>}
    {data?.run && <div role={data.run.status === 'failed' ? 'alert' : 'status'} className={data.run.status === 'failed' ? 'mode-notice discovery-failure' : undefined}>
      {data.run.status === 'failed' && <h4>Evidence search failed</h4>}
      <p>{data.run.status === 'running' ? `Searching: ${data.run.completed_batches} of ${data.run.total_batches} batches complete. You can leave and return to this Evidence overview; findings will appear below.` : data.run.status === 'failed' ? `Search failed: ${data.run.error}` : `Search complete: ${data.run.total_sources} career claims checked using ${data.run.model ?? 'the configured model'}.`}</p>
      {data.run.status === 'failed' && <p>The scan did not finish. {pending.length ? 'Findings already saved remain below for review.' : 'No findings are waiting for review; this does not mean your records lack suitable evidence.'} Your profile has not been changed by this scan. Use “Find supporting evidence” to retry.</p>}
      {data.run.status !== 'failed' && <p>{pending.length} findings await review. Scanning does not change your profile; approve individual findings below to save their connections.</p>}
      {data.run.status === 'complete' && pending.length === 0 && <p>{data.findings.length ? 'All saved findings have been reviewed. Enable history to inspect them.' : 'No proposed connections were returned. Review your recorded career examples or add a specific example for the requirement; an empty scan is not proof of a capability gap.'}</p>}
    </div>}
    <label className="discovery-check"><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} /> Show accepted and rejected findings</label>
    {(history ? data?.findings ?? [] : pending).map(f => <FindingCard key={`${f.id}:${f.revision}`} finding={f} base={base} onSaved={saved} />)}
  </section>
}
