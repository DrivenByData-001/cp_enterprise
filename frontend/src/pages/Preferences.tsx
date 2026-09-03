import { useEffect, useState } from 'react'
import { api, type PreferenceDimension, type PreferenceObservation, type PreferenceObservationInput } from '../lib/api'

const BASIS_OPTIONS: PreferenceObservationInput['basis'][] = [
  'observed_behavior',
  'user_stated',
  'repeated_episode_evidence',
  'validated_psychometric',
  'typology_hypothesis',
]

const BASIS_LABEL: Record<string, string> = {
  observed_behavior: 'Observed career behaviour',
  user_stated: 'Explicit user statement',
  repeated_episode_evidence: 'Repeated episode evidence',
  validated_psychometric: 'Validated psychometric trait',
  typology_hypothesis: 'Typology / MBTI hypothesis',
}

const PSYCHOMETRIC_BASES = new Set(['validated_psychometric', 'typology_hypothesis'])

function AddObservationForm({ dimensions, onAdded }: { dimensions: PreferenceDimension[]; onAdded: () => Promise<void> }) {
  const [form, setForm] = useState<PreferenceObservationInput>({
    dimension_code: dimensions[0]?.code ?? '',
    direction: 'toward',
    strength: 2,
    basis: 'user_stated',
    source_label: '',
    note: '',
  })
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try {
      await api.createPreferenceObservation({ ...form, source_label: form.source_label || undefined, note: form.note || undefined })
      await onAdded()
      setForm({ ...form, source_label: '', note: '' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Dimension</span>
          <select value={form.dimension_code} onChange={(e) => setForm({ ...form, dimension_code: e.target.value })}>
            {dimensions.map((d) => (
              <option key={d.code} value={d.code}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Direction</span>
          <select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value as PreferenceObservationInput['direction'] })}>
            <option value="toward">Toward</option>
            <option value="away">Away</option>
            <option value="neutral">Neutral</option>
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Strength (1-3)</span>
          <select value={form.strength} onChange={(e) => setForm({ ...form, strength: Number(e.target.value) })}>
            <option value={1}>1 — mild</option>
            <option value={2}>2 — clear</option>
            <option value={3}>3 — strong</option>
          </select>
        </label>
      </div>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Basis</span>
        <select value={form.basis} onChange={(e) => setForm({ ...form, basis: e.target.value as PreferenceObservationInput['basis'] })}>
          {BASIS_OPTIONS.map((b) => (
            <option key={b} value={b}>
              {BASIS_LABEL[b]}
            </option>
          ))}
        </select>
        {PSYCHOMETRIC_BASES.has(form.basis) && (
          <span className="muted" style={{ fontSize: 11 }}>
            Hypothesis-generating only — never treated as capability evidence or a deterministic role-fit input.
          </span>
        )}
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <input
          value={form.source_label ?? ''}
          onChange={(e) => setForm({ ...form, source_label: e.target.value })}
          placeholder="Source (e.g. 'MBTI: INTP', 'episode 4')"
        />
        <input value={form.note ?? ''} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="Note (optional)" />
      </div>
      <div>
        <button className="primary" onClick={submit} disabled={busy || !form.dimension_code}>
          {busy ? 'Saving…' : 'Add observation'}
        </button>
      </div>
    </div>
  )
}

export default function Preferences() {
  const [dimensions, setDimensions] = useState<PreferenceDimension[]>([])
  const [observations, setObservations] = useState<PreferenceObservation[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const reloadObservations = () => api.listPreferenceObservations().then(setObservations)

  useEffect(() => {
    Promise.all([api.listPreferenceDimensions(), reloadObservations()])
      .then(([d]) => setDimensions(d))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const dimensionLabel = (code: string) => dimensions.find((d) => d.code === code)?.label ?? code

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (loading) return <p className="muted">Loading…</p>

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>Preferences</h1>
      <p className="secondary">
        Would you enjoy a role, separately from whether you can do it. Structurally independent of the capability
        model — nothing here feeds a fit score. Personality/psychometric material is a hypothesis-generating input
        only, ranked below observed behaviour and explicit statements.
      </p>

      <div style={{ marginBottom: 20 }}>
        <AddObservationForm dimensions={dimensions} onAdded={reloadObservations} />
      </div>

      <h2 style={{ fontSize: 16 }}>Observations</h2>
      {observations.length === 0 && <p className="muted">None recorded yet.</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {observations.map((o) => (
          <div key={o.id} className="card" style={{ fontSize: 13 }}>
            <strong>{dimensionLabel(o.dimension_code)}</strong> — {o.direction} (strength {o.strength}/3)
            <div className="muted" style={{ fontSize: 12 }}>
              {BASIS_LABEL[o.basis] ?? o.basis}
              {o.source_label ? ` · ${o.source_label}` : ''} · confidence {o.confidence}
            </div>
            {o.note && <div className="secondary" style={{ marginTop: 4 }}>{o.note}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
