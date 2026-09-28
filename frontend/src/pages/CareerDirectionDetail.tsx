import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api,
  type CareerDirection,
  type CareerDirectionCandidate,
  type CareerDirectionDiscoveryRun,
  type PreferenceDimension,
  type Role,
} from '../lib/api'

const STATE_LABEL: Record<CareerDirection['state'], string> = {
  exploring: 'Exploring', selected: 'Selected', archived: 'Archived',
}

function DesiredProperties({ direction, labels }: { direction: CareerDirection; labels: Record<string, string> }) {
  if (direction.dimensions.length === 0) {
    return <p className="muted" style={{ fontSize: 13 }}>No desired properties recorded yet.</p>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {direction.dimensions.map((d) => (
        <div key={d.dimension_code} style={{ fontSize: 13 }}>
          <strong>{labels[d.dimension_code] ?? d.dimension_code}</strong> — {d.desired_direction} (importance {d.importance}/3)
          {d.note && <div className="secondary">{d.note}</div>}
        </div>
      ))}
    </div>
  )
}

function ConstraintsSummary({ direction }: { direction: CareerDirection }) {
  const c = direction.constraints
  const rows: { label: string; value: string }[] = []
  if (c.locations.length) rows.push({ label: 'Geography', value: c.locations.join(', ') })
  if (c.remote_types.length) rows.push({ label: 'Working mode', value: c.remote_types.join(', ') })
  if (c.employment_types.length) rows.push({ label: 'Employment type', value: c.employment_types.join(', ') })
  if (c.seniority_levels.length) rows.push({ label: 'Seniority', value: c.seniority_levels.join(', ') })
  if (c.compensation_floor) {
    const f = c.compensation_floor
    rows.push({
      label: 'Compensation floor',
      value: `${f.amount.toLocaleString()} ${f.currency} (${f.pay_period}, ${f.employment_basis})${f.hard ? ' — hard floor' : ' — soft preference'}`,
    })
  }
  if (c.other.length) rows.push({ label: 'Other context', value: c.other.join('; ') })
  if (rows.length === 0) return <p className="muted" style={{ fontSize: 13 }}>No practical constraints recorded.</p>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {rows.map((r) => (
        <div key={r.label} style={{ fontSize: 13 }}>
          <strong>{r.label}:</strong> {r.value}
        </div>
      ))}
    </div>
  )
}

function RationaleSection({ direction }: { direction: CareerDirection }) {
  const [run, setRun] = useState<CareerDirectionDiscoveryRun | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (direction.origin !== 'ai_adopted' || !direction.source_discovery_run_id) return
    let current = true
    api.getCareerDirectionDiscoveryRun(direction.source_discovery_run_id)
      .then((r) => { if (current) setRun(r) })
      .catch((e) => { if (current) setError(e instanceof Error ? e.message : String(e)) })
    return () => { current = false }
  }, [direction.origin, direction.source_discovery_run_id])

  if (direction.origin !== 'ai_adopted') {
    return <p className="secondary" style={{ fontSize: 13 }}>This direction was defined manually by you.</p>
  }
  if (error) return <p role="alert" style={{ fontSize: 13 }}>Could not load the discovery run this direction came from: {error}</p>
  if (!run) return <p className="muted" style={{ fontSize: 13 }}>Loading provenance…</p>

  const candidate: CareerDirectionCandidate | undefined = run.output?.candidates.find((c) => c.id === direction.source_candidate_id)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <p className="secondary" style={{ fontSize: 13, margin: 0 }}>
        Adopted from an AI-generated hypothesis (discovery run {run.created_at.slice(0, 10)}). This was one reviewable hypothesis among
        several — never a ranked recommendation.
      </p>
      {!candidate && (
        <p className="muted" style={{ fontSize: 13 }}>
          The original candidate detail is no longer available in this run's record; only the direction's own saved dimensions/constraints
          remain.
        </p>
      )}
      {candidate && (
        <>
          <p style={{ fontSize: 13, margin: 0 }}>{candidate.summary.text}</p>
          {candidate.market_basis.length > 0 && (
            <div>
              <strong style={{ fontSize: 12 }}>Market basis</strong>
              <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
                {candidate.market_basis.map((m, i) => <li key={i}>{m.text}</li>)}
              </ul>
            </div>
          )}
          {candidate.person_basis.length > 0 && (
            <div>
              <strong style={{ fontSize: 12 }}>Where your evidence overlaps</strong>
              <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
                {candidate.person_basis.map((m, i) => <li key={i}>{m.text}</li>)}
              </ul>
            </div>
          )}
          <div>
            <strong style={{ fontSize: 12 }}>Compensation context</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0' }}>
              {candidate.compensation_context ? candidate.compensation_context.text : 'No comparable compensation evidence was available.'}
            </p>
          </div>
          {candidate.tradeoffs.length > 0 && (
            <div>
              <strong style={{ fontSize: 12 }}>Trade-offs</strong>
              <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
                {candidate.tradeoffs.map((t, i) => <li key={i}>{t.text}</li>)}
              </ul>
            </div>
          )}
          {candidate.unknowns.length > 0 && (
            <div>
              <strong style={{ fontSize: 12 }}>Unknowns</strong>
              <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
                {candidate.unknowns.map((u, i) => <li key={i}>{u.text}</li>)}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function TargetSection({ direction, onLinked }: { direction: CareerDirection; onLinked: () => void }) {
  const [targets, setTargets] = useState<Role[] | null>(null)
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadTargets = () => {
    if (targets) return
    api.listTargets().then(setTargets).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }

  const linkExisting = async () => {
    if (!selected) return
    setBusy(true); setError(null)
    try {
      await api.updateCareerDirection(direction.id, { target_role_instance_id: selected })
      onLinked()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  if (direction.target) {
    return (
      <div>
        <p style={{ fontSize: 13, margin: '0 0 8px' }}>
          <strong>{direction.target.title}</strong>{direction.target.organisation ? ` — ${direction.target.organisation}` : ''}
        </p>
        <div className="actions">
          <Link to={`/targets/${direction.target.id}`} className="button">Open Target</Link>
          <Link to={`/pathways/${direction.target.id}`} className="button">Pathways</Link>
        </div>
      </div>
    )
  }

  return (
    <div>
      <p className="secondary" style={{ fontSize: 13, marginTop: 0 }}>This is still a direction, not yet a concrete Target.</p>
      <div className="actions" style={{ marginBottom: 10 }}>
        <Link to={`/targets/new?direction_id=${direction.id}`} className="button primary">Create a Target from this direction</Link>
      </div>
      <details onToggle={loadTargets}>
        <summary className="muted" style={{ fontSize: 13, cursor: 'pointer' }}>Link an existing Target instead</summary>
        {error && <p role="alert" style={{ fontSize: 12 }}>{error}</p>}
        {!targets ? (
          <p className="muted" style={{ fontSize: 12 }}>Loading targets…</p>
        ) : targets.length === 0 ? (
          <p className="muted" style={{ fontSize: 12 }}>No existing targets to link.</p>
        ) : (
          <div className="actions" style={{ marginTop: 8 }}>
            <select value={selected} onChange={(e) => setSelected(e.target.value)}>
              <option value="">Choose a target…</option>
              {targets.map((t) => <option key={t.id} value={t.id}>{t.title}{t.organisation ? ` — ${t.organisation}` : ''}</option>)}
            </select>
            <button disabled={!selected || busy} onClick={linkExisting}>{busy ? 'Linking…' : 'Link Target'}</button>
          </div>
        )}
      </details>
    </div>
  )
}

export default function CareerDirectionDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [direction, setDirection] = useState<CareerDirection | null>(null)
  const [dimensionLabels, setDimensionLabels] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    if (!id) return
    return api.getCareerDirection(id)
      .then(setDirection)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    setLoading(true)
    Promise.all([load(), api.listPreferenceDimensions().then((dims: PreferenceDimension[]) => {
      setDimensionLabels(Object.fromEntries(dims.map((d) => [d.code, d.label])))
    })]).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const runAction = async (action: () => Promise<CareerDirection>) => {
    setBusy(true); setActionError(null)
    try {
      const updated = await action()
      setDirection(updated)
    } catch (e) { setActionError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  if (loading) return <p className="muted">Loading…</p>
  if (error) return <p role="alert">{error}</p>
  if (!direction) return null

  return (
    <div>
      <Link to="/future">← Back to Explore my future</Link>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>{direction.name}</h1>
          {direction.summary && <p className="secondary" style={{ marginTop: 4, maxWidth: 640 }}>{direction.summary}</p>}
          <p className="muted" style={{ fontSize: 12 }}>
            {STATE_LABEL[direction.state]} · {direction.origin === 'ai_adopted' ? 'AI-adopted, then reviewed and saved by you' : 'User-defined'} ·
            {' '}created {direction.created_at.slice(0, 10)}
          </p>
        </div>
        <div className="actions">
          {direction.state === 'exploring' && (
            <button className="primary" disabled={busy} onClick={() => runAction(() => api.selectCareerDirection(direction.id))}>
              Select as my current direction
            </button>
          )}
          {direction.state === 'selected' && <span className="button primary" aria-current="true">Current direction</span>}
          {direction.state !== 'archived' ? (
            <button disabled={busy} onClick={() => runAction(() => api.archiveCareerDirection(direction.id))}>Archive</button>
          ) : (
            <button disabled={busy} onClick={() => runAction(() => api.reopenCareerDirection(direction.id))}>Reopen</button>
          )}
        </div>
      </div>
      {actionError && <p role="alert">{actionError}</p>}

      <div className="home-grid" style={{ marginTop: 16 }}>
        <section className="card" aria-labelledby="properties-h">
          <h2 id="properties-h" style={{ fontSize: 16, marginTop: 0 }}>Desired properties</h2>
          <DesiredProperties direction={direction} labels={dimensionLabels} />
        </section>

        <section className="card" aria-labelledby="constraints-h">
          <h2 id="constraints-h" style={{ fontSize: 16, marginTop: 0 }}>Practical constraints</h2>
          <ConstraintsSummary direction={direction} />
        </section>

        <section className="card" aria-labelledby="why-h">
          <h2 id="why-h" style={{ fontSize: 16, marginTop: 0 }}>Why this direction exists</h2>
          <RationaleSection direction={direction} />
        </section>

        <section className="card" aria-labelledby="target-h">
          <h2 id="target-h" style={{ fontSize: 16, marginTop: 0 }}>Concrete Target</h2>
          <TargetSection direction={direction} onLinked={() => { load(); }} />
        </section>
      </div>

      {direction.archetype && (
        <section className="card" style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Anchored market archetype</h2>
          <p style={{ fontSize: 13 }}>
            <strong>{direction.archetype.canonical_name}</strong>
            {direction.archetype_evidence && (
              <span className="muted">
                {' '}— {direction.archetype_evidence.assigned_postings} assigned postings, {direction.archetype_evidence.demand_capabilities} demand
                signals, {direction.archetype_evidence.compensation_buckets} compensation benchmarks.
              </span>
            )}
          </p>
        </section>
      )}

      <p className="muted" style={{ fontSize: 12, marginTop: 16 }}>
        <button type="button" onClick={() => navigate(-1)} style={{ border: 'none', background: 'none', padding: 0, textDecoration: 'underline', cursor: 'pointer' }}>
          Back
        </button>
      </p>
    </div>
  )
}
