import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  type ApplicationDetail,
  type ApplicationEvidence,
  type ApplicationEvidenceItem,
  type ApplicationNote,
  type ApplicationNoteType,
  type ApplicationStatus,
  type ComparisonStatus,
  type GapConcept,
} from '../lib/api'

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
}: {
  note: ApplicationNote
  busy: boolean
  onSave: (text: string) => void
  onDelete: () => void
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
    </div>
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

// --- Future stages -----------------------------------------------------

function FutureStages() {
  const stages = [
    { title: 'Positioning', text: 'Positioning and tailored document generation arrive in the next phase.' },
    { title: 'CV / application material', text: 'Grounded CV and application-material generation arrive in the next phase.' },
    { title: 'Interview preparation', text: 'Interview preparation arrives in a later phase.' },
  ]
  return (
    <section className="card" aria-labelledby="future-stages-h">
      <h2 id="future-stages-h" style={{ fontSize: 18, marginTop: 0 }}>
        Coming next
      </h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {stages.map((s) => (
          <div key={s.title} style={{ opacity: 0.7 }}>
            <strong style={{ fontSize: 13 }}>{s.title}</strong>
            <p className="muted" style={{ fontSize: 13, margin: '2px 0 0' }}>
              {s.text}
            </p>
          </div>
        ))}
      </div>
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
}: {
  notes: ApplicationNote[]
  busy: boolean
  onAdd: (text: string) => void
  onSave: (noteId: string, text: string) => void
  onDelete: (noteId: string) => void
}) {
  const [adding, setAdding] = useState(false)
  return (
    <section className="card" aria-labelledby="app-notes-h">
      <h2 id="app-notes-h" style={{ fontSize: 18, marginTop: 0 }}>
        Application notes
      </h2>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Notes here are specific to this application. They never become Profile360 claims, mappings or evidence.
      </p>
      {notes.length === 0 && !adding && <p className="muted">No notes yet.</p>}
      {notes.map((note) => (
        <NoteItem
          key={note.id}
          note={note}
          busy={busy}
          onSave={(text) => onSave(note.id, text)}
          onDelete={() => onDelete(note.id)}
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

  const [statusBusy, setStatusBusy] = useState(false)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [noteBusy, setNoteBusy] = useState(false)
  const [noteError, setNoteError] = useState<string | null>(null)

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

  useEffect(() => {
    currentId.current = id
    setDetail(null)
    setDetailError(null)
    setEvidence(null)
    setEvidenceError(null)
  }, [id])

  // Two independent, parallel requests (build §13) — never a waterfall.
  useEffect(loadDetail, [loadDetail])
  useEffect(loadEvidence, [loadEvidence])

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

      <div style={{ marginTop: 16 }}>
        <ApplicationNotesSection
          notes={evidence?.notes ?? detail.notes}
          busy={noteBusy}
          onAdd={handleAddGeneralNote}
          onSave={handleSaveNote}
          onDelete={handleDeleteNote}
        />
      </div>

      <div style={{ marginTop: 16 }}>
        <FutureStages />
      </div>
    </div>
  )
}
