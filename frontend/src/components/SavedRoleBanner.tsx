import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Role } from '../lib/api'
import { roleListUrl } from '../lib/roleNavigation'

// Explicit-save-checkpoint UX: once a page has a real roleId, the role is
// already persisted (POST /api/role-instances/ingest writes the source
// document + role_instance before any AI enrichment ever runs) — this banner
// makes that fact visible and persistent across the whole review workflow
// (Review details, Review requirements, Compare) rather than a one-off toast,
// so the user always knows they can leave without losing the posting. Never
// invents its own "saved" flag: it reflects whatever `api.getRole` returns
// for this real, already-persisted id, so it can never disagree with the
// database the way local-only state could.
export default function SavedRoleBanner({ roleId }: { roleId: string }) {
  const [role, setRole] = useState<Role | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    setRole(null); setError(null)
    api.getRole(roleId)
      .then((r) => { if (current) setRole(r) })
      .catch((e) => { if (current) setError(e instanceof Error ? e.message : String(e)) })
    return () => { current = false }
  }, [roleId])

  return (
    <div className="card" role="status" style={{ marginBottom: 16, borderColor: 'var(--good)' }}>
      <strong style={{ color: 'var(--good)' }}>Saved to Roles</strong>
      <p className="secondary" style={{ margin: '4px 0' }}>
        This posting is stored. You can leave this workflow and return later.
      </p>
      {role && (
        <div style={{ margin: '6px 0' }}>
          <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>Saved details</div>
          <div>
            <strong>{role.title}</strong>
            {role.organisation ? ` · ${role.organisation}` : ''}
            {role.location ? ` · ${role.location}` : ''}
          </div>
        </div>
      )}
      {error && <p role="alert" style={{ fontSize: 13, color: 'var(--critical)' }}>Could not confirm saved details: {error}</p>}
      <div className="actions" style={{ display: 'flex', gap: 8 }}>
        <Link to={`/roles/${roleId}`}>Open saved role</Link>
        <Link to={roleListUrl()}>Back to Roles</Link>
      </div>
    </div>
  )
}
