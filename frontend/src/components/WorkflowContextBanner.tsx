import { useEffect, useState } from 'react'
import { Link, useSearchParams, type LinkProps } from 'react-router-dom'
import { api, type ApplicationDetail } from '../lib/api'
import { APPLICATION_PARAM, useWorkflowContext } from '../lib/workflowContext'

// Shown on shared screens (Role Detail, Requirements, Comparison) when the URL
// carries an application context. The application is re-read from the API so a
// refresh/deep link rebuilds the indicator; a stale, unknown or mismatched id
// degrades to a quiet notice and never blocks the screen underneath.
export function ApplicationContextBanner({ roleId }: { roleId: string }) {
  const { applicationId } = useWorkflowContext()
  const [detail, setDetail] = useState<ApplicationDetail | null>(null)
  const [failed, setFailed] = useState(false)
  const [rejected, setRejected] = useState<'unavailable' | 'mismatch' | null>(null)
  const [, setParams] = useSearchParams()

  useEffect(() => {
    if (!applicationId) return
    let current = true
    setDetail(null); setFailed(false); setRejected(null)
    api.getApplication(applicationId)
      .then((d) => { if (current) setDetail(d) })
      .catch(() => { if (current) setFailed(true) })
    return () => { current = false }
  }, [applicationId])

  const mismatched = detail !== null && detail.application.role_instance_id !== roleId
  // A context that fails validation is discarded, not just hidden: stripping
  // the param makes nav, Apply availability and every later link behave as an
  // ordinary Opportunities journey instead of disagreeing with the notice.
  const invalid = failed || mismatched
  useEffect(() => {
    if (!applicationId || !invalid) return
    setRejected(mismatched ? 'mismatch' : 'unavailable')
    setParams((prev) => { const p = new URLSearchParams(prev); p.delete(APPLICATION_PARAM); return p }, { replace: true })
  }, [applicationId, invalid, mismatched, setParams])

  if (!applicationId && rejected) {
    return (
      <div className="card" role="status" style={{ marginBottom: 12 }}>
        <p className="secondary" style={{ margin: 0 }}>
          This link referred to an application that couldn't be {rejected === 'mismatch' ? 'matched to this opportunity' : 'loaded'}, so it's shown as an ordinary opportunity.{' '}
          <Link to="/applications">Go to applications</Link>
        </p>
      </div>
    )
  }
  if (!applicationId || invalid) return null
  return (
    <div className="card" role="status" aria-label="Application context" style={{ marginBottom: 12, borderColor: 'var(--series-1)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <strong>Working on your application</strong>
          <div className="secondary" style={{ fontSize: 14 }}>
            {detail
              ? `${detail.role.title ?? 'Untitled role'}${detail.role.organisation ? ` · ${detail.role.organisation}` : ''}`
              : 'Loading application…'}
          </div>
        </div>
        {detail && <Link to={`/applications/${applicationId}`} className="button">Back to application</Link>}
      </div>
    </div>
  )
}

export function ApplyIntentNotice() {
  const { applyIntent } = useWorkflowContext()
  if (!applyIntent) return null
  return (
    <div className="card" role="status" aria-label="Application intent" style={{ marginBottom: 12, borderColor: 'var(--series-1)' }}>
      <strong>Choosing an opportunity to apply for</strong>
      <p className="secondary" style={{ margin: '4px 0 0', fontSize: 14 }}>
        Open an opportunity, then choose "I want to apply" to start (or reopen) your application.{' '}
        <Link to="/applications">Back to applications</Link>
      </p>
    </div>
  )
}

// A Link between shared screens that carries the current workflow context.
export function WorkflowLink({ to, ...rest }: Omit<LinkProps, 'to'> & { to: string }) {
  const { link } = useWorkflowContext()
  return <Link to={link(to)} {...rest} />
}
