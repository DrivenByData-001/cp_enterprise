import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ApplicationListItem, type ApplicationStatus } from '../lib/api'

// Phase 3 (docs/34 §8): a real, persisted Applications index — one bounded
// list call, role metadata already joined server-side, no N+1.

const ACTIVE_STATUSES: ApplicationStatus[] = ['preparing', 'ready', 'submitted', 'interviewing']

const STATUS_LABEL: Record<ApplicationStatus, string> = {
  preparing: 'Preparing',
  ready: 'Ready',
  submitted: 'Submitted',
  interviewing: 'Interviewing',
  closed: 'Closed',
  withdrawn: 'Withdrawn',
}

function formatDate(value: string): string {
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString()
}

function ApplicationRow({ item }: { item: ApplicationListItem }) {
  return (
    <article className="card" style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong>{item.role.title ?? 'Untitled role'}</strong>
          <div className="secondary" style={{ fontSize: 13 }}>
            {item.role.organisation ?? 'Unknown org'}
            {item.role.location ? ` · ${item.role.location}` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <span className="secondary" style={{ fontSize: 13, fontWeight: 600 }}>{STATUS_LABEL[item.status]}</span>
          <div className="muted" style={{ fontSize: 11 }}>Updated {formatDate(item.updated_at)}</div>
        </div>
      </div>
      <div className="actions" style={{ marginTop: 10 }}>
        <Link to={`/applications/${item.id}`} className="button primary">
          Open application
        </Link>
        <Link to={`/roles/${item.role_instance_id}`}>View opportunity</Link>
      </div>
    </article>
  )
}

export default function Applications() {
  const [items, setItems] = useState<ApplicationListItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    api
      .listApplications({ limit: 200 })
      .then((res) => { if (current) setItems(res.items) })
      .catch((e) => { if (current) setError(e instanceof Error ? e.message : String(e)) })
    return () => { current = false }
  }, [])

  const active = items?.filter((i) => ACTIVE_STATUSES.includes(i.status)) ?? []
  const historical = items?.filter((i) => !ACTIVE_STATUSES.includes(i.status)) ?? []

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Applications</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 640 }}>
        Persistent workspaces for the opportunities you've decided to pursue.
      </p>

      {error && <p role="alert" style={{ color: 'var(--critical)' }}>Applications couldn't be loaded: {error}</p>}
      {!error && items === null && <p className="muted">Loading…</p>}

      {!error && items !== null && items.length === 0 && (
        <div className="card" style={{ marginTop: 12 }}>
          <p style={{ marginTop: 0 }}>No applications yet. Choose an opportunity when you decide you want to pursue it.</p>
          <div className="actions">
            <Link to="/opportunities" className="button primary">Browse opportunities</Link>
          </div>
        </div>
      )}

      {!error && items !== null && items.length > 0 && (
        <>
          <section aria-labelledby="active-applications-h" style={{ marginTop: 16 }}>
            <h2 id="active-applications-h" style={{ fontSize: 16 }}>
              Active
            </h2>
            {active.length === 0 && <p className="muted">No active applications right now.</p>}
            {active.map((item) => <ApplicationRow key={item.id} item={item} />)}
          </section>

          {historical.length > 0 && (
            <section aria-labelledby="historical-applications-h" style={{ marginTop: 20 }}>
              <h2 id="historical-applications-h" style={{ fontSize: 16 }}>
                Past attempts
              </h2>
              {historical.map((item) => <ApplicationRow key={item.id} item={item} />)}
            </section>
          )}

          <div className="actions" style={{ marginTop: 16 }}>
            <Link to="/opportunities">Browse opportunities</Link>
          </div>
        </>
      )}
    </div>
  )
}
