import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type ManualCompensationInput, type Role, type RoleCompensationObservation, type RoleMetadataInput, type TargetDraft } from '../lib/api'
import { ApplicationContextBanner } from '../components/WorkflowContextBanner'
import { useWorkflowContext } from '../lib/workflowContext'
import RoleMetadataForm from '../components/RoleMetadataForm'
import TargetDraftEditor from '../components/TargetDraftEditor'

type Result = { ok: boolean; message: string }

// --- Manual compensation (Phase 3 addendum) ---------------------------------
//
// "Add/update compensation" for a posting: salary discovered after capture,
// with no passage in the source document to quote. Uses the same
// jobber.compensation_observation model as every other compensation path —
// never the legacy salary_min/salary_max columns — and the same component/
// pay-period/amount/currency shape validation the source-backed review path
// uses (server-side, app/posting_compensation.py::_value_problems), just
// without a quote requirement. Only observations with no evidence_span
// (i.e. recorded through this same manual path) are editable here; a
// source-quoted figure is reviewed from the Economics section instead.

const MANUAL_COMPONENTS: { value: string; label: string; period: string | null }[] = [
  { value: 'base', label: 'Base salary', period: 'annual' },
  { value: 'day_rate', label: 'Day rate', period: 'daily' },
  { value: 'total_package', label: 'Total package', period: 'annual' },
  { value: 'bonus_pct', label: 'Bonus (%)', period: null },
]
const MANUAL_PAY_PERIODS = ['annual', 'daily']
const MANUAL_EMPLOYMENT_BASES = ['permanent', 'contract', 'unknown']

function emptyManualForm() {
  return { component: 'base', payPeriod: 'annual', employmentBasis: '', currency: '', amountMin: '', amountMax: '', bonusPct: '', note: '' }
}

function ManualCompensationSection({ roleId }: { roleId: string }) {
  const [observations, setObservations] = useState<RoleCompensationObservation[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [form, setForm] = useState(emptyManualForm())
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    api
      .getRoleCompensation(roleId)
      .then((r) => setObservations(r.observations))
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)))
  }
  useEffect(load, [roleId])

  const numeric = (value: string): number | null => (value.trim() === '' ? null : Number(value))
  const impliedPeriod = (component: string) => MANUAL_COMPONENTS.find((c) => c.value === component)?.period ?? null

  const startEdit = (obs: RoleCompensationObservation) => {
    setEditingId(obs.id)
    setForm({
      component: obs.component,
      payPeriod: obs.pay_period,
      employmentBasis: obs.employment_basis ?? '',
      currency: obs.currency ?? '',
      amountMin: obs.amount_min?.toString() ?? '',
      amountMax: obs.amount_max?.toString() ?? '',
      bonusPct: obs.bonus_pct?.toString() ?? '',
      note: obs.source_note ?? '',
    })
    setError(null)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setForm(emptyManualForm())
    setError(null)
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    const payload: ManualCompensationInput = {
      component: form.component,
      pay_period: form.payPeriod,
      employment_basis: form.employmentBasis || null,
      currency: form.currency || null,
      amount_min: numeric(form.amountMin),
      amount_max: numeric(form.amountMax),
      bonus_pct: numeric(form.bonusPct),
      note: form.note || null,
    }
    try {
      if (editingId) {
        await api.correctManualRoleCompensation(roleId, editingId, payload)
      } else {
        await api.acceptManualRoleCompensation(roleId, payload)
      }
      cancelEdit()
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const reject = async (observationId: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.rejectRoleCompensation(roleId, observationId)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const editableObservations = (observations ?? []).filter((o) => o.evidence_span == null)
  const quotedObservations = (observations ?? []).filter((o) => o.evidence_span != null && o.review_status === 'accepted')

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>Add/update compensation</h3>
      <p className="secondary" style={{ marginTop: 0, fontSize: 13 }}>
        Record compensation discovered after this posting was captured — a recruiter conversation, a separate
        disclosure, research elsewhere. This is stored as a proper compensation observation (never the legacy salary
        fields) and becomes this role's own resolved compensation, same as a quoted figure would.
      </p>

      {loadError && <p role="alert" style={{ color: 'var(--critical)', fontSize: 13 }}>{loadError}</p>}

      {quotedObservations.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          This role also has {quotedObservations.length} source-quoted compensation{' '}
          {quotedObservations.length === 1 ? 'figure' : 'figures'} — reviewed from the opportunity's Economics
          section, not here.
        </p>
      )}

      {editableObservations.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0' }}>
          {editableObservations.map((obs) => (
            <li key={obs.id} className="card" style={{ padding: 10, marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                    {obs.component === 'bonus_pct'
                      ? `${obs.bonus_pct ?? '?'}%`
                      : `${obs.amount_min ?? '?'} – ${obs.amount_max ?? '?'} ${obs.currency ?? ''}`}
                  </div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {obs.component} · {obs.pay_period}
                    {obs.employment_basis ? ` · ${obs.employment_basis}` : ''} · {obs.review_status}
                  </div>
                  {obs.source_note && (
                    <div className="secondary" style={{ fontSize: 12, marginTop: 4 }}>
                      {obs.source_note}
                    </div>
                  )}
                </div>
                {obs.review_status === 'accepted' && (
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <button type="button" onClick={() => startEdit(obs)} disabled={busy} style={{ fontSize: 12, padding: '3px 8px' }}>
                      Edit
                    </button>
                    <button type="button" onClick={() => reject(obs.id)} disabled={busy} style={{ fontSize: 12, padding: '3px 8px', color: 'var(--critical)' }}>
                      Reject
                    </button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end', marginTop: 8 }}>
        <label style={{ fontSize: 12 }}>
          Component
          <select
            aria-label="Component"
            value={form.component}
            onChange={(e) => {
              const chosen = MANUAL_COMPONENTS.find((c) => c.value === e.target.value)
              setForm({ ...form, component: e.target.value, payPeriod: chosen?.period ?? form.payPeriod })
            }}
            style={{ display: 'block' }}
          >
            {MANUAL_COMPONENTS.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12 }}>
          Pay period
          <select
            aria-label="Pay period"
            value={form.payPeriod}
            disabled={impliedPeriod(form.component) !== null}
            title={impliedPeriod(form.component) !== null ? 'Set by the component.' : undefined}
            onChange={(e) => setForm({ ...form, payPeriod: e.target.value })}
            style={{ display: 'block' }}
          >
            {MANUAL_PAY_PERIODS.map((period) => (
              <option key={period} value={period}>
                {period}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12 }}>
          Employment basis
          <select
            aria-label="Employment basis"
            value={form.employmentBasis}
            onChange={(e) => setForm({ ...form, employmentBasis: e.target.value })}
            style={{ display: 'block' }}
          >
            <option value="">Unspecified</option>
            {MANUAL_EMPLOYMENT_BASES.map((basis) => (
              <option key={basis} value={basis}>
                {basis}
              </option>
            ))}
          </select>
        </label>
        {form.component === 'bonus_pct' ? (
          <label style={{ fontSize: 12 }}>
            Bonus %
            <input
              type="number"
              aria-label="Bonus percentage"
              value={form.bonusPct}
              onChange={(e) => setForm({ ...form, bonusPct: e.target.value })}
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
                value={form.amountMin}
                onChange={(e) => setForm({ ...form, amountMin: e.target.value })}
                style={{ display: 'block', width: 110 }}
              />
            </label>
            <label style={{ fontSize: 12 }}>
              Maximum
              <input
                type="number"
                aria-label="Maximum amount"
                value={form.amountMax}
                onChange={(e) => setForm({ ...form, amountMax: e.target.value })}
                style={{ display: 'block', width: 110 }}
              />
            </label>
            <label style={{ fontSize: 12 }}>
              Currency
              <input
                type="text"
                aria-label="Currency"
                value={form.currency}
                onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })}
                style={{ display: 'block', width: 70 }}
              />
            </label>
          </>
        )}
        <label style={{ fontSize: 12, flex: '1 1 220px' }}>
          Source / evidence note
          <input
            type="text"
            aria-label="Source or evidence note"
            value={form.note}
            placeholder="e.g. told by recruiter on a call"
            onChange={(e) => setForm({ ...form, note: e.target.value })}
            style={{ display: 'block', width: '100%' }}
          />
        </label>
      </div>

      {error && (
        <p role="alert" style={{ color: 'var(--critical)', fontSize: 13, marginTop: 8 }}>
          {error}
        </p>
      )}

      <div className="actions" style={{ marginTop: 10 }}>
        <button type="button" className="primary" onClick={submit} disabled={busy}>
          {busy ? 'Saving…' : editingId ? 'Save correction' : 'Add compensation'}
        </button>
        {editingId && (
          <button type="button" onClick={cancelEdit} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}

function metadataFromRole(role: Role): RoleMetadataInput {
  return {
    title: role.title,
    organisation: role.organisation,
    location: role.location,
    country: role.country,
    remote_type: role.remote_type,
    employment_type: role.employment_type,
    seniority_level: role.seniority_level,
    posting_date: role.posting_date,
  }
}

// Source-aware roles (captured via /api/role-instances/ingest, PDF or
// pasted text) have no `raw_json` — there was never a full JobPostingImport
// extraction to overwrite. Editing metadata for one of these goes through
// the lighter PATCH /api/role-instances/{id}/metadata instead of the
// legacy full-JSON overwrite below (source-aware ingest cleanup, problem
// #7). Never touches the immutable source document, skills, or requirement
// claims — reinterpreting the source stays a separate, explicit action
// (Requirements page).
function SourceAwareMetadataEditor({ role }: { role: Role }) {
  const navigate = useNavigate()
  const workflow = useWorkflowContext()
  const [metadata, setMetadata] = useState<RoleMetadataInput>(metadataFromRole(role))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  const submit = async () => {
    setBusy(true)
    setResult(null)
    try {
      await api.updateRoleMetadata(role.id, metadata)
      setResult({ ok: true, message: 'Metadata saved.' })
      setTimeout(() => navigate(workflow.link(`/roles/${role.id}`)), 700)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>Metadata</h3>
      <p className="secondary" style={{ marginTop: 0, fontSize: 13 }}>
        This role was captured as an immutable source document rather than a full AI extraction, so there is no JSON
        to overwrite here — correct the fields below directly. This never modifies the captured source text; re-running
        requirement extraction against it stays a separate action on the{' '}
        <Link to={workflow.link(`/role-instances/${role.id}/requirements`)}>Requirements</Link> page.
      </p>
      {role.url && (
        <p className="muted" style={{ fontSize: 12 }}>
          Source URL:{' '}
          <a href={role.url} target="_blank" rel="noreferrer">
            {role.url}
          </a>{' '}
          (read-only — tied to the immutable source document)
        </p>
      )}
      <RoleMetadataForm value={metadata} onChange={setMetadata} disabled={busy} />
      <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
        <button className="primary" onClick={submit} disabled={busy}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
        <button onClick={() => navigate(workflow.link(`/roles/${role.id}`))} disabled={busy}>
          Cancel
        </button>
      </div>
      {result && <p style={{ marginTop: 16, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>}
    </div>
  )
}

function LegacyJsonEditor({ role }: { role: Role }) {
  const navigate = useNavigate()
  const workflow = useWorkflowContext()
  const isTarget = role.node_type !== 'posting'
  const promptName = isTarget ? 'prompts/decompose_target_role.md' : 'prompts/extract_job_posting.md'
  const [text, setText] = useState(() => JSON.stringify(role.raw_json, null, 2))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  const submit = async () => {
    setBusy(true)
    setResult(null)
    try {
      const parsed = JSON.parse(text)
      if (isTarget) {
        await api.updateTarget(role.id, parsed)
      } else {
        await api.updateRole(role.id, parsed)
      }
      setResult({ ok: true, message: 'Saved. Re-embedded from the updated content.' })
      setTimeout(() => navigate(isTarget ? `/targets/${role.id}` : workflow.link(`/roles/${role.id}`)), 700)
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <p className="secondary">
        To add more info to this {isTarget ? 'target' : 'posting'} (e.g. you found extra detail, or want to
        re-research it with new supporting material), go back to Claude/ChatGPT with{' '}
        <code>{promptName}</code>, get an updated JSON, and paste the full object below. This replaces the stored
        fields and skills for this entry and re-embeds it — it's a full overwrite, not a merge, so paste the
        complete object each time.
      </p>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>Updated JSON</h3>
        <textarea
          rows={18}
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
        />
        <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
          <button className="primary" onClick={submit} disabled={busy || !text.trim()}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
          <button onClick={() => navigate(isTarget ? `/targets/${role.id}` : workflow.link(`/roles/${role.id}`))} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>

      {result && (
        <p style={{ marginTop: 16, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>
      )}
    </>
  )
}

function SavedTargetEditor({ role }: { role: Role }) {
  const navigate = useNavigate()
  const [draft, setDraft] = useState<TargetDraft>(() => {
    const raw = role.raw_json as Partial<TargetDraft> | null
    const target = raw?.target
    return {
      metadata: raw?.metadata ?? { source: 'user_defined', notes_for_user: role.extraction_notes },
      target: { ...target, title: target?.title ?? role.title, is_imagined: target?.is_imagined ?? role.node_type === 'target_imagined',
        description: target?.description ?? role.description, organisation: target?.organisation ?? role.organisation,
        summary: target?.summary ?? role.summary, career_track: target?.career_track ?? role.career_track,
        seniority_level: target?.seniority_level ?? role.seniority_level,
        grounding_note: target?.grounding_note ?? role.grounding_note,
        feasibility_note: target?.feasibility_note ?? role.feasibility_note,
        is_plausible: target?.is_plausible ?? role.is_plausible,
        typical_tasks: target?.typical_tasks ?? role.typical_tasks ?? [],
        skill_decomposition: target?.skill_decomposition ?? role.skill_decomposition ?? [],
        technical_subjects: target?.technical_subjects ?? role.technical_subjects ?? [] },
      skills: raw?.skills ?? role.skills?.map(skill => ({ ...skill, concept_id: skill.resolved_concept_id, mapping_reviewed: true })) ?? (role.path?.target_mapping?.items ?? []).map(item => ({
        name: item.name, concept_id: item.concept_id, mapping_reviewed: true,
      })),
    }
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <div className="form-stack">
    <TargetDraftEditor value={draft} onChange={setDraft} disabled={busy} />
    {error && <p role="alert">{error} Your edits are preserved.</p>}
    <button disabled={busy || !draft.target.title.trim()} onClick={async () => {
      setBusy(true); setError('')
      try { await api.updateTarget(role.id, draft); navigate(`/targets/${role.id}`) }
      catch (e) { setError(String(e)) }
      finally { setBusy(false) }
    }}>{busy ? 'Saving…' : 'Save target changes'}</button>
    {role.raw_json != null && <details><summary>Advanced: replace target JSON</summary><LegacyJsonEditor role={role} /></details>}
  </div>
}

export default function RoleEdit() {
  const { id } = useParams()
  const workflow = useWorkflowContext()
  const [role, setRole] = useState<Role | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api.getRole(id).then(setRole).catch((e) => setLoadError(String(e)))
  }, [id])

  if (loadError) return <p style={{ color: 'var(--critical)' }}>{loadError}</p>
  if (!role) return <p className="muted">Loading…</p>

  const isTarget = role.node_type !== 'posting'
  const hasRawJson = role.raw_json != null

  return (
    <div>
      {!isTarget && <ApplicationContextBanner roleId={role.id} />}
      <Link to={isTarget ? `/targets/${role.id}` : workflow.link(`/roles/${role.id}`)} className="muted" style={{ fontSize: 13 }}>
        ← Back to {role.title}
      </Link>

      <h1 style={{ fontSize: 22, marginTop: 12 }}>Edit "{role.title}"</h1>

      {isTarget && <SavedTargetEditor role={role} />}
      {hasRawJson && !isTarget && <LegacyJsonEditor role={role} />}

      {!hasRawJson && !isTarget && <SourceAwareMetadataEditor role={role} />}

      {!isTarget && <ManualCompensationSection roleId={role.id} />}

    </div>
  )
}
