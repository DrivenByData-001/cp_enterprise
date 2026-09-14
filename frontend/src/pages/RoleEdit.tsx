import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Role, type RoleMetadataInput, type TargetDraft } from '../lib/api'
import RoleMetadataForm from '../components/RoleMetadataForm'
import TargetDraftEditor from '../components/TargetDraftEditor'

type Result = { ok: boolean; message: string }

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
  const [metadata, setMetadata] = useState<RoleMetadataInput>(metadataFromRole(role))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  const submit = async () => {
    setBusy(true)
    setResult(null)
    try {
      await api.updateRoleMetadata(role.id, metadata)
      setResult({ ok: true, message: 'Metadata saved.' })
      setTimeout(() => navigate(`/roles/${role.id}`), 700)
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
        <Link to={`/role-instances/${role.id}/requirements`}>Requirements</Link> page.
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
        <button onClick={() => navigate(`/roles/${role.id}`)} disabled={busy}>
          Cancel
        </button>
      </div>
      {result && <p style={{ marginTop: 16, color: result.ok ? 'var(--good)' : 'var(--critical)' }}>{result.message}</p>}
    </div>
  )
}

function LegacyJsonEditor({ role }: { role: Role }) {
  const navigate = useNavigate()
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
      setTimeout(() => navigate(`/roles/${role.id}`), 700)
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
          <button onClick={() => navigate(`/roles/${role.id}`)} disabled={busy}>
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
      try { await api.updateTarget(role.id, draft); navigate(`/roles/${role.id}`) }
      catch (e) { setError(String(e)) }
      finally { setBusy(false) }
    }}>{busy ? 'Saving…' : 'Save target changes'}</button>
    {role.raw_json != null && <details><summary>Advanced: replace target JSON</summary><LegacyJsonEditor role={role} /></details>}
  </div>
}

export default function RoleEdit() {
  const { id } = useParams()
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
      <Link to={`/roles/${role.id}`} className="muted" style={{ fontSize: 13 }}>
        ← Back to {role.title}
      </Link>

      <h1 style={{ fontSize: 22, marginTop: 12 }}>Edit "{role.title}"</h1>

      {isTarget && <SavedTargetEditor role={role} />}
      {hasRawJson && !isTarget && <LegacyJsonEditor role={role} />}

      {!hasRawJson && !isTarget && <SourceAwareMetadataEditor role={role} />}

    </div>
  )
}
