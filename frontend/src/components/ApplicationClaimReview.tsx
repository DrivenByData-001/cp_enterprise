import { useEffect, useState } from 'react'
import { applicationMode, type ClaimAcceptance, type ClaimContext, type EvidenceSource } from '../lib/applicationMode'

export default function ApplicationClaimReview({ applicationId, conceptId, claimId, onAccepted, onClose }: {
  applicationId: string; conceptId: string; claimId?: string;
  onAccepted: (source: EvidenceSource) => void; onClose: () => void;
}) {
  const key = `career-claim-draft:${applicationId}:${conceptId}:${claimId ?? 'new'}`
  const [recovered] = useState(() => { try { return JSON.parse(sessionStorage.getItem(key) ?? 'null') } catch { return null } })
  const [context, setContext] = useState<ClaimContext | null>(null)
  const [draft, setDraft] = useState<ClaimAcceptance | null>(recovered)
  const [reviewing, setReviewing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    applicationMode.claimContext(applicationId, conceptId, claimId).then(result => {
      if (!active) return
      setContext(result)
      setDraft(previous => previous ?? { operation_id: crypto.randomUUID(), claim_id: claimId ?? null,
        source_revision: result.claim?.source_revision ?? null, claim_text: result.claim?.claim_text ?? '',
        episode_id: result.claim?.episode_id ?? null, reason: '', confirmed: true })
    }).catch(e => { if (active) setError(String(e)) })
    return () => { active = false }
  }, [applicationId, conceptId, claimId])
  const change = (next: ClaimAcceptance) => {
    // Preserve the operation key: an uncertain response must not create a
    // duplicate. The server rejects changed payloads for an accepted key.
    setDraft(next); sessionStorage.setItem(key, JSON.stringify(next))
  }
  const accept = async () => {
    if (!draft) return
    setBusy(true); setError(null)
    sessionStorage.setItem(key, JSON.stringify(draft))
    try {
      const result = await applicationMode.acceptClaim(applicationId, conceptId, draft)
      sessionStorage.removeItem(key)
      onAccepted(result.source)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <section className="mode-new-evidence" aria-label="Review career fact">
    <h3>{claimId ? 'Correct a shared career claim' : 'Add a reviewed career claim'}</h3>
    <p>This updates your shared Profile360 career record. Other applications using a corrected claim will need a fresh evidence review.</p>
    {error && <p role="alert">{error} Your draft is retained. Retry the same acceptance if the response was interrupted.</p>}
    {!context && !error && <p role="status">Loading career record…</p>}
    {draft && context && <fieldset disabled={busy} className="mode-fields">
      {reviewing ? <>
        {context.claim && <><h4>Current career claim</h4><p className="mode-source-text">{context.claim.claim_text}</p></>}
        <h4>Career fact to accept</h4><p className="mode-source-text">{draft.claim_text}</p>
        <p>Career episode: {context.episodes.find(e => e.id === draft.episode_id)?.title ?? 'Not linked to an episode'}</p>
        <p>Source or correction reason: {draft.reason}</p>
        <p>Accept only facts you have checked. New claims are recorded as user asserted, with your explanation retained as provenance.</p>
        <button className="primary" onClick={accept}>{busy ? 'Accepting…' : 'Accept into Profile360 & use here'}</button>
        <button onClick={() => setReviewing(false)}>Back to edit</button>
      </> : <>
        <label>Career fact<textarea aria-label="Career fact" rows={5} value={draft.claim_text} onChange={e => change({ ...draft, claim_text: e.target.value })} maxLength={10000} /></label>
        <label>Career episode<select value={draft.episode_id ?? ''} onChange={e => change({ ...draft, episode_id: e.target.value || null })}>
          <option value="">Not linked to an episode</option>
          {context.episodes.map(e => <option key={e.id} value={e.id}>{e.title ?? 'Untitled episode'}{e.organisation ? ` · ${e.organisation}` : ''}{e.start_date ? ` · ${e.start_date}` : ''}</option>)}
        </select></label>
        <label>Source or correction reason<textarea aria-label="Source or correction reason" rows={3} value={draft.reason} onChange={e => change({ ...draft, reason: e.target.value })} maxLength={10000} /></label>
        <button disabled={!draft.claim_text.trim() || !draft.reason.trim()} onClick={() => setReviewing(true)}>Review before accepting</button>
      </>}
    </fieldset>}
    <button disabled={busy} onClick={onClose}>Close and keep draft</button>
    {error && <button disabled={busy} onClick={() => { sessionStorage.removeItem(key); onClose() }}>Discard draft and close</button>}
  </section>
}
