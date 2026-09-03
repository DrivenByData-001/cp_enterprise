import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type ExtractionSummary, type RequirementClaim } from '../lib/api'

const BASIS_LABEL: Record<string, string> = {
  stated: 'stated',
  implied: 'implied',
  inferred: 'inferred (no verbatim span — provenance too weak to trust one)',
  user_asserted: 'user-asserted',
}

export default function RoleRequirements() {
  const { id } = useParams()
  const roleId = Number(id)
  const [claims, setClaims] = useState<RequirementClaim[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [lastRun, setLastRun] = useState<ExtractionSummary | null>(null)

  const reload = () => api.listRequirements(roleId).then(setClaims)

  useEffect(() => {
    setLoading(true)
    reload()
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleId])

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

  const review = async (claimId: number, action: 'accept' | 'reject') => {
    await api.reviewRequirement(roleId, claimId, action)
    await reload()
  }

  return (
    <div>
      <Link to={`/roles/${roleId}`} className="muted" style={{ fontSize: 13 }}>
        ← Back to role
      </Link>
      <h1 style={{ fontSize: 22, marginTop: 12 }}>Requirement claims</h1>
      <p className="secondary">
        Closed-vocabulary extraction against the canonical concept list (docs/11 §7.3). Every claim below carries its
        basis and, where trusted, a verbatim span from the source document — click through to see it. Nothing here
        was auto-accepted; review each before treating it as confirmed.
      </p>

      <div style={{ marginBottom: 16 }}>
        <button className="primary" onClick={runExtraction} disabled={extracting}>
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

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
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
                    <button onClick={() => review(c.id, 'accept')}>Accept</button>
                    <button onClick={() => review(c.id, 'reject')}>Reject</button>
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
