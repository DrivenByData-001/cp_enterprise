import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Role } from '../lib/api'
import { trackColor, trackLabel } from '../lib/trackColor'

export default function RoleDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [role, setRole] = useState<Role | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api
      .getRole(Number(id))
      .then(setRole)
      .catch((e) => setError(String(e)))
  }, [id])

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!role) return <p className="muted">Loading…</p>

  const handleDelete = async () => {
    if (!confirm(`Delete "${role.title}"? This can't be undone.`)) return
    await api.deleteRole(role.id)
    navigate('/')
  }

  return (
    <div>
      <Link to="/" className="muted" style={{ fontSize: 13 }}>
        ← Back to roles
      </Link>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginTop: 12 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              aria-hidden
              style={{ width: 10, height: 10, borderRadius: '50%', background: trackColor(role.career_track) }}
            />
            <span className="secondary" style={{ fontSize: 13 }}>
              {trackLabel(role.career_track)}
            </span>
          </div>
          <h1 style={{ fontSize: 24, margin: '4px 0' }}>{role.title}</h1>
          <div className="secondary">
            {role.organisation ?? 'Unknown org'}
            {role.location ? ` · ${role.location}` : ''}
            {role.remote_type ? ` · ${role.remote_type}` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 28, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
            {role.similarity !== null ? `${Math.round(role.similarity * 100)}%` : '—'}
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            similarity to profile
          </div>
        </div>
      </div>

      {role.extraction_status && role.extraction_status !== 'ok' && (
        <div
          className="card"
          style={{ marginTop: 16, borderColor: 'var(--warning)', background: 'rgba(250,178,25,0.08)' }}
        >
          <strong>Extraction {role.extraction_status}</strong>
          {role.extraction_notes && <p style={{ margin: '4px 0 0' }}>{role.extraction_notes}</p>}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 16 }}>
        <div className="card">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Salary</h3>
          {role.salary_min || role.salary_max ? (
            <p>
              {role.salary_min ?? '?'} – {role.salary_max ?? '?'} {role.currency ?? ''}
            </p>
          ) : (
            <p className="muted">Not stated</p>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Dates</h3>
          <p className="secondary">
            Posted: {role.posting_date ?? '—'}
            <br />
            Captured: {role.captured_at?.slice(0, 10) ?? '—'}
          </p>
        </div>
      </div>

      {role.summary && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Summary</h3>
          <p>{role.summary}</p>
        </div>
      )}

      {role.skills && role.skills.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Skills</h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {role.skills.map((s, i) => (
              <span
                key={i}
                className="secondary"
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 999,
                  padding: '4px 10px',
                  fontSize: 12,
                  opacity: s.requirement_type === 'preferred' ? 0.7 : 1,
                }}
              >
                {s.name}
                {s.requirement_type ? ` · ${s.requirement_type}` : ''}
              </span>
            ))}
          </div>
        </div>
      )}

      {role.top_adjacent_roles && role.top_adjacent_roles.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Adjacent roles</h3>
          <p className="secondary">{role.top_adjacent_roles.join(', ')}</p>
        </div>
      )}

      {(role.description || role.requirements || role.responsibilities) && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Raw posting</h3>
          {role.description && <p className="secondary">{role.description}</p>}
          {role.requirements && (
            <>
              <strong style={{ fontSize: 13 }}>Requirements</strong>
              <p className="secondary">{role.requirements}</p>
            </>
          )}
          {role.responsibilities && (
            <>
              <strong style={{ fontSize: 13 }}>Responsibilities</strong>
              <p className="secondary">{role.responsibilities}</p>
            </>
          )}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
        {role.url ? (
          <a href={role.url} target="_blank" rel="noreferrer" className="muted" style={{ fontSize: 13 }}>
            View original posting ↗
          </a>
        ) : (
          <span />
        )}
        <button onClick={handleDelete} style={{ color: 'var(--critical)' }}>
          Delete role
        </button>
      </div>
    </div>
  )
}
