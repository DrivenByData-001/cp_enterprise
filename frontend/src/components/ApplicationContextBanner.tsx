import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ApplicationDetail } from '../lib/api'

export default function ApplicationContextBanner() {
  const [params] = useSearchParams()
  const applicationId = params.get('application')
  const [detail, setDetail] = useState<ApplicationDetail | null>(null)

  useEffect(() => {
    let current = true
    setDetail(null)
    if (!applicationId) return () => { current = false }
    api.getApplication(applicationId).then(d => { if (current) setDetail(d) }).catch(() => {})
    return () => { current = false }
  }, [applicationId])

  if (!applicationId) return null

  return (
    <aside className="card" aria-label="Active application" style={{ marginBottom: 16, borderColor: 'var(--series-1)' }}>
      <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>Application workflow</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>{detail ? `${detail.role.title ?? 'Application'} · ${detail.role.organisation ?? 'Unknown organisation'}` : 'Working on application'}</strong>
        <Link to={`/applications/${applicationId}`} className="button">Back to application</Link>
      </div>
      <p className="muted" style={{ fontSize: 13, margin: '6px 0 0' }}>
        This view is scoped to the application you are preparing. Following review links keeps that application context.
      </p>
    </aside>
  )
}
