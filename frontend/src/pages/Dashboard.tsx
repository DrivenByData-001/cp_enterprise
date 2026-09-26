import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type Facet, type Role, type YearRange } from '../lib/api'
import { trackColor, trackLabel } from '../lib/trackColor'
import { rememberRoleList } from '../lib/roleNavigation'

const TRACKS = ['actuarial', 'data_science', 'quant', 'risk', 'finance', 'mixed', 'other']
const PAGE_SIZE = 20

// The Phase 1 "Ships" facets (docs/11 §11): filter/group postings by domain,
// regulation, tool, function, product — the atomic types most postings are
// actually differentiated by. capability/role_archetype aren't populated yet
// (Phase 3/4) and knowledge/method/credential are less useful as list filters.
const FACET_TYPES = [
  { code: 'domain', label: 'Domain' },
  { code: 'regulation', label: 'Regulation' },
  { code: 'tool', label: 'Tool' },
  { code: 'function', label: 'Function' },
  { code: 'product', label: 'Product' },
]

function ExtractionQualityBadge({ role }: { role: Role }) {
  // The run-level signal (docs/18 §5) is authoritative when present; a role
  // never processed through the document-processing pipeline falls back to
  // its own self-reported extraction_status. Restrained on purpose — never
  // an alarming colour, never shown for 'ok'/unknown, and never implies the
  // role is unusable (partial means usable extraction with known/model-
  // declared incompleteness, not failure).
  const isPartial = role.extraction_quality
    ? role.extraction_quality.status === 'partial'
    : role.extraction_status != null && role.extraction_status !== 'ok'
  if (!isPartial) return null
  return (
    <span
      title="This extraction was flagged incomplete/uncertain — open the role for details. Eligible for later review."
      style={{
        fontSize: 11,
        color: 'var(--warning)',
        border: '1px solid var(--warning)',
        borderRadius: 999,
        padding: '1px 7px',
        whiteSpace: 'nowrap',
      }}
    >
      Partial
    </span>
  )
}

export default function Dashboard() {
  const [roles, setRoles] = useState<Role[]>([])
  const [total, setTotal] = useState(0)
  const [yearRange, setYearRange] = useState<YearRange>(null)
  const [params, setParams] = useSearchParams()
  useEffect(() => { rememberRoleList(params.toString()) }, [params])
  const track = params.get('track') ?? ''
  const facetType = params.get('facet') ?? ''
  const conceptId = facetType ? params.get('concept') ?? '' : ''
  const periodValue = params.get('period') ?? 'current'
  const period = ['current', 'recent', 'all', 'year', 'unknown_date'].includes(periodValue) ? periodValue : 'current'
  // Current's own default sort is newest/recently-captured first, not
  // similarity (brief §6.3): sending `sort=captured_at` explicitly here is
  // exactly the same request the server would resolve to on its own if
  // `sort` were omitted under `period=current` (app/routes/roles.py unifies
  // both paths through one comparator) — sent explicitly anyway so the
  // dropdown's own displayed value is never out of step with what's
  // actually being requested. An explicit choice from the dropdown always
  // wins over the default (brief §6.3/§13).
  const sort = params.get('sort') ?? (period === 'current' ? 'captured_at' : 'similarity')
  const year = /^\d{4}$/.test(params.get('year') ?? '') ? Number(params.get('year')) : ''
  const rawOffset = Number(params.get('offset') ?? 0)
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0
  const [facets, setFacets] = useState<Facet[]>([])
  const [retry, setRetry] = useState(0)
  const update = (key: string, value: string | number) => {
    const next = new URLSearchParams(params)
    if (value === '') next.delete(key)
    else next.set(key, String(value))
    if (key !== 'offset') next.delete('offset')
    if (key === 'facet') next.delete('concept')
    setParams(next)
  }
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    setFacets([])
    if (facetType) api.getFacets(facetType)
      .then((value) => { if (current) setFacets(value) })
      .catch((e) => { if (current) setError(String(e)) })
    return () => { current = false }
  }, [facetType, retry])

  useEffect(() => {
    let current = true
    setError(null)
    setRoles([])
    setTotal(0)
    if (period === 'year' && year === '') { setLoading(false); return }
    setLoading(true)
    api.listRoles({
      career_track: track || undefined, concept_id: conceptId || undefined, sort,
      period: period === 'year' ? 'all' : period as 'current' | 'all' | 'recent' | 'unknown_date',
      year: period === 'year' && year !== '' ? year : undefined,
      limit: PAGE_SIZE, offset,
    }).then((res) => {
      if (!current) return
      setRoles(res.items); setTotal(res.total); setYearRange(res.year_range)
    }).catch((e) => { if (current) setError(String(e)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [track, conceptId, sort, period, year, offset, retry])

  const availableYears: number[] = yearRange ? Array.from({ length: yearRange.max - yearRange.min + 1 }, (_, i) => yearRange.max - i) : []
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + PAGE_SIZE, total)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>Opportunities</h1>
          <p className="secondary" style={{ marginTop: 4, maxWidth: 640 }}>
            Current roles and historical postings are market observations. Open one to understand the role and
            evaluate it against your evidence.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select aria-label="Career track" value={track} onChange={(e) => update('track', e.target.value)}>
            <option value="">All tracks</option>
            {TRACKS.map((t) => (
              <option key={t} value={t}>
                {trackLabel(t)}
              </option>
            ))}
          </select>
          <select aria-label="Sort roles" value={sort} onChange={(e) => update('sort', e.target.value)}>
            <option value="similarity">Sort: similarity</option>
            <option value="posting_date">Sort: posting date</option>
            <option value="captured_at">Sort: captured</option>
            <option value="title">Sort: title</option>
          </select>
          <select aria-label="Filter category" value={facetType} onChange={(e) => update('facet', e.target.value)}>
            <option value="">Facet: none</option>
            {FACET_TYPES.map((t) => (
              <option key={t.code} value={t.code}>
                Facet: {t.label}
              </option>
            ))}
          </select>
          {facetType && (
            <select aria-label="Concept" value={conceptId} onChange={(e) => update('concept', e.target.value)}>
              <option value="">All {facetType}s</option>
              {facets.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.canonical_name} ({f.role_count})
                </option>
              ))}
            </select>
          )}
          <Link to="/import">
            <button type="button" className="primary">Add posting</button>
          </Link>
        </div>
      </div>

      {/* Temporal filter (Save-checkpoint / Current-roles brief §6): defaults
          to "Current" so a role just saved is immediately visible without
          being drowned out by the ~2008-2025 historical corpus — the
          historical corpus stays one click away, never hidden at the
          persistence layer, only in this default view. Labels are explicit
          about *posting* year (source-aware ingest cleanup, problem #8):
          this filters by when the role was posted, never by when it was
          captured/uploaded — capture date is a separate, optional axis this
          filter never substitutes for a missing posting date, except for
          Current's own well-documented undated-but-newly-saved carve-out
          below. */}
      <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16, padding: '8px 12px' }}>
        <span className="secondary" style={{ fontSize: 13 }}>
          Showing:
        </span>
        <select aria-label="Posting period" value={period} onChange={(e) => update('period', e.target.value)}>
          <option value="current">Current roles</option>
          <option value="recent">Recent (last few years)</option>
          <option value="all">All years{yearRange ? ` (${yearRange.min}–${yearRange.max})` : ''}</option>
          <option value="year">A specific posting year…</option>
          <option value="unknown_date">Unknown posting date</option>
        </select>
        {period === 'year' && (
          <>
            <span className="secondary" style={{ fontSize: 13 }}>
              Posting year:
            </span>
            <select aria-label="Posting year" value={year} onChange={(e) => update('year', e.target.value)}>
              <option value="">Choose a year</option>
              {availableYears.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </>
        )}
        {period === 'current' && (
          <span className="muted" style={{ fontSize: 12 }}>
            Roles posted this calendar year, plus newly captured roles whose posting date is not known.
          </span>
        )}
        {(period === 'all' || period === 'year') && (
          <span className="muted" style={{ fontSize: 12 }}>
            Historical roles included — the full captured corpus spans {yearRange ? `${yearRange.min}–${yearRange.max}` : 'multiple years'}.
          </span>
        )}
        {period === 'unknown_date' && (
          <span className="muted" style={{ fontSize: 12 }}>
            Roles with no known posting date — never assumed to be the date they were captured.
          </span>
        )}
      </div>

      {error && <div role="alert"><p>{error}</p><button onClick={() => setRetry(retry + 1)}>Retry loading roles</button></div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && roles.length === 0 && (
        <div className="card">
          <p>{period === 'year' && year === '' ? 'Choose a posting year to see matching roles.' :
            track || conceptId || period !== 'all' || offset > 0 ? 'No roles match these filters.' : 'No roles captured yet.'}</p>
          <button onClick={() => setParams({ period: 'all' })}>Clear filters and show all years</button>{' '}
          <Link to="/import">Add a posting</Link>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {roles.map((r) => (
          <Link
            key={r.id}
            to={`/roles/${r.id}`}
            state={{ returnTo: `/opportunities?${params.toString()}` }}
            className="card"
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', textDecoration: 'none' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span
                aria-hidden
                style={{ width: 10, height: 10, borderRadius: '50%', background: trackColor(r.career_track), flexShrink: 0 }}
              />
              <div>
                <div style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}>
                  {r.title}
                  <ExtractionQualityBadge role={r} />
                </div>
                <div className="secondary" style={{ fontSize: 13 }}>
                  {r.organisation ?? 'Unknown org'}
                  {r.location ? ` · ${r.location}` : ''}
                  {r.posting_date ? ` · ${r.posting_date}` : ''}
                </div>
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
                {r.similarity !== null ? `${Math.round(r.similarity * 100)}%` : '—'}
              </div>
              <div className="muted" style={{ fontSize: 12 }}>
                similarity
              </div>
            </div>
          </Link>
        ))}
      </div>

      {total > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 16 }}>
          <span className="muted" style={{ fontSize: 13 }}>
            {pageStart}–{pageEnd} of {total}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button disabled={offset === 0} onClick={() => update('offset', Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <button disabled={offset + PAGE_SIZE >= total} onClick={() => update('offset', offset + PAGE_SIZE)}>
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
