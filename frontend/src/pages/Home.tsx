import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Profile, type Role } from '../lib/api'

const OPPORTUNITIES_PREVIEW_LIMIT = 5

// Career Cockpit (Phase 1 product shell). Deliberately built from existing,
// bounded data only — no selected-direction/application-workspace state, no
// AI call, no whole-corpus fetch. Each card loads independently (own
// loading/error state) so one failing source never blanks the others.
export default function Home() {
  const [roles, setRoles] = useState<Role[] | null>(null)
  const [rolesTotal, setRolesTotal] = useState(0)
  const [rolesLoading, setRolesLoading] = useState(true)
  const [rolesError, setRolesError] = useState<string | null>(null)

  const [profile, setProfile] = useState<Profile>(null)
  const [profileLoading, setProfileLoading] = useState(true)
  const [profileError, setProfileError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    api
      .listRoles({ period: 'current', sort: 'captured_at', limit: OPPORTUNITIES_PREVIEW_LIMIT })
      .then((res) => { if (current) { setRoles(res.items); setRolesTotal(res.total) } })
      .catch((e) => { if (current) setRolesError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (current) setRolesLoading(false) })
    return () => { current = false }
  }, [])

  useEffect(() => {
    let current = true
    api
      .getProfile()
      .then((p) => { if (current) setProfile(p) })
      .catch((e) => { if (current) setProfileError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (current) setProfileLoading(false) })
    return () => { current = false }
  }, [])

  const hasCurrentRoles = rolesTotal > 0

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Career cockpit</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 640 }}>
        See your current direction, evidence, opportunities and next useful actions.
      </p>

      <div className="home-grid">
        <section className="card" aria-labelledby="home-direction-h">
          <h2 id="home-direction-h" style={{ fontSize: 16, marginTop: 0 }}>Career direction</h2>
          <p style={{ fontWeight: 600, margin: '4px 0' }}>No career direction selected yet</p>
          <p className="secondary" style={{ fontSize: 13 }}>
            Explore existing targets, preferences and pathways while the fuller direction-builder is developed.
          </p>
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to="/future" className="button primary">Explore my future</Link>
            <Link to="/targets">View saved targets</Link>
          </div>
        </section>

        <section className="card" aria-labelledby="home-opportunities-h">
          <h2 id="home-opportunities-h" style={{ fontSize: 16, marginTop: 0 }}>Opportunities</h2>
          {rolesLoading && <p className="muted">Loading…</p>}
          {rolesError && <p role="alert" style={{ fontSize: 13 }}>Could not load current opportunities: {rolesError}</p>}
          {!rolesLoading && !rolesError && roles && roles.length === 0 && (
            <p className="muted">No current roles captured yet.</p>
          )}
          {!rolesLoading && !rolesError && roles && roles.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '8px 0' }}>
              {roles.map((r) => (
                <Link
                  key={r.id}
                  to={`/roles/${r.id}`}
                  state={{ returnTo: '/opportunities?period=current' }}
                  style={{ textDecoration: 'none' }}
                >
                  <strong>{r.title}</strong>
                  <div className="secondary" style={{ fontSize: 12 }}>
                    {r.organisation ?? 'Unknown org'}
                    {r.location ? ` · ${r.location}` : ''}
                    {r.posting_date ? ` · ${r.posting_date}` : ''}
                  </div>
                </Link>
              ))}
            </div>
          )}
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to="/opportunities?period=current" className="button primary">View current opportunities</Link>
            <Link to="/import">Add posting</Link>
          </div>
        </section>

        <section className="card" aria-labelledby="home-evidence-h">
          <h2 id="home-evidence-h" style={{ fontSize: 16, marginTop: 0 }}>My profile &amp; evidence</h2>
          {profileLoading && <p className="muted">Loading…</p>}
          {profileError && <p role="alert" style={{ fontSize: 13 }}>Could not load your profile summary.</p>}
          {!profileLoading && !profileError && profile && (
            <p
              className="secondary"
              style={{ fontSize: 13, whiteSpace: 'pre-wrap', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 4, WebkitBoxOrient: 'vertical' }}
            >
              {profile._display}
            </p>
          )}
          {!profileLoading && !profileError && !profile && (
            <p className="muted">No profile360 snapshot found yet.</p>
          )}
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to="/profile">Profile overview</Link>
            <Link to="/episodes">Career history</Link>
            <Link to="/profile360">Evidence and mappings</Link>
          </div>
        </section>

        <section className="card" aria-labelledby="home-applications-h">
          <h2 id="home-applications-h" style={{ fontSize: 16, marginTop: 0 }}>Applications</h2>
          <p style={{ fontWeight: 600, margin: '4px 0' }}>No application workspaces yet</p>
          <p className="secondary" style={{ fontSize: 13 }}>
            Application workspaces will turn a chosen opportunity into evidence, positioning, CV and interview
            preparation. For now, start from an opportunity.
          </p>
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to="/opportunities" className="button">Browse opportunities</Link>
          </div>
        </section>
      </div>

      <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-next-h">
        <h2 id="home-next-h" style={{ fontSize: 16, marginTop: 0 }}>A useful next step</h2>
        {rolesLoading && <p className="muted">Loading…</p>}
        {!rolesLoading && rolesError && (
          <p>Opportunities couldn't be checked just now — <Link to="/opportunities">browse opportunities</Link> directly.</p>
        )}
        {!rolesLoading && !rolesError && (
          hasCurrentRoles
            ? <p>Take a look at what's current. <Link to="/opportunities?period=current">Review current opportunities →</Link></p>
            : <p>Nothing current is captured yet. <Link to="/import">Add a posting →</Link></p>
        )}
      </section>
    </div>
  )
}
