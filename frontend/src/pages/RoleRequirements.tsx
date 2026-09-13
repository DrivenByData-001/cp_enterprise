import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, type ExtractionSummary, type RequirementClaim, type RoleMetadataInput } from '../lib/api'
import ImportSteps from '../components/ImportSteps'
import RoleMetadataForm from '../components/RoleMetadataForm'

const BASIS_LABEL: Record<string, string> = {
  stated: 'stated',
  implied: 'implied',
  inferred: 'inferred (no verbatim span — provenance too weak to trust one)',
  user_asserted: 'user-asserted',
}

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

export default function RoleRequirements() {
  const { id } = useParams()
  const roleId = id ?? ''
  const [params, setParams] = useSearchParams()
  const detailsStep = params.get('step') === 'details'
  const [busyClaim, setBusyClaim] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [claims, setClaims] = useState<RequirementClaim[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [lastRun, setLastRun] = useState<ExtractionSummary | null>(null)

  const reload = () => api.listRequirements(roleId).then(setClaims)

  useEffect(() => {
    let current = true
    setLoading(true); setClaims([]); setError(null); setLastRun(null)
    api.listRequirements(roleId).then(rows => { if (current) setClaims(rows) })
      .catch(e => { if (current) setError(String(e)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [roleId, retry])

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

  const review = async (claimId: string, action: 'accept' | 'reject') => {
    setBusyClaim(claimId); setError(null)
    try {
      await api.reviewRequirement(roleId, claimId, action)
      setClaims(rows => rows.map(row => row.id === claimId ? { ...row, review_status: action === 'accept' ? 'accepted' : 'rejected' } : row))
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusyClaim(null) }
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
        Extract requirements from the advert, then accept or reject each suggestion. Quotes show what the source
        says; inferred requirements are labelled separately. Review suggestions before relying on the comparison.
      </p>

      <div style={{ marginBottom: 16 }}>
        <button className="primary" onClick={runExtraction} disabled={extracting || busyClaim !== null}>
          {extracting ? 'Extracting…' : 'Extract requirements with AI'}
        </button>
        {lastRun && (
          <span className="muted" style={{ fontSize: 12, marginLeft: 10 }}>
            {lastRun.status === 'failed'
              ? `Run failed: ${lastRun.error} (recorded as extraction_run #${lastRun.extraction_run_id})`
              : `Run #${lastRun.extraction_run_id}: ${lastRun.claims_created ?? 0} claim(s), ${lastRun.proposals_created ?? 0} new proposal(s)${lastRun.rejected_span_count ? `, ${lastRun.rejected_span_count} rejected for an invalid span` : ''}.`}
          </span>
        )}
      </div>

      {error && <p role="alert" style={{ color: 'var(--critical)' }}>{error}</p>}
      {error && <button onClick={() => setRetry(retry + 1)}>Reload requirements</button>}
      {loading && <p className="muted">Loading…</p>}

      {!loading && claims.length === 0 && (
        <p className="muted">No requirement claims yet — run extraction above, or check the Vocabulary page for unresolved proposals it may have created.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {claims.map((c) => (
          <div key={c.id} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <div>
                <strong>{c.canonical_name}</strong>{' '}
                <span className="muted" style={{ fontSize: 12 }}>
                  {c.type_code} · {c.requirement_type} · {BASIS_LABEL[c.basis] ?? c.basis}
                  {c.importance ? ` · importance ${c.importance}/5` : ''}
                </span>
                {c.evidence_span && (
                  <p className="secondary" style={{ fontSize: 13, margin: '6px 0 0', fontStyle: 'italic' }}>
                    “{c.evidence_span}”
                  </p>
                )}
                {c.document_provenance && c.document_provenance !== 'original_capture' && (
                  <p className="muted" style={{ fontSize: 12, margin: '4px 0 0' }}>
                    Source document provenance: {c.document_provenance} — treated as weaker evidence.
                  </p>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'center' }}>
                <span
                  className="muted"
                  style={{
                    fontSize: 12,
                    color:
                      c.review_status === 'accepted' ? 'var(--good)' : c.review_status === 'rejected' ? 'var(--critical)' : undefined,
                  }}
                >
                  {c.review_status}
                </span>
                {c.review_status === 'unreviewed' && (
                  <>
                    <button disabled={busyClaim !== null || extracting} onClick={() => review(c.id, 'accept')}>Accept</button>
                    <button disabled={busyClaim !== null || extracting} onClick={() => review(c.id, 'reject')}>Reject</button>
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
      <p>{claims.filter(c => c.review_status === 'unreviewed').length} requirement(s) still need review.</p>
      <Link to={`/comparison/${roleId}`}>Continue to comparison</Link>
      </div>
    </div>
  )
}
