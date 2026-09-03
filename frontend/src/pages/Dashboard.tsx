import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Facet, type Role } from '../lib/api'
import { trackColor, trackLabel } from '../lib/trackColor'

const TRACKS = ['actuarial', 'data_science', 'quant', 'risk', 'finance', 'mixed', 'other']

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

export default function Dashboard() {
  const [roles, setRoles] = useState<Role[]>([])
  const [track, setTrack] = useState('')
  const [sort, setSort] = useState('similarity')
  const [facetType, setFacetType] = useState('')
  const [facets, setFacets] = useState<Facet[]>([])
  const [conceptId, setConceptId] = useState<string>('')
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

  useEffect(() => {
    setLoading(true)
    api
      .listRoles({ career_track: track || undefined, concept_id: conceptId === '' ? undefined : conceptId, sort })
      .then(setRoles)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [track, conceptId, sort])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>Roles</h1>
        <div style={{ display: 'flex', gap: 8 }}>
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
            <select
              value={conceptId}
              onChange={(e) => setConceptId(e.target.value)}
            >
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
                <div style={{ fontWeight: 600 }}>{r.title}</div>
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
    </div>
  )
}
