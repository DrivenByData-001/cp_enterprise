import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Role } from '../lib/api'
import { trackLabel } from '../lib/trackColor'

export default function Targets() {
  const [targets, setTargets] = useState<Role[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listTargets()
      .then(setTargets)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>Targets</h1>
          <p className="secondary" style={{ marginTop: 4 }}>
            Real or imagined roles you're navigating towards — with a decomposition of the work, the skills, and
            what to study, plus a path from where you are now.
          </p>
        </div>
        <Link to="/targets/new">
          <button className="primary">Add target</button>
        </Link>
      </div>

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && targets.length === 0 && (
        <p className="muted">
          No targets yet. Head to <Link to="/targets/new">Add target</Link> to define your first one — real or
          imagined.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {targets.map((t) => (
          <Link
            key={t.id}
            to={`/roles/${t.id}`}
            className="card"
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', textDecoration: 'none' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span
                aria-hidden
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  background: t.node_type === 'target_imagined' ? '#c792ff' : '#ffffff',
                  border: '1px solid var(--border)',
                  flexShrink: 0,
                }}
              />
              <div>
                <div style={{ fontWeight: 600 }}>{t.title}</div>
                <div className="secondary" style={{ fontSize: 13 }}>
                  {t.organisation ?? (t.node_type === 'target_imagined' ? 'Imagined role' : 'Real role')}
                  {t.career_track ? ` · ${trackLabel(t.career_track)}` : ''}
                  {t.is_plausible === false ? ' · flagged implausible' : ''}
                </div>
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
                {t.similarity !== null && t.similarity !== undefined ? `${Math.round(t.similarity * 100)}%` : '—'}
              </div>
              <div className="muted" style={{ fontSize: 12 }}>
                alignment
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
