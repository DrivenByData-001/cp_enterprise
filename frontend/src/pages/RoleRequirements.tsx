import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  api,
  type Concept,
  type ExtractionSummary,
  type RequirementClaim,
  type RequirementClaimEditInput,
  type RoleMetadataInput,
} from '../lib/api'
import ImportSteps from '../components/ImportSteps'
import RoleMetadataForm from '../components/RoleMetadataForm'

const BASIS_LABEL: Record<string, string> = {
  stated: 'stated',
  implied: 'implied',
  inferred: 'inferred (no verbatim span — provenance too weak to trust one)',
  user_asserted: 'user-asserted',
}

const REQUIREMENT_TYPE_OPTIONS = ['required', 'preferred', 'contextual'] as const
const BASIS_OPTIONS = ['stated', 'implied', 'inferred', 'user_asserted'] as const

// Reviewable, AI-assisted metadata enrichment (source-aware ingest cleanup,
// problem #5): proposes title/organisation/location/... from the role's own
// source document, but never writes anything until the user explicitly
// accepts — editable in between, same posture as requirement claims below
// ("nothing here was auto-accepted"). Placed on this page rather than a new
// one, per the brief's own suggestion that this is the cleanest fit.
function MetadataEnrichmentPanel({ roleId }: { roleId: string }) {
  const [proposal, setProposal] = useState<RoleMetadataInput | null>(null)
  const [proposing, setProposing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const propose = async () => {
    setProposing(true)
    setError(null)
    setSaved(false)
    try {
      const result = await api.proposeRoleMetadata(roleId)
      if (result.status === 'failed' || !result.proposal) {
        setError(result.error ?? 'Metadata proposal failed.')
      } else {
        setProposal(result.proposal)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setProposing(false)
    }
  }

  const manual = async () => {
    setProposing(true); setError(null); setSaved(false)
    try {
      const role = await api.getRole(roleId)
      setProposal({ title: role.title, organisation: role.organisation, location: role.location,
        country: role.country, posting_date: role.posting_date, remote_type: role.remote_type,
        employment_type: role.employment_type, seniority_level: role.seniority_level })
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setProposing(false) }
  }

  const accept = async () => {
    if (!proposal) return
    setSaving(true)
    setError(null)
    try {
      await api.updateRoleMetadata(roleId, proposal)
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h3 style={{ marginTop: 0, marginBottom: 4, fontSize: 14 }}>Review role details</h3>
          <p className="muted" style={{ fontSize: 12, margin: 0, maxWidth: 560 }}>
            Proposes title/employer/location/date/etc. from this role's own captured source — never invented, and
            never authoritative until you accept it below. A posting date left blank in the source stays blank here
            too; it is never filled in from the capture date.
          </p>
        </div>
        {!proposal && (
          <button className="primary" onClick={propose} disabled={proposing} style={{ flexShrink: 0 }}>
            {proposing ? 'Proposing…' : 'Suggest details from the source'}
          </button>
        )}
      </div>

      {!proposal && <button onClick={manual} disabled={proposing}>Edit details manually</button>}
      {error && <p role="alert" style={{ color: 'var(--critical)', fontSize: 13, marginTop: 10 }}>{error}</p>}

      {proposal && (
        <div style={{ marginTop: 12 }}>
          <RoleMetadataForm value={proposal} onChange={value => { setProposal(value); setSaved(false) }} disabled={saving} />
          <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="primary" onClick={accept} disabled={saving}>
              {saving ? 'Saving…' : 'Save details'}
            </button>
            <button onClick={() => setProposal(null)} disabled={saving}>
              Discard proposal
            </button>
            {saved && <span style={{ color: 'var(--good)', fontSize: 13 }}>Saved.</span>}
          </div>
        </div>
      )}
    </div>
  )
}

// Debounced search against active accepted vocabulary concepts (mirrors
// TargetRequirementPicker's pattern) — never fires one uncontrolled request
// per keystroke, and never lets the user invent a concept inline; if
// nothing matches, the route to Vocabulary is the only way forward.
function ConceptPicker({ onSelect }: { onSelect: (c: Concept) => void }) {
  const [query, setQuery] = useState('')
  const [options, setOptions] = useState<Concept[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  useEffect(() => {
    let active = true
    setOptions([]); setSearchError('')
    if (!query.trim()) { setSearching(false); return }
    setSearching(true)
    const timer = setTimeout(() => {
      api.listConcepts({ q: query, status: 'active' }).then(rows => { if (active) setOptions(rows) })
        .catch(e => { if (active) setSearchError(e instanceof Error ? e.message : String(e)) })
        .finally(() => { if (active) setSearching(false) })
    }, 250)
    return () => { active = false; clearTimeout(timer) }
  }, [query])

  return (
    <div>
      <label>
        Search active vocabulary concepts
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. Python, Solvency II…" />
      </label>
      {searching && <span className="muted" style={{ fontSize: 12 }}>Searching…</span>}
      {searchError && <p role="alert" style={{ fontSize: 13 }}>Search failed: {searchError}. Change the search to retry.</p>}
      {options.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
          {options.slice(0, 20).map(c => (
            <button type="button" key={c.id} onClick={() => { onSelect(c); setQuery('') }}>
              {c.canonical_name} <span className="muted">({c.type_code})</span>
            </button>
          ))}
        </div>
      )}
      {query.trim() && !searching && !searchError && options.length === 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          No active concept matches. <Link to={`/vocabulary?focusConceptName=${encodeURIComponent(query)}`}>Add it in Vocabulary</Link> rather
          than inventing one here.
        </p>
      )}
    </div>
  )
}

type EditDraft = {
  concept_id: string
  canonical_name: string
  type_code: string
  requirement_type: string
  basis: string
  importance: string
  evidence_span: string
}

function draftFromClaim(c: RequirementClaim): EditDraft {
  return {
    concept_id: c.concept_id,
    canonical_name: c.canonical_name,
    type_code: c.type_code,
    requirement_type: c.requirement_type,
    basis: c.basis,
    importance: c.importance != null ? String(c.importance) : '',
    evidence_span: c.evidence_span ?? '',
  }
}

function EvidenceList({ claim }: { claim: RequirementClaim }) {
  const evidence = claim.evidence ?? []
  const [expanded, setExpanded] = useState(false)

  if (evidence.length === 0) {
    // Pre-backfill or evidence-less claims (e.g. a manual add with no
    // linked document) still show the claim's own legacy evidence_span, if
    // any, rather than an empty card.
    return claim.evidence_span ? (
      <p className="secondary" style={{ fontSize: 13, margin: '6px 0 0', fontStyle: 'italic' }}>
        “{claim.evidence_span}”
      </p>
    ) : null
  }

  const shown = expanded ? evidence : evidence.slice(0, 1)

  return (
    <div style={{ marginTop: 6 }}>
      <button
        type="button"
        onClick={() => setExpanded(e => !e)}
        style={{ fontSize: 12, background: 'none', border: 'none', padding: 0, color: 'var(--accent, inherit)', cursor: 'pointer', textDecoration: 'underline' }}
      >
        {evidence.length} supporting passage{evidence.length === 1 ? '' : 's'} {expanded ? '(hide)' : '(show all)'}
      </button>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
        {shown.map(e => (
          <div key={e.id}>
            {e.evidence_span ? (
              <p className="secondary" style={{ fontSize: 13, margin: 0, fontStyle: 'italic' }}>
                “{e.evidence_span}”
              </p>
            ) : (
              <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                {e.basis ? BASIS_LABEL[e.basis] ?? e.basis : 'no quoted source text'}
                {e.surface_form ? ` — “${e.surface_form}”` : ''}
              </p>
            )}
            {e.document_provenance && e.document_provenance !== 'original' && (
              <p className="muted" style={{ fontSize: 11, margin: 0 }}>
                Source document provenance: {e.document_provenance} — treated as weaker evidence.
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function RequirementCard({
  claim, roleId, busy, onBusyChange, onUpdated, onError,
}: {
  claim: RequirementClaim
  roleId: string
  busy: boolean
  onBusyChange: (busy: boolean) => void
  onUpdated: (oldClaimId: string, updated: RequirementClaim) => void
  onError: (message: string | null) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<EditDraft>(() => draftFromClaim(claim))
  const [saveError, setSaveError] = useState<string | null>(null)

  const startEdit = () => { setDraft(draftFromClaim(claim)); setSaveError(null); setEditing(true) }
  const cancelEdit = () => { setEditing(false); setSaveError(null) }

  const run = async (action: () => Promise<RequirementClaim>) => {
    onBusyChange(true); onError(null)
    try {
      const updated = await action()
      onUpdated(claim.id, updated)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      onBusyChange(false)
    }
  }

  const saveEdit = async () => {
    const original = draftFromClaim(claim)
    const unchanged = draft.concept_id === original.concept_id && draft.requirement_type === original.requirement_type
      && draft.basis === original.basis && draft.importance === original.importance
      && draft.evidence_span === original.evidence_span

    onBusyChange(true); setSaveError(null)
    try {
      const updated = unchanged
        ? (claim.review_status === 'accepted' ? claim : await api.acceptRequirement(roleId, claim.id))
        : await api.editRequirement(roleId, claim.id, {
            concept_id: draft.concept_id,
            requirement_type: draft.requirement_type as RequirementClaimEditInput['requirement_type'],
            basis: draft.basis as RequirementClaimEditInput['basis'],
            importance: draft.importance.trim() === '' ? null : Number(draft.importance),
            // The server would null this out anyway once basis no longer
            // needs it, but a stale quote must not even be sent once the
            // field disappears — what's submitted should match what's shown.
            evidence_span: (draft.basis === 'stated' || draft.basis === 'implied') ? (draft.evidence_span || null) : null,
          })
      onUpdated(claim.id, updated)
      setEditing(false)
    } catch (e) {
      // Never discard the draft on a failed save — the user's edits stay on
      // screen, with the error, so they can fix and retry.
      setSaveError(e instanceof Error ? e.message : String(e))
    } finally {
      onBusyChange(false)
    }
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <strong>{claim.canonical_name}</strong>{' '}
          <span className="muted" style={{ fontSize: 12 }}>
            {claim.type_code} · {claim.requirement_type} · {BASIS_LABEL[claim.basis] ?? claim.basis}
            {claim.importance ? ` · importance ${claim.importance}/5` : ''}
          </span>
          <EvidenceList claim={claim} />
          {claim.document_provenance && claim.document_provenance !== 'original' && (
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 0' }}>
              Source document provenance: {claim.document_provenance} — treated as weaker evidence.
            </p>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'center' }}>
          <span
            className="muted"
            style={{
              fontSize: 12,
              color:
                claim.review_status === 'accepted' ? 'var(--good)' : claim.review_status === 'rejected' ? 'var(--critical)' : undefined,
            }}
          >
            {claim.review_status}
          </span>
          {claim.review_status === 'unreviewed' && !editing && (
            <>
              <button disabled={busy} onClick={() => run(() => api.acceptRequirement(roleId, claim.id))}>Accept</button>
              <button disabled={busy} onClick={startEdit}>Edit &amp; accept</button>
              <button disabled={busy} onClick={() => run(() => api.rejectRequirement(roleId, claim.id))}>Reject</button>
            </>
          )}
          {claim.review_status === 'accepted' && !editing && (
            <>
              <button disabled={busy} onClick={startEdit}>Edit</button>
              <button disabled={busy} onClick={() => run(() => api.rejectRequirement(roleId, claim.id))}>Reject</button>
            </>
          )}
          {claim.review_status === 'rejected' && (
            <button disabled={busy} onClick={() => run(() => api.reopenRequirement(roleId, claim.id))}>Reopen for review</button>
          )}
        </div>
      </div>

      {editing && (
        <div className="form-stack" style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div>
            <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>Concept</div>
            <div>{draft.canonical_name} <span className="muted">({draft.type_code})</span></div>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0' }}>
              <span>Concept type comes from the accepted vocabulary.</span>{' '}
              <Link to={`/vocabulary?focusConceptName=${encodeURIComponent(draft.canonical_name)}`}>
                Open in Vocabulary
              </Link>{' '}
              if this classification looks wrong.
            </p>
            <ConceptPicker
              onSelect={(c) => setDraft(d => ({ ...d, concept_id: c.id, canonical_name: c.canonical_name, type_code: c.type_code }))}
            />
          </div>

          <label>
            Requirement type
            <select value={draft.requirement_type} onChange={e => setDraft(d => ({ ...d, requirement_type: e.target.value }))}>
              {REQUIREMENT_TYPE_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>

          <label>
            Basis
            <select value={draft.basis} onChange={e => setDraft(d => ({ ...d, basis: e.target.value }))}>
              {BASIS_OPTIONS.map(b => <option key={b} value={b}>{BASIS_LABEL[b] ?? b}</option>)}
            </select>
          </label>

          {(draft.basis === 'stated' || draft.basis === 'implied') && (
            <label>
              Evidence span (must be an exact quote from the source document)
              <textarea value={draft.evidence_span} onChange={e => setDraft(d => ({ ...d, evidence_span: e.target.value }))} rows={3} />
            </label>
          )}

          <label>
            Importance (1–5, optional)
            <input type="number" min={1} max={5} value={draft.importance}
              onChange={e => setDraft(d => ({ ...d, importance: e.target.value }))} />
          </label>

          {saveError && <p role="alert" style={{ color: 'var(--critical)' }}>{saveError}</p>}

          <div style={{ display: 'flex', gap: 8 }}>
            <button className="primary" disabled={busy} onClick={saveEdit}>
              {claim.review_status === 'accepted' ? 'Save correction' : 'Save & accept'}
            </button>
            <button disabled={busy} onClick={cancelEdit}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}

function AddRequirementForm({ roleId, busy, onBusyChange, onAdded, onCancel }: {
  roleId: string
  busy: boolean
  onBusyChange: (busy: boolean) => void
  onAdded: (created: RequirementClaim) => void
  onCancel: () => void
}) {
  const [concept, setConcept] = useState<Concept | null>(null)
  const [requirementType, setRequirementType] = useState<string>('required')
  const [importance, setImportance] = useState('')
  const [evidenceSpan, setEvidenceSpan] = useState('')
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (!concept) { setError('Choose an active vocabulary concept first.'); return }
    if (!evidenceSpan.trim()) { setError('Paste the exact supporting text from the source document.'); return }
    onBusyChange(true); setError(null)
    try {
      const created = await api.addRequirement(roleId, {
        concept_id: concept.id,
        requirement_type: requirementType as 'required' | 'preferred' | 'contextual',
        importance: importance.trim() === '' ? null : Number(importance),
        evidence_span: evidenceSpan,
      })
      onAdded(created)
    } catch (e) {
      // Preserve everything entered so far — only the error is new.
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      onBusyChange(false)
    }
  }

  return (
    <div className="card form-stack">
      <h3 style={{ marginTop: 0, fontSize: 14 }}>Add a requirement</h3>
      <p className="muted" style={{ fontSize: 12 }}>
        Only for a requirement genuinely present in the source document — paste the exact supporting text below; it
        is validated against the immutable source before saving. This is not the place to record a requirement you
        believe the employer wants but the posting never actually says.
      </p>
      <div>
        <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>Concept</div>
        {concept ? (
          <div>
            {concept.canonical_name} <span className="muted">({concept.type_code})</span>{' '}
            <button type="button" onClick={() => setConcept(null)}>Change</button>
          </div>
        ) : (
          <ConceptPicker onSelect={setConcept} />
        )}
      </div>
      <label>
        Requirement type
        <select value={requirementType} onChange={e => setRequirementType(e.target.value)}>
          {REQUIREMENT_TYPE_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </label>
      <label>
        Evidence span (exact quote from the source document)
        <textarea value={evidenceSpan} onChange={e => setEvidenceSpan(e.target.value)} rows={3} />
      </label>
      <label>
        Importance (1–5, optional)
        <input type="number" min={1} max={5} value={importance} onChange={e => setImportance(e.target.value)} />
      </label>
      {error && <p role="alert" style={{ color: 'var(--critical)' }}>{error}</p>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="primary" disabled={busy} onClick={save}>Save requirement</button>
        <button disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

export default function RoleRequirements() {
  const { id } = useParams()
  const roleId = id ?? ''
  const [params, setParams] = useSearchParams()
  const detailsStep = params.get('step') === 'details'
  const [busy, setBusy] = useState(false)
  const [retry, setRetry] = useState(0)
  const [claims, setClaims] = useState<RequirementClaim[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [lastRun, setLastRun] = useState<ExtractionSummary | null>(null)
  const [addingRequirement, setAddingRequirement] = useState(false)
  // Unlike accepted/unreviewed/rejected counts, these can't be derived from
  // `claims`. unresolvedProposals: a surface form extraction couldn't
  // resolve to any concept becomes a concept_proposal, never a
  // requirement_claim at all. needsReextraction: a proposal this role
  // contributed to was later accepted/merged in Vocabulary but couldn't be
  // turned into a claim (not enough occurrence data) and still has no
  // current claim for that concept. Both only ever come from the server's
  // review_summary (refreshed on load and after every extraction run, the
  // only actions that can change either).
  const [unresolvedProposals, setUnresolvedProposals] = useState(0)
  const [needsReextraction, setNeedsReextraction] = useState(0)

  const reload = () => api.listRequirements(roleId).then(res => {
    setClaims(res.items)
    setUnresolvedProposals(res.review_summary.unresolved_proposals)
    setNeedsReextraction(res.review_summary.needs_reextraction)
  })

  useEffect(() => {
    let current = true
    setLoading(true); setClaims([]); setError(null); setLastRun(null)
    api.listRequirements(roleId).then(res => {
      if (!current) return
      setClaims(res.items)
      setUnresolvedProposals(res.review_summary.unresolved_proposals)
      setNeedsReextraction(res.review_summary.needs_reextraction)
    })
      .catch(e => { if (current) setError(String(e)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [roleId, retry])

  // The claim-status counts are derived from the same current-claims list
  // the review cards render, rather than tracked separately — they can
  // never drift out of sync with what's on screen, and every action already
  // updates `claims` locally. `complete` also requires zero unresolved
  // vocabulary proposals and zero pending re-extraction need — both are
  // just as excluded from analysis as an unreviewed claim, even though
  // neither ever became a claim to review here.
  const summary = useMemo(() => {
    const accepted = claims.filter(c => c.review_status === 'accepted').length
    const unreviewed = claims.filter(c => c.review_status === 'unreviewed').length
    const rejected = claims.filter(c => c.review_status === 'rejected').length
    return { accepted, unreviewed, rejected, complete: unreviewed === 0 && unresolvedProposals === 0 && needsReextraction === 0 }
  }, [claims, unresolvedProposals, needsReextraction])

  const runExtraction = async () => {
    setExtracting(true)
    setError(null)
    try {
      const run = await api.extractRequirements(roleId)
      setLastRun(run)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setExtracting(false)
    }
  }

  const handleUpdated = (oldClaimId: string, updated: RequirementClaim) => {
    setClaims(rows => rows.map(row => row.id === oldClaimId ? updated : row))
  }

  const handleAdded = (created: RequirementClaim) => {
    setClaims(rows => [...rows, created])
    setAddingRequirement(false)
  }

  return (
    <div>
      <Link to={`/roles/${roleId}`} className="muted" style={{ fontSize: 13 }}>
        ← Back to role
      </Link>
      <ImportSteps step={detailsStep ? 1 : 2} />
      <h1 style={{ fontSize: 22, marginTop: 12 }}>{detailsStep ? 'Review role details' : 'Review requirements'}</h1>
      <div hidden={!detailsStep}><MetadataEnrichmentPanel key={roleId} roleId={roleId} /></div>
      {detailsStep ? <button className="primary" onClick={() => setParams({})}>Continue to requirements</button> :
        <button onClick={() => setParams({ step: 'details' })}>Back to role details</button>}
      <div hidden={detailsStep}>

      <p className="secondary">
        <strong>AI suggestions are not used as authoritative role requirements until you accept them.</strong> Extract
        requirements from the advert, then accept, correct, or reject each suggestion. Quotes show what the source
        says; inferred requirements are labelled separately.
      </p>

      <div style={{ marginBottom: 16 }}>
        <button className="primary" onClick={runExtraction} disabled={extracting || busy}>
          {extracting ? 'Extracting…' : 'Extract requirements with AI'}
        </button>
        {lastRun && (
          <span className="muted" style={{ fontSize: 12, marginLeft: 10 }}>
            {lastRun.status === 'failed'
              ? `Run failed: ${lastRun.error} (recorded as extraction_run #${lastRun.extraction_run_id})`
              : `Run #${lastRun.extraction_run_id}: ${lastRun.claims_created ?? 0} claim(s)` +
                `${lastRun.claims_superseded ? `, ${lastRun.claims_superseded} superseding an earlier unreviewed proposal` : ''}` +
                `${lastRun.claims_deduplicated ? `, ${lastRun.claims_deduplicated} identical proposal(s) skipped` : ''}` +
                `${lastRun.evidence_created ? `, ${lastRun.evidence_created} supporting passage(s) attached` : ''}` +
                `${lastRun.evidence_deduplicated ? ` (${lastRun.evidence_deduplicated} already attached)` : ''}` +
                `, ${lastRun.proposals_created ?? 0} new vocabulary proposal(s)` +
                `${lastRun.rejected_span_count ? `, ${lastRun.rejected_span_count} rejected for an invalid span` : ''}.`}
          </span>
        )}
      </div>

      {error && <p role="alert" style={{ color: 'var(--critical)' }}>{error}</p>}
      {error && <button onClick={() => setRetry(retry + 1)}>Reload requirements</button>}
      {loading && <p className="muted">Loading…</p>}

      {!loading && (claims.length > 0 || addingRequirement) && (
        <div className="card" style={{ marginBottom: 16, display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
          <span><strong style={{ color: 'var(--good)' }}>{summary.accepted}</strong> <span className="muted">accepted</span></span>
          <span><strong style={{ color: summary.unreviewed ? 'var(--warning)' : undefined }}>{summary.unreviewed}</strong> <span className="muted">need review</span></span>
          <span><strong style={{ color: 'var(--critical)' }}>{summary.rejected}</strong> <span className="muted">rejected</span></span>
        </div>
      )}

      {!loading && claims.length === 0 && !addingRequirement && (
        <p className="muted">No requirement claims yet — run extraction above, add one manually below, or check the Vocabulary page for unresolved proposals it may have created.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {claims.map((c) => (
          <RequirementCard key={c.id} claim={c} roleId={roleId} busy={busy || extracting}
            onBusyChange={setBusy} onUpdated={handleUpdated} onError={setError} />
        ))}
      </div>

      <div style={{ marginTop: 16 }}>
        {!addingRequirement ? (
          <button onClick={() => setAddingRequirement(true)} disabled={busy || extracting}>Add requirement</button>
        ) : (
          <AddRequirementForm roleId={roleId} busy={busy || extracting} onBusyChange={setBusy}
            onAdded={handleAdded} onCancel={() => setAddingRequirement(false)} />
        )}
      </div>

      <p style={{ marginTop: 16 }}>{summary.unreviewed} requirement(s) still need review.</p>
      {unresolvedProposals > 0 && (
        <p className="secondary">
          {unresolvedProposals} extracted term{unresolvedProposals === 1 ? '' : 's'} could not be matched to the vocabulary and{' '}
          {unresolvedProposals === 1 ? 'is' : 'are'} excluded here too. <Link to="/vocabulary">Review in Vocabulary</Link>.
        </p>
      )}
      {needsReextraction > 0 && (
        <p className="secondary">
          {needsReextraction} extracted term{needsReextraction === 1 ? '' : 's'} {needsReextraction === 1 ? 'was' : 'were'} since added to
          the vocabulary but {needsReextraction === 1 ? 'has' : 'have'} no requirement claim yet for this role — re-run extraction above to
          pick {needsReextraction === 1 ? 'it' : 'them'} up.
        </p>
      )}
      {!summary.complete && (
        <p role="alert" style={{ color: 'var(--warning)' }}>
          Requirement review is incomplete — {summary.unreviewed + unresolvedProposals + needsReextraction} pending item{summary.unreviewed + unresolvedProposals + needsReextraction === 1 ? '' : 's'} excluded
          from comparison and analysis until reviewed.
        </p>
      )}
      <Link to={`/comparison/${roleId}`}>
        Continue to comparison{!summary.complete ? ' (requirement review incomplete)' : ''}
      </Link>
      </div>
    </div>
  )
}
