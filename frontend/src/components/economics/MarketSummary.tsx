import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type MarketAnalyticsCoverage,
  type MarketAnalyticsFacetValue,
  type MarketAnalyticsFiltersInput,
  type MarketAnalyticsPoint,
  type MarketAnalyticsSummary as MarketAnalyticsSummaryData,
  type MarketComponentComparison,
  type MarketEvidenceRow,
  type MarketPracticeComparison,
  type MarketRoleRange,
  type MarketTrendSeries,
} from '../../lib/api'
import { formatMoney } from '../../lib/money'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-other)', '#9b6bd6', '#c94f7c']

function colorFor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length]
}

// Recharts' Tooltip formatter can hand back a string/array for a non-numeric
// series — only ever money-format an actual number.
function moneyTooltipValue(value: unknown, currency: string): string {
  return typeof value === 'number' ? formatMoney(value, currency) : String(value ?? '')
}

// A chart axis can only honestly represent one currency and one pay period
// (docs/25 §8.4/§8.5) — when the currently filtered evidence spans more
// than one, the section falls back to its table view instead of drawing a
// mixed-unit chart.
function singleValue<T, K extends keyof T>(items: T[], key: K): T[K] | null {
  if (items.length === 0) return null
  const first = items[0][key]
  return items.every((item) => item[key] === first) ? first : null
}

function EvidenceQualityNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="muted" style={{ fontSize: 12, margin: '6px 0' }}>
      {children}
    </p>
  )
}

// --- Filter bar --------------------------------------------------------------

type Filters = MarketAnalyticsFiltersInput & { year?: string }

function FacetSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string | undefined
  options: MarketAnalyticsFacetValue[]
  onChange: (value: string | undefined) => void
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11 }}>
      <span className="muted">{label}</span>
      <select value={value ?? ''} onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">All</option>
        {options.map((opt) => (
          <option key={String(opt.value)} value={String(opt.value)}>
            {opt.label} ({opt.count})
          </option>
        ))}
      </select>
    </label>
  )
}

function FilterBar({
  summary,
  filters,
  setFilters,
}: {
  summary: MarketAnalyticsSummaryData
  filters: Filters
  setFilters: (f: Filters) => void
}) {
  const active = Object.values(filters).some((v) => v !== undefined && v !== '')
  return (
    <div className="card" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', padding: '10px 12px' }}>
      <FacetSelect label="Market" value={filters.market_id} options={summary.facets.markets} onChange={(v) => setFilters({ ...filters, market_id: v })} />
      <FacetSelect label="Currency" value={filters.currency} options={summary.facets.currencies} onChange={(v) => setFilters({ ...filters, currency: v })} />
      <FacetSelect label="Component" value={filters.component} options={summary.facets.components} onChange={(v) => setFilters({ ...filters, component: v })} />
      <FacetSelect label="Practice" value={filters.practice_group} options={summary.facets.practice_groups} onChange={(v) => setFilters({ ...filters, practice_group: v })} />
      <FacetSelect label="Provider" value={filters.provider} options={summary.facets.providers} onChange={(v) => setFilters({ ...filters, provider: v })} />
      <FacetSelect label="Year" value={filters.year} options={summary.facets.years} onChange={(v) => setFilters({ ...filters, year: v })} />
      {active && (
        <button type="button" className="secondary" onClick={() => setFilters({})}>
          Clear filters
        </button>
      )}
    </div>
  )
}

// --- Headline coverage --------------------------------------------------------

function CoverageCards({ coverage }: { coverage: MarketAnalyticsCoverage }) {
  const range = coverage.earliest_period && coverage.latest_period
    ? coverage.earliest_period === coverage.latest_period
      ? coverage.earliest_period
      : `${coverage.earliest_period} – ${coverage.latest_period}`
    : '—'
  const stat = (label: string, value: React.ReactNode) => (
    <div>
      <div className="muted" style={{ fontSize: 11 }}>
        {label}
      </div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{value}</div>
    </div>
  )
  return (
    <div className="card">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
        {stat('Accepted observations', coverage.accepted_observation_count)}
        {stat('Source reports', coverage.distinct_source_document_count)}
        {stat('Providers', coverage.distinct_provider_count)}
        {stat('Period covered', range)}
        {stat('With reported median', coverage.with_reported_median_count)}
        {stat('With reported mean', coverage.with_reported_mean_count)}
        {stat('With a range', coverage.with_range_count)}
        {stat('With published sample n', coverage.with_sample_size_count)}
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
        {coverage.archetype_linked_count} row(s) linked to a role archetype · {coverage.not_archetype_linked_count} not yet
        linked — unlinked evidence still contributes fully to the analytics below.
      </p>
    </div>
  )
}

// --- PQE / experience progression --------------------------------------------

function pivotPoints(points: MarketAnalyticsPoint[], statKey: 'reported_p50' | 'reported_mean') {
  const bandOrder: string[] = []
  const providers: string[] = []
  const byBand = new Map<string, Record<string, number | null>>()
  for (const p of points) {
    if (!bandOrder.includes(p.band)) bandOrder.push(p.band)
    const providerLabel = p.provider || 'Unlabelled source'
    if (!providers.includes(providerLabel)) providers.push(providerLabel)
    const row = byBand.get(p.band) || {}
    const value = p[statKey]
    if (value !== null && value !== undefined) row[providerLabel] = value
    byBand.set(p.band, row)
  }
  return { data: bandOrder.map((band) => ({ band, ...byBand.get(band) })), providers }
}

function PointsTable({ points }: { points: MarketAnalyticsPoint[] }) {
  if (points.length === 0) return null
  return (
    <div style={{ overflowX: 'auto', marginTop: 8 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr className="muted" style={{ textAlign: 'left' }}>
            <th style={{ padding: '4px 8px' }}>Provider</th>
            <th style={{ padding: '4px 8px' }}>Band</th>
            <th style={{ padding: '4px 8px' }}>Practice</th>
            <th style={{ padding: '4px 8px' }}>Reported median</th>
            <th style={{ padding: '4px 8px' }}>Reported mean</th>
            <th style={{ padding: '4px 8px' }}>Range</th>
            <th style={{ padding: '4px 8px' }}>n</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.observation_id} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '4px 8px' }}>{p.provider || '—'}</td>
              <td style={{ padding: '4px 8px' }}>{p.band}</td>
              <td style={{ padding: '4px 8px' }}>
                {p.practice_group}
                {p.practice_reported && p.practice_reported !== p.practice_group ? ` (${p.practice_reported})` : ''}
              </td>
              <td style={{ padding: '4px 8px' }}>{p.reported_p50 != null ? formatMoney(p.reported_p50, p.currency) : '—'}</td>
              <td style={{ padding: '4px 8px' }}>{p.reported_mean != null ? formatMoney(p.reported_mean, p.currency) : '—'}</td>
              <td style={{ padding: '4px 8px' }}>
                {p.amount_min != null || p.amount_max != null ? `${formatMoney(p.amount_min, p.currency)}–${formatMoney(p.amount_max, p.currency)}` : '—'}
              </td>
              <td style={{ padding: '4px 8px' }}>{p.reported_sample_size ?? 'not published'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BandProgressionChart({ title, points }: { title: string; points: MarketAnalyticsPoint[] }) {
  const [stat, setStat] = useState<'reported_p50' | 'reported_mean'>('reported_p50')
  const currency = useMemo(() => singleValue(points, 'currency'), [points])
  const payPeriod = useMemo(() => singleValue(points, 'pay_period'), [points])
  const { data, providers } = useMemo(() => pivotPoints(points, stat), [points, stat])
  const hasStat = points.some((p) => p[stat] != null)

  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
        <h3 style={{ fontSize: 14, margin: 0 }}>{title}</h3>
        {points.length > 0 && (
          <div style={{ display: 'flex', gap: 4 }}>
            <button type="button" className={stat === 'reported_p50' ? 'primary' : 'secondary'} onClick={() => setStat('reported_p50')}>
              Reported median
            </button>
            <button type="button" className={stat === 'reported_mean' ? 'primary' : 'secondary'} onClick={() => setStat('reported_mean')}>
              Reported mean
            </button>
          </div>
        )}
      </div>
      {points.length === 0 ? (
        <p className="muted">No evidence with this band under the current filters.</p>
      ) : !currency || !payPeriod ? (
        <EvidenceQualityNote>
          This view mixes more than one currency or pay period — pick one from the filters above for a comparable chart.
          The rows are still listed in the table below.
        </EvidenceQualityNote>
      ) : !hasStat ? (
        <EvidenceQualityNote>
          None of the matching rows report a {stat === 'reported_p50' ? 'median' : 'mean'} — see the range/table evidence below.
        </EvidenceQualityNote>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--gridline)" />
            <XAxis dataKey="band" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} width={70} tickFormatter={(v) => formatMoney(Number(v), currency)} />
            <Tooltip formatter={(value) => moneyTooltipValue(value, currency)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {providers.map((prov, i) => (
              <Bar key={prov} dataKey={prov} name={prov} fill={colorFor(i)} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
      <PointsTable points={points} />
    </section>
  )
}

// --- Practice / component comparisons ----------------------------------------

function PracticeComparisonCard({ comparison }: { comparison: MarketPracticeComparison }) {
  const { context, practices } = comparison
  const currency = context.currency
  const canChart = practices.every((p) => p.reported_p50 != null)
  return (
    <div className="card">
      <div style={{ fontSize: 12 }} className="muted">
        {[context.provider, context.document_title, context.report_date, context.band ? `${context.band_type} ${context.band}` : null, context.component, context.pay_period]
          .filter(Boolean)
          .join(' · ')}
      </div>
      {canChart ? (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={practices.map((p) => ({ name: p.practice_group, value: p.reported_p50 }))} layout="vertical" margin={{ left: 24 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--gridline)" />
            <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => formatMoney(Number(v), currency)} />
            <YAxis type="category" dataKey="name" tick={{ fontSize: 12 }} width={140} />
            <Tooltip formatter={(value) => moneyTooltipValue(value, currency)} />
            <Bar dataKey="value" fill={colorFor(0)} />
          </BarChart>
        </ResponsiveContainer>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginTop: 8 }}>
          <tbody>
            {practices.map((p) => (
              <tr key={p.observation_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 8px' }}>{p.practice_group}</td>
                <td style={{ padding: '4px 8px' }}>{p.reported_p50 != null ? `median ${formatMoney(p.reported_p50, currency)}` : ''}</td>
                <td style={{ padding: '4px 8px' }}>{p.reported_mean != null ? `mean ${formatMoney(p.reported_mean, currency)}` : ''}</td>
                <td style={{ padding: '4px 8px' }}>
                  {p.amount_min != null || p.amount_max != null ? `range ${formatMoney(p.amount_min, currency)}–${formatMoney(p.amount_max, currency)}` : ''}
                </td>
                <td className="muted" style={{ padding: '4px 8px' }}>
                  {p.reported_sample_size != null ? `n=${p.reported_sample_size}` : 'n not published'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function ComponentComparisonCard({ comparison }: { comparison: MarketComponentComparison }) {
  const { context, components } = comparison
  const currency = context.currency
  return (
    <div className="card">
      <div className="muted" style={{ fontSize: 12 }}>
        {[context.provider, context.document_title, context.practice_group, context.band ? `${context.band_type} ${context.band}` : null, context.pay_period]
          .filter(Boolean)
          .join(' · ')}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginTop: 8 }}>
        <tbody>
          {components.map((c) => (
            <tr key={c.observation_id} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '4px 8px', fontWeight: 600 }}>{c.component === 'total_package' ? 'Total package' : 'Base'}</td>
              <td style={{ padding: '4px 8px' }}>{c.reported_p50 != null ? `median ${formatMoney(c.reported_p50, currency)}` : ''}</td>
              <td style={{ padding: '4px 8px' }}>{c.reported_mean != null ? `mean ${formatMoney(c.reported_mean, currency)}` : ''}</td>
              <td style={{ padding: '4px 8px' }}>
                {c.amount_min != null || c.amount_max != null ? `range ${formatMoney(c.amount_min, currency)}–${formatMoney(c.amount_max, currency)}` : ''}
              </td>
              <td className="muted" style={{ padding: '4px 8px' }}>{c.reported_sample_size != null ? `n=${c.reported_sample_size}` : 'n not published'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// --- Named-role ranges ---------------------------------------------------------

function RoleRangesTable({ ranges }: { ranges: MarketRoleRange[] }) {
  if (ranges.length === 0) return <p className="muted">No range-based evidence under the current filters.</p>
  const domainMin = Math.min(...ranges.map((r) => r.amount_min ?? r.amount_max ?? 0))
  const domainMax = Math.max(...ranges.map((r) => r.amount_max ?? r.amount_min ?? 0))
  const span = Math.max(domainMax - domainMin, 1)
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr className="muted" style={{ textAlign: 'left' }}>
            <th style={{ padding: '4px 8px' }}>Role</th>
            <th style={{ padding: '4px 8px' }}>Provider</th>
            <th style={{ padding: '4px 8px' }}>Practice</th>
            <th style={{ padding: '4px 8px' }}>Range</th>
            <th style={{ padding: '4px 8px', minWidth: 140 }}></th>
            <th style={{ padding: '4px 8px' }}>Median</th>
            <th style={{ padding: '4px 8px' }}>n</th>
          </tr>
        </thead>
        <tbody>
          {ranges.map((r) => {
            const lo = r.amount_min ?? r.amount_max ?? domainMin
            const hi = r.amount_max ?? r.amount_min ?? domainMax
            const leftPct = ((lo - domainMin) / span) * 100
            const widthPct = Math.max(((hi - lo) / span) * 100, 1.5)
            return (
              <tr key={r.observation_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 8px' }}>{r.raw_role_label || '—'}</td>
                <td style={{ padding: '4px 8px' }}>{r.provider || '—'}</td>
                <td style={{ padding: '4px 8px' }}>{r.practice_group}</td>
                <td style={{ padding: '4px 8px', whiteSpace: 'nowrap' }}>
                  {formatMoney(r.amount_min, r.currency)}–{formatMoney(r.amount_max, r.currency)}
                </td>
                <td style={{ padding: '4px 8px' }}>
                  <div style={{ position: 'relative', height: 8, background: 'var(--border)', borderRadius: 4 }}>
                    <div
                      style={{ position: 'absolute', left: `${leftPct}%`, width: `${widthPct}%`, height: '100%', background: 'var(--series-1)', borderRadius: 4 }}
                      title={`derived midpoint: ${r.derived_range_midpoint != null ? formatMoney(r.derived_range_midpoint, r.currency) : '—'}`}
                    />
                  </div>
                </td>
                <td className="muted" style={{ padding: '4px 8px' }}>{r.reported_p50 != null ? formatMoney(r.reported_p50, r.currency) : 'not reported'}</td>
                <td className="muted" style={{ padding: '4px 8px' }}>{r.reported_sample_size ?? 'not published'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Bars show each source's reported min–max range; position/width only, never a reported median. Hover a bar for its derived midpoint.
      </p>
    </div>
  )
}

// --- Trends --------------------------------------------------------------------

function TrendChart({ series }: { series: MarketTrendSeries }) {
  const { context, points } = series
  const isRange = context.statistic === 'range'
  const data = points.map((p) => ({
    period: p.period,
    value: isRange ? undefined : p.value,
    amount_min: isRange ? p.amount_min : undefined,
    amount_max: isRange ? p.amount_max : undefined,
  }))
  return (
    <div className="card">
      <div className="muted" style={{ fontSize: 12 }}>
        {[context.provider, context.practice_group, `${context.band_type} ${context.band}`, context.component, context.pay_period, context.currency, `reported ${context.statistic}`]
          .filter(Boolean)
          .join(' · ')}
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--gridline)" />
          <XAxis dataKey="period" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} width={70} tickFormatter={(v) => formatMoney(Number(v), context.currency)} />
          <Tooltip formatter={(value) => moneyTooltipValue(value, context.currency)} />
          {isRange ? (
            <>
              <Line type="monotone" dataKey="amount_min" name="reported min" stroke={colorFor(0)} dot />
              <Line type="monotone" dataKey="amount_max" name="reported max" stroke={colorFor(1)} dot />
            </>
          ) : (
            <Line type="monotone" dataKey="value" name={`reported ${context.statistic}`} stroke={colorFor(0)} dot />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// --- Evidence table --------------------------------------------------------------

function EvidenceTable({
  summary,
  offset,
  setOffset,
}: {
  summary: MarketAnalyticsSummaryData
  offset: number
  setOffset: (n: number) => void
}) {
  const page = summary.evidence_rows
  const rows: MarketEvidenceRow[] = page.items
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h3 style={{ fontSize: 14, margin: 0 }}>Evidence behind this view</h3>
        <span className="muted" style={{ fontSize: 12 }}>
          {page.total === 0 ? 'no rows' : `${page.offset + 1}–${Math.min(page.offset + page.limit, page.total)} of ${page.total}`}
        </span>
      </div>
      <div style={{ overflowX: 'auto', marginTop: 8 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr className="muted" style={{ textAlign: 'left' }}>
              <th style={{ padding: '4px 8px' }}>Provider</th>
              <th style={{ padding: '4px 8px' }}>Report</th>
              <th style={{ padding: '4px 8px' }}>Market</th>
              <th style={{ padding: '4px 8px' }}>Practice</th>
              <th style={{ padding: '4px 8px' }}>Role</th>
              <th style={{ padding: '4px 8px' }}>PQE / Exp</th>
              <th style={{ padding: '4px 8px' }}>Component</th>
              <th style={{ padding: '4px 8px' }}>Median</th>
              <th style={{ padding: '4px 8px' }}>Mean</th>
              <th style={{ padding: '4px 8px' }}>Range</th>
              <th style={{ padding: '4px 8px' }}>n</th>
              <th style={{ padding: '4px 8px' }}>Source</th>
              <th style={{ padding: '4px 8px' }}>Archetype</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.observation_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 8px' }}>{r.provider || '—'}</td>
                <td style={{ padding: '4px 8px' }}>{[r.document_title, r.report_date].filter(Boolean).join(' · ') || '—'}</td>
                <td style={{ padding: '4px 8px' }}>{r.market_label || r.geography_reported || '—'}</td>
                <td style={{ padding: '4px 8px' }}>{r.practice_group}</td>
                <td style={{ padding: '4px 8px' }}>{r.raw_role_label || '—'}</td>
                <td style={{ padding: '4px 8px' }}>{[r.pqe_band && `PQE ${r.pqe_band}`, r.experience_band && `Exp ${r.experience_band}`].filter(Boolean).join(' / ') || '—'}</td>
                <td style={{ padding: '4px 8px' }}>
                  {r.component}/{r.pay_period}
                </td>
                <td style={{ padding: '4px 8px' }}>{r.reported_p50 != null ? formatMoney(r.reported_p50, r.currency) : '—'}</td>
                <td style={{ padding: '4px 8px' }}>{r.reported_mean != null ? formatMoney(r.reported_mean, r.currency) : '—'}</td>
                <td style={{ padding: '4px 8px' }}>
                  {r.amount_min != null || r.amount_max != null ? `${formatMoney(r.amount_min, r.currency)}–${formatMoney(r.amount_max, r.currency)}` : '—'}
                </td>
                <td style={{ padding: '4px 8px' }}>{r.reported_sample_size ?? 'not published'}</td>
                <td style={{ padding: '4px 8px' }}>{r.source_kind || r.basis}</td>
                <td className="muted" style={{ padding: '4px 8px' }}>{r.archetype_concept_id ? 'linked' : 'unlinked'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - page.limit))}>
          Previous
        </button>
        <button type="button" className="secondary" disabled={offset + page.limit >= page.total} onClick={() => setOffset(offset + page.limit)}>
          Next
        </button>
      </div>
    </section>
  )
}

// --- Page ------------------------------------------------------------------------

export function MarketSummary() {
  const [filters, setFiltersState] = useState<Filters>({})
  const [offset, setOffset] = useState(0)
  const [summary, setSummary] = useState<MarketAnalyticsSummaryData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const setFilters = (f: Filters) => {
    setFiltersState(f)
    setOffset(0)
  }

  useEffect(() => {
    const { year, ...rest } = filters
    const params: MarketAnalyticsFiltersInput = {
      ...rest,
      period_from: year ? `${year}-01-01` : undefined,
      period_to: year ? `${year}-12-31` : undefined,
      evidence_offset: offset,
      evidence_limit: 25,
    }
    api
      .getMarketAnalyticsSummary(params)
      .then(setSummary)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters), offset])

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!summary) return <p className="muted">Loading market summary…</p>

  const noEvidence = summary.coverage.accepted_observation_count === 0

  return (
    <div>
      <h2 style={{ fontSize: 16, marginBottom: 4 }}>Market summary</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        Every accepted market/survey observation, whether or not it has been mapped to a role archetype. Descriptive
        market evidence, not a consensus engine — see the provenance on every figure below.
      </p>
      <FilterBar summary={summary} filters={filters} setFilters={setFilters} />
      <div style={{ marginTop: 12 }}>
        <CoverageCards coverage={summary.coverage} />
      </div>

      {noEvidence ? (
        <p className="muted" style={{ marginTop: 16 }}>
          No accepted market evidence matches these filters yet.
        </p>
      ) : (
        <>
          <BandProgressionChart title="Compensation by PQE" points={summary.pqe_series} />
          <BandProgressionChart title="Compensation by total experience" points={summary.experience_series} />

          <section style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 14 }}>Practice comparison</h3>
            {summary.practice_comparisons.length === 0 ? (
              <p className="muted">No same-report practice comparison is available under the current filters.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {summary.practice_comparisons.map((c, i) => (
                  <PracticeComparisonCard key={i} comparison={c} />
                ))}
              </div>
            )}
          </section>

          <section style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 14 }}>Base vs total package</h3>
            {summary.component_comparisons.length === 0 ? (
              <p className="muted">No same-report base/total-package comparison is available under the current filters.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {summary.component_comparisons.map((c, i) => (
                  <ComponentComparisonCard key={i} comparison={c} />
                ))}
              </div>
            )}
          </section>

          <section className="card" style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 14, marginTop: 0 }}>Named-role salary ranges</h3>
            <RoleRangesTable ranges={summary.role_ranges} />
          </section>

          <section style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 14 }}>Trends over time</h3>
            {summary.trends.length === 0 ? (
              <p className="muted">No like-for-like historical series yet. Trends will appear as comparable historical reports are backfilled.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {summary.trends.map((s, i) => (
                  <TrendChart key={i} series={s} />
                ))}
              </div>
            )}
          </section>

          <EvidenceTable summary={summary} offset={offset} setOffset={setOffset} />
        </>
      )}
    </div>
  )
}
