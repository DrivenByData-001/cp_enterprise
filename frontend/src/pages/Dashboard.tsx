import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Facet, type Role, type YearRange } from '../lib/api'
import { trackColor, trackLabel } from '../lib/trackColor'

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
  const [track, setTrack] = useState('')
  const [sort, setSort] = useState('similarity')
  const [facetType, setFacetType] = useState('')
  const [facets, setFacets] = useState<Facet[]>([])
  const [conceptId, setConceptId] = useState<string>('')
  const [period, setPeriod] = useState<'recent' | 'all' | 'year'>('recent')
  const [year, setYear] = useState<number | ''>('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!facetType) {
      setFacets([])
      setConceptId('')
      return
    }
    api
      .getFacets(facetType)
      .then(setFacets)
      .catch((e) => setError(String(e)))
  }, [facetType])

  // Any filter change resets pagination to the first page — a stale offset
  // against a differently-sized filtered set would silently show an
  // out-of-range empty page.
  useEffect(() => {
    setOffset(0)
  }, [track, conceptId, sort, period, year])

  useEffect(() => {
    setLoading(true)
    api
      .listRoles({
        career_track: track || undefined,
        concept_id: conceptId === '' ? undefined : conceptId,
        sort,
        period: period === 'year' ? 'all' : period, // an explicit year always wins server-side; 'all' just avoids double-filtering
        year: period === 'year' && year !== '' ? year : undefined,
        limit: PAGE_SIZE,
        offset,
      })
      .then((res) => {
        setRoles(res.items)
        setTotal(res.total)
        setYearRange(res.year_range)
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [track, conceptId, sort, period, year, offset])

  const availableYears: number[] = yearRange ? Array.from({ length: yearRange.max - yearRange.min + 1 }, (_, i) => yearRange.max - i) : []
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + PAGE_SIZE, total)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>Roles</h1>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <select value={track} onChange={(e) => setTrack(e.target.value)}>
            <option value="">All tracks</option>
            {TRACKS.map((t) => (
              <option key={t} value={t}>
                {trackLabel(t)}
              </option>
            ))}
          </select>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="similarity">Sort: similarity</option>
            <option value="posting_date">Sort: posting date</option>
            <option value="captured_at">Sort: captured</option>
            <option value="title">Sort: title</option>
          </select>
          <select value={facetType} onChange={(e) => setFacetType(e.target.value)}>
            <option value="">Facet: none</option>
            {FACET_TYPES.map((t) => (
              <option key={t.code} value={t.code}>
                Facet: {t.label}
              </option>
            ))}
          </select>
          {facetType && (
            <select value={conceptId} onChange={(e) => setConceptId(e.target.value)}>
              <option value="">All {facetType}s</option>
              {facets.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.canonical_name} ({f.role_count})
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Temporal filter (docs/18 §3): defaults to "recent" so the ~2008-2025
          historical corpus doesn't drown out current roles day to day, while
          every historical year stays one click away — never hidden at the
          persistence layer, only in this default view. */}
      <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16, padding: '8px 12px' }}>
        <span className="secondary" style={{ fontSize: 13 }}>
          Showing:
        </span>
        <select value={period} onChange={(e) => setPeriod(e.target.value as typeof period)}>
          <option value="recent">Recent (last few years)</option>
          <option value="all">All years{yearRange ? ` (${yearRange.min}–${yearRange.max})` : ''}</option>
          <option value="year">A specific year…</option>
        </select>
        {period === 'year' && (
          <select value={year} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : '')}>
            <option value="">Choose a year</option>
            {availableYears.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        )}
        {period !== 'recent' && (
          <span className="muted" style={{ fontSize: 12 }}>
            Historical roles included — the full captured corpus spans {yearRange ? `${yearRange.min}–${yearRange.max}` : 'multiple years'}.
          </span>
        )}
      </div>

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && roles.length === 0 && (
        <p className="muted">
          No roles captured yet. Head to <Link to="/import">Import</Link> to add your first one.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {roles.map((r) => (
          <Link
            key={r.id}
            to={`/roles/${r.id}`}
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
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <button disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
