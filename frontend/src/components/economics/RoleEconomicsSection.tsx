import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type ArchetypeCatalogueEntry,
  type CompensationProposalItem,
  type RoleArchetypeSummary,
  type RoleCompensationResponse,
} from '../../lib/api'
import { CompensationFigure, PersonalComparisonPanel } from './Compensation'

// Role/Target Detail's economic block (build §14): compensation with its
// basis, the personal comparison, the reviewed archetype, and the entry
// point into Pathways.
//
// Everything here loads from one GET that makes no model call. The two
// review affordances (extract stated compensation, suggest an archetype)
// are explicit buttons — nothing fans out to AI merely because the page
// opened.

function CompensationReview({
  roleId,
  hasSourceDocument,
  onAccepted,
}: {
  roleId: string
  hasSourceDocument: boolean
  onAccepted: () => void
}) {
  const [items, setItems] = useState<CompensationProposalItem[] | null>(null)
  const [noneStated, setNoneStated] = useState(false)
  const [notes, setNotes] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [accepted, setAccepted] = useState<number[]>([])

  const propose = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.proposeRoleCompensation(roleId)
      setItems(result.proposal.items)
      setNoneStated(result.proposal.no_compensation_stated)
      setNotes(result.proposal.notes)
      setAccepted([])
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  // "Edit & accept": the reviewer's corrections to the proposed figures. The
  // server re-applies every rule to an edited item exactly as it does to an
  // untouched one, so this is a convenience, never a shortcut past validation.
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<CompensationProposalItem | null>(null)

  const startEdit = (item: CompensationProposalItem, index: number) => {
    setEditing(index)
    setDraft({ ...item })
    setError(null)
  }

  const accept = async (item: CompensationProposalItem, index: number) => {
    setBusy(true)
    setError(null)
    try {
      await api.acceptRoleCompensation(roleId, {
        amount_min: item.amount_min,
        amount_max: item.amount_max,
        bonus_pct: item.bonus_pct,
        currency: item.currency,
        component: item.component as string,
        pay_period: item.pay_period as string,
        employment_basis: item.employment_basis,
        evidence_span: item.evidence_span as string,
        note: item.note,
      })
      setAccepted((prior) => [...prior, index])
      setEditing(null)
      onAccepted()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const numeric = (value: string) => (value.trim() === '' ? null : Number(value))

  if (!hasSourceDocument) {
    return (
      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        This role has no linked source document, so stated compensation cannot be reviewed against a source.
      </p>
    )
  }

  return (
    <div style={{ marginTop: 10 }}>
      <button type="button" onClick={propose} disabled={busy}>
        {busy ? 'Working…' : items ? 'Re-extract stated compensation' : 'Extract stated compensation from source'}
      </button>
      {error && (
        <p style={{ color: 'var(--critical)', fontSize: 13, marginTop: 6 }} role="alert">
          {error}
        </p>
      )}
      {noneStated && (
        <p className="muted" style={{ fontSize: 13, marginTop: 6 }}>
          No compensation is stated in this posting's source text.
        </p>
      )}
      {notes && (
        <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          {notes}
        </p>
      )}
      {items && items.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '10px 0 0' }}>
          {items.map((item, index) => (
            <li key={index} className="card" style={{ padding: 10, marginBottom: 8 }}>
              <div style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                {item.amount_min ?? '?'} – {item.amount_max ?? '?'} {item.currency ?? '(no currency)'}
              </div>
              <div className="muted" style={{ fontSize: 12 }}>
                {item.component ?? 'no component'} · {item.pay_period ?? 'no pay period'}
                {item.employment_basis ? ` · ${item.employment_basis}` : ''}
              </div>
              {item.evidence_span && (
                <blockquote
                  className="secondary"
                  style={{ margin: '6px 0', fontSize: 13, borderLeft: '2px solid var(--border)', paddingLeft: 8 }}
                >
                  {item.evidence_span}
                </blockquote>
              )}
              {item.problems.length > 0 && (
                <ul style={{ color: 'var(--critical)', fontSize: 12, margin: '4px 0', paddingLeft: 18 }}>
                  {item.problems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              )}
              {accepted.includes(index) ? (
                <span className="muted" style={{ fontSize: 12 }}>
                  Accepted as advert-stated compensation.
                </span>
              ) : editing === index && draft ? (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                  {draft.component === 'bonus_pct' ? (
                    <label style={{ fontSize: 12 }}>
                      Bonus %
                      <input
                        type="number"
                        aria-label="Bonus percentage"
                        value={draft.bonus_pct ?? ''}
                        onChange={(e) => setDraft({ ...draft, bonus_pct: numeric(e.target.value) })}
                        style={{ display: 'block', width: 90 }}
                      />
                    </label>
                  ) : (
                    <>
                      <label style={{ fontSize: 12 }}>
                        Minimum
                        <input
                          type="number"
                          aria-label="Minimum amount"
                          value={draft.amount_min ?? ''}
                          onChange={(e) => setDraft({ ...draft, amount_min: numeric(e.target.value) })}
                          style={{ display: 'block', width: 110 }}
                        />
                      </label>
                      <label style={{ fontSize: 12 }}>
                        Maximum
                        <input
                          type="number"
                          aria-label="Maximum amount"
                          value={draft.amount_max ?? ''}
                          onChange={(e) => setDraft({ ...draft, amount_max: numeric(e.target.value) })}
                          style={{ display: 'block', width: 110 }}
                        />
                      </label>
                      <label style={{ fontSize: 12 }}>
                        Currency
                        <input
                          type="text"
                          aria-label="Currency"
                          value={draft.currency ?? ''}
                          onChange={(e) => setDraft({ ...draft, currency: e.target.value.toUpperCase() })}
                          style={{ display: 'block', width: 70 }}
                        />
                      </label>
                    </>
                  )}
                  <button type="button" onClick={() => accept(draft, index)} disabled={busy}>
                    Save & accept
                  </button>
                  <button type="button" onClick={() => setEditing(null)} disabled={busy}>
                    Cancel
                  </button>
                </div>
              ) : (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="button" onClick={() => accept(item, index)} disabled={busy || !item.acceptable}>
                    Accept
                  </button>
                  <button type="button" onClick={() => startEdit(item, index)} disabled={busy}>
                    Edit &amp; accept
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
        A proposal is never stored. Declining one simply leaves nothing behind; accepting one records an
        advert-stated fact backed by the exact quote above.
      </p>
    </div>
  )
}

function ArchetypeReview({
  roleId,
  summary,
  onChanged,
}: {
  roleId: string
  summary: RoleArchetypeSummary | undefined
  onChanged: () => void
}) {
  const [catalogue, setCatalogue] = useState<ArchetypeCatalogueEntry[]>([])
  const [suggestion, setSuggestion] = useState<string | null>(null)
  const [rationale, setRationale] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (open && catalogue.length === 0) {
      api.getArchetypeCatalogue().then(setCatalogue).catch((e) => setError(String(e)))
    }
  }, [open, catalogue.length])

  const propose = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.proposeRoleArchetype(roleId)
      setSuggestion(result.proposal.archetype_concept_id)
      setRationale(result.proposal.rationale ?? result.proposal.note)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const assign = async (archetypeId: string | null) => {
    setBusy(true)
    setError(null)
    try {
      await api.setRoleArchetype(roleId, archetypeId)
      setSuggestion(null)
      onChanged()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      {summary?.assigned ? (
        <div>
          <div style={{ fontWeight: 600 }}>{summary.archetype_name}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {summary.seniority_band ?? 'No seniority band'}
            {summary.state === 'assigned_to_deprecated_archetype' ? ' · archetype is deprecated' : ''}
          </div>
        </div>
      ) : (
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          Unclassified. Leaving a role unclassified is a valid choice; Pathways simply has no archetype-level
          economics for it.
        </p>
      )}

      <button type="button" onClick={() => setOpen((prior) => !prior)} style={{ marginTop: 8 }}>
        {open ? 'Close' : summary?.assigned ? 'Change archetype' : 'Review archetype'}
      </button>

      {open && (
        <div style={{ marginTop: 8 }}>
          <button type="button" onClick={propose} disabled={busy || summary?.catalogue_size === 0}>
            {busy ? 'Working…' : 'Suggest an archetype'}
          </button>
          {summary?.catalogue_size === 0 && (
            <p className="muted" style={{ fontSize: 12 }}>
              No active archetypes exist yet. Create one on the <Link to="/economics">Economics</Link> page —
              archetypes are never created automatically.
            </p>
          )}
          {rationale && (
            <p className="secondary" style={{ fontSize: 13, marginTop: 6 }}>
              {rationale}
            </p>
          )}
          {suggestion && (
            <button type="button" onClick={() => assign(suggestion)} disabled={busy}>
              Accept suggestion
            </button>
          )}

          <label style={{ display: 'block', marginTop: 8 }}>
            <span className="muted" style={{ fontSize: 12 }}>
              Choose another
            </span>
            <select
              value={summary?.archetype_concept_id ?? ''}
              onChange={(e) => assign(e.target.value || null)}
              disabled={busy}
              style={{ display: 'block', marginTop: 4 }}
            >
              <option value="">Leave unclassified</option>
              {catalogue.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.canonical_name}
                  {entry.seniority_band ? ` (${entry.seniority_band})` : ''}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {error && (
        <p style={{ color: 'var(--critical)', fontSize: 13 }} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

/** One accepted or rejected observation, with the affordances to correct or
 * retire it. Both go through the compensation review lifecycle that already
 * exists (`reviewCompensationObservation` / `correctCompensationObservation`),
 * rather than a second mechanism specific to Role Detail. */
function ObservationRow({
  observation,
  onChanged,
}: {
  observation: RoleCompensationResponse['observations'][number]
  onChanged: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [min, setMin] = useState(observation.amount_min?.toString() ?? '')
  const [max, setMax] = useState(observation.amount_max?.toString() ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const act = async (run: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await run()
      setEditing(false)
      onChanged()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const amount =
    observation.bonus_pct !== null && observation.bonus_pct !== undefined
      ? `${observation.bonus_pct}%`
      : `${observation.amount_min ?? '?'} – ${observation.amount_max ?? '?'} ${observation.currency}`

  return (
    <li style={{ marginBottom: 8 }}>
      <span style={{ textDecoration: observation.review_status === 'rejected' ? 'line-through' : undefined }}>
        {amount}
      </span>{' '}
      <span className="muted">
        ({observation.component}, {observation.basis}, {observation.review_status})
      </span>
      {observation.evidence_span && (
        <div className="secondary" style={{ fontSize: 12 }}>
          “{observation.evidence_span}”
        </div>
      )}
      {observation.source_note && (
        <div className="muted" style={{ fontSize: 12 }}>
          {observation.source_note}
        </div>
      )}

      {observation.review_status === 'accepted' && !editing && (
        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          <button type="button" onClick={() => setEditing(true)} disabled={busy}>
            Correct
          </button>
          <button
            type="button"
            onClick={() => act(() => api.reviewCompensationObservation(observation.id, 'reject'))}
            disabled={busy}
          >
            Reject
          </button>
        </div>
      )}

      {observation.review_status === 'rejected' && (
        <button
          type="button"
          onClick={() => act(() => api.reviewCompensationObservation(observation.id, 'accept'))}
          disabled={busy}
          style={{ marginTop: 4 }}
        >
          Re-accept
        </button>
      )}

      {editing && (
        <div style={{ display: 'flex', gap: 8, marginTop: 4, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12 }}>
            Minimum
            <input
              type="number"
              aria-label={`Minimum amount for ${observation.component}`}
              value={min}
              onChange={(e) => setMin(e.target.value)}
              style={{ display: 'block', width: 110 }}
            />
          </label>
          <label style={{ fontSize: 12 }}>
            Maximum
            <input
              type="number"
              aria-label={`Maximum amount for ${observation.component}`}
              value={max}
              onChange={(e) => setMax(e.target.value)}
              style={{ display: 'block', width: 110 }}
            />
          </label>
          <button
            type="button"
            // The shared correction endpoint only ever changes fields that
            // are supplied, so a blank box means "leave as is", not "clear".
            // With both blank there is nothing to correct.
            disabled={busy || (min.trim() === '' && max.trim() === '')}
            onClick={() =>
              act(() =>
                api.correctCompensationObservation(observation.id, {
                  ...(min.trim() === '' ? {} : { amount_min: Number(min) }),
                  ...(max.trim() === '' ? {} : { amount_max: Number(max) }),
                }),
              )
            }
          >
            Save correction
          </button>
          <button type="button" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </button>
        </div>
      )}

      {error && (
        <p style={{ color: 'var(--critical)', fontSize: 12 }} role="alert">
          {error}
        </p>
      )}
    </li>
  )
}

export function RoleEconomicsSection({
  roleId,
  isTarget,
  hasSourceDocument,
  archetype,
  onArchetypeChanged,
}: {
  roleId: string
  isTarget: boolean
  hasSourceDocument: boolean
  archetype: RoleArchetypeSummary | undefined
  onArchetypeChanged: () => void
}) {
  const [data, setData] = useState<RoleCompensationResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .getRoleCompensation(roleId)
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [roleId])

  useEffect(load, [load])

  return (
    <div className="form-grid" style={{ marginTop: 16 }}>
      <section className="card">
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Compensation</h3>
        {error && <p style={{ color: 'var(--critical)', fontSize: 13 }}>{error}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && (
          <>
            <CompensationFigure compensation={data.compensation} />

            <h4 style={{ fontSize: 13, margin: '16px 0 6px' }}>Your comparison</h4>
            <PersonalComparisonPanel comparison={data.personal_comparison} />

            {data.observations.length > 0 && (
              <details style={{ marginTop: 12 }}>
                <summary className="muted" style={{ fontSize: 12, cursor: 'pointer' }}>
                  Compensation evidence on this role ({data.observations.length})
                </summary>
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13 }}>
                  {data.observations.map((observation) => (
                    <ObservationRow key={observation.id} observation={observation} onChanged={load} />
                  ))}
                </ul>
              </details>
            )}

            {!isTarget && (
              <CompensationReview roleId={roleId} hasSourceDocument={hasSourceDocument} onAccepted={load} />
            )}
          </>
        )}
      </section>

      <section className="card">
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Archetype</h3>
        <ArchetypeReview roleId={roleId} summary={archetype} onChanged={onArchetypeChanged} />

        <h3 style={{ fontSize: 14, marginTop: 20 }}>Pathways</h3>
        {isTarget ? (
          <p style={{ fontSize: 13, margin: 0 }}>
            <Link to={`/pathways/${roleId}`}>Explore path to this role</Link> — the direct route, useful
            intermediate archetypes, and what each would pay.
          </p>
        ) : (
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            Pathways is built around a target. <Link to="/targets/new">Add this as a target</Link> to explore
            routes towards it.
          </p>
        )}
      </section>
    </div>
  )
}
