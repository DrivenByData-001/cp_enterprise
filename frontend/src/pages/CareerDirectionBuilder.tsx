import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  api,
  EMPTY_CAREER_DIRECTION_CONSTRAINTS,
  type CareerDirectionCandidate,
  type CareerDirectionConstraints,
  type CareerDirectionCorpusDisclosure,
  type CareerDirectionDimension,
  type CareerDirectionDiscoverResponse,
  type PreferenceDimension,
  type PreferenceSummaryEntry,
} from '../lib/api'

// Property-first Career Direction builder (docs/37 build §8). Nothing here is
// pre-populated as if the system already knows the answer — every dimension
// starts "not set", and "Use my recorded preferences" only ever fills this
// unsaved draft, never creates or saves anything by itself.

type DimensionValue = { desired_direction: 'toward' | 'away' | 'neutral'; importance: number; note: string }
type DimensionDrafts = Record<string, DimensionValue | undefined>

function listToLines(items: string[]): string {
  return items.join('\n')
}
function linesToList(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
}
function dimensionsPayload(drafts: DimensionDrafts): CareerDirectionDimension[] {
  const out: CareerDirectionDimension[] = []
  for (const [code, d] of Object.entries(drafts)) {
    if (!d) continue
    out.push({ dimension_code: code, desired_direction: d.desired_direction, importance: d.importance, note: d.note || null })
  }
  return out
}

function DimensionRow({
  dimension, draft, suggestion, onChange,
}: {
  dimension: PreferenceDimension
  draft: DimensionValue | undefined
  suggestion: PreferenceSummaryEntry | undefined
  onChange: (next: DimensionValue | undefined) => void
}) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <strong style={{ fontSize: 13 }}>{dimension.label}</strong>
      <p className="secondary" style={{ fontSize: 12, margin: 0 }}>{dimension.definition}</p>

      {suggestion && suggestion.conflict && (
        <p className="muted" style={{ fontSize: 11, margin: 0 }}>Mixed evidence recorded for this dimension — no suggestion, you decide.</p>
      )}
      {suggestion && !suggestion.conflict && suggestion.suggested_direction && (
        <p className="muted" style={{ fontSize: 11, margin: 0 }}>
          Recorded preferences suggest: {suggestion.suggested_direction} ({suggestion.basis})
        </p>
      )}
      {suggestion && suggestion.recent_observations.length > 0 && (
        <details style={{ fontSize: 11 }}>
          <summary className="muted">Recorded observations ({suggestion.recent_observations.length})</summary>
          <ul style={{ margin: '4px 0', paddingLeft: 16 }}>
            {suggestion.recent_observations.map((o) => (
              <li key={o.id}>
                {o.direction} (strength {o.strength}/3, {o.basis}{o.is_psychometric ? ', hypothesis-only' : ''}){o.note ? ` — ${o.note}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="form-grid">
        <label style={{ fontSize: 13 }}>
          <span className="secondary">This direction</span>
          <select
            value={draft ? draft.desired_direction : ''}
            onChange={(e) => {
              const v = e.target.value
              if (v === '') onChange(undefined)
              else onChange({ desired_direction: v as DimensionValue['desired_direction'], importance: draft?.importance ?? 2, note: draft?.note ?? '' })
            }}
          >
            <option value="">Not set</option>
            <option value="toward">Toward</option>
            <option value="away">Away</option>
            <option value="neutral">Neutral</option>
          </select>
        </label>
        {draft && (
          <label style={{ fontSize: 13 }}>
            <span className="secondary">Importance</span>
            <select value={draft.importance} onChange={(e) => onChange({ ...draft, importance: Number(e.target.value) })}>
              <option value={1}>1 — mild</option>
              <option value={2}>2 — clear</option>
              <option value={3}>3 — strong</option>
            </select>
          </label>
        )}
      </div>
      {draft && (
        <label style={{ fontSize: 13 }}>
          <span className="secondary">Note (optional)</span>
          <input value={draft.note} maxLength={1000} onChange={(e) => onChange({ ...draft, note: e.target.value })} />
        </label>
      )}
    </div>
  )
}

function AlignmentChip({ alignment }: { alignment: string }) {
  const color = alignment === 'supports' ? 'var(--good)' : alignment === 'tension' ? 'var(--warning)' : 'var(--text-muted)'
  return (
    <span style={{ fontSize: 11, color, border: `1px solid ${color}`, borderRadius: 999, padding: '1px 7px', whiteSpace: 'nowrap' }}>
      {alignment}
    </span>
  )
}

function CandidateCard({
  candidate, onAdopt, adopting,
}: {
  candidate: CareerDirectionCandidate
  onAdopt: (name: string) => void
  adopting: boolean
}) {
  const [name, setName] = useState(candidate.name)
  return (
    <section className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <label style={{ fontSize: 13 }}>
        <span className="secondary">Name</span>
        <input value={name} maxLength={300} onChange={(e) => setName(e.target.value)} />
      </label>
      <p style={{ margin: 0 }}>{candidate.summary.text}</p>

      {candidate.priority_alignment.length > 0 && (
        <div>
          <strong style={{ fontSize: 12 }}>Priority alignment</strong>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
            {candidate.priority_alignment.map((a, i) => (
              <div key={i} style={{ fontSize: 12 }}>
                <AlignmentChip alignment={a.alignment} /> <strong>{a.dimension_code}</strong>: {a.explanation}
              </div>
            ))}
          </div>
        </div>
      )}

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
          <strong style={{ fontSize: 12 }}>Where your evidence already overlaps</strong>
          <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
            {candidate.person_basis.map((m, i) => <li key={i}>{m.text}</li>)}
          </ul>
        </div>
      )}

      <div>
        <strong style={{ fontSize: 12 }}>Compensation context</strong>
        <p className="muted" style={{ fontSize: 12, margin: '4px 0' }}>
          {candidate.compensation_context ? candidate.compensation_context.text : 'No comparable compensation evidence available for this hypothesis.'}
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

      <div className="actions">
        <button className="primary" disabled={adopting || !name.trim()} onClick={() => onAdopt(name.trim())}>
          {adopting ? 'Saving…' : 'Save as Career Direction'}
        </button>
      </div>
    </section>
  )
}

function CorpusDisclosurePanel({ disclosure }: { disclosure: CareerDirectionCorpusDisclosure }) {
  return (
    <div className="card" style={{ fontSize: 12, marginBottom: 12 }}>
      <strong className="secondary" style={{ display: 'block', marginBottom: 4 }}>About this evidence</strong>
      <span className="muted">
        {disclosure.total_observed_postings} observed postings captured, {disclosure.postings_with_archetype_assignment} with an archetype
        assigned ({disclosure.postings_with_reviewed_requirements} with reviewed requirements); {disclosure.supported_archetypes} of{' '}
        {disclosure.active_archetypes_total} archetypes have at least one assigned posting; {disclosure.archetypes_with_compensation_evidence}{' '}
        {disclosure.archetypes_with_compensation_evidence === 1 ? 'has' : 'have'} comparable compensation evidence. Observed corpus only —
        not a claim of full market coverage.
      </span>
      {!disclosure.economics_freshness.fresh && disclosure.economics_freshness.reason && (
        <p className="muted" style={{ margin: '6px 0 0' }}>{disclosure.economics_freshness.reason}</p>
      )}
    </div>
  )
}

export default function CareerDirectionBuilder() {
  const navigate = useNavigate()
  const [dimensionCatalogue, setDimensionCatalogue] = useState<PreferenceDimension[]>([])
  const [preferenceSummary, setPreferenceSummary] = useState<Record<string, PreferenceSummaryEntry> | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [name, setName] = useState('')
  const [dimensionDrafts, setDimensionDrafts] = useState<DimensionDrafts>({})
  const [constraints, setConstraints] = useState<CareerDirectionConstraints>(EMPTY_CAREER_DIRECTION_CONSTRAINTS)
  const [hasFloor, setHasFloor] = useState(false)
  const [guidance, setGuidance] = useState('')

  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [discoverBusy, setDiscoverBusy] = useState(false)
  const [discoverError, setDiscoverError] = useState<string | null>(null)
  const [discoverResult, setDiscoverResult] = useState<CareerDirectionDiscoverResponse | null>(null)
  const [adoptingId, setAdoptingId] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.listPreferenceDimensions(), api.getCareerDirectionPreferenceSummary()])
      .then(([dims, summary]) => {
        setDimensionCatalogue(dims)
        setPreferenceSummary(summary.dimensions)
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

  const seedFromPreferences = () => {
    if (!preferenceSummary) return
    setDimensionDrafts((prev) => {
      const next = { ...prev }
      for (const entry of Object.values(preferenceSummary)) {
        if (!entry.conflict && entry.suggested_direction) {
          next[entry.dimension_code] = { desired_direction: entry.suggested_direction, importance: 2, note: '' }
        }
      }
      return next
    })
  }

  const floor = constraints.compensation_floor

  const saveManually = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const created = await api.createCareerDirection({
        name: name.trim(), dimensions: dimensionsPayload(dimensionDrafts), constraints,
      })
      navigate(`/future/directions/${created.id}`)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const generate = async () => {
    setDiscoverBusy(true)
    setDiscoverError(null)
    setDiscoverResult(null)
    try {
      const result = await api.discoverCareerDirections({
        name: name.trim() || undefined, dimensions: dimensionsPayload(dimensionDrafts), constraints,
        guidance: guidance.trim() || undefined,
      })
      setDiscoverResult(result)
    } catch (e) {
      setDiscoverError(e instanceof Error ? e.message : String(e))
    } finally {
      setDiscoverBusy(false)
    }
  }

  const adopt = async (candidateId: string, chosenName: string) => {
    if (!discoverResult) return
    setAdoptingId(candidateId)
    try {
      const res = await api.adoptCareerDirectionCandidate(discoverResult.discovery_run_id, candidateId, { name: chosenName })
      navigate(`/future/directions/${res.direction.id}`)
    } catch (e) {
      setDiscoverError(e instanceof Error ? e.message : String(e))
    } finally {
      setAdoptingId(null)
    }
  }

  if (loading) return <p className="muted">Loading…</p>
  if (loadError) return <p role="alert">{loadError}</p>

  return (
    <div>
      <Link to="/future">← Back to Explore my future</Link>
      <h1 style={{ fontSize: 22 }}>Design a career direction</h1>
      <p className="secondary" style={{ maxWidth: 700 }}>
        Start from the properties and trade-offs you want in a future career state — before choosing a title.
      </p>

      <fieldset className="form-stack card" disabled={saving || discoverBusy}>
        <legend>Name</legend>
        <label>
          Name this direction (required to save manually; optional for generating hypotheses)
          <input value={name} maxLength={300} onChange={(e) => setName(e.target.value)} placeholder="e.g. Technical actuarial leadership" />
        </label>
      </fieldset>

      <section style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <h2 style={{ fontSize: 16 }}>What matters most?</h2>
          <button type="button" onClick={seedFromPreferences} disabled={!preferenceSummary}>
            Use my recorded preferences as a starting point
          </button>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>
          Nothing below is pre-filled from what the system thinks it knows — set only what you actually want to state for this direction.
        </p>
        <div className="hub-grid">
          {dimensionCatalogue.map((d) => (
            <DimensionRow
              key={d.code}
              dimension={d}
              draft={dimensionDrafts[d.code]}
              suggestion={preferenceSummary?.[d.code]}
              onChange={(next) => setDimensionDrafts((prev) => ({ ...prev, [d.code]: next }))}
            />
          ))}
        </div>
      </section>

      <section className="card form-stack" style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 16, marginTop: 0 }}>Practical constraints</h2>
        <div className="form-grid">
          <label>
            Locations (one per line)
            <textarea rows={3} value={listToLines(constraints.locations)} onChange={(e) => setConstraints({ ...constraints, locations: linesToList(e.target.value) })} />
          </label>
          <label>
            Working mode (one per line — e.g. remote, hybrid, onsite)
            <textarea rows={3} value={listToLines(constraints.remote_types)} onChange={(e) => setConstraints({ ...constraints, remote_types: linesToList(e.target.value) })} />
          </label>
          <label>
            Employment type (one per line — e.g. permanent, contract)
            <textarea rows={3} value={listToLines(constraints.employment_types)} onChange={(e) => setConstraints({ ...constraints, employment_types: linesToList(e.target.value) })} />
          </label>
          <label>
            Seniority levels (one per line)
            <textarea rows={3} value={listToLines(constraints.seniority_levels)} onChange={(e) => setConstraints({ ...constraints, seniority_levels: linesToList(e.target.value) })} />
          </label>
        </div>

        <label style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <input
            type="checkbox" checked={hasFloor}
            onChange={(e) => {
              setHasFloor(e.target.checked)
              if (!e.target.checked) setConstraints({ ...constraints, compensation_floor: null })
              else if (!constraints.compensation_floor) {
                setConstraints({ ...constraints, compensation_floor: { amount: 0, currency: 'GBP', pay_period: 'annual', employment_basis: 'permanent', hard: false } })
              }
            }}
          />
          <span>Set a compensation floor</span>
        </label>
        {hasFloor && floor && (
          <div className="form-grid">
            <label>Amount<input type="number" min={0} value={floor.amount} onChange={(e) => setConstraints({ ...constraints, compensation_floor: { ...floor, amount: Number(e.target.value) } })} /></label>
            <label>Currency<input value={floor.currency} maxLength={3} onChange={(e) => setConstraints({ ...constraints, compensation_floor: { ...floor, currency: e.target.value.toUpperCase() } })} /></label>
            <label>
              Pay period
              <select value={floor.pay_period} onChange={(e) => setConstraints({ ...constraints, compensation_floor: { ...floor, pay_period: e.target.value as 'annual' | 'daily' } })}>
                <option value="annual">Annual</option>
                <option value="daily">Daily rate</option>
              </select>
            </label>
            <label>
              Basis
              <select value={floor.employment_basis} onChange={(e) => setConstraints({ ...constraints, compensation_floor: { ...floor, employment_basis: e.target.value as 'permanent' | 'contract' | 'unknown' } })}>
                <option value="permanent">Permanent</option>
                <option value="contract">Contract</option>
                <option value="unknown">Unknown</option>
              </select>
            </label>
            <label style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" checked={floor.hard} onChange={(e) => setConstraints({ ...constraints, compensation_floor: { ...floor, hard: e.target.checked } })} />
              <span>Hard floor (non-negotiable), not a soft preference</span>
            </label>
          </div>
        )}

        <label>
          Other context (one per line — your own intent, never treated as market evidence)
          <textarea rows={3} value={listToLines(constraints.other)} onChange={(e) => setConstraints({ ...constraints, other: linesToList(e.target.value) })} />
        </label>
      </section>

      <section className="card form-stack" style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 16, marginTop: 0 }}>Free context (optional)</h2>
        <label>
          Guidance for AI discovery
          <textarea rows={3} maxLength={4000} value={guidance} onChange={(e) => setGuidance(e.target.value)} />
        </label>
      </section>

      {saveError && <p role="alert">{saveError} Your inputs above are preserved.</p>}
      <div className="actions" style={{ marginTop: 16 }}>
        <button className="primary" disabled={saving || !name.trim()} onClick={saveManually}>
          {saving ? 'Saving…' : 'Save this direction manually'}
        </button>
        <button disabled={discoverBusy} onClick={generate}>
          {discoverBusy ? 'Generating…' : 'Generate direction hypotheses'}
        </button>
      </div>
      {!name.trim() && <p className="muted" style={{ fontSize: 12 }}>Manual save needs a name above; generating hypotheses does not.</p>}
      <p className="muted" style={{ fontSize: 12 }}>
        AI generation sends the properties above to your configured provider and records the run for review. No direction is saved or
        selected until you explicitly save or adopt one.
      </p>

      {discoverError && <p role="alert">{discoverError} Your inputs above are preserved — adjust and retry, or save manually.</p>}

      {discoverResult && (
        <section style={{ marginTop: 24 }}>
          <h2 style={{ fontSize: 18 }}>AI-generated hypotheses for review</h2>
          <p className="secondary" style={{ fontSize: 13 }}>None are selected or saved automatically.</p>
          <CorpusDisclosurePanel disclosure={discoverResult.corpus_disclosure} />
          {discoverResult.caveats.length > 0 && (
            <ul className="muted" style={{ fontSize: 12 }}>
              {discoverResult.caveats.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          )}
          {discoverResult.result.insufficient_evidence ? (
            <div className="card">
              <p style={{ margin: 0 }}>
                {discoverResult.result.insufficient_evidence_reason ?? 'No grounded hypothesis could be produced from the available evidence.'}
              </p>
              <p className="secondary" style={{ fontSize: 13, marginBottom: 0 }}>
                You can still save a direction manually above, or adjust your inputs and try again.
              </p>
            </div>
          ) : (
            <div className="hub-grid">
              {discoverResult.result.candidates.map((c) => (
                <CandidateCard key={c.id} candidate={c} adopting={adoptingId === c.id} onAdopt={(chosenName) => adopt(c.id, chosenName)} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
