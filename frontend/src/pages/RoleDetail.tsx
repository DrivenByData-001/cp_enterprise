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

  const isTarget = role.node_type !== 'posting'

  const handleDelete = async () => {
    if (!confirm(`Delete "${role.title}"? This can't be undone.`)) return
    await api.deleteRole(role.id)
    navigate(isTarget ? '/targets' : '/')
  }

  return (
    <div>
      <Link to={isTarget ? '/targets' : '/'} className="muted" style={{ fontSize: 13 }}>
        ← Back to {isTarget ? 'targets' : 'roles'}
      </Link>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginTop: 12 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              aria-hidden
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: isTarget
                  ? role.node_type === 'target_imagined'
                    ? '#c792ff'
                    : '#ffffff'
                  : trackColor(role.career_track),
                border: isTarget ? '1px solid var(--border)' : undefined,
              }}
            />
            <span className="secondary" style={{ fontSize: 13 }}>
              {isTarget
                ? role.node_type === 'target_imagined'
                  ? 'Target · imagined'
                  : 'Target · real'
                : trackLabel(role.career_track)}
              {role.career_track && isTarget ? ` · ${trackLabel(role.career_track)}` : ''}
            </span>
          </div>
          <h1 style={{ fontSize: 24, margin: '4px 0' }}>{role.title}</h1>
          <div className="secondary">
            {role.organisation ?? (isTarget ? 'No organisation specified' : 'Unknown org')}
            {role.location ? ` · ${role.location}` : ''}
            {role.remote_type ? ` · ${role.remote_type}` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 28, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
            {role.similarity !== null ? `${Math.round(role.similarity! * 100)}%` : '—'}
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            {isTarget ? 'current alignment' : 'similarity to profile'}
          </div>
        </div>
      </div>

      {isTarget && role.is_plausible === false && (
        <div
          className="card"
          style={{ marginTop: 16, borderColor: 'var(--critical)', background: 'rgba(208,59,59,0.08)' }}
        >
          <strong style={{ color: 'var(--critical)' }}>Flagged as not realistically reachable</strong>
          {role.feasibility_note && <p style={{ margin: '4px 0 0' }}>{role.feasibility_note}</p>}
        </div>
      )}

      {role.extraction_status && role.extraction_status !== 'ok' && (
        <div
          className="card"
          style={{ marginTop: 16, borderColor: 'var(--warning)', background: 'rgba(250,178,25,0.08)' }}
        >
          <strong>Extraction {role.extraction_status}</strong>
          {role.extraction_notes && <p style={{ margin: '4px 0 0' }}>{role.extraction_notes}</p>}
        </div>
      )}

      {!isTarget && (
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
      )}

      {isTarget && role.path && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Path from where you are now</h3>
          <p className="secondary" style={{ marginTop: 0 }}>
            {role.path.profile_to_target_similarity !== null
              ? `Your current profile is ${Math.round(role.path.profile_to_target_similarity * 100)}% aligned with this target already.`
              : 'Save a profile narrative to see your alignment with this target.'}
          </p>
          {role.path.stepping_stones.length > 0 ? (
            <>
              <strong style={{ fontSize: 13 }}>Closest captured roles — real stepping stones toward this target</strong>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                {role.path.stepping_stones.map((s) => (
                  <Link
                    key={s.id}
                    to={`/roles/${s.id}`}
                    className="secondary"
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      padding: '6px 10px',
                      fontSize: 13,
                      textDecoration: 'none',
                    }}
                  >
                    <span>
                      {s.title} {s.organisation ? `· ${s.organisation}` : ''}
                    </span>
                    <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {Math.round(s.similarity_to_target * 100)}%
                    </span>
                  </Link>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">No captured postings yet to use as stepping stones toward this target.</p>
          )}
        </div>
      )}

      {isTarget && role.feasibility_note && role.is_plausible !== false && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Feasibility</h3>
          <p className="secondary">{role.feasibility_note}</p>
        </div>
      )}

      {isTarget && role.grounding_note && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Grounding</h3>
          <p className="secondary">{role.grounding_note}</p>
        </div>
      )}

      {isTarget && role.typical_tasks && role.typical_tasks.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Typical tasks</h3>
          <ul className="secondary" style={{ margin: 0, paddingLeft: 20 }}>
            {role.typical_tasks.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {isTarget && role.skill_decomposition && role.skill_decomposition.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Skill decomposition</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {role.skill_decomposition.map((s, i) => (
              <div key={i}>
                <strong style={{ fontSize: 13 }}>{s.skill}</strong>
                {s.examples.length > 0 && (
                  <ul className="secondary" style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                    {s.examples.map((ex, j) => (
                      <li key={j}>{ex}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {isTarget && role.technical_subjects && role.technical_subjects.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Technical subjects to study</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {role.technical_subjects.map((s, i) => (
              <div key={i}>
                <strong style={{ fontSize: 13 }}>{s.subject}</strong>
                {s.why && <p className="secondary" style={{ margin: '2px 0' }}>{s.why}</p>}
                {s.resources.length > 0 && (
                  <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                    Resources: {s.resources.join(', ')}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

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
          <h3 style={{ marginTop: 0, fontSize: 14 }}>{isTarget ? 'Role narrative' : 'Raw posting'}</h3>
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

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 16 }}>
        {role.url ? (
          <a href={role.url} target="_blank" rel="noreferrer" className="muted" style={{ fontSize: 13 }}>
            View original posting ↗
          </a>
        ) : (
          <span />
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <Link to={`/roles/${role.id}/edit`}>
            <button>Edit</button>
          </Link>
          <button onClick={handleDelete} style={{ color: 'var(--critical)' }}>
            Delete {isTarget ? 'target' : 'role'}
          </button>
        </div>
      </div>
    </div>
  )
}
