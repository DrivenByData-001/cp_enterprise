import { Link } from 'react-router-dom'
import type { ArchetypeEvidence, EvidenceDepth, EvidenceDepthState, MarketEvidenceContext } from '../../lib/api'

// Phase 8 (docs/39 build §25): the one reusable "what backs this archetype"
// panel, used identically by Market Coverage's drill-down, Career Direction
// detail, Opportunity alignment's market_evidence_context, and Pathways'
// archetype nodes — every one of those reads the same
// market_coverage.archetype_coverage_detail-shaped data, so this component
// never re-derives what "evidence depth" or "coverage" means. No AI, no
// score — a named state plus the facts it came from.

const EVIDENCE_DEPTH_LABEL: Record<EvidenceDepthState, { text: string; color: string }> = {
  insufficient: { text: 'Insufficient evidence', color: 'var(--muted, #888)' },
  thin: { text: 'Thin evidence', color: 'var(--warning)' },
  supported: { text: 'Supported', color: 'var(--good)' },
  broader_support: { text: 'Broader support', color: 'var(--series-1)' },
}

export function EvidenceDepthBadge({ depth }: { depth: EvidenceDepth }) {
  const style = EVIDENCE_DEPTH_LABEL[depth.state]
  return (
    <span
      title={depth.reason}
      style={{
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.4,
        padding: '2px 7px',
        borderRadius: 4,
        color: style.color,
        border: `1px solid ${style.color}`,
        whiteSpace: 'nowrap',
      }}
    >
      {style.text}
    </span>
  )
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 11 }}>{label}</div>
      <div style={{ fontSize: 13 }}>{value}</div>
    </div>
  )
}

/** The compact form — a single line plus a depth badge, for embedding inside
 * another card (Opportunity alignment, a Pathways route node). */
export function ArchetypeEvidenceInline({ evidence }: { evidence: MarketEvidenceContext }) {
  if (!evidence.available) {
    return <p className="muted" style={{ fontSize: 12, margin: 0 }}>{evidence.message}</p>
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12 }}>
      <span className="secondary">{evidence.canonical_name}</span>
      <span className="muted">
        {evidence.assigned_posting_count} supporting posting{evidence.assigned_posting_count === 1 ? '' : 's'}
        {evidence.distinct_country_count > 0 ? ` across ${evidence.distinct_country_count} countr${evidence.distinct_country_count === 1 ? 'y' : 'ies'}` : ''}
      </span>
      <EvidenceDepthBadge depth={evidence.evidence_depth} />
      {evidence.status !== 'active' && <span className="muted">({evidence.status})</span>}
    </div>
  )
}

/** The full card form — Market Coverage's archetype drill-down and Career
 * Direction detail's archetype section. */
export function ArchetypeEvidenceCard({ evidence, linkToCoverage = true }: { evidence: ArchetypeEvidence; linkToCoverage?: boolean }) {
  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
        <strong style={{ fontSize: 14 }}>
          {evidence.canonical_name}
          {evidence.status !== 'active' && <span className="muted" style={{ fontWeight: 400 }}> ({evidence.status})</span>}
        </strong>
        <EvidenceDepthBadge depth={evidence.evidence_depth} />
      </div>
      {(evidence.seniority_band || evidence.typical_market) && (
        <p className="muted" style={{ fontSize: 12, margin: '4px 0 0' }}>
          {[evidence.seniority_band, evidence.typical_market].filter(Boolean).join(' · ')}
        </p>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginTop: 10 }}>
        <Fact label="Supporting postings" value={evidence.assigned_posting_count} />
        <Fact label="With reviewed requirements" value={evidence.reviewed_requirement_posting_count} />
        <Fact
          label="Posting dates"
          value={`${evidence.known_posting_date_count} known, ${evidence.unknown_posting_date_count} unknown`}
        />
        <Fact label="Latest known posting" value={evidence.latest_known_posting_date ?? '—'} />
        <Fact label="Countries represented" value={evidence.distinct_country_count > 0 ? evidence.countries.join(', ') : '—'} />
        <Fact label="Demand derivation" value={evidence.demand_derivation_available ? 'Available' : 'Not available'} />
        <Fact label="Compensation benchmark" value={evidence.compensation_benchmark_available ? 'Available' : 'Not available'} />
      </div>
      {!evidence.economics_freshness.fresh && evidence.economics_freshness.reason && (
        <p className="muted" style={{ fontSize: 12, margin: '10px 0 0' }}>{evidence.economics_freshness.reason}</p>
      )}
      <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>{evidence.evidence_depth.reason}</p>
      {linkToCoverage && (
        <p style={{ margin: '8px 0 0' }}>
          <Link to={`/market/coverage?archetype_id=${evidence.archetype_concept_id}`} style={{ fontSize: 12 }}>
            Open in Market Coverage
          </Link>
        </p>
      )}
    </div>
  )
}
