import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  api,
  type ArchetypeEvidence,
  type CoverageDistributionItem,
  type CoverageFraction,
  type MarketCoverageFilters,
  type MarketCoverageSummary,
  type RequirementReviewState,
} from '../lib/api'
import { ArchetypeEvidenceCard, EvidenceDepthBadge } from '../components/market/ArchetypeEvidencePanel'

// Phase 8 (docs/39): Market Coverage & Confidence. Renders exactly what the
// shared coverage service returns — counts, numerator/denominator
// proportions and plain-English meanings — never a synthesised score,
// gauge or red/amber/green verdict (build §19).

const REQUIREMENT_STATE_LABEL: Record<RequirementReviewState, string> = {
  needs_reextraction: 'Needs re-extraction',
  unresolved_vocabulary: 'Unresolved vocabulary',
  review_pending: 'Review pending',
  reviewed: 'Reviewed',
  legacy_only: 'Legacy-only',
  not_extracted: 'Not extracted',
}

const REQUIREMENT_STATE_COLOR: Record<RequirementReviewState, string> = {
  needs_reextraction: 'var(--critical)',
  unresolved_vocabulary: 'var(--warning)',
  review_pending: 'var(--warning)',
  reviewed: 'var(--good)',
  legacy_only: 'var(--series-1)',
  not_extracted: 'var(--muted, #888)',
}

function FractionBar({ label, fraction }: { label: string; fraction: CoverageFraction }) {
  const pct = fraction.proportion !== null ? Math.round(fraction.proportion * 100) : null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }} title={fraction.meaning}>
      <span style={{ width: 170, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      <div style={{ flex: 1, background: 'var(--border)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
        <div style={{ width: `${pct ?? 0}%`, background: 'var(--series-1)', height: '100%' }} />
      </div>
      <span className="muted" style={{ width: 110, flexShrink: 0, textAlign: 'right' }}>
        {fraction.count} of {fraction.total}
        {pct !== null ? ` (${pct}%)` : ''}
      </span>
    </div>
  )
}

// A bar whose *visual width* scales against the largest bucket in its group
// (so a small bucket isn't an invisible sliver next to a dominant one) while
// the *displayed* count/denominator/percentage is a real, meaningful
// statistic — never the chart-scaling value. The two are deliberately
// different numbers and must never be conflated into one `proportion`
// (unlike FractionBar, whose `fraction.total` is always the real
// denominator both for the bar width and the text).
function ScaledBar({ label, count, statTotal, barScaleTotal, meaning }: {
  label: string
  count: number
  statTotal: number
  barScaleTotal: number
  meaning: string
}) {
  const pct = statTotal > 0 ? Math.round((count / statTotal) * 100) : null
  const barPct = barScaleTotal > 0 ? Math.round((count / barScaleTotal) * 100) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }} title={meaning}>
      <span style={{ width: 170, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      <div style={{ flex: 1, background: 'var(--border)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
        <div style={{ width: `${barPct}%`, background: 'var(--series-1)', height: '100%' }} />
      </div>
      <span className="muted" style={{ width: 110, flexShrink: 0, textAlign: 'right' }}>
        {count} of {statTotal}
        {pct !== null ? ` (${pct}%)` : ''}
      </span>
    </div>
  )
}

function DistributionBars({ items, total }: { items: CoverageDistributionItem[]; total: number }) {
  if (items.length === 0) return <p className="muted" style={{ fontSize: 13 }}>No values recorded in this scope.</p>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {items.map((item) => (
        <FractionBar
          key={String(item.value)}
          label={item.label}
          fraction={{ count: item.count, total, proportion: total ? item.count / total : null, meaning: `${item.count} of ${total}` }}
        />
      ))}
    </div>
  )
}

function SectionCard({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <h2 style={{ fontSize: 16, margin: 0 }}>{title}</h2>
      {subtitle && <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>{subtitle}</p>}
      <div style={{ marginTop: 12 }}>{children}</div>
    </section>
  )
}

// --- Filters -----------------------------------------------------------------

function FilterBar({
  filters,
  setFilters,
  archetypeOptions,
}: {
  filters: MarketCoverageFilters
  setFilters: (f: MarketCoverageFilters) => void
  archetypeOptions: { archetype_concept_id: string; canonical_name: string }[]
}) {
  const active = Object.values(filters).some((v) => v !== undefined && v !== '')
  return (
    <div className="card" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', padding: '8px 12px' }}>
      <span className="secondary" style={{ fontSize: 13 }}>Filters:</span>
      <input
        type="number"
        placeholder="From year"
        style={{ width: 100 }}
        value={filters.year_from ?? ''}
        onChange={(e) => setFilters({ ...filters, year_from: e.target.value ? Number(e.target.value) : undefined })}
      />
      <input
        type="number"
        placeholder="To year"
        style={{ width: 100 }}
        value={filters.year_to ?? ''}
        onChange={(e) => setFilters({ ...filters, year_to: e.target.value ? Number(e.target.value) : undefined })}
      />
      <input
        placeholder="Country"
        style={{ width: 140 }}
        value={filters.country ?? ''}
        onChange={(e) => setFilters({ ...filters, country: e.target.value || undefined })}
      />
      <select
        value={filters.seniority_level ?? ''}
        onChange={(e) => setFilters({ ...filters, seniority_level: e.target.value || undefined })}
      >
        <option value="">Any seniority</option>
        <option value="junior">Junior</option>
        <option value="mid">Mid</option>
        <option value="senior">Senior</option>
        <option value="lead">Lead</option>
        <option value="head">Head</option>
        <option value="director">Director</option>
      </select>
      <select
        value={filters.archetype_id ?? ''}
        onChange={(e) => setFilters({ ...filters, archetype_id: e.target.value || undefined })}
      >
        <option value="">Any archetype</option>
        {archetypeOptions.map((a) => (
          <option key={a.archetype_concept_id} value={a.archetype_concept_id}>{a.canonical_name}</option>
        ))}
      </select>
      {active && <button onClick={() => setFilters({})}>Clear</button>}
    </div>
  )
}

// --- Sections ------------------------------------------------------------------

function CorpusInScopeSection({ summary }: { summary: MarketCoverageSummary }) {
  const { roles, scope } = summary
  return (
    <SectionCard title="Corpus in scope" subtitle="What your captured corpus covers in this filtered scope.">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
        <strong style={{ fontSize: 20 }}>{roles.total}</strong>
        <span className="muted" style={{ fontSize: 12 }}>captured posting{roles.total === 1 ? '' : 's'} in scope</span>
      </div>
      {scope.dated_scope_active && roles.excluded_undated_count > 0 && (
        <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
          {roles.excluded_undated_count} posting(s) matching every other filter were excluded from this dated scope
          because their posting date is unknown — never silently dropped.
        </p>
      )}
      {roles.total === 0 ? (
        <p className="muted">No postings match these filters.</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 24px' }}>
          <FractionBar label="Has source document" fraction={roles.with_source_document} />
          <FractionBar label="Known posting date" fraction={roles.known_posting_date} />
          <FractionBar label="Known country" fraction={roles.known_country} />
          <FractionBar label="Known seniority" fraction={roles.known_seniority} />
          <FractionBar label="Known employment type" fraction={roles.known_employment_type} />
          <FractionBar label="Known remote type" fraction={roles.known_remote_type} />
        </div>
      )}
    </SectionCard>
  )
}

function TimeCoverageSection({ summary }: { summary: MarketCoverageSummary }) {
  const { time, roles } = summary
  // The bar's visual width scales against the largest year (pure chart
  // legibility) — the displayed count/denominator/percentage is a
  // completely separate number: each year's share of every captured
  // posting in this scope (consistent with Trends' own "by year" chart,
  // which uses the same scope-wide denominator). Never conflate the two —
  // "10 of 20" must always mean 10 of 20 real postings, never 10 of "the
  // tallest bar".
  const maxYearCount = Math.max(1, ...time.by_year.map((y) => y.count))
  return (
    <SectionCard title="Time coverage" subtitle="Capture recency is not posting recency — dates below are posting_date only.">
      <p className="secondary" style={{ fontSize: 13, marginTop: 0 }}>
        {time.earliest_known_posting_date && time.latest_known_posting_date
          ? `${time.earliest_known_posting_date} – ${time.latest_known_posting_date}`
          : 'No dated postings in this scope.'}
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 10 }}>
        {time.by_year.map((y) => (
          <ScaledBar
            key={y.value}
            label={String(y.value)}
            count={y.count}
            statTotal={roles.total}
            barScaleTotal={maxYearCount}
            meaning={`${y.count} of ${roles.total} captured postings in this scope are from ${y.value}`}
          />
        ))}
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12 }} className="secondary">
        <span>Current year: {time.current_calendar_year_count}</span>
        <span>Previous year: {time.previous_calendar_year_count}</span>
        <span>Older: {time.older_count}</span>
        <span>Undated: {roles.unknown_posting_date.count}</span>
      </div>
      {time.freshly_captured_but_posting_date_unknown_count > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          {time.freshly_captured_but_posting_date_unknown_count} posting(s) were captured this year but have no
          known posting date — a recent capture is not the same as a current market observation.
        </p>
      )}
    </SectionCard>
  )
}

function GeographySection({ summary }: { summary: MarketCoverageSummary }) {
  const { geography, roles } = summary
  return (
    <SectionCard title="Geography" subtitle="Countries as captured — never geocoded or fuzzy-normalised.">
      <FractionBar label="Known country" fraction={roles.known_country} />
      <p className="muted" style={{ fontSize: 12, margin: '8px 0' }}>
        {geography.distinct_known_country_count} distinct known countr{geography.distinct_known_country_count === 1 ? 'y' : 'ies'}
      </p>
      <DistributionBars items={geography.country_distribution} total={roles.total} />
    </SectionCard>
  )
}

function SourceProvenanceSection({ summary }: { summary: MarketCoverageSummary }) {
  const { sources, roles } = summary
  return (
    <SectionCard title="Source / provenance" subtitle="document.source is called capture source — never presented as an independent market publisher without evidence.">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 24px', marginBottom: 10 }}>
        <FractionBar label="Has source document" fraction={sources.with_source_document} />
        <FractionBar label="Has captured URL" fraction={sources.url_present} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 4 }}>Capture source</div>
          <DistributionBars items={sources.capture_source_distribution} total={roles.total} />
        </div>
        <div>
          <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 4 }}>Provenance quality</div>
          <DistributionBars items={sources.provenance_quality_distribution} total={roles.total} />
        </div>
      </div>
    </SectionCard>
  )
}

function RequirementReadinessSection({ summary }: { summary: MarketCoverageSummary }) {
  const { requirements } = summary
  return (
    <SectionCard title="Requirement readiness" subtitle="Canonical review-summary semantics — the same state definitions used across this app.">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {requirements.state_precedence.map((state) => (
          <span
            key={state}
            style={{
              fontSize: 12, padding: '4px 10px', borderRadius: 999,
              border: `1px solid ${REQUIREMENT_STATE_COLOR[state]}`, color: REQUIREMENT_STATE_COLOR[state],
            }}
          >
            {REQUIREMENT_STATE_LABEL[state]}: {requirements.state_distribution[state]}
          </span>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 24px' }}>
        <FractionBar label="Review complete" fraction={requirements.review_complete} />
        <FractionBar label="Accepted requirements present" fraction={requirements.accepted_present} />
        <FractionBar label="Unreviewed present" fraction={requirements.unreviewed_present} />
        <FractionBar label="Unresolved vocabulary" fraction={requirements.unresolved_vocabulary_present} />
        <FractionBar label="Needs re-extraction" fraction={requirements.needs_reextraction_present} />
        <FractionBar label="Legacy-only" fraction={requirements.legacy_only} />
        <FractionBar label="No usable evidence" fraction={requirements.no_usable_evidence} />
        <FractionBar label="Extraction never attempted" fraction={requirements.extraction_never_attempted} />
      </div>
    </SectionCard>
  )
}

function ArchetypeCoverageSection({
  summary,
  filters,
  onSelectArchetype,
}: {
  summary: MarketCoverageSummary
  filters: MarketCoverageFilters
  onSelectArchetype: (evidence: ArchetypeEvidence) => void
}) {
  const { archetypes } = summary
  return (
    <SectionCard title="Archetype coverage" subtitle="No archetype is ever auto-assigned from this page.">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 24px', marginBottom: 12 }}>
        <FractionBar label="Assigned to active archetype" fraction={archetypes.assigned_active} />
        <FractionBar label="Unassigned" fraction={archetypes.unassigned} />
        <FractionBar label="Active archetypes with support" fraction={archetypes.active_archetypes_with_support} />
      </div>
      {archetypes.support.length === 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>No archetype has a supporting posting in this scope.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr className="muted" style={{ textAlign: 'left' }}>
                <th style={{ padding: '4px 8px' }}>Archetype</th>
                <th style={{ padding: '4px 8px' }}>Postings</th>
                <th style={{ padding: '4px 8px' }}>Reviewed reqs</th>
                <th style={{ padding: '4px 8px' }}>Countries</th>
                <th style={{ padding: '4px 8px' }}>Comp benchmark</th>
                <th style={{ padding: '4px 8px' }}>Evidence depth</th>
              </tr>
            </thead>
            <tbody>
              {archetypes.support.map((a) => (
                <tr
                  key={a.archetype_concept_id}
                  style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }}
                  onClick={() => onSelectArchetype(a)}
                >
                  <td style={{ padding: '4px 8px' }}>{a.canonical_name}</td>
                  <td style={{ padding: '4px 8px' }}>{a.assigned_posting_count}</td>
                  <td style={{ padding: '4px 8px' }}>{a.reviewed_requirement_posting_count}</td>
                  <td style={{ padding: '4px 8px' }}>{a.distinct_country_count}</td>
                  <td style={{ padding: '4px 8px' }}>{a.compensation_benchmark_available ? 'Yes' : 'No'}</td>
                  <td style={{ padding: '4px 8px' }}><EvidenceDepthBadge depth={a.evidence_depth} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {archetypes.unsupported_active_archetypes.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer', fontSize: 12 }}>
            {archetypes.unsupported_active_archetypes.length} active archetype(s) with zero supporting postings in this scope
          </summary>
          <ul className="muted" style={{ fontSize: 12, margin: '6px 0 0', paddingLeft: 18 }}>
            {archetypes.unsupported_active_archetypes.map((a) => (
              <li key={a.archetype_concept_id}>{a.canonical_name}</li>
            ))}
          </ul>
        </details>
      )}
      {filters.archetype_id && (
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Filtered to one archetype — the table above reflects only it.
        </p>
      )}
    </SectionCard>
  )
}

function CompensationEvidenceSection({ summary }: { summary: MarketCoverageSummary }) {
  const { compensation, derived_economics } = summary
  const c = compensation.coverage
  return (
    <SectionCard title="Compensation evidence" subtitle={compensation.scope_note}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 12 }}>
        <Stat label="Accepted observations" value={c.accepted_observation_count} />
        <Stat label="Source documents" value={c.distinct_source_document_count} />
        <Stat label="Providers" value={c.distinct_provider_count} />
        <Stat label="Period" value={c.earliest_period && c.latest_period ? `${c.earliest_period} – ${c.latest_period}` : '—'} />
        <Stat label="With sample n" value={c.with_sample_size_count} />
        <Stat label="Archetype-linked" value={c.archetype_linked_count} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 24px' }}>
        <FractionBar label="Role has accepted comp" fraction={compensation.role_linked_coverage.with_accepted_role_linked_compensation} />
        <FractionBar label="Role has no comp evidence" fraction={compensation.role_linked_coverage.without_accepted_role_linked_compensation} />
      </div>
      {!derived_economics.fresh && (
        <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>{derived_economics.reason}</p>
      )}
    </SectionCard>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 11 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{value}</div>
    </div>
  )
}

function LimitationsSection({ summary }: { summary: MarketCoverageSummary }) {
  return (
    <SectionCard title="What this does not tell you">
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        {summary.limitations.map((note, i) => (
          <li key={i} style={{ marginBottom: 6 }}>{note}</li>
        ))}
      </ul>
    </SectionCard>
  )
}

// --- Page ------------------------------------------------------------------------

export default function MarketCoverage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const filters: MarketCoverageFilters = {
    year_from: searchParams.get('year_from') ? Number(searchParams.get('year_from')) : undefined,
    year_to: searchParams.get('year_to') ? Number(searchParams.get('year_to')) : undefined,
    country: searchParams.get('country') || undefined,
    seniority_level: searchParams.get('seniority_level') || undefined,
    archetype_id: searchParams.get('archetype_id') || undefined,
  }
  const setFilters = (f: MarketCoverageFilters) => {
    const params: Record<string, string> = {}
    for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== '') params[k] = String(v)
    setSearchParams(params)
  }

  const [summary, setSummary] = useState<MarketCoverageSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<ArchetypeEvidence | null>(null)

  useEffect(() => {
    setSummary(null)
    setSelected(null)
    api.getMarketCoverageSummary(filters).then(setSummary).catch((e) => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters)])

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Market coverage</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 760 }}>
        What your captured corpus covers — and what it cannot establish about the wider market.
      </p>

      <FilterBar filters={filters} setFilters={setFilters} archetypeOptions={summary?.archetypes.filter_options ?? []} />

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {!summary && !error && <p className="muted" style={{ marginTop: 16 }}>Loading…</p>}

      {summary && (
        <>
          <CorpusInScopeSection summary={summary} />
          <TimeCoverageSection summary={summary} />
          <GeographySection summary={summary} />
          <SourceProvenanceSection summary={summary} />
          <RequirementReadinessSection summary={summary} />
          <ArchetypeCoverageSection summary={summary} filters={filters} onSelectArchetype={setSelected} />
          {selected && (
            <div style={{ marginTop: 12 }}>
              <ArchetypeEvidenceCard evidence={selected} linkToCoverage={false} />
            </div>
          )}
          <CompensationEvidenceSection summary={summary} />
          <LimitationsSection summary={summary} />
        </>
      )}
    </div>
  )
}
