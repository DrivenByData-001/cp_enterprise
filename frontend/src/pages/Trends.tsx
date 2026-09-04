import { useEffect, useState } from 'react'
import {
  api,
  type CooccurrenceItem,
  type CorpusOverview,
  type DimensionCompareItem,
  type RequirementFrequencyItem,
  type RequirementKey,
  type RequirementTrend,
  type TopRequirements,
  type TrendFilterInput,
  type TrendLabel,
} from '../lib/api'

const TREND_LABEL: Record<TrendLabel, { text: string; color: string }> = {
  emerging: { text: 'Emerging', color: 'var(--series-1)' },
  increasing: { text: 'Increasing', color: 'var(--good, #2f9e44)' },
  persistent: { text: 'Persistent', color: 'var(--muted, #888)' },
  declining: { text: 'Declining', color: 'var(--critical)' },
  sparse_insufficient_evidence: { text: 'Sparse — insufficient evidence', color: 'var(--warning)' },
}

function Bar({ label, count, total, sub }: { label: string; count: number; total: number; sub?: string }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
      <span style={{ width: 130, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={label}>
        {label}
      </span>
      <div style={{ flex: 1, background: 'var(--border)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, background: 'var(--series-1)', height: '100%' }} />
      </div>
      <span className="muted" style={{ width: 90, flexShrink: 0, textAlign: 'right' }}>
        {count} ({pct}%){sub ? ` ${sub}` : ''}
      </span>
    </div>
  )
}

function SampleSize({ n, insufficient }: { n: number; insufficient?: boolean }) {
  return (
    <span className="muted" style={{ fontSize: 12, color: insufficient ? 'var(--warning)' : undefined }}>
      n={n}
      {insufficient ? ' — too few roles to read much into this' : ''}
    </span>
  )
}

function FilterBar({ filters, setFilters }: { filters: TrendFilterInput; setFilters: (f: TrendFilterInput) => void }) {
  return (
    <div className="card" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', padding: '8px 12px' }}>
      <span className="secondary" style={{ fontSize: 13 }}>
        Filters:
      </span>
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
      <select value={filters.seniority_level ?? ''} onChange={(e) => setFilters({ ...filters, seniority_level: e.target.value || undefined })}>
        <option value="">Any seniority</option>
        <option value="junior">Junior</option>
        <option value="mid">Mid</option>
        <option value="senior">Senior</option>
        <option value="lead">Lead</option>
        <option value="head">Head</option>
        <option value="director">Director</option>
      </select>
      <select value={filters.career_track ?? ''} onChange={(e) => setFilters({ ...filters, career_track: e.target.value || undefined })}>
        <option value="">Any track</option>
        <option value="actuarial">Actuarial</option>
        <option value="data_science">Data science</option>
        <option value="quant">Quant</option>
        <option value="risk">Risk</option>
        <option value="finance">Finance</option>
        <option value="mixed">Mixed</option>
        <option value="other">Other</option>
      </select>
      {(filters.year_from || filters.year_to || filters.country || filters.seniority_level || filters.career_track) && (
        <button onClick={() => setFilters({})}>Clear</button>
      )}
    </div>
  )
}

function RequirementDetail({ item, filters }: { item: RequirementFrequencyItem; filters: TrendFilterInput }) {
  const [trend, setTrend] = useState<RequirementTrend | null>(null)
  const [cooc, setCooc] = useState<CooccurrenceItem[] | null>(null)
  const [dimension, setDimension] = useState<'country' | 'seniority_level' | 'career_track'>('seniority_level')
  const [compareItems, setCompareItems] = useState<DimensionCompareItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const key: RequirementKey = item.concept_id ? { concept_id: item.concept_id } : { surface_form: item.label }

  useEffect(() => {
    setTrend(null)
    setCooc(null)
    api.getRequirementTrend(key, filters).then(setTrend).catch((e) => setError(String(e)))
    api.getCooccurrence(key, filters).then((r) => setCooc(r.items)).catch(() => setCooc([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.concept_id, item.label, JSON.stringify(filters)])

  useEffect(() => {
    api
      .compareDimension(key, dimension, filters)
      .then((r) => setCompareItems(r.items))
      .catch(() => setCompareItems([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dimension, item.concept_id, item.label, JSON.stringify(filters)])

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 700 }}>{item.label}</div>
        <div className="muted" style={{ fontSize: 12 }}>
          {item.is_canonical ? `Canonical concept (${item.type_code})` : 'Unresolved surface form — not yet in the canonical vocabulary'} ·{' '}
          required {item.by_requirement_type.required} · preferred {item.by_requirement_type.preferred} · inferred {item.by_requirement_type.inferred}
        </div>
      </div>

      <div>
        <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
          Trend over time
        </div>
        {!trend ? (
          <p className="muted">Loading…</p>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span
                style={{
                  fontWeight: 600,
                  fontSize: 13,
                  color: TREND_LABEL[trend.classification.label].color,
                  border: `1px solid ${TREND_LABEL[trend.classification.label].color}`,
                  borderRadius: 999,
                  padding: '2px 10px',
                }}
              >
                {TREND_LABEL[trend.classification.label].text}
              </span>
              <span className="muted" style={{ fontSize: 12 }}>
                {trend.classification.rationale}
              </span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {trend.series.map((p) => (
                <Bar key={p.period} label={String(p.period)} count={p.role_count} total={p.total_roles} />
              ))}
            </div>
            {trend.series.length === 0 && <p className="muted">No dated roles in this filtered scope.</p>}
          </>
        )}
      </div>

      <div>
        <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
          Compare by{' '}
          <select value={dimension} onChange={(e) => setDimension(e.target.value as typeof dimension)} style={{ fontSize: 11 }}>
            <option value="seniority_level">seniority</option>
            <option value="country">country</option>
            <option value="career_track">career track</option>
          </select>
        </div>
        {!compareItems ? (
          <p className="muted">Loading…</p>
        ) : compareItems.length === 0 ? (
          <p className="muted">No breakdown available.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {compareItems.map((c) => (
              <Bar
                key={String(c.value)}
                label={String(c.value ?? 'unknown')}
                count={c.role_count}
                total={c.sample_size}
                sub={c.insufficient_sample ? '· thin sample' : undefined}
              />
            ))}
          </div>
        )}
      </div>

      <div>
        <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
          Clusters with (co-occurring requirements)
        </div>
        {!cooc ? (
          <p className="muted">Loading…</p>
        ) : cooc.length === 0 ? (
          <p className="muted">No other resolved concept co-occurs often enough in this scope.</p>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {cooc.map((c) => (
              <span
                key={c.concept_id}
                className="secondary"
                style={{ border: '1px solid var(--border)', borderRadius: 999, padding: '3px 10px', fontSize: 12 }}
                title={`${c.co_count} shared roles (${Math.round(c.proportion_of_roles * 100)}%)`}
              >
                {c.canonical_name} · {Math.round(c.proportion_of_roles * 100)}%
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Trends() {
  const [filters, setFilters] = useState<TrendFilterInput>({})
  const [overview, setOverview] = useState<CorpusOverview | null>(null)
  const [topRequirements, setTopRequirements] = useState<TopRequirements | null>(null)
  const [selected, setSelected] = useState<RequirementFrequencyItem | null>(null)
  const [methodologyOpen, setMethodologyOpen] = useState(false)
  const [methodology, setMethodology] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setOverview(null)
    setTopRequirements(null)
    Promise.all([api.getTrendOverview(filters), api.getTopRequirements(filters)])
      .then(([o, t]) => {
        setOverview(o)
        setTopRequirements(t)
      })
      .catch((e) => setError(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters)])

  useEffect(() => {
    setSelected(null)
  }, [filters])

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Trends</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 720 }}>
        Within your collected role corpus — descriptive statistics over the roles you've captured, not a survey of
        the wider labour market. Every number here carries its own sample size; treat anything with a small n as
        illustrative, not conclusive.
      </p>

      <FilterBar filters={filters} setFilters={setFilters} />

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}

      {overview && (
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <strong>Corpus in scope</strong>
            <SampleSize n={overview.sample_size} />
          </div>
          {overview.sample_size === 0 ? (
            <p className="muted" style={{ marginTop: 8 }}>No roles match these filters.</p>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 10 }}>
              <div>
                <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
                  By year
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {overview.by_year.map((b) => (
                    <Bar key={String(b.value)} label={String(b.value)} count={b.role_count} total={overview.sample_size} />
                  ))}
                </div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
                  By region ({overview.by_country.length} countries)
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {(overview.by_region.length > 0 ? overview.by_region : overview.by_country).slice(0, 8).map((b) => (
                    <Bar key={String(b.value)} label={String(b.value ?? 'Unknown')} count={b.role_count} total={overview.sample_size} />
                  ))}
                </div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
                  By seniority
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {overview.by_seniority.map((b) => (
                    <Bar key={String(b.value)} label={String(b.value ?? 'Unknown')} count={b.role_count} total={overview.sample_size} />
                  ))}
                </div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', marginBottom: 6 }}>
                  By career track
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {overview.by_career_track.map((b) => (
                    <Bar key={String(b.value)} label={String(b.value ?? 'Unknown')} count={b.role_count} total={overview.sample_size} />
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: selected ? '360px 1fr' : '1fr', gap: 16, marginTop: 16, alignItems: 'start' }}>
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <strong>Most common requirements</strong>
            {topRequirements && <SampleSize n={topRequirements.sample_size} insufficient={topRequirements.insufficient_sample} />}
          </div>
          {!topRequirements ? (
            <p className="muted" style={{ marginTop: 8 }}>Loading…</p>
          ) : topRequirements.items.length === 0 ? (
            <p className="muted" style={{ marginTop: 8 }}>No skill observations in this scope.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 10 }}>
              {topRequirements.items.map((item) => (
                <button
                  key={item.concept_id ?? item.label}
                  onClick={() => setSelected(item)}
                  style={{
                    textAlign: 'left',
                    background: selected?.label === item.label ? 'var(--card-active, rgba(255,255,255,0.06))' : undefined,
                  }}
                >
                  <Bar label={item.label} count={item.role_count} total={topRequirements.sample_size} />
                </button>
              ))}
            </div>
          )}
        </div>

        {selected && <RequirementDetail item={selected} filters={filters} />}
      </div>

      <div style={{ marginTop: 16 }}>
        <button
          onClick={() => {
            setMethodologyOpen(!methodologyOpen)
            if (!methodology) api.getTrendMethodology().then((m) => setMethodology(m.text))
          }}
        >
          {methodologyOpen ? 'Hide' : 'How is “trend” decided?'}
        </button>
        {methodologyOpen && (
          <pre
            className="secondary card"
            style={{ marginTop: 8, whiteSpace: 'pre-wrap', fontSize: 12, fontFamily: 'inherit' }}
          >
            {methodology ?? 'Loading…'}
          </pre>
        )}
      </div>
    </div>
  )
}
