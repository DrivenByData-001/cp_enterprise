import { useEffect, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { api, type ApplicationEventType, type ApplicationListItem, type ApplicationStatus } from '../lib/api'

// Phase 3 (docs/34 §8): a real, persisted Applications index — one bounded
// list call, role metadata already joined server-side, no N+1.
// Phase 5 (docs/36 §6): the same bounded list response now also carries a
// lifecycle summary (latest event / next scheduled interview) per row, so
// this index needs no per-application events request.

const ACTIVE_STATUSES: ApplicationStatus[] = ['preparing', 'ready', 'submitted', 'interviewing']

const STATUS_LABEL: Record<ApplicationStatus, string> = {
  preparing: 'Preparing',
  ready: 'Ready',
  submitted: 'Submitted',
  interviewing: 'Interviewing',
  closed: 'Closed',
  withdrawn: 'Withdrawn',
}

const EVENT_TYPE_LABEL: Record<ApplicationEventType, string> = {
  submitted: 'Submitted',
  interview_scheduled: 'Interview scheduled',
  interview_completed: 'Interview completed',
  offer_received: 'Offer received',
  offer_accepted: 'Offer accepted',
  offer_declined: 'Offer declined',
  rejected: 'Rejected',
  role_closed: 'Role closed',
  withdrawn: 'Withdrawn',
  closed: 'Closed',
  other: 'Other',
}

function formatDate(value: string): string {
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString()
}

function LifecycleSummaryLine({ item }: { item: ApplicationListItem }) {
  if (item.next_interview) {
    return (
      <div className="muted" style={{ fontSize: 14, marginTop: 6 }}>
        Next interview{item.next_interview.label ? ` — ${item.next_interview.label}` : ''}: {formatDate(item.next_interview.event_at)}
      </div>
    )
  }
  if (item.latest_event) {
    return (
      <div className="muted" style={{ fontSize: 14, marginTop: 6 }}>
        Latest: {EVENT_TYPE_LABEL[item.latest_event.event_type]} on {formatDate(item.latest_event.event_at)}
      </div>
    )
  }
  return null
}

function ApplicationRow({ item }: { item: ApplicationListItem }) {
  const location = useLocation()
  return (
    <article className="card application-row" style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong>{item.role.title ?? 'Untitled role'}</strong>
          <div className="secondary" style={{ fontSize: 14 }}>
            {item.role.organisation ?? 'Unknown org'}
            {item.role.location ? ` · ${item.role.location}` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <span className="secondary" style={{ fontSize: 14, fontWeight: 600 }}>{STATUS_LABEL[item.status]}</span>
          <div className="muted" style={{ fontSize: 14 }}>Updated {formatDate(item.updated_at)}</div>
        </div>
      </div>
      <LifecycleSummaryLine item={item} />
      <div className="actions" style={{ marginTop: 10 }}>
        <Link to={`/applications/${item.id}`} state={{ returnTo: location.pathname + location.search }} className="button">
          Open application
        </Link>
        <Link to={`/roles/${item.role_instance_id}`}>View opportunity</Link>
      </div>
    </article>
  )
}

export default function Applications() {
  const [params, setParams] = useSearchParams()
  const rawOffset = Number(params.get('offset') ?? 0)
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0
  const rawStatus = params.get('status') ?? ''
  const status = Object.hasOwn(STATUS_LABEL, rawStatus) ? rawStatus as ApplicationStatus : undefined
  const pageSize = 25
  const [total, setTotal] = useState(0)
  const [retry, setRetry] = useState(0)
  const changePage = (next: number) => setParams(prev => { const p = new URLSearchParams(prev); if (next) p.set('offset', String(next)); else p.delete('offset'); return p })
  const [items, setItems] = useState<ApplicationListItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    setItems(null); setError(null)
    api
      .listApplications({ limit: pageSize, offset, status })
      .then((res) => { if (current) {
        if (res.total > 0 && offset >= res.total) {
          setParams(prev => { const p = new URLSearchParams(prev); p.set('offset', String(Math.floor((res.total - 1) / pageSize) * pageSize)); return p }, { replace: true })
          return
        }
        setItems(res.items); setTotal(res.total)
      } })
      .catch((e) => { if (current) setError(e instanceof Error ? e.message : String(e)) })
    return () => { current = false }
  }, [offset, status, retry, setParams])

  const active = items?.filter((i) => ACTIVE_STATUSES.includes(i.status)) ?? []
  const historical = items?.filter((i) => !ACTIVE_STATUSES.includes(i.status)) ?? []

  return (
    <div>
      <p className="eyebrow">Opportunities in motion</p><h1>Applications</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 640 }}>
        A space to prepare, follow up, and keep each next step in view.
      </p>

      <div className="list-toolbar"><label>Application status<select value={status ?? ''} onChange={e => setParams(prev => { const p = new URLSearchParams(prev); p.delete('offset'); if (e.target.value) p.set('status', e.target.value); else p.delete('status'); return p })}><option value="">All statuses</option>{Object.entries(STATUS_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><Link className="button" to="/opportunities">Find an opportunity ↗</Link></div>
      {error && <div role="alert" className="error-notice"><p>Applications couldn't be loaded: {error}</p><button onClick={() => setRetry(n => n + 1)}>Retry applications</button></div>}
      {!error && items === null && <p className="muted">Loading…</p>}

      {!error && items !== null && items.length === 0 && (
        <div className="card" style={{ marginTop: 12 }}>
          <p style={{ marginTop: 0 }}>{status ? 'No applications match this status.' : 'No applications yet. Choose an opportunity when you decide you want to pursue it.'}</p>
          {status && <button onClick={() => setParams({})}>Clear filters</button>}
          <div className="actions">
            <Link to="/opportunities" className="button primary">Browse opportunities</Link>
          </div>
        </div>
      )}

      {!error && items !== null && total > 0 && <nav className="pagination" aria-label="Application pages"><span>{offset + 1}–{Math.min(offset + items.length, total)} of {total} applications</span><div className="actions"><button disabled={offset === 0} onClick={() => changePage(Math.max(0, offset - pageSize))}>Previous page</button><button disabled={offset + pageSize >= total} onClick={() => changePage(offset + pageSize)}>Next page</button></div></nav>}
      {!error && items !== null && items.length > 0 && (
        <>
          <section aria-labelledby="active-applications-h" style={{ marginTop: 16 }}>
            <h2 id="active-applications-h" style={{ fontSize: 16 }}>
              Active
            </h2>
            {active.length === 0 && <p className="muted">No active applications on this page.</p>}
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
