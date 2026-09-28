import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  type ApplicationArtifact,
  type ApplicationArtifactsResponse,
  type ApplicationDetail,
  type ApplicationEvent,
  type ApplicationEventInput,
  type ApplicationEventType,
  type ApplicationEvidence,
  type ApplicationEvidenceItem,
  type ApplicationLearningState,
  type ApplicationLearningStateItem,
  type ApplicationNote,
  type ApplicationNoteType,
  type LearningQueueStatus,
  type ApplicationStatus,
  type ArtifactContent,
  type ArtifactType,
  type ComparisonStatus,
  type CoverLetterContent,
  type CVContent,
  type GapConcept,
  type InterviewGenerationContextSummary,
  type InterviewPrepContent,
  type OpportunityAlignment,
  type PositioningContent,
  type SourceCategory,
  type SourceManifestEntry,
  type SourcedText,
  type SupportingStatementContent,
} from '../lib/api'
import { AlignmentDecisionTile } from '../components/opportunity/AlignmentSection'

// Phase 3 (docs/34 §9): a guided preparation workspace, not another
// analytics dump. Every structural fact here is read from the existing
// comparison/capability engines via the application evidence pack — nothing
// on this page scores, ranks, or judges the application, and nothing here
// is an AI call. Application notes are always shown as clearly distinct
// from accepted Profile360 evidence (build §6).

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  evidenced: 'Evidenced',
  partial: 'Partially evidenced',
  user_asserted: 'User asserted',
  not_found: 'No evidence found',
}

const STATUS_COLOR: Record<ComparisonStatus, string> = {
  evidenced: 'var(--good)',
  partial: 'var(--warning)',
  user_asserted: 'var(--series-1)',
  not_found: 'var(--muted, #888)',
}

const APPLICATION_STATUSES: { value: ApplicationStatus; label: string }[] = [
  { value: 'preparing', label: 'Preparing' },
  { value: 'ready', label: 'Ready' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'interviewing', label: 'Interviewing' },
  { value: 'closed', label: 'Closed' },
  { value: 'withdrawn', label: 'Withdrawn' },
]

function pluralize(n: number, noun: string, plural = `${noun}s`): string {
  return `${n} ${n === 1 ? noun : plural}`
}

function formatDateTime(value: string): string {
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

// --- Phase 4: grounded Application package generation (docs/35) -----------

const ARTIFACT_LABELS: Record<ArtifactType, string> = {
  positioning: 'Positioning',
  cv: 'CV',
  cover_letter: 'Cover letter',
  supporting_statement: 'Supporting statement',
  interview_prep: 'Interview prep',
}

// Phase 5 (docs/36): lifecycle event types/labels — a plain user-recorded
// timeline, never an AI judgment and never a status derivation.
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

function formatEventAt(value: string): string {
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

function upcomingInterview(events: ApplicationEvent[]): ApplicationEvent | null {
  const now = Date.now()
  const upcoming = events.filter((e) => e.event_type === 'interview_scheduled' && new Date(e.event_at).getTime() >= now)
  if (upcoming.length === 0) return null
  return upcoming.reduce((soonest, e) => (new Date(e.event_at).getTime() < new Date(soonest.event_at).getTime() ? e : soonest))
}

function toLocalDateTimeInput(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

const CATEGORY_LABELS: Record<SourceCategory, string> = {
  canonical_evidence: 'Accepted profile evidence',
  partial_evidence: 'Partial evidence',
  user_supplied_context: 'Application-only user input',
  role_side_context: 'Role-side requirement/context',
  strategy: 'Current positioning strategy',
}

function formatDate(value: string | null): string {
  if (!value) return 'present'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short' })
}

/** The strongest currently-available person-side evidence line for one
 * requirement, reusing the capability engine's own generated explanation
 * where one exists (never re-deriving that judgment here) and falling back
 * to a plain summary for an atomic (non-capability) concept. */
function personSideSummary(item: ApplicationEvidenceItem): string {
  const coverage = item.person_side.coverage
  if (coverage) return coverage.trace.status_reason.message
  if (item.person_side.assertion) {
    return item.person_side.assertion.note
      ? `User asserted, without supporting evidence: “${item.person_side.assertion.note}”`
      : 'User asserted, without supporting evidence.'
  }
  const displays = item.person_side.mappings.map((m) => m.display).filter(Boolean)
  if (displays.length > 0) return displays.join('; ')
  return 'No accepted profile evidence found.'
}

// --- Phase 9: learning-loop promotion (docs/40) -----------------------------
//
// The explicit, user-triggered path from an application-local note/event
// into Profile360's own review queue (app/application_learning.py). Careful
// copy throughout: "send for review", never "add to my evidence" — review
// hasn't happened yet, and queueing never itself changes any capability or
// Target evidence status (build §14/§34).

const QUEUE_STATUS_LABEL: Record<LearningQueueStatus, string> = {
  not_queued: 'Application-only',
  queued_pending: 'Queued for Profile360 review',
  processed_by_profile360: 'Processed by Profile360',
  source_changed_since_queue: 'Changed since queued',
}

function findLearningStatus(
  learningState: ApplicationLearningState | null,
  sourceType: 'note' | 'event',
  sourceId: string,
): ApplicationLearningStateItem | undefined {
  const list = sourceType === 'note' ? learningState?.notes : learningState?.events
  return list?.find((item) => item.source_id === sourceId)
}

function LearningActionRow({
  status,
  busy,
  onPromote,
}: {
  status: LearningQueueStatus | undefined
  busy: boolean
  onPromote: () => void
}) {
  if (!status || status === 'not_queued') {
    return (
      <div className="actions" style={{ marginTop: 6 }}>
        <button type="button" onClick={onPromote} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
          {busy ? 'Sending…' : 'Send to Profile360 for review'}
        </button>
      </div>
    )
  }
  if (status === 'source_changed_since_queue') {
    return (
      <div className="actions" style={{ marginTop: 6, alignItems: 'center' }}>
        <span className="muted" style={{ fontSize: 12 }}>{QUEUE_STATUS_LABEL[status]}</span>
        <button type="button" onClick={onPromote} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
          {busy ? 'Requeuing…' : 'Requeue'}
        </button>
      </div>
    )
  }
  return (
    <div style={{ marginTop: 6 }}>
      <span className="muted" style={{ fontSize: 12 }}>{QUEUE_STATUS_LABEL[status]}</span>
    </div>
  )
}

// --- Notes -------------------------------------------------------------

function NoteComposer({
  conceptId,
  defaultType,
  busy,
  onSubmit,
  onCancel,
}: {
  conceptId?: string
  defaultType: ApplicationNoteType
  busy: boolean
  onSubmit: (text: string) => void
  onCancel: () => void
}) {
  const [text, setText] = useState('')
  return (
    <div className="card" style={{ marginTop: 8, padding: 10 }}>
      <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
        {conceptId ? 'Example for this application' : 'General note'}
      </label>
      <textarea
        rows={3}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={defaultType === 'evidence_example' ? 'Describe an example you could use for this requirement…' : 'Add a note…'}
        style={{ width: '100%' }}
      />
      <div className="actions" style={{ marginTop: 8 }}>
        <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => { if (text.trim()) { onSubmit(text.trim()); setText('') } }}>
          {busy ? 'Saving…' : 'Save note'}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function NoteItem({
  note,
  busy,
  onSave,
  onDelete,
  learningStatus,
  promoteBusy,
  onPromote,
}: {
  note: ApplicationNote
  busy: boolean
  onSave: (text: string) => void
  onDelete: () => void
  learningStatus: LearningQueueStatus | undefined
  promoteBusy: boolean
  onPromote: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(note.note_text)

  if (editing) {
    return (
      <div className="card" style={{ marginTop: 8, padding: 10 }}>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} style={{ width: '100%' }} />
        <div className="actions" style={{ marginTop: 8 }}>
          <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => { onSave(text.trim()); setEditing(false) }}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          <button type="button" onClick={() => { setText(note.note_text); setEditing(false) }} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="card" style={{ marginTop: 8, padding: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
        <div>
          <span
            className="muted"
            style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.3 }}
          >
            {note.note_type === 'evidence_example' ? 'Application-only example' : 'General note'}
          </span>
          <p style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap' }}>{note.note_text}</p>
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button type="button" onClick={() => setEditing(true)} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
            Edit
          </button>
          <button type="button" onClick={onDelete} disabled={busy} style={{ fontSize: 12, padding: '3px 8px', color: 'var(--critical)' }}>
            Delete
          </button>
        </div>
      </div>
      <LearningActionRow status={learningStatus} busy={promoteBusy} onPromote={onPromote} />
    </div>
  )
}

// --- Phase 5: Lifecycle timeline (docs/36) ---------------------------------
//
// A plain, user-recorded timeline — submission, interviews, offers, closure.
// Never an AI call, and recording an event never changes the Application
// status above: the two are independent by design (build §3).

function EventComposer({
  initial,
  defaultType,
  busy,
  onSubmit,
  onCancel,
}: {
  initial?: ApplicationEvent
  defaultType: ApplicationEventType
  busy: boolean
  onSubmit: (input: ApplicationEventInput) => void
  onCancel: () => void
}) {
  const [eventType, setEventType] = useState<ApplicationEventType>(initial?.event_type ?? defaultType)
  const [eventAt, setEventAt] = useState(() => toLocalDateTimeInput(initial?.event_at ?? new Date().toISOString()))
  const [label, setLabel] = useState(initial?.label ?? '')
  const [notes, setNotes] = useState(initial?.notes ?? '')

  return (
    <div className="card" style={{ marginTop: 8, padding: 10 }}>
      <label style={{ display: 'block', fontSize: 12 }}>
        Event type
        <select
          value={eventType}
          onChange={(e) => setEventType(e.target.value as ApplicationEventType)}
          style={{ display: 'block', marginTop: 4 }}
        >
          {(Object.entries(EVENT_TYPE_LABEL) as [ApplicationEventType, string][]).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
        Date/time
        <input
          type="datetime-local"
          value={eventAt}
          onChange={(e) => setEventAt(e.target.value)}
          style={{ display: 'block', marginTop: 4 }}
        />
      </label>
      <label style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
        Label (optional) — stage, format, etc.
        <input
          value={label ?? ''}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. Technical panel, video call"
          style={{ width: '100%' }}
        />
      </label>
      <label style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
        Notes (optional)
        <textarea rows={3} value={notes ?? ''} onChange={(e) => setNotes(e.target.value)} style={{ width: '100%' }} />
      </label>
      <div className="actions" style={{ marginTop: 8 }}>
        <button
          type="button"
          className="primary"
          disabled={busy || !eventAt}
          onClick={() => {
            if (!eventAt) return
            onSubmit({
              event_type: eventType,
              event_at: new Date(eventAt).toISOString(),
              label: label.trim() || undefined,
              notes: notes.trim() || undefined,
            })
          }}
        >
          {busy ? 'Saving…' : 'Save event'}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function EventRow({
  event,
  busy,
  onSave,
  onDelete,
  learningStatus,
  promoteBusy,
  onPromote,
}: {
  event: ApplicationEvent
  busy: boolean
  onSave: (input: ApplicationEventInput) => void
  onDelete: () => void
  learningStatus: LearningQueueStatus | undefined
  promoteBusy: boolean
  onPromote: () => void
}) {
  const [editing, setEditing] = useState(false)
  if (editing) {
    return (
      <EventComposer
        initial={event}
        defaultType={event.event_type}
        busy={busy}
        onSubmit={(input) => {
          onSave(input)
          setEditing(false)
        }}
        onCancel={() => setEditing(false)}
      />
    )
  }
  return (
    <div className="card" style={{ marginTop: 8, padding: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div>
          <strong style={{ fontSize: 13 }}>{EVENT_TYPE_LABEL[event.event_type]}</strong>
          {event.label && <span className="secondary" style={{ fontSize: 13 }}> — {event.label}</span>}
          <div className="muted" style={{ fontSize: 11 }}>
            {formatEventAt(event.event_at)}
          </div>
          {event.notes && <p style={{ margin: '4px 0 0', fontSize: 13, whiteSpace: 'pre-wrap' }}>{event.notes}</p>}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button type="button" onClick={() => setEditing(true)} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
            Edit
          </button>
          <button type="button" onClick={onDelete} disabled={busy} style={{ fontSize: 12, padding: '3px 8px', color: 'var(--critical)' }}>
            Delete
          </button>
        </div>
      </div>
      {event.notes && <LearningActionRow status={learningStatus} busy={promoteBusy} onPromote={onPromote} />}
    </div>
  )
}

function LifecycleSection({
  status,
  events,
  eventsError,
  busy,
  actionError,
  onAdd,
  onSave,
  onDelete,
  learningState,
  promoteBusyKey,
  onPromoteEvent,
}: {
  status: ApplicationStatus
  events: ApplicationEvent[] | null
  eventsError: string | null
  busy: boolean
  actionError: string | null
  onAdd: (input: ApplicationEventInput) => void
  onSave: (eventId: string, input: ApplicationEventInput) => void
  onDelete: (eventId: string) => void
  learningState: ApplicationLearningState | null
  promoteBusyKey: string | null
  onPromoteEvent: (eventId: string) => void
}) {
  const [composing, setComposing] = useState<ApplicationEventType | null>(null)
  const upcoming = events ? upcomingInterview(events) : null

  return (
    <section className="card" aria-labelledby="lifecycle-h">
      <h2 id="lifecycle-h" style={{ fontSize: 18, marginTop: 0 }}>
        Lifecycle
      </h2>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        What happened with this application, recorded by you. This never changes the status above automatically.
      </p>
      <p className="secondary" style={{ fontSize: 13, marginTop: 4 }}>
        Current status: <strong>{APPLICATION_STATUSES.find((s) => s.value === status)?.label ?? status}</strong>
      </p>

      {eventsError && (
        <p role="alert" style={{ color: 'var(--critical)', fontSize: 13 }}>
          Timeline couldn't be loaded: {eventsError}
        </p>
      )}
      {actionError && (
        <p role="alert" style={{ color: 'var(--critical)', fontSize: 13 }}>
          {actionError}
        </p>
      )}

      {events && upcoming && (
        <p role="status" style={{ fontSize: 13, color: 'var(--series-1)', fontWeight: 600 }}>
          Upcoming: {EVENT_TYPE_LABEL[upcoming.event_type]}
          {upcoming.label ? ` — ${upcoming.label}` : ''} on {formatEventAt(upcoming.event_at)}
        </p>
      )}

      {events === null && !eventsError && <p className="muted">Loading timeline…</p>}
      {events && events.length === 0 && <p className="muted">No events recorded yet.</p>}
      {events?.map((event) => (
        <EventRow
          key={event.id}
          event={event}
          busy={busy}
          onSave={(input) => onSave(event.id, input)}
          onDelete={() => onDelete(event.id)}
          learningStatus={findLearningStatus(learningState, 'event', event.id)?.status}
          promoteBusy={promoteBusyKey === `event:${event.id}`}
          onPromote={() => onPromoteEvent(event.id)}
        />
      ))}

      {composing ? (
        <EventComposer
          defaultType={composing}
          busy={busy}
          onSubmit={(input) => {
            onAdd(input)
            setComposing(null)
          }}
          onCancel={() => setComposing(null)}
        />
      ) : (
        <div className="actions" style={{ marginTop: 10, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setComposing('submitted')}>
            Record submission
          </button>
          <button type="button" onClick={() => setComposing('interview_scheduled')}>
            Schedule interview
          </button>
          <button type="button" onClick={() => setComposing('interview_completed')}>
            Record completed interview
          </button>
          <button type="button" onClick={() => setComposing('offer_received')}>
            Record offer
          </button>
          <button type="button" onClick={() => setComposing('rejected')}>
            Record rejection / role closed
          </button>
          <button type="button" onClick={() => setComposing('other')}>
            Add another event
          </button>
        </div>
      )}
    </section>
  )
}

// --- Preparation checks --------------------------------------------------

function CheckRow({ heading, children, tone }: { heading: string; children: React.ReactNode; tone?: 'warning' | 'critical' }) {
  const color = tone === 'critical' ? 'var(--critical)' : tone === 'warning' ? 'var(--warning)' : undefined
  return (
    <div style={{ padding: '8px 0', borderTop: '1px solid var(--border)' }}>
      <strong style={{ fontSize: 13, color }}>{heading}</strong>
      <p className="secondary" style={{ margin: '2px 0 0', fontSize: 13 }}>
        {children}
      </p>
    </div>
  )
}

function PreparationChecks({ detail, evidence }: { detail: ApplicationDetail; evidence: ApplicationEvidence | null }) {
  const review = evidence?.review_summary
  const reviewedCount = evidence ? evidence.items.filter((i) => i.role_requirement_reviewed).length : 0
  const legacyCount = evidence ? evidence.items.filter((i) => !i.role_requirement_reviewed).length : 0
  const exampleCount = evidence ? evidence.notes.filter((n) => n.note_type === 'evidence_example').length : 0

  return (
    <section className="card" aria-labelledby="prep-checks-h">
      <h2 id="prep-checks-h" style={{ fontSize: 18, marginTop: 0 }}>
        Preparation checks
      </h2>
      <CheckRow heading="Source role">{detail.role.title ? 'Available.' : 'The linked opportunity is no longer available.'}</CheckRow>

      {!review ? (
        <CheckRow heading="Requirements">Requirement review data unavailable right now.</CheckRow>
      ) : !review.extraction_attempted && review.accepted === 0 ? (
        <CheckRow heading="Requirements not yet extracted" tone="warning">
          Requirements haven't been extracted from this posting yet.
        </CheckRow>
      ) : review.complete ? (
        <CheckRow heading="Requirement review complete">
          {pluralize(reviewedCount, 'reviewed requirement')}
          {legacyCount > 0 ? ` and ${pluralize(legacyCount, 'legacy role-side observation')}` : ''}.
        </CheckRow>
      ) : (
        <CheckRow heading="Requirement review incomplete" tone="warning">
          {pluralize(review.unreviewed + review.unresolved_proposals + review.needs_reextraction, 'item')} still excluded from
          reviewed analysis.{' '}
          {evidence && <Link to={`/role-instances/${evidence.role_instance_id}/requirements`}>Review now</Link>}
        </CheckRow>
      )}

      {evidence && legacyCount > 0 && (
        <CheckRow heading="Legacy role-side observations">
          {pluralize(legacyCount, 'legacy role extraction')} — not human-reviewed, shown separately from reviewed requirements.
        </CheckRow>
      )}

      {evidence && (
        <CheckRow heading="Evidence coverage">
          {evidence.counts.evidenced} evidenced · {evidence.counts.partial} partial · {evidence.counts.user_asserted} user
          asserted · {evidence.counts.not_found} not found.
        </CheckRow>
      )}

      {evidence && (
        <CheckRow
          heading={pluralize(evidence.blocking_gaps.length, 'required evidence gap')}
          tone={evidence.blocking_gaps.length > 0 ? 'critical' : undefined}
        >
          {evidence.blocking_gaps.length > 0
            ? 'No accepted evidence was found for these requirements. This does not mean you lack the capability.'
            : 'No required evidence gaps.'}
        </CheckRow>
      )}

      {evidence && (
        <CheckRow
          heading={`${evidence.unverified_required.length} required ${evidence.unverified_required.length === 1 ? 'capability' : 'capabilities'} not fully verified`}
          tone={evidence.unverified_required.length > 0 ? 'warning' : undefined}
        >
          {evidence.unverified_required.length > 0
            ? 'Some evidence exists but does not yet fully meet the requirement.'
            : 'Every required capability that has evidence is fully verified.'}
        </CheckRow>
      )}

      <CheckRow heading="Application-only evidence examples added">
        {exampleCount > 0 ? `${pluralize(exampleCount, 'example')} added for this application.` : 'None added yet.'}
      </CheckRow>
    </section>
  )
}

// --- Evidence to use -------------------------------------------------------

function EvidenceToUse({ items }: { items: ApplicationEvidenceItem[] }) {
  if (items.length === 0) {
    return (
      <section className="card" aria-labelledby="evidence-to-use-h">
        <h2 id="evidence-to-use-h" style={{ fontSize: 18, marginTop: 0 }}>
          Evidence to use
        </h2>
        <p className="muted">No reviewed requirements to show evidence for yet.</p>
      </section>
    )
  }
  return (
    <section className="card" aria-labelledby="evidence-to-use-h">
      <h2 id="evidence-to-use-h" style={{ fontSize: 18, marginTop: 0 }}>
        Evidence to use
      </h2>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        The strongest currently accepted evidence for each requirement. An application-only example is never presented
        as accepted Profile360 evidence.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {items.map((item) => (
          <div key={item.concept.id} style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 13 }}>{item.concept.canonical_name}</strong>
              <span style={{ fontSize: 12, color: STATUS_COLOR[item.status], fontWeight: 600 }}>{STATUS_LABEL[item.status]}</span>
            </div>
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              {item.role_requirement_reviewed ? 'Reviewed requirement' : 'Legacy role extraction — not human-reviewed'}
              {` · ${item.role_side.requirement_type}`}
            </div>
            <p className="secondary" style={{ fontSize: 13, margin: '4px 0 0' }}>
              {personSideSummary(item)}
            </p>
            {item.notes.length > 0 && (
              <p style={{ fontSize: 12, margin: '4px 0 0', color: 'var(--series-1)' }}>
                Application-only example added ({item.notes.length}) — does not change the status above.
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

// --- Gaps and uncertainties -------------------------------------------------

function GapRow({
  concept,
  status,
  roleId,
  existingNotes,
  onAddExample,
  busy,
}: {
  concept: GapConcept
  status: ComparisonStatus
  roleId: string
  existingNotes: ApplicationNote[]
  onAddExample: (conceptId: string, text: string) => void
  busy: boolean
}) {
  const [composing, setComposing] = useState(false)
  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, marginTop: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 13 }}>{concept.canonical_name}</strong>
        <span style={{ fontSize: 12, color: STATUS_COLOR[status], fontWeight: 600 }}>{STATUS_LABEL[status]}</span>
      </div>
      <p className="secondary" style={{ fontSize: 13, margin: '4px 0 0' }}>
        {status === 'not_found'
          ? 'No accepted evidence found for this requirement. This does not mean you lack the capability.'
          : 'Some evidence exists but does not yet fully verify this requirement.'}
      </p>
      {existingNotes.length > 0 && (
        <p style={{ fontSize: 12, margin: '4px 0 0', color: 'var(--series-1)' }}>
          Application-only example added ({existingNotes.length}).
        </p>
      )}
      <div className="actions" style={{ marginTop: 8 }}>
        <button type="button" onClick={() => setComposing((c) => !c)} style={{ fontSize: 12, padding: '4px 10px' }}>
          {composing ? 'Cancel' : 'Add an example for this application'}
        </button>
        <Link to={`/comparison/${roleId}`} style={{ fontSize: 12 }}>
          Review my evidence
        </Link>
      </div>
      {composing && (
        <NoteComposer
          conceptId={concept.id}
          defaultType="evidence_example"
          busy={busy}
          onSubmit={(text) => { onAddExample(concept.id, text); setComposing(false) }}
          onCancel={() => setComposing(false)}
        />
      )}
      <p className="muted" style={{ fontSize: 12, margin: '6px 0 0' }}>
        Or continue without adding anything — a gap here never blocks the application.
      </p>
    </div>
  )
}

function GapsAndUncertainties({
  evidence,
  roleId,
  onAddExample,
  busy,
}: {
  evidence: ApplicationEvidence
  roleId: string
  onAddExample: (conceptId: string, text: string) => void
  busy: boolean
}) {
  const notesByConcept = new Map<string, ApplicationNote[]>()
  for (const item of evidence.items) notesByConcept.set(item.concept.id, item.notes)

  const hasGaps = evidence.blocking_gaps.length > 0 || evidence.unverified_required.length > 0

  return (
    <section className="card" aria-labelledby="gaps-h">
      <h2 id="gaps-h" style={{ fontSize: 18, marginTop: 0 }}>
        Gaps and uncertainties
      </h2>
      {!hasGaps && <p className="muted">No required gaps or unverified requirements right now.</p>}
      {evidence.blocking_gaps.map((gap) => (
        <GapRow
          key={gap.id}
          concept={gap}
          status="not_found"
          roleId={roleId}
          existingNotes={notesByConcept.get(gap.id) ?? []}
          onAddExample={onAddExample}
          busy={busy}
        />
      ))}
      {evidence.unverified_required.map((gap) => (
        <GapRow
          key={gap.id}
          concept={gap}
          status={gap.status}
          roleId={roleId}
          existingNotes={notesByConcept.get(gap.id) ?? []}
          onAddExample={onAddExample}
          busy={busy}
        />
      ))}
    </section>
  )
}

// --- Phase 4: source provenance -------------------------------------------

function SourceRefs({ refs, manifest }: { refs: string[]; manifest: SourceManifestEntry[] }) {
  const [open, setOpen] = useState(false)
  if (refs.length === 0) return null
  const entries = refs
    .map((ref) => manifest.find((m) => m.ref === ref))
    .filter((e): e is SourceManifestEntry => Boolean(e))
  return (
    <span>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{ fontSize: 11, padding: '1px 6px', marginLeft: 6, verticalAlign: 'middle' }}
      >
        {open ? 'Hide sources' : `Sources (${refs.length})`}
      </button>
      {open && (
        <ul style={{ margin: '4px 0 4px 0', paddingLeft: 18, fontSize: 11 }}>
          {entries.map((e) => (
            <li key={e.ref}>
              <span className="muted">{CATEGORY_LABELS[e.category]}:</span> {e.label}
            </li>
          ))}
          {entries.length < refs.length && <li className="muted">(some cited sources are no longer available)</li>}
        </ul>
      )}
    </span>
  )
}

function SourcedBlock({ block, manifest }: { block: SourcedText; manifest: SourceManifestEntry[] }) {
  return (
    <p style={{ margin: '4px 0', whiteSpace: 'pre-wrap' }}>
      {block.text}
      <SourceRefs refs={block.source_refs} manifest={manifest} />
    </p>
  )
}

// --- Phase 4: deterministic Markdown rendering (copy/download, build §18) --

function renderArtifactMarkdown(artifactType: ArtifactType, content: ArtifactContent): string {
  const lines: string[] = []
  if (artifactType === 'positioning') {
    const c = content as PositioningContent
    lines.push('# Positioning brief', '', c.positioning_statement.text, '')
    if (c.themes.length) {
      lines.push('## Themes', '')
      c.themes.forEach((t) => lines.push(`- **${t.title}** — ${t.message}`))
      lines.push('')
    }
    if (c.requirements_to_lead_with.length) {
      lines.push('## Requirements to lead with', '')
      c.requirements_to_lead_with.forEach((r) => lines.push(`- ${r.reason}`))
      lines.push('')
    }
    if (c.gaps_and_cautions.length) {
      lines.push('## Gaps and cautions', '')
      c.gaps_and_cautions.forEach((g) => lines.push(`- ${g.message}`))
      lines.push('')
    }
    if (c.language_to_mirror.length) lines.push('## Language to mirror', '', c.language_to_mirror.map((l) => `- ${l}`).join('\n'), '')
    if (c.avoid_claiming.length) lines.push('## Avoid claiming', '', c.avoid_claiming.map((l) => `- ${l}`).join('\n'), '')
  } else if (artifactType === 'cv') {
    const c = content as CVContent
    lines.push('# CV', '', c.profile_summary.text, '')
    if (c.experience.length) {
      lines.push('## Experience', '')
      c.experience.forEach((e) => {
        lines.push(`### ${e.title ?? 'Unknown role'} — ${e.organisation ?? 'Unknown organisation'}`)
        lines.push(`*${formatDate(e.start_date)} – ${formatDate(e.end_date)}*`, '')
        e.bullets.forEach((b) => lines.push(`- ${b.text}`))
        lines.push('')
      })
    }
    if (c.skills.length) lines.push('## Skills', '', c.skills.map((s) => `- ${s.text}`).join('\n'), '')
    if (c.omissions_or_cautions.length) lines.push('## Notes', '', c.omissions_or_cautions.map((l) => `- ${l}`).join('\n'), '')
  } else if (artifactType === 'cover_letter') {
    const c = content as CoverLetterContent
    lines.push(c.salutation, '', c.opening.text, '')
    c.body.forEach((b) => lines.push(b.text, ''))
    lines.push(c.closing.text, '', c.sign_off)
  } else if (artifactType === 'supporting_statement') {
    const c = content as SupportingStatementContent
    lines.push('# Supporting statement', '', c.opening.text, '')
    c.sections.forEach((s) => {
      lines.push(`## ${s.heading}`, '')
      s.paragraphs.forEach((p) => lines.push(p.text, ''))
    })
  } else {
    const c = content as InterviewPrepContent
    lines.push('# Interview preparation', '')
    if (c.focus_areas.length) {
      lines.push('## Focus areas', '')
      c.focus_areas.forEach((f) => lines.push(`- **${f.title}** — ${f.why_it_matters}`))
      lines.push('')
    }
    if (c.questions.length) {
      lines.push('## Practice questions', '')
      c.questions.forEach((q) => {
        lines.push(`### ${q.question}`, `*${q.question_type} — a practice question, not a known employer question*`, '')
        lines.push(`Approach: ${q.answer_plan.approach}`, '')
        if (q.answer_plan.evidence_points.length) {
          lines.push('Evidence to draw on:')
          q.answer_plan.evidence_points.forEach((e) => lines.push(`- ${e.text}`))
          lines.push('')
        }
        if (q.answer_plan.cautions.length) {
          lines.push('Cautions:')
          q.answer_plan.cautions.forEach((c2) => lines.push(`- ${c2.text}`))
          lines.push('')
        }
      })
    }
    if (c.questions_to_ask.length) {
      lines.push('## Questions to ask', '')
      c.questions_to_ask.forEach((q) => lines.push(`- ${q.text}`))
      lines.push('')
    }
    if (c.closing_points.length) {
      lines.push('## Closing points', '')
      c.closing_points.forEach((p) => lines.push(`- ${p.text}`))
      lines.push('')
    }
    if (c.prep_checklist.length) {
      lines.push('## Prep checklist', '')
      c.prep_checklist.forEach((item) => lines.push(`- ${item}`))
      lines.push('')
    }
  }
  return lines.join('\n').trim() + '\n'
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// --- Phase 4: read-only content viewers -----------------------------------

function ArtifactContentView({ artifactType, content, manifest }: { artifactType: ArtifactType; content: ArtifactContent; manifest: SourceManifestEntry[] }) {
  if (artifactType === 'positioning') {
    const c = content as PositioningContent
    return (
      <div>
        <SourcedBlock block={c.positioning_statement} manifest={manifest} />
        {c.themes.length > 0 && (
          <>
            <strong style={{ fontSize: 12 }}>Themes</strong>
            {c.themes.map((t, i) => (
              <p key={i} style={{ margin: '4px 0', fontSize: 13 }}>
                <strong>{t.title}:</strong> {t.message}
                <SourceRefs refs={t.source_refs} manifest={manifest} />
              </p>
            ))}
          </>
        )}
        {c.requirements_to_lead_with.length > 0 && (
          <>
            <strong style={{ fontSize: 12 }}>Requirements to lead with</strong>
            {c.requirements_to_lead_with.map((r, i) => (
              <p key={i} style={{ margin: '4px 0', fontSize: 13 }}>
                {r.reason}
                <SourceRefs refs={r.source_refs} manifest={manifest} />
              </p>
            ))}
          </>
        )}
        {c.gaps_and_cautions.length > 0 && (
          <>
            <strong style={{ fontSize: 12 }}>Gaps and cautions</strong>
            {c.gaps_and_cautions.map((g, i) => (
              <p key={i} style={{ margin: '4px 0', fontSize: 13, color: 'var(--warning)' }}>
                {g.message}
                <SourceRefs refs={g.source_refs} manifest={manifest} />
              </p>
            ))}
          </>
        )}
        {c.language_to_mirror.length > 0 && (
          <p style={{ fontSize: 13 }}>
            <strong>Language to mirror:</strong> {c.language_to_mirror.join(' · ')}
          </p>
        )}
        {c.avoid_claiming.length > 0 && (
          <p style={{ fontSize: 13, color: 'var(--critical)' }}>
            <strong>Avoid claiming:</strong> {c.avoid_claiming.join(' · ')}
          </p>
        )}
      </div>
    )
  }
  if (artifactType === 'cv') {
    const c = content as CVContent
    return (
      <div>
        <SourcedBlock block={c.profile_summary} manifest={manifest} />
        {c.experience.map((e, i) => (
          <div key={i} style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
            <strong style={{ fontSize: 13 }}>
              {e.title ?? 'Unknown role'} — {e.organisation ?? 'Unknown organisation'}
            </strong>
            {!e.episode_found && <span style={{ color: 'var(--critical)', fontSize: 11 }}> (episode no longer available)</span>}
            <div className="muted" style={{ fontSize: 11 }}>
              {formatDate(e.start_date)} – {formatDate(e.end_date)}
            </div>
            <ul style={{ margin: '4px 0 0 18px', fontSize: 13 }}>
              {e.bullets.map((b, j) => (
                <li key={j}>
                  {b.text}
                  <SourceRefs refs={b.source_refs} manifest={manifest} />
                </li>
              ))}
            </ul>
          </div>
        ))}
        {c.skills.length > 0 && (
          <p style={{ fontSize: 13, marginTop: 10 }}>
            <strong>Skills:</strong> {c.skills.map((s) => s.text).join(' · ')}
          </p>
        )}
        {c.omissions_or_cautions.length > 0 && (
          <p style={{ fontSize: 12, color: 'var(--warning)' }}>{c.omissions_or_cautions.join(' · ')}</p>
        )}
      </div>
    )
  }
  if (artifactType === 'cover_letter') {
    const c = content as CoverLetterContent
    return (
      <div>
        <p style={{ fontSize: 13 }}>{c.salutation}</p>
        <SourcedBlock block={c.opening} manifest={manifest} />
        {c.body.map((b, i) => (
          <SourcedBlock key={i} block={b} manifest={manifest} />
        ))}
        <SourcedBlock block={c.closing} manifest={manifest} />
        <p style={{ fontSize: 13 }}>{c.sign_off}</p>
      </div>
    )
  }
  if (artifactType === 'supporting_statement') {
    const c = content as SupportingStatementContent
    return (
      <div>
        <SourcedBlock block={c.opening} manifest={manifest} />
        {c.sections.map((s, i) => (
          <div key={i} style={{ marginTop: 8 }}>
            <strong style={{ fontSize: 13 }}>{s.heading}</strong>
            {s.paragraphs.map((p, j) => (
              <SourcedBlock key={j} block={p} manifest={manifest} />
            ))}
          </div>
        ))}
        {c.gaps_addressed.length > 0 && (
          <p style={{ fontSize: 12, color: 'var(--warning)' }}>
            <strong>Gaps addressed:</strong> {c.gaps_addressed.join(' · ')}
          </p>
        )}
      </div>
    )
  }
  const c = content as InterviewPrepContent
  return (
    <div>
      {c.focus_areas.length > 0 && (
        <>
          <strong style={{ fontSize: 12 }}>Focus areas</strong>
          {c.focus_areas.map((f, i) => (
            <p key={i} style={{ margin: '4px 0', fontSize: 13 }}>
              <strong>{f.title}:</strong> {f.why_it_matters}
              <SourceRefs refs={f.source_refs} manifest={manifest} />
            </p>
          ))}
        </>
      )}
      {c.questions.length > 0 && (
        <>
          <strong style={{ fontSize: 12 }}>Practice questions</strong>
          <p className="muted" style={{ fontSize: 11, margin: '2px 0 6px' }}>
            Practice questions based on this role's requirements — not known employer questions.
          </p>
          {c.questions.map((q, i) => (
            <div key={i} style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
              <p style={{ margin: '4px 0', fontSize: 13 }}>
                <strong>{q.question}</strong> <span className="muted">({q.question_type})</span>
                <SourceRefs refs={q.source_refs} manifest={manifest} />
              </p>
              <p style={{ fontSize: 13, margin: '4px 0' }}>{q.answer_plan.approach}</p>
              {q.answer_plan.evidence_points.length > 0 && (
                <ul style={{ margin: '4px 0 0 18px', fontSize: 13 }}>
                  {q.answer_plan.evidence_points.map((e, j) => (
                    <li key={j}>
                      {e.text}
                      <SourceRefs refs={e.source_refs} manifest={manifest} />
                    </li>
                  ))}
                </ul>
              )}
              {q.answer_plan.cautions.map((caution, j) => (
                <p key={j} style={{ fontSize: 12, margin: '4px 0', color: 'var(--warning)' }}>
                  {caution.text}
                  <SourceRefs refs={caution.source_refs} manifest={manifest} />
                </p>
              ))}
            </div>
          ))}
        </>
      )}
      {c.questions_to_ask.length > 0 && (
        <>
          <strong style={{ fontSize: 12 }}>Questions to ask</strong>
          {c.questions_to_ask.map((q, i) => (
            <SourcedBlock key={i} block={q} manifest={manifest} />
          ))}
        </>
      )}
      {c.closing_points.length > 0 && (
        <>
          <strong style={{ fontSize: 12 }}>Closing points</strong>
          {c.closing_points.map((p, i) => (
            <SourcedBlock key={i} block={p} manifest={manifest} />
          ))}
        </>
      )}
      {c.prep_checklist.length > 0 && (
        <p style={{ fontSize: 13 }}>
          <strong>Prep checklist:</strong> {c.prep_checklist.join(' · ')}
        </p>
      )}
    </div>
  )
}

// --- Phase 4: minimal structural editors (build §17 — text fields only) ----

function ListEditor({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <label style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
      {label}
      <textarea
        rows={2}
        style={{ width: '100%', fontSize: 13 }}
        value={value.join('\n')}
        onChange={(e) => onChange(e.target.value.split('\n'))}
        placeholder="One per line"
      />
    </label>
  )
}

function ArtifactEditor({
  artifactType,
  draft,
  onChange,
}: {
  artifactType: ArtifactType
  draft: ArtifactContent
  onChange: (next: ArtifactContent) => void
}) {
  if (artifactType === 'positioning') {
    const c = draft as PositioningContent
    return (
      <div>
        <label style={{ display: 'block', fontSize: 12 }}>
          Positioning statement
          <textarea
            rows={3}
            style={{ width: '100%', fontSize: 13 }}
            value={c.positioning_statement.text}
            onChange={(e) => onChange({ ...c, positioning_statement: { ...c.positioning_statement, text: e.target.value } })}
          />
        </label>
        {c.themes.map((t, i) => (
          <label key={i} style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
            Theme: {t.title}
            <textarea
              rows={2}
              style={{ width: '100%', fontSize: 13 }}
              value={t.message}
              onChange={(e) => onChange({ ...c, themes: c.themes.map((x, j) => (j === i ? { ...x, message: e.target.value } : x)) })}
            />
          </label>
        ))}
        {c.requirements_to_lead_with.map((r, i) => (
          <label key={i} style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
            Reason to lead with this requirement
            <textarea
              rows={2}
              style={{ width: '100%', fontSize: 13 }}
              value={r.reason}
              onChange={(e) =>
                onChange({ ...c, requirements_to_lead_with: c.requirements_to_lead_with.map((x, j) => (j === i ? { ...x, reason: e.target.value } : x)) })
              }
            />
          </label>
        ))}
        {c.gaps_and_cautions.map((g, i) => (
          <label key={i} style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
            Gap/caution
            <textarea
              rows={2}
              style={{ width: '100%', fontSize: 13 }}
              value={g.message}
              onChange={(e) => onChange({ ...c, gaps_and_cautions: c.gaps_and_cautions.map((x, j) => (j === i ? { ...x, message: e.target.value } : x)) })}
            />
          </label>
        ))}
        <ListEditor label="Language to mirror" value={c.language_to_mirror} onChange={(v) => onChange({ ...c, language_to_mirror: v })} />
        <ListEditor label="Avoid claiming" value={c.avoid_claiming} onChange={(v) => onChange({ ...c, avoid_claiming: v })} />
      </div>
    )
  }
  if (artifactType === 'cv') {
    const c = draft as CVContent
    return (
      <div>
        <label style={{ display: 'block', fontSize: 12 }}>
          Profile summary
          <textarea
            rows={3}
            style={{ width: '100%', fontSize: 13 }}
            value={c.profile_summary.text}
            onChange={(e) => onChange({ ...c, profile_summary: { ...c.profile_summary, text: e.target.value } })}
          />
        </label>
        {c.experience.map((entry, i) => (
          <div key={i} style={{ marginTop: 8 }}>
            <div className="muted" style={{ fontSize: 12 }}>
              {entry.title} — {entry.organisation}
            </div>
            {entry.bullets.map((b, j) => (
              <textarea
                key={j}
                rows={2}
                style={{ width: '100%', fontSize: 13, marginTop: 4 }}
                value={b.text}
                onChange={(e) =>
                  onChange({
                    ...c,
                    experience: c.experience.map((x, ei) =>
                      ei === i ? { ...x, bullets: x.bullets.map((bx, bj) => (bj === j ? { ...bx, text: e.target.value } : bx)) } : x,
                    ),
                  })
                }
              />
            ))}
          </div>
        ))}
        {c.skills.map((s, i) => (
          <input
            key={i}
            style={{ width: '100%', fontSize: 13, marginTop: 4 }}
            value={s.text}
            onChange={(e) => onChange({ ...c, skills: c.skills.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })}
          />
        ))}
        <ListEditor label="Omissions/cautions" value={c.omissions_or_cautions} onChange={(v) => onChange({ ...c, omissions_or_cautions: v })} />
      </div>
    )
  }
  if (artifactType === 'cover_letter') {
    const c = draft as CoverLetterContent
    return (
      <div>
        <label style={{ display: 'block', fontSize: 12 }}>
          Salutation
          <input style={{ width: '100%', fontSize: 13 }} value={c.salutation} onChange={(e) => onChange({ ...c, salutation: e.target.value })} />
        </label>
        <label style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
          Opening
          <textarea rows={2} style={{ width: '100%', fontSize: 13 }} value={c.opening.text} onChange={(e) => onChange({ ...c, opening: { ...c.opening, text: e.target.value } })} />
        </label>
        {c.body.map((b, i) => (
          <textarea
            key={i}
            rows={3}
            style={{ width: '100%', fontSize: 13, marginTop: 8 }}
            value={b.text}
            onChange={(e) => onChange({ ...c, body: c.body.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })}
          />
        ))}
        <label style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
          Closing
          <textarea rows={2} style={{ width: '100%', fontSize: 13 }} value={c.closing.text} onChange={(e) => onChange({ ...c, closing: { ...c.closing, text: e.target.value } })} />
        </label>
        <label style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
          Sign-off
          <input style={{ width: '100%', fontSize: 13 }} value={c.sign_off} onChange={(e) => onChange({ ...c, sign_off: e.target.value })} />
        </label>
      </div>
    )
  }
  if (artifactType === 'supporting_statement') {
    const c = draft as SupportingStatementContent
    return (
      <div>
        <label style={{ display: 'block', fontSize: 12 }}>
          Opening
          <textarea rows={2} style={{ width: '100%', fontSize: 13 }} value={c.opening.text} onChange={(e) => onChange({ ...c, opening: { ...c.opening, text: e.target.value } })} />
        </label>
        {c.sections.map((s, i) => (
          <div key={i} style={{ marginTop: 8 }}>
            <input
              style={{ width: '100%', fontSize: 13, fontWeight: 600 }}
              value={s.heading}
              onChange={(e) => onChange({ ...c, sections: c.sections.map((x, j) => (j === i ? { ...x, heading: e.target.value } : x)) })}
            />
            {s.paragraphs.map((p, k) => (
              <textarea
                key={k}
                rows={2}
                style={{ width: '100%', fontSize: 13, marginTop: 4 }}
                value={p.text}
                onChange={(e) =>
                  onChange({
                    ...c,
                    sections: c.sections.map((x, si) =>
                      si === i ? { ...x, paragraphs: x.paragraphs.map((px, pj) => (pj === k ? { ...px, text: e.target.value } : px)) } : x,
                    ),
                  })
                }
              />
            ))}
          </div>
        ))}
        <ListEditor label="Gaps addressed" value={c.gaps_addressed} onChange={(v) => onChange({ ...c, gaps_addressed: v })} />
      </div>
    )
  }
  const c = draft as InterviewPrepContent
  return (
    <div>
      {c.focus_areas.map((f, i) => (
        <label key={i} style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
          Focus area: {f.title}
          <textarea
            rows={2}
            style={{ width: '100%', fontSize: 13 }}
            value={f.why_it_matters}
            onChange={(e) => onChange({ ...c, focus_areas: c.focus_areas.map((x, j) => (j === i ? { ...x, why_it_matters: e.target.value } : x)) })}
          />
        </label>
      ))}
      {c.questions.map((q, i) => (
        <div key={i} style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
          <label style={{ display: 'block', fontSize: 12 }}>
            Question
            <textarea
              rows={2}
              style={{ width: '100%', fontSize: 13 }}
              value={q.question}
              onChange={(e) => onChange({ ...c, questions: c.questions.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)) })}
            />
          </label>
          <label style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
            Approach
            <textarea
              rows={2}
              style={{ width: '100%', fontSize: 13 }}
              value={q.answer_plan.approach}
              onChange={(e) =>
                onChange({
                  ...c,
                  questions: c.questions.map((x, j) => (j === i ? { ...x, answer_plan: { ...x.answer_plan, approach: e.target.value } } : x)),
                })
              }
            />
          </label>
          {q.answer_plan.evidence_points.map((ep, k) => (
            <textarea
              key={k}
              rows={2}
              style={{ width: '100%', fontSize: 13, marginTop: 4 }}
              value={ep.text}
              onChange={(e) =>
                onChange({
                  ...c,
                  questions: c.questions.map((x, j) =>
                    j === i
                      ? {
                          ...x,
                          answer_plan: {
                            ...x.answer_plan,
                            evidence_points: x.answer_plan.evidence_points.map((y, m) => (m === k ? { ...y, text: e.target.value } : y)),
                          },
                        }
                      : x,
                  ),
                })
              }
            />
          ))}
          {q.answer_plan.cautions.map((caution, k) => (
            <textarea
              key={k}
              rows={2}
              style={{ width: '100%', fontSize: 13, marginTop: 4 }}
              value={caution.text}
              onChange={(e) =>
                onChange({
                  ...c,
                  questions: c.questions.map((x, j) =>
                    j === i
                      ? {
                          ...x,
                          answer_plan: {
                            ...x.answer_plan,
                            cautions: x.answer_plan.cautions.map((y, m) => (m === k ? { ...y, text: e.target.value } : y)),
                          },
                        }
                      : x,
                  ),
                })
              }
            />
          ))}
        </div>
      ))}
      {c.questions_to_ask.map((q, i) => (
        <textarea
          key={i}
          rows={2}
          style={{ width: '100%', fontSize: 13, marginTop: 8 }}
          value={q.text}
          onChange={(e) => onChange({ ...c, questions_to_ask: c.questions_to_ask.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })}
        />
      ))}
      {c.closing_points.map((p, i) => (
        <textarea
          key={i}
          rows={2}
          style={{ width: '100%', fontSize: 13, marginTop: 8 }}
          value={p.text}
          onChange={(e) => onChange({ ...c, closing_points: c.closing_points.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })}
        />
      ))}
      <ListEditor label="Prep checklist" value={c.prep_checklist} onChange={(v) => onChange({ ...c, prep_checklist: v })} />
    </div>
  )
}

// --- Phase 4: generation-context summary (build §19 — deterministic, no AI) --

function GenerationContextCard({ evidence, hasAdoptedPositioning }: { evidence: ApplicationEvidence | null; hasAdoptedPositioning: boolean }) {
  if (!evidence) return null
  const reviewed = evidence.items.filter((i) => i.role_requirement_reviewed).length
  const legacy = evidence.items.filter((i) => !i.role_requirement_reviewed).length
  const review = evidence.review_summary
  const pending = review ? review.unreviewed + review.unresolved_proposals + review.needs_reextraction : 0
  const examples = evidence.notes.filter((n) => n.note_type === 'evidence_example').length
  return (
    <div className="card" style={{ padding: 10, marginBottom: 12, fontSize: 12 }}>
      <strong style={{ fontSize: 13 }}>What a new draft will use</strong>
      <p className="secondary" style={{ margin: '4px 0 0' }}>
        {pluralize(reviewed, 'reviewed requirement')} will be used
        {pending > 0 ? ` · ${pluralize(pending, 'pending/unreviewed item')} excluded` : ''}
        {legacy > 0 ? ` · ${pluralize(legacy, 'legacy role-side observation')} excluded from accepted tailoring` : ''}
        {' · '}
        {evidence.counts.evidenced} evidenced · {evidence.counts.partial} partial · {evidence.counts.user_asserted} asserted ·{' '}
        {evidence.counts.not_found} not found
        {examples > 0 ? ` · ${pluralize(examples, 'application-only example')}` : ''}
        {' · '}
        {hasAdoptedPositioning ? 'an adopted positioning brief will be used' : 'no adopted positioning brief yet'}.
      </p>
    </div>
  )
}

// --- Phase 4: one lifecycle stage card (Positioning / CV / Cover letter / Supporting statement) --

function StateBadge({ artifact }: { artifact: ApplicationArtifact }) {
  if (artifact.status === 'active' && artifact.stale) {
    return <span style={{ fontSize: 11, color: 'var(--warning)', fontWeight: 600 }}>Current — but stale</span>
  }
  if (artifact.status === 'active') return <span style={{ fontSize: 11, color: 'var(--good)', fontWeight: 600 }}>Current</span>
  return <span style={{ fontSize: 11, color: 'var(--series-1)', fontWeight: 600 }}>Draft awaiting review</span>
}

function ArtifactStageCard({
  applicationId,
  artifactType,
  active,
  draft,
  onChanged,
}: {
  applicationId: string
  artifactType: ArtifactType
  active: ApplicationArtifact | null
  draft: ApplicationArtifact | null
  onChanged: () => void
}) {
  const [guidance, setGuidance] = useState('')
  const [targetWords, setTargetWords] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState<ArtifactContent | null>(null)
  const [viewing, setViewing] = useState<'active' | 'draft'>(draft ? 'draft' : 'active')
  const [copyStatus, setCopyStatus] = useState<string | null>(null)

  const shown = viewing === 'draft' && draft ? draft : active

  const runAction = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleGenerate = () =>
    runAction(async () => {
      const words = targetWords.trim() ? Number(targetWords) : undefined
      await api.generateApplicationArtifact(applicationId, artifactType, {
        guidance: guidance.trim() || undefined,
        target_words: words,
      })
      setViewing('draft')
    })

  const handleAdopt = (artifactId: string) => runAction(async () => { await api.adoptApplicationArtifact(applicationId, artifactId); setViewing('active') })
  const handleDiscard = (artifactId: string) => runAction(async () => { await api.discardApplicationArtifact(applicationId, artifactId); setViewing('active') })
  const handleSaveEdit = (artifactId: string) =>
    runAction(async () => {
      if (!editDraft) return
      await api.editApplicationArtifact(applicationId, artifactId, editDraft)
      setEditing(false)
      setViewing('draft')
    })

  const startEdit = (artifact: ApplicationArtifact) => {
    setEditDraft(JSON.parse(JSON.stringify(artifact.content)))
    setEditing(true)
  }

  return (
    <section className="card" aria-labelledby={`stage-${artifactType}-h`} style={{ marginTop: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
        <h3 id={`stage-${artifactType}-h`} style={{ fontSize: 16, margin: 0 }}>
          {ARTIFACT_LABELS[artifactType]}
        </h3>
        {shown ? <StateBadge artifact={shown} /> : <span className="muted" style={{ fontSize: 11 }}>Not generated</span>}
      </div>

      {(active || draft) && (
        <div className="actions" style={{ marginTop: 6 }}>
          {active && (
            <button type="button" onClick={() => setViewing('active')} disabled={viewing === 'active'} style={{ fontSize: 12, padding: '3px 8px' }}>
              View current
            </button>
          )}
          {draft && (
            <button type="button" onClick={() => setViewing('draft')} disabled={viewing === 'draft'} style={{ fontSize: 12, padding: '3px 8px' }}>
              View draft
            </button>
          )}
        </div>
      )}

      {shown && shown.grounding_status === 'user_edited_not_revalidated' && (
        <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          User edited — source trace has not been automatically revalidated after this edit.
        </p>
      )}
      {shown && shown.status === 'active' && shown.stale && (
        <p role="status" style={{ fontSize: 12, color: 'var(--warning)', marginTop: 6 }}>
          Evidence or application context has changed since this version was generated. You can keep using it or regenerate.
        </p>
      )}

      {shown && !editing && (
        <div style={{ marginTop: 8 }}>
          <ArtifactContentView artifactType={artifactType} content={shown.content} manifest={shown.source_manifest} />
        </div>
      )}
      {shown && editing && editDraft && (
        <div style={{ marginTop: 8 }}>
          <ArtifactEditor artifactType={artifactType} draft={editDraft} onChange={setEditDraft} />
        </div>
      )}

      {!shown && <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>Not generated yet.</p>}

      {error && <p role="alert" style={{ color: 'var(--critical)', fontSize: 12, marginTop: 8 }}>{error}</p>}

      <div className="actions" style={{ marginTop: 10, flexWrap: 'wrap' }}>
        {shown && !editing && (
          <>
            <button type="button" onClick={() => startEdit(shown)} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
              Edit
            </button>
            {viewing === 'draft' && draft && (
              <>
                <button type="button" className="primary" onClick={() => handleAdopt(draft.id)} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
                  Adopt
                </button>
                <button type="button" onClick={() => handleDiscard(draft.id)} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
                  Discard
                </button>
              </>
            )}
            <button
              type="button"
              onClick={async () => { setCopyStatus((await copyToClipboard(renderArtifactMarkdown(artifactType, shown.content))) ? 'Copied' : 'Copy failed'); setTimeout(() => setCopyStatus(null), 2000) }}
              style={{ fontSize: 12, padding: '3px 8px' }}
            >
              {copyStatus ?? 'Copy as Markdown'}
            </button>
            <button
              type="button"
              onClick={() => downloadText(`${artifactType}.md`, renderArtifactMarkdown(artifactType, shown.content))}
              style={{ fontSize: 12, padding: '3px 8px' }}
            >
              Download .md
            </button>
          </>
        )}
        {editing && shown && (
          <>
            <button type="button" className="primary" disabled={busy} onClick={() => handleSaveEdit(shown.id)} style={{ fontSize: 12, padding: '3px 8px' }}>
              {busy ? 'Saving…' : 'Save edit'}
            </button>
            <button type="button" disabled={busy} onClick={() => setEditing(false)} style={{ fontSize: 12, padding: '3px 8px' }}>
              Cancel
            </button>
          </>
        )}
      </div>

      {!editing && (
        <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
          <details>
            <summary style={{ fontSize: 12, cursor: 'pointer' }}>{active || draft ? 'Regenerate' : 'Generate'} with optional guidance</summary>
            <div style={{ marginTop: 6 }}>
              <textarea
                rows={2}
                placeholder="Optional guidance — tone, emphasis, specific instructions…"
                style={{ width: '100%', fontSize: 13 }}
                value={guidance}
                onChange={(e) => setGuidance(e.target.value)}
              />
              <input
                type="number"
                placeholder="Target words (optional)"
                min={50}
                max={5000}
                style={{ width: 180, fontSize: 13, marginTop: 6 }}
                value={targetWords}
                onChange={(e) => setTargetWords(e.target.value)}
              />
              <div className="actions" style={{ marginTop: 6 }}>
                <button type="button" className="primary" disabled={busy} onClick={handleGenerate} style={{ fontSize: 12, padding: '3px 8px' }}>
                  {busy ? 'Generating…' : active || draft ? 'Regenerate' : 'Generate'}
                </button>
              </div>
            </div>
          </details>
        </div>
      )}
    </section>
  )
}

// --- Phase 4: Application package section ----------------------------------

function ApplicationPackageSection({
  applicationId,
  evidence,
  artifactsData,
  onChanged,
}: {
  applicationId: string
  evidence: ApplicationEvidence | null
  artifactsData: ApplicationArtifactsResponse | null
  onChanged: () => void
}) {
  if (!artifactsData) return <p className="muted">Loading application package…</p>
  const { artifacts } = artifactsData
  return (
    <section aria-labelledby="app-package-h">
      <h2 id="app-package-h" style={{ fontSize: 18 }}>
        Application package
      </h2>
      <GenerationContextCard evidence={evidence} hasAdoptedPositioning={Boolean(artifacts.positioning.active)} />
      <ArtifactStageCard applicationId={applicationId} artifactType="positioning" active={artifacts.positioning.active} draft={artifacts.positioning.draft} onChanged={onChanged} />
      <ArtifactStageCard applicationId={applicationId} artifactType="cv" active={artifacts.cv.active} draft={artifacts.cv.draft} onChanged={onChanged} />
      <h3 style={{ fontSize: 15, marginTop: 16 }}>Supporting material</h3>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Optional — generate either or both only if this application needs them.
      </p>
      <ArtifactStageCard applicationId={applicationId} artifactType="cover_letter" active={artifacts.cover_letter.active} draft={artifacts.cover_letter.draft} onChanged={onChanged} />
      <ArtifactStageCard
        applicationId={applicationId}
        artifactType="supporting_statement"
        active={artifacts.supporting_statement.active}
        draft={artifacts.supporting_statement.draft}
        onChanged={onChanged}
      />
    </section>
  )
}

// --- Phase 5: real Interview stage (docs/36), replacing the Phase 4 -------
// placeholder -------------------------------------------------------------

function InterviewContextCard({ events, status }: { events: ApplicationEvent[] | null; status: ApplicationStatus }) {
  const upcoming = events ? upcomingInterview(events) : null
  const latestWithNotes = events?.find((e) => e.notes)
  return (
    <div className="card" style={{ padding: 10, marginBottom: 12 }}>
      <strong style={{ fontSize: 13 }}>Interview context</strong>
      <p className="secondary" style={{ margin: '4px 0 0', fontSize: 13 }}>
        Current status: {APPLICATION_STATUSES.find((s) => s.value === status)?.label ?? status}.{' '}
        {upcoming
          ? `Next: ${EVENT_TYPE_LABEL[upcoming.event_type]}${upcoming.label ? ` — ${upcoming.label}` : ''} on ${formatEventAt(upcoming.event_at)}.`
          : 'No interview currently scheduled.'}
      </p>
      {latestWithNotes && (
        <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>
          Latest note ({EVENT_TYPE_LABEL[latestWithNotes.event_type]}): {latestWithNotes.notes}
        </p>
      )}
    </div>
  )
}

function InterviewReadinessCard({ ctx }: { ctx: InterviewGenerationContextSummary | null }) {
  if (!ctx) return null
  const materials = [
    ctx.active_positioning_available && 'positioning',
    ctx.active_cv_available && 'CV',
    ctx.active_cover_letter_available && 'cover letter',
    ctx.active_supporting_statement_available && 'supporting statement',
  ].filter((x): x is string => Boolean(x))
  return (
    <div className="card" style={{ padding: 10, marginBottom: 12, fontSize: 12 }}>
      <strong style={{ fontSize: 13 }}>What this prep will use</strong>
      <p className="secondary" style={{ margin: '4px 0 0' }}>
        {pluralize(ctx.reviewed_requirements_used, 'reviewed requirement')} will be used
        {ctx.pending_unreviewed_excluded > 0 ? ` · ${pluralize(ctx.pending_unreviewed_excluded, 'pending/unreviewed item')} excluded` : ''}
        {ctx.legacy_requirements_excluded > 0
          ? ` · ${pluralize(ctx.legacy_requirements_excluded, 'legacy role-side observation')} as legacy context only`
          : ''}
        {' · '}
        {ctx.counts.evidenced} evidenced · {ctx.counts.partial} partial · {ctx.counts.user_asserted} asserted ·{' '}
        {ctx.counts.not_found} not found
        {ctx.application_examples > 0 ? ` · ${pluralize(ctx.application_examples, 'application-only example')}` : ''}
        {' · '}
        {materials.length > 0 ? `current ${materials.join(', ')} available` : 'no adopted application material yet'}
        {' · '}
        {ctx.upcoming_interview_available ? 'upcoming interview context available' : 'no upcoming interview recorded'}.
      </p>
    </div>
  )
}

function InterviewStage({
  applicationId,
  status,
  events,
  artifactsData,
  onChanged,
}: {
  applicationId: string
  status: ApplicationStatus
  events: ApplicationEvent[] | null
  artifactsData: ApplicationArtifactsResponse | null
  onChanged: () => void
}) {
  return (
    <section aria-labelledby="interview-stage-h" style={{ marginTop: 16 }}>
      <h2 id="interview-stage-h" style={{ fontSize: 18 }}>
        Interview
      </h2>
      <InterviewContextCard events={events} status={status} />
      <InterviewReadinessCard ctx={artifactsData?.interview_generation_context ?? null} />
      {artifactsData && (
        <ArtifactStageCard
          applicationId={applicationId}
          artifactType="interview_prep"
          active={artifactsData.artifacts.interview_prep.active}
          draft={artifactsData.artifacts.interview_prep.draft}
          onChanged={onChanged}
        />
      )}
    </section>
  )
}

// --- Application notes ---------------------------------------------------

function ApplicationNotesSection({
  notes,
  busy,
  onAdd,
  onSave,
  onDelete,
  learningState,
  promoteBusyKey,
  onPromoteNote,
}: {
  notes: ApplicationNote[]
  busy: boolean
  onAdd: (text: string) => void
  onSave: (noteId: string, text: string) => void
  onDelete: (noteId: string) => void
  learningState: ApplicationLearningState | null
  promoteBusyKey: string | null
  onPromoteNote: (noteId: string) => void
}) {
  const [adding, setAdding] = useState(false)
  return (
    <section className="card" aria-labelledby="app-notes-h">
      <h2 id="app-notes-h" style={{ fontSize: 18, marginTop: 0 }}>
        Application notes
      </h2>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Notes here are specific to this application. They never become Profile360 claims, mappings or evidence
        unless you explicitly send one to Profile360 for review.
      </p>
      {notes.length === 0 && !adding && <p className="muted">No notes yet.</p>}
      {notes.map((note) => (
        <NoteItem
          key={note.id}
          note={note}
          busy={busy}
          onSave={(text) => onSave(note.id, text)}
          onDelete={() => onDelete(note.id)}
          learningStatus={findLearningStatus(learningState, 'note', note.id)?.status}
          promoteBusy={promoteBusyKey === `note:${note.id}`}
          onPromote={() => onPromoteNote(note.id)}
        />
      ))}
      {adding ? (
        <NoteComposer
          defaultType="general"
          busy={busy}
          onSubmit={(text) => { onAdd(text); setAdding(false) }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <div className="actions" style={{ marginTop: 10 }}>
          <button type="button" onClick={() => setAdding(true)}>
            Add a note
          </button>
        </div>
      )}
    </section>
  )
}

// --- Page -------------------------------------------------------------

export default function ApplicationWorkspace() {
  const { id } = useParams()
  const currentId = useRef(id)

  const [detail, setDetail] = useState<ApplicationDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [evidence, setEvidence] = useState<ApplicationEvidence | null>(null)
  const [evidenceError, setEvidenceError] = useState<string | null>(null)
  const [artifactsData, setArtifactsData] = useState<ApplicationArtifactsResponse | null>(null)
  const [artifactsError, setArtifactsError] = useState<string | null>(null)
  const [events, setEvents] = useState<ApplicationEvent[] | null>(null)
  const [eventsError, setEventsError] = useState<string | null>(null)
  // Phase 9 (docs/40 build §12): one bounded queue-status read for every
  // note/event on this Application — never a request per row.
  const [learningState, setLearningState] = useState<ApplicationLearningState | null>(null)
  const [learningStateError, setLearningStateError] = useState<string | null>(null)
  // Phase 7 (docs/38 build §16): a compact, read-only alignment summary —
  // reuses the same endpoint/service Role Detail and Pathways call, never a
  // second alignment engine. Independent of the other four loads: a failed
  // fetch here never blanks the preparation checks/evidence/artifacts below.
  const [alignment, setAlignment] = useState<OpportunityAlignment | null>(null)
  const [alignmentError, setAlignmentError] = useState<string | null>(null)

  const [statusBusy, setStatusBusy] = useState(false)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [noteBusy, setNoteBusy] = useState(false)
  const [noteError, setNoteError] = useState<string | null>(null)
  const [eventBusy, setEventBusy] = useState(false)
  const [eventActionError, setEventActionError] = useState<string | null>(null)
  const [promoteBusyKey, setPromoteBusyKey] = useState<string | null>(null)
  const [promoteError, setPromoteError] = useState<string | null>(null)

  const loadDetail = useCallback(() => {
    if (!id) return
    setDetailError(null)
    api
      .getApplication(id)
      .then((d) => { if (currentId.current === id) setDetail(d) })
      .catch((e) => { if (currentId.current === id) setDetailError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  const loadEvidence = useCallback(() => {
    if (!id) return
    setEvidenceError(null)
    api
      .getApplicationEvidence(id)
      .then((d) => { if (currentId.current === id) setEvidence(d) })
      .catch((e) => { if (currentId.current === id) setEvidenceError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  // Phase 4 (docs/35 §13/§20): one bounded, AI-free artifact-state request —
  // never generates anything itself, just reports current draft/active/stale
  // state so the workspace can render the package section without a
  // request-per-artifact-type or a request-per-source.
  const loadArtifacts = useCallback(() => {
    if (!id) return
    setArtifactsError(null)
    api
      .getApplicationArtifacts(id)
      .then((d) => { if (currentId.current === id) setArtifactsData(d) })
      .catch((e) => { if (currentId.current === id) setArtifactsError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  // Phase 5 (docs/36 §4/§21): one bounded lifecycle-event request, never
  // called per row and never blocking the rest of the workspace if it fails
  // (see the Lifecycle section's own failure isolation below).
  const loadEvents = useCallback(() => {
    if (!id) return
    setEventsError(null)
    api
      .listApplicationEvents(id)
      .then((d) => { if (currentId.current === id) setEvents(d.events) })
      .catch((e) => { if (currentId.current === id) setEventsError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  const loadLearningState = useCallback(() => {
    if (!id) return
    setLearningStateError(null)
    api
      .getApplicationLearningState(id)
      .then((d) => { if (currentId.current === id) setLearningState(d) })
      .catch((e) => { if (currentId.current === id) setLearningStateError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  // Phase 7: the alignment endpoint is keyed by the *role* id, which is only
  // known once `detail` has loaded — so this fires as its own effect, after
  // detail resolves, rather than joining the four requests below that all
  // key off the application id directly.
  const roleId = detail?.role.id
  const loadAlignment = useCallback(() => {
    if (!roleId) return
    setAlignmentError(null)
    api
      .getCareerAlignment(roleId)
      .then((r) => { if (currentId.current === id) setAlignment(r) })
      .catch((e) => { if (currentId.current === id) setAlignmentError(e instanceof Error ? e.message : String(e)) })
  }, [roleId, id])

  useEffect(() => {
    currentId.current = id
    setDetail(null)
    setDetailError(null)
    setEvidence(null)
    setEvidenceError(null)
    setArtifactsData(null)
    setArtifactsError(null)
    setEvents(null)
    setEventsError(null)
    setAlignment(null)
    setAlignmentError(null)
    setLearningState(null)
    setLearningStateError(null)
  }, [id])

  // Five independent, parallel requests (build §13/§20/§21) — never a
  // waterfall, and generation never happens on load.
  useEffect(loadDetail, [loadDetail])
  useEffect(loadEvidence, [loadEvidence])
  useEffect(loadArtifacts, [loadArtifacts])
  useEffect(loadEvents, [loadEvents])
  useEffect(loadAlignment, [loadAlignment])
  useEffect(loadLearningState, [loadLearningState])

  const handleStatusChange = async (status: ApplicationStatus) => {
    if (!id) return
    setStatusBusy(true)
    setStatusError(null)
    try {
      const updated = await api.updateApplicationStatus(id, status)
      setDetail((d) => (d ? { ...d, application: updated } : d))
    } catch (e) {
      setStatusError(e instanceof Error ? e.message : String(e))
    } finally {
      setStatusBusy(false)
    }
  }

  const handleAddGeneralNote = async (text: string) => {
    if (!id) return
    setNoteBusy(true)
    setNoteError(null)
    try {
      await api.createApplicationNote(id, { note_type: 'general', note_text: text })
      loadEvidence()
    } catch (e) {
      setNoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setNoteBusy(false)
    }
  }

  const handleAddExample = async (conceptId: string, text: string) => {
    if (!id) return
    setNoteBusy(true)
    setNoteError(null)
    try {
      await api.createApplicationNote(id, { concept_id: conceptId, note_type: 'evidence_example', note_text: text })
      loadEvidence()
    } catch (e) {
      setNoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setNoteBusy(false)
    }
  }

  const handleSaveNote = async (noteId: string, text: string) => {
    if (!id) return
    setNoteBusy(true)
    setNoteError(null)
    try {
      await api.updateApplicationNote(id, noteId, { note_text: text })
      loadEvidence()
    } catch (e) {
      setNoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setNoteBusy(false)
    }
  }

  const handleDeleteNote = async (noteId: string) => {
    if (!id) return
    if (!confirm('Delete this note?')) return
    setNoteBusy(true)
    setNoteError(null)
    try {
      await api.deleteApplicationNote(id, noteId)
      loadEvidence()
    } catch (e) {
      setNoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setNoteBusy(false)
    }
  }

  const handleAddEvent = async (input: ApplicationEventInput) => {
    if (!id) return
    setEventBusy(true)
    setEventActionError(null)
    try {
      await api.createApplicationEvent(id, input)
      loadEvents()
    } catch (e) {
      setEventActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setEventBusy(false)
    }
  }

  const handleSaveEvent = async (eventId: string, input: ApplicationEventInput) => {
    if (!id) return
    setEventBusy(true)
    setEventActionError(null)
    try {
      await api.updateApplicationEvent(id, eventId, input)
      loadEvents()
    } catch (e) {
      setEventActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setEventBusy(false)
    }
  }

  const handleDeleteEvent = async (eventId: string) => {
    if (!id) return
    if (!confirm('Delete this event?')) return
    setEventBusy(true)
    setEventActionError(null)
    try {
      await api.deleteApplicationEvent(id, eventId)
      loadEvents()
    } catch (e) {
      setEventActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setEventBusy(false)
    }
  }

  const handlePromoteNote = async (noteId: string) => {
    if (!id) return
    setPromoteBusyKey(`note:${noteId}`)
    setPromoteError(null)
    try {
      await api.promoteApplicationNote(id, noteId)
      loadLearningState()
    } catch (e) {
      setPromoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setPromoteBusyKey(null)
    }
  }

  const handlePromoteEvent = async (eventId: string) => {
    if (!id) return
    setPromoteBusyKey(`event:${eventId}`)
    setPromoteError(null)
    try {
      await api.promoteApplicationEvent(id, eventId)
      loadLearningState()
    } catch (e) {
      setPromoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setPromoteBusyKey(null)
    }
  }

  if (detailError) return <p role="alert" style={{ color: 'var(--critical)' }}>{detailError}</p>
  if (!detail) return <p className="muted">Loading…</p>

  const { application, role } = detail

  return (
    <div>
      <Link to={`/roles/${role.id}`} className="muted" style={{ fontSize: 13 }}>
        ← Back to opportunity
      </Link>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginTop: 12, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 24, margin: '4px 0' }}>{role.title ?? 'Application'}</h1>
          <div className="secondary">
            {role.organisation ?? 'Unknown org'}
            {role.location ? ` · ${role.location}` : ''}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Created {formatDateTime(application.created_at)} · updated {formatDateTime(application.updated_at)}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <label htmlFor="application-status" className="muted" style={{ fontSize: 12, display: 'block' }}>
            Application status
          </label>
          <select
            id="application-status"
            value={application.status}
            disabled={statusBusy}
            onChange={(e) => handleStatusChange(e.target.value as ApplicationStatus)}
            style={{ marginTop: 4 }}
          >
            {APPLICATION_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {statusError && <p role="alert" style={{ color: 'var(--critical)', fontSize: 13 }}>{statusError}</p>}

      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        This status is set by you — it never changes automatically based on the preparation checks below.
      </p>

      {/* Phase 7 (docs/38 build §16): compact, read-only — never mutates
          application status or artifacts, and a failed fetch here never
          blanks the rest of the workspace (build §26). */}
      <div style={{ marginTop: 16 }}>
        <AlignmentDecisionTile alignment={alignment} error={alignmentError} onRetry={loadAlignment} />
        {alignment?.target && (
          <p style={{ marginTop: 8, fontSize: 13 }}>
            <Link to={`/pathways/${alignment.target.id}?opportunity_id=${role.id}`}>View this opportunity in Pathways</Link>
          </p>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <LifecycleSection
          status={application.status}
          events={events}
          eventsError={eventsError}
          busy={eventBusy}
          actionError={eventActionError}
          onAdd={handleAddEvent}
          onSave={handleSaveEvent}
          onDelete={handleDeleteEvent}
          learningState={learningState}
          promoteBusyKey={promoteBusyKey}
          onPromoteEvent={handlePromoteEvent}
        />
      </div>

      <div className="home-grid" style={{ marginTop: 16 }}>
        <PreparationChecks detail={detail} evidence={evidence} />
      </div>

      {evidenceError && (
        <p role="alert" style={{ color: 'var(--critical)', marginTop: 16 }}>
          Evidence couldn't be loaded: {evidenceError}
        </p>
      )}

      {evidence && (
        <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <EvidenceToUse items={evidence.items} />
          <GapsAndUncertainties evidence={evidence} roleId={role.id} onAddExample={handleAddExample} busy={noteBusy} />
        </div>
      )}

      {noteError && <p role="alert" style={{ color: 'var(--critical)', marginTop: 16 }}>{noteError}</p>}
      {promoteError && <p role="alert" style={{ color: 'var(--critical)', marginTop: 16 }}>{promoteError}</p>}
      {learningStateError && (
        <p className="muted" style={{ marginTop: 16, fontSize: 12 }}>
          Profile360 review status couldn't be checked just now: {learningStateError}
        </p>
      )}

      <div style={{ marginTop: 16 }}>
        <ApplicationNotesSection
          notes={evidence?.notes ?? detail.notes}
          busy={noteBusy}
          onAdd={handleAddGeneralNote}
          onSave={handleSaveNote}
          onDelete={handleDeleteNote}
          learningState={learningState}
          promoteBusyKey={promoteBusyKey}
          onPromoteNote={handlePromoteNote}
        />
      </div>

      {artifactsError && (
        <p role="alert" style={{ color: 'var(--critical)', marginTop: 16 }}>
          Application package couldn't be loaded: {artifactsError}
        </p>
      )}

      <div style={{ marginTop: 16 }}>
        <ApplicationPackageSection applicationId={application.id} evidence={evidence} artifactsData={artifactsData} onChanged={loadArtifacts} />
      </div>

      <InterviewStage
        applicationId={application.id}
        status={application.status}
        events={events}
        artifactsData={artifactsData}
        onChanged={loadArtifacts}
      />
    </div>
  )
}
