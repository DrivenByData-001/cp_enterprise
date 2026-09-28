import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type Cockpit,
  type CockpitLearningItem,
  type CockpitOpportunityItem,
  type CockpitEvidenceStatus,
  type ProgressCheckpointHistoryItem,
  type ProgressDiff,
} from '../lib/api'

// Phase 9: Career Cockpit (docs/40). One composed GET backs the whole page
// (build §16/§38) — every section below reads straight off that single
// response; the only follow-up requests are the explicit checkpoint POST
// (build §5) and promotion POSTs (routes/applications.py), never anything
// triggered by this page loading or re-rendering.

const STATUS_LABEL: Record<CockpitEvidenceStatus, string> = {
  evidenced: 'Evidenced',
  partial: 'Partial',
  user_asserted: 'Self-asserted',
  not_found: 'Not found',
}

const STATUS_COLOR: Record<CockpitEvidenceStatus, string> = {
  evidenced: 'var(--good)',
  partial: 'var(--series-1)',
  user_asserted: 'var(--warning)',
  not_found: 'var(--text-muted)',
}

const QUEUE_STATUS_LABEL: Record<string, string> = {
  not_queued: 'Not sent to Profile360',
  queued_pending: 'Queued for Profile360 review',
  processed_by_profile360: 'Processed by Profile360',
  source_changed_since_queue: 'Changed since queued — requeue available',
}

function formatDate(value: string | null | undefined): string {
  if (!value) return 'unknown date'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString()
}

export default function Home() {
  const [cockpit, setCockpit] = useState<Cockpit | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [checkpointBusy, setCheckpointBusy] = useState(false)
  const [checkpointError, setCheckpointError] = useState<string | null>(null)
  const [checkpointLabel, setCheckpointLabel] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)

  const load = useCallback(() => {
    let current = true
    setLoading(true)
    setError(null)
    api
      .getCockpit()
      .then((c) => { if (current) setCockpit(c) })
      .catch((e) => { if (current) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [])

  useEffect(() => load(), [load])

  const recordCheckpoint = async () => {
    setCheckpointBusy(true)
    setCheckpointError(null)
    try {
      await api.recordProgressCheckpoint(checkpointLabel.trim() || undefined)
      setCheckpointLabel('')
      load()
    } catch (e) {
      setCheckpointError(e instanceof Error ? e.message : String(e))
    } finally {
      setCheckpointBusy(false)
    }
  }

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Career cockpit</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 680 }}>
        Where you're trying to go, what's happening around that direction, what you're actively pursuing, and
        whether your evidence position has changed.
      </p>

      {loading && !cockpit && <p className="muted">Loading…</p>}
      {error && !cockpit && <p role="alert">Could not load your Career Cockpit: {error}</p>}

      {cockpit && (
        <>
          <DirectionSection cockpit={cockpit} />
          <TargetProgressSection
            cockpit={cockpit}
            checkpointBusy={checkpointBusy}
            checkpointError={checkpointError}
            checkpointLabel={checkpointLabel}
            setCheckpointLabel={setCheckpointLabel}
            onRecordCheckpoint={recordCheckpoint}
            historyOpen={historyOpen}
            setHistoryOpen={setHistoryOpen}
          />
          <OpportunitiesSection cockpit={cockpit} />
          <ApplicationsSection cockpit={cockpit} />
          <LearningSection cockpit={cockpit} />
          <MarketContextSection cockpit={cockpit} />
          <NextActionsSection cockpit={cockpit} />
        </>
      )}
    </div>
  )
}

// --- Current direction (build §17/§28/§29) ----------------------------------

function DirectionSection({ cockpit }: { cockpit: Cockpit }) {
  const section = cockpit.direction

  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-direction-h">
      <h2 id="home-direction-h" style={{ fontSize: 16, marginTop: 0 }}>Current direction</h2>
      {section.state === 'unavailable' && <p role="alert" style={{ fontSize: 13 }}>Could not load your career direction: {section.reason}</p>}
      {section.state === 'no_direction' && (
        <>
          <p style={{ fontWeight: 600, margin: '4px 0' }}>Define or select a Career Direction</p>
          <p className="secondary" style={{ fontSize: 13 }}>
            Explore existing targets, preferences and pathways, or design a new direction from your priorities.
          </p>
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to="/future" className="button primary">Explore my future</Link>
            <Link to="/targets">View saved targets</Link>
          </div>
        </>
      )}
      {(section.state === 'no_target' || section.state === 'with_target') && (
        <>
          <p style={{ fontWeight: 600, margin: '4px 0' }}>{section.direction.name}</p>
          {section.direction.summary && <p className="secondary" style={{ fontSize: 13 }}>{section.direction.summary}</p>}
          <p className="secondary" style={{ fontSize: 13 }}>
            {section.target ? `Target: ${section.target.title}` : 'No concrete Target linked yet.'}
          </p>
          {section.direction.top_dimensions.length > 0 && (
            <ul style={{ margin: '8px 0', paddingLeft: 18, fontSize: 13 }} className="secondary">
              {section.direction.top_dimensions.map((d) => (
                <li key={d.dimension_code}>
                  {d.dimension_code} ({d.desired_direction}, importance {d.importance})
                </li>
              ))}
            </ul>
          )}
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to={`/future/directions/${section.direction.id}`} className="button primary">Open direction</Link>
            {section.target ? (
              <Link to={`/pathways/${section.target.id}`}>View Pathways</Link>
            ) : (
              <Link to={`/future/directions/${section.direction.id}`}>Create or link a concrete Target</Link>
            )}
          </div>
        </>
      )}
    </section>
  )
}

// --- Progress toward Target (build §18/§28/§30/§31) -------------------------

function requirementLine(item: { canonical_name: string; requirement_type: string | null; previous_status: CockpitEvidenceStatus; current_status: CockpitEvidenceStatus }) {
  return `${item.canonical_name} — ${STATUS_LABEL[item.previous_status]} → ${STATUS_LABEL[item.current_status]}`
}

function DiffSummary({ diff, since }: { diff: ProgressDiff; since: string | null }) {
  const defChanges = diff.target_definition_changed
  const hasDefChanges = defChanges.requirements_added.length > 0 || defChanges.requirements_removed.length > 0 || defChanges.requirement_type_changed.length > 0
  const nothingChanged = diff.evidence_strengthened.length === 0 && diff.assertion_added.length === 0 && diff.evidence_weakened.length === 0 && !hasDefChanges

  return (
    <div style={{ marginTop: 12 }}>
      <p style={{ fontWeight: 600, margin: '4px 0' }}>Changes since {formatDate(since)}</p>
      {nothingChanged && <p className="muted" style={{ fontSize: 13 }}>No evidence or Target-definition changes since your last checkpoint.</p>}

      {diff.evidence_strengthened.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--good)' }}>
            {diff.evidence_strengthened.length} Target requirement{diff.evidence_strengthened.length === 1 ? '' : 's'} {diff.evidence_strengthened.length === 1 ? 'has' : 'have'} stronger evidence than at your last checkpoint.
          </p>
          <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 13 }}>
            {diff.evidence_strengthened.map((e) => <li key={e.concept_id}>{requirementLine(e)}</li>)}
          </ul>
        </div>
      )}

      {diff.assertion_added.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--warning)' }}>
            {diff.assertion_added.length} self-assertion{diff.assertion_added.length === 1 ? '' : 's'} added — still not accepted Profile360 evidence.
          </p>
          <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 13 }}>
            {diff.assertion_added.map((e) => <li key={e.concept_id}>{requirementLine(e)}</li>)}
          </ul>
        </div>
      )}

      {diff.evidence_weakened.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--critical)' }}>
            {diff.evidence_weakened.length} requirement{diff.evidence_weakened.length === 1 ? '' : 's'} weakened or no longer supported.
          </p>
          <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 13 }}>
            {diff.evidence_weakened.map((e) => <li key={e.concept_id}>{requirementLine(e)}</li>)}
          </ul>
        </div>
      )}

      {hasDefChanges && (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: 13, fontWeight: 600 }}>The Target definition changed since your last checkpoint.</p>
          <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 13 }} className="secondary">
            {defChanges.requirements_added.map((r) => <li key={`add-${r.concept_id}`}>Added: {r.canonical_name} ({r.requirement_type})</li>)}
            {defChanges.requirements_removed.map((r) => <li key={`rem-${r.concept_id}`}>Removed: {r.canonical_name} ({r.requirement_type})</li>)}
            {defChanges.requirement_type_changed.map((r) => (
              <li key={`type-${r.concept_id}`}>{r.canonical_name}: {r.previous_requirement_type} → {r.current_requirement_type}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function CheckpointHistoryList({ history }: { history: ProgressCheckpointHistoryItem[] }) {
  if (history.length === 0) return <p className="muted" style={{ fontSize: 13 }}>No checkpoints recorded yet.</p>
  return (
    <ul style={{ margin: '8px 0', paddingLeft: 18, fontSize: 13 }}>
      {history.map((h) => (
        <li key={h.id}>
          {formatDate(h.created_at)} — {h.checkpoint_type}{h.label ? ` (“${h.label}”)` : ''} — {h.target_title ?? 'Target'}
          {typeof h.counts.evidenced === 'number' && (
            <span className="secondary"> · {h.counts.evidenced} evidenced / {h.counts.requirements_total} total</span>
          )}
        </li>
      ))}
    </ul>
  )
}

function TargetProgressSection({
  cockpit, checkpointBusy, checkpointError, checkpointLabel, setCheckpointLabel, onRecordCheckpoint, historyOpen, setHistoryOpen,
}: {
  cockpit: Cockpit
  checkpointBusy: boolean
  checkpointError: string | null
  checkpointLabel: string
  setCheckpointLabel: (v: string) => void
  onRecordCheckpoint: () => void
  historyOpen: boolean
  setHistoryOpen: (v: boolean) => void
}) {
  const section = cockpit.target_progress

  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-progress-h">
      <h2 id="home-progress-h" style={{ fontSize: 16, marginTop: 0 }}>Progress toward Target</h2>

      {section.state === 'unavailable' && <p role="alert" style={{ fontSize: 13 }}>Could not load Target progress: {section.reason}</p>}
      {section.state === 'no_direction' && <p className="muted" style={{ fontSize: 13 }}>Select a Career Direction to see Target progress here.</p>}
      {section.state === 'no_target' && (
        <>
          <p className="muted" style={{ fontSize: 13 }}>“{section.direction.name}” has no linked Target yet.</p>
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to={`/future/directions/${section.direction.id}`} className="button primary">Create or link a concrete Target</Link>
          </div>
        </>
      )}

      {(section.state === 'no_checkpoint' || section.state === 'available') && (
        <>
          {!(section.current.review.target_review_complete && section.current.review.target_mapping_complete) && (
            <p className="secondary" style={{ fontSize: 13 }}>
              Requirement review or Target mapping is incomplete — the counts below are structural facts, not a final comparison.
            </p>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, margin: '8px 0' }}>
            {(['evidenced', 'partial', 'user_asserted', 'not_found'] as const).map((status) => (
              <div key={status}>
                <div style={{ fontSize: 20, fontWeight: 700, color: STATUS_COLOR[status] }}>{section.current.counts[status]}</div>
                <div className="secondary" style={{ fontSize: 12 }}>{STATUS_LABEL[status]}</div>
              </div>
            ))}
          </div>
          <p className="secondary" style={{ fontSize: 13 }}>
            {section.current.counts.required_total} required requirement(s), {section.current.counts.blocking_required} blocking, {section.current.counts.unverified_required} unverified.
          </p>

          {section.state === 'no_checkpoint' && (
            <div style={{ marginTop: 12 }}>
              <p className="secondary" style={{ fontSize: 13 }}>
                This records today's derived Target evidence states so future changes can be compared. It does not create evidence or change your profile.
              </p>
              <div className="actions">
                <button type="button" className="button primary" disabled={checkpointBusy} onClick={onRecordCheckpoint}>
                  {checkpointBusy ? 'Recording…' : 'Record progress baseline'}
                </button>
              </div>
            </div>
          )}

          {section.state === 'available' && section.diff && (
            <DiffSummary diff={section.diff} since={section.checkpoint?.created_at ?? null} />
          )}

          {section.state === 'available' && (
            <div style={{ marginTop: 12 }}>
              <label htmlFor="checkpoint-label" className="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                Optional label
              </label>
              <input
                id="checkpoint-label"
                type="text"
                value={checkpointLabel}
                onChange={(e) => setCheckpointLabel(e.target.value)}
                placeholder="e.g. After Q3 applications"
                style={{ marginBottom: 8 }}
              />
              <div className="actions">
                <button type="button" className="button primary" disabled={checkpointBusy} onClick={onRecordCheckpoint}>
                  {checkpointBusy ? 'Recording…' : 'Record new checkpoint'}
                </button>
              </div>
            </div>
          )}
          {checkpointError && <p role="alert" style={{ fontSize: 13 }}>{checkpointError}</p>}

          <div className="actions" style={{ marginTop: 12 }}>
            <Link to={`/pathways/${section.current.target.id}`}>View Pathways</Link>
            <Link to={`/targets/${section.current.target.id}`}>Review Target evidence</Link>
            <button type="button" onClick={() => setHistoryOpen(!historyOpen)} style={{ background: 'none', border: 'none', color: 'inherit', textDecoration: 'underline', cursor: 'pointer', padding: 0 }}>
              {historyOpen ? 'Hide checkpoint history' : 'Show checkpoint history'}
            </button>
          </div>
          {historyOpen && <CheckpointHistoryList history={section.history} />}
        </>
      )}
    </section>
  )
}

// --- Opportunities around this direction (build §19/§20/§28/§29) -----------

function OpportunitiesSection({ cockpit }: { cockpit: Cockpit }) {
  const section = cockpit.opportunities

  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-opportunities-h">
      <h2 id="home-opportunities-h" style={{ fontSize: 16, marginTop: 0 }}>Opportunities around this direction</h2>
      {section.state === 'unavailable' && <p role="alert" style={{ fontSize: 13 }}>Could not load recent opportunities: {section.reason}</p>}
      {section.state === 'available' && section.items.length === 0 && <p className="muted" style={{ fontSize: 13 }}>No current roles captured yet.</p>}
      {section.state === 'available' && section.items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, margin: '8px 0' }}>
          {section.items.map((item: CockpitOpportunityItem) => (
            <div key={item.role_instance_id}>
              <Link to={`/roles/${item.role_instance_id}`} style={{ textDecoration: 'none' }}>
                <strong>{item.title}</strong>
              </Link>
              <div className="secondary" style={{ fontSize: 12 }}>
                {item.organisation ?? 'Unknown org'}
                {item.location ? ` · ${item.location}` : ''}
                {item.posting_date ? ` · ${item.posting_date}` : ''}
                {item.has_application && ' · Application in progress'}
              </div>
              {item.relationship && (
                <div style={{ fontSize: 12 }} title={item.relationship.reason}>
                  {item.relationship.label}
                  {item.target_gaps_involved.length > 0 && ` — involves ${item.target_gaps_involved.length} target gap(s)`}
                </div>
              )}
              {item.review_caveat && <div className="muted" style={{ fontSize: 11 }}>{item.review_caveat}</div>}
            </div>
          ))}
        </div>
      )}
      <div className="actions" style={{ marginTop: 12 }}>
        <Link to="/opportunities?period=current" className="button primary">View current opportunities</Link>
        <Link to="/import">Add posting</Link>
      </div>
    </section>
  )
}

// --- Applications in motion (build §21/§22/§28) -----------------------------

function ApplicationsSection({ cockpit }: { cockpit: Cockpit }) {
  const section = cockpit.applications

  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-applications-h">
      <h2 id="home-applications-h" style={{ fontSize: 16, marginTop: 0 }}>Applications in motion</h2>
      {section.state === 'unavailable' && <p role="alert" style={{ fontSize: 13 }}>Could not load applications: {section.reason}</p>}
      {section.state === 'available' && (
        <>
          {section.active_count === 0 ? (
            <>
              <p style={{ fontWeight: 600, margin: '4px 0' }}>No applications yet</p>
              <p className="secondary" style={{ fontSize: 13 }}>Choose an opportunity when you decide you want to pursue it.</p>
            </>
          ) : (
            <>
              <p className="secondary" style={{ fontSize: 13 }}>{section.active_count} active application(s).</p>
              {section.next_interview && (
                <p style={{ fontSize: 13 }}>
                  Next interview: <strong>{section.next_interview.role_title}</strong> — {formatDate(section.next_interview.event_at)}
                </p>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '8px 0' }}>
                {section.active_items.map((a) => (
                  <Link key={a.id} to={`/applications/${a.id}`} style={{ textDecoration: 'none' }}>
                    <strong>{a.role.title ?? 'Untitled role'}</strong>
                    <div className="secondary" style={{ fontSize: 12 }}>{a.role.organisation ?? 'Unknown org'} · {a.status}</div>
                  </Link>
                ))}
              </div>
            </>
          )}
          {section.recent_outcomes.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <p style={{ fontSize: 13, fontWeight: 600 }}>Recent outcomes</p>
              <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 13 }} className="secondary">
                {section.recent_outcomes.map((o) => (
                  <li key={o.id}>{o.event_type} — {o.role.title} — {formatDate(o.event_at)}{o.has_notes ? '' : ' (no notes)'}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <div className="actions" style={{ marginTop: 12 }}>
        <Link to="/applications" className="button primary">View applications</Link>
        <Link to="/opportunities">Browse opportunities</Link>
      </div>
    </section>
  )
}

// --- Learning & evidence (build §23/§28) ------------------------------------

function LearningItemRow({ item }: { item: CockpitLearningItem }) {
  return (
    <div>
      <div className="secondary" style={{ fontSize: 12 }}>
        {item.role.title ?? 'Untitled role'} · {formatDate(item.date)} · {item.source_type === 'note' ? item.note_type : item.event_type}
      </div>
      <p style={{ margin: '2px 0', fontSize: 13 }}>{item.text_preview}</p>
      <div style={{ fontSize: 12 }}>
        {QUEUE_STATUS_LABEL[item.queue_status] ?? item.queue_status}
        {item.is_current_target_requirement && item.concept_canonical_name && (
          <span className="secondary"> · relates to Target requirement “{item.concept_canonical_name}” ({STATUS_LABEL[item.current_target_evidence_status ?? 'not_found']})</span>
        )}
      </div>
    </div>
  )
}

function LearningSection({ cockpit }: { cockpit: Cockpit }) {
  const section = cockpit.learning

  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-learning-h">
      <h2 id="home-learning-h" style={{ fontSize: 16, marginTop: 0 }}>Learning &amp; evidence</h2>
      {section.state === 'unavailable' && <p role="alert" style={{ fontSize: 13 }}>Could not load recent learning items: {section.reason}</p>}
      {section.state === 'available' && section.items.length === 0 && (
        <p className="muted" style={{ fontSize: 13 }}>Application reflections and evidence examples will appear here when you record them.</p>
      )}
      {section.state === 'available' && section.items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, margin: '8px 0' }}>
          {section.items.map((item) => <LearningItemRow key={`${item.source_type}-${item.source_id}`} item={item} />)}
        </div>
      )}
      <div className="actions" style={{ marginTop: 12 }}>
        <Link to="/applications">Review in Applications</Link>
      </div>
    </section>
  )
}

// --- Market evidence behind this direction (build §25/§28) ------------------

function MarketContextSection({ cockpit }: { cockpit: Cockpit }) {
  const section = cockpit.market_context

  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-market-h">
      <h2 id="home-market-h" style={{ fontSize: 16, marginTop: 0 }}>Market evidence behind this direction</h2>
      {section.state === 'unavailable' && <p role="alert" style={{ fontSize: 13 }}>Could not load market context: {section.reason}</p>}
      {section.state === 'no_direction' && <p className="muted" style={{ fontSize: 13 }}>Select a Career Direction to see market context here.</p>}
      {section.state === 'no_archetype' && <p className="muted" style={{ fontSize: 13 }}>{section.reason ?? 'No archetype is assigned yet.'}</p>}
      {section.state === 'available' && (
        <>
          <p style={{ fontWeight: 600, margin: '4px 0' }}>{section.canonical_name}</p>
          <p className="secondary" style={{ fontSize: 13 }}>
            {section.assigned_posting_count} supporting posting(s), {section.reviewed_requirement_posting_count} with reviewed requirements
            {section.latest_known_posting_date ? `, latest ${formatDate(section.latest_known_posting_date)}` : ''}.
            {section.distinct_country_count > 0 && ` Represented across ${section.distinct_country_count} countries.`}
          </p>
          <p className="secondary" style={{ fontSize: 13 }}>
            Evidence depth: {section.evidence_depth.state} — {section.evidence_depth.reason}
          </p>
          <p className="secondary" style={{ fontSize: 13 }}>
            Compensation evidence: {section.compensation_benchmark_available ? 'available' : 'not yet available'}. Economics: {section.economics_freshness.state}.
          </p>
          <p className="muted" style={{ fontSize: 12 }}>{section.representativeness.reason}</p>
        </>
      )}
      <div className="actions" style={{ marginTop: 12 }}>
        <Link to="/market/coverage">Open full Market Coverage</Link>
      </div>
    </section>
  )
}

// --- Useful next actions (build §26/§27/§28) --------------------------------

function NextActionsSection({ cockpit }: { cockpit: Cockpit }) {
  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="home-next-h">
      <h2 id="home-next-h" style={{ fontSize: 16, marginTop: 0 }}>Useful next actions</h2>
      <ol className="action-list">
        {cockpit.next_actions.map((a) => (
          <li key={a.code}>
            <Link to={a.href}>{a.title}</Link>
            <div className="secondary" style={{ fontSize: 13 }}>{a.reason}</div>
          </li>
        ))}
      </ol>
    </section>
  )
}
