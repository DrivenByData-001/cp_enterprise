import { useEffect, useState } from 'react'
import { api, type Episode, type EpisodeInput, type EpisodeKind, type Timeline } from '../lib/api'

const KIND_LABEL: Record<EpisodeKind, string> = {
  employment: 'Employment',
  project: 'Project',
  study: 'Study',
  qualification: 'Qualification',
  other: 'Other',
}

const KIND_COLOR: Record<EpisodeKind, string> = {
  employment: 'var(--series-1)',
  project: 'var(--series-3)',
  study: 'var(--series-2)',
  qualification: 'var(--series-2)',
  other: 'var(--series-other)',
}

const EMPTY_FORM: EpisodeInput = {
  kind: 'employment',
  title: '',
  organisation: '',
  start_date: '',
  end_date: '',
  date_precision: 'month',
  parent_episode_id: null,
  domain_hint: '',
  context_note: '',
}

// Mirrors backend/app/routes/episodes.py::_parse_partial_date — accepts YYYY,
// YYYY-MM, or YYYY-MM-DD. Used only for laying out the timeline bars; the
// authoritative computation (duration_years, total_span_years) always comes
// from the API, never recomputed here.
function parsePartialDate(raw: string | null): Date | null {
  if (!raw) return null
  const parts = raw.split('-')
  const year = Number(parts[0])
  if (!Number.isFinite(year)) return null
  const month = parts[1] ? Number(parts[1]) - 1 : 0
  const day = parts[2] ? Number(parts[2]) : 1
  const d = new Date(Date.UTC(year, month, day))
  return Number.isNaN(d.getTime()) ? null : d
}

function cleanPayload(form: EpisodeInput): EpisodeInput {
  return {
    ...form,
    organisation: form.organisation?.trim() || null,
    start_date: form.start_date?.trim() || null,
    end_date: form.end_date?.trim() || null,
    domain_hint: form.domain_hint?.trim() || null,
    context_note: form.context_note?.trim() || null,
    parent_episode_id: form.parent_episode_id || null,
  }
}

function EpisodeForm({
  initial,
  episodes,
  excludeId,
  onCancel,
  onSaved,
}: {
  initial: EpisodeInput
  episodes: Episode[]
  excludeId?: number
  onCancel: () => void
  onSaved: (payload: EpisodeInput) => Promise<void>
}) {
  const [form, setForm] = useState<EpisodeInput>(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!form.title.trim()) {
      setError('Title is required.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await onSaved(cleanPayload(form))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const field = (label: string, node: React.ReactNode) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
      <span className="secondary">{label}</span>
      {node}
    </label>
  )

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {field(
          'Kind',
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as EpisodeKind })}>
            {Object.entries(KIND_LABEL).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>,
        )}
        {field(
          'Title',
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Senior Pricing Actuary" />,
        )}
        {field(
          'Organisation',
          <input
            value={form.organisation ?? ''}
            onChange={(e) => setForm({ ...form, organisation: e.target.value })}
            placeholder="e.g. Aviva"
          />,
        )}
        {field(
          'Nested inside',
          <select
            value={form.parent_episode_id ?? ''}
            onChange={(e) => setForm({ ...form, parent_episode_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">— none —</option>
            {episodes
              .filter((e) => e.id !== excludeId)
              .map((e) => (
                <option key={e.id} value={e.id}>
                  {e.title}
                </option>
              ))}
          </select>,
        )}
        {field(
          'Start date',
          <input
            value={form.start_date ?? ''}
            onChange={(e) => setForm({ ...form, start_date: e.target.value })}
            placeholder="YYYY-MM or YYYY-MM-DD"
          />,
        )}
        {field(
          'End date',
          <input
            value={form.end_date ?? ''}
            onChange={(e) => setForm({ ...form, end_date: e.target.value })}
            placeholder="blank = ongoing"
          />,
        )}
      </div>
      {field(
        'Domain hint',
        <input
          value={form.domain_hint ?? ''}
          onChange={(e) => setForm({ ...form, domain_hint: e.target.value })}
          placeholder="e.g. general insurance, pricing — free text for now"
        />,
      )}
      {field(
        'Notes',
        <textarea
          rows={2}
          value={form.context_note ?? ''}
          onChange={(e) => setForm({ ...form, context_note: e.target.value })}
          placeholder="Anything worth remembering about this period"
        />,
      )}
      {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="primary" onClick={submit} disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function TimelineChart({ timeline }: { timeline: Timeline }) {
  const roots = timeline.episodes.filter((e) => !e.parent_episode_id)
  if (roots.length === 0) return null

  const today = new Date()
  const starts = timeline.episodes.map((e) => parsePartialDate(e.start_date)).filter((d): d is Date => d !== null)
  const ends = timeline.episodes.map((e) => parsePartialDate(e.end_date) ?? today)
  const rangeStart = starts.length ? new Date(Math.min(...starts.map((d) => d.getTime()))) : today
  const rangeEnd = ends.length ? new Date(Math.max(...ends.map((d) => d.getTime()), today.getTime())) : today
  const totalMs = Math.max(rangeEnd.getTime() - rangeStart.getTime(), 1)

  const barStyle = (ep: Episode): React.CSSProperties => {
    const start = parsePartialDate(ep.start_date)
    if (!start) return { display: 'none' }
    const end = parsePartialDate(ep.end_date) ?? today
    const left = ((start.getTime() - rangeStart.getTime()) / totalMs) * 100
    const width = Math.max(((end.getTime() - start.getTime()) / totalMs) * 100, 0.6)
    return { position: 'absolute', left: `${left}%`, width: `${width}%`, background: KIND_COLOR[ep.kind] }
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }} className="muted">
        <span>{rangeStart.getUTCFullYear()}</span>
        <span>{rangeEnd.getUTCFullYear()}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
        {roots.map((root) => {
          const children = timeline.episodes.filter((e) => e.parent_episode_id === root.id)
          return (
            <div key={root.id} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div style={{ position: 'relative', height: 20, background: 'var(--gridline)', borderRadius: 4 }}>
                <div
                  style={{ ...barStyle(root), height: '100%', borderRadius: 4, display: 'flex', alignItems: 'center' }}
                  title={`${root.title} (${root.duration_years ?? '?'}y)`}
                >
                  <span style={{ fontSize: 11, color: 'white', padding: '0 6px', whiteSpace: 'nowrap', overflow: 'hidden' }}>
                    {root.title}
                  </span>
                </div>
              </div>
              {children.map((child) => (
                <div key={child.id} style={{ position: 'relative', height: 10, marginLeft: 16, background: 'var(--gridline)', borderRadius: 3 }}>
                  <div style={{ ...barStyle(child), height: '100%', borderRadius: 3, opacity: 0.85 }} title={`${child.title} (${child.duration_years ?? '?'}y)`} />
                </div>
              ))}
            </div>
          )
        })}
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
        Total career span (union of episode dates, overlaps not double-counted): <strong>{timeline.total_span_years} years</strong>
      </p>
    </div>
  )
}

export default function Episodes() {
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)

  const reload = () => api.getTimeline().then(setTimeline)

  useEffect(() => {
    reload()
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const episodes = timeline?.episodes ?? []

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this episode?')) return
    try {
      await api.deleteEpisode(id)
      await reload()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>Career history</h1>
          <p className="secondary" style={{ marginTop: 4 }}>
            Your career as discrete episodes — jobs, projects, study, qualifications — each with real dates. This is
            the foundation everything else (evidence, capabilities, gap analysis) will hang off in later phases.
          </p>
        </div>
        {!adding && (
          <button className="primary" onClick={() => setAdding(true)}>
            Add episode
          </button>
        )}
      </div>

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {loading && <p className="muted">Loading…</p>}

      {!loading && timeline && <TimelineChart timeline={timeline} />}

      {adding && (
        <div style={{ marginBottom: 16 }}>
          <EpisodeForm
            initial={EMPTY_FORM}
            episodes={episodes}
            onCancel={() => setAdding(false)}
            onSaved={async (payload) => {
              await api.createEpisode(payload)
              setAdding(false)
              await reload()
            }}
          />
        </div>
      )}

      {!loading && episodes.length === 0 && !adding && (
        <p className="muted">
          No episodes yet. Add your first one — expect to enter roughly 8–15 to cover a full career history by hand;
          there's no automated extraction for this yet.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {episodes.map((ep) =>
          editingId === ep.id ? (
            <EpisodeForm
              key={ep.id}
              initial={{
                kind: ep.kind,
                title: ep.title,
                organisation: ep.organisation ?? '',
                start_date: ep.start_date ?? '',
                end_date: ep.end_date ?? '',
                date_precision: ep.date_precision,
                parent_episode_id: ep.parent_episode_id,
                domain_hint: ep.domain_hint ?? '',
                context_note: ep.context_note ?? '',
              }}
              episodes={episodes}
              excludeId={ep.id}
              onCancel={() => setEditingId(null)}
              onSaved={async (payload) => {
                await api.updateEpisode(ep.id, payload)
                setEditingId(null)
                await reload()
              }}
            />
          ) : (
            <div
              key={ep.id}
              className="card"
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginLeft: ep.parent_episode_id ? 24 : 0,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span
                  aria-hidden
                  style={{ width: 10, height: 10, borderRadius: '50%', background: KIND_COLOR[ep.kind], flexShrink: 0 }}
                />
                <div>
                  <div style={{ fontWeight: 600 }}>
                    {ep.title}
                    {ep.organisation ? <span className="secondary"> · {ep.organisation}</span> : null}
                  </div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {KIND_LABEL[ep.kind]} · {ep.start_date ?? '?'} → {ep.end_date ?? 'ongoing'}
                    {ep.duration_years !== null ? ` · ${ep.duration_years}y` : ''}
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => setEditingId(ep.id)}>Edit</button>
                <button onClick={() => handleDelete(ep.id)}>Delete</button>
              </div>
            </div>
          ),
        )}
      </div>
    </div>
  )
}
