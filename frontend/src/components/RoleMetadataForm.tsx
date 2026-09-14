import type { RoleMetadataInput } from '../lib/api'

// Shared by RoleEdit.tsx (source-aware roles with no raw_json) and the
// metadata-enrichment review panel on RoleRequirements.tsx — the same field
// set/controlled vocabulary either way (source-aware ingest cleanup,
// problems #5/#7). Deliberately excludes source URL: that lives on the
// immutable source document, not the role, and is shown read-only by
// callers that have it — never editable from here.

const REMOTE_TYPES = ['onsite', 'hybrid', 'remote', 'unknown']
const EMPLOYMENT_TYPES = ['full_time', 'part_time', 'contract', 'internship', 'unknown']
const SENIORITY_LEVELS = ['junior', 'mid', 'senior', 'lead', 'unknown']

function labelize(value: string): string {
  return value.replace(/_/g, ' ')
}

export default function RoleMetadataForm({
  value,
  onChange,
  disabled = false,
}: {
  value: RoleMetadataInput
  onChange: (next: RoleMetadataInput) => void
  disabled?: boolean
}) {
  const set = <K extends keyof RoleMetadataInput>(key: K, v: RoleMetadataInput[K]) => onChange({ ...value, [key]: v })

  return (
    <div className="form-grid">
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Title
        <input value={value.title ?? ''} disabled={disabled} onChange={(e) => set('title', e.target.value || null)} />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Employer / Organisation
        <input
          value={value.organisation ?? ''}
          disabled={disabled}
          onChange={(e) => set('organisation', e.target.value || null)}
        />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Location
        <input value={value.location ?? ''} disabled={disabled} onChange={(e) => set('location', e.target.value || null)} />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Country
        <input value={value.country ?? ''} disabled={disabled} onChange={(e) => set('country', e.target.value || null)} />
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Posting date
        <input
          type="date"
          value={value.posting_date ?? ''}
          disabled={disabled}
          onChange={(e) => set('posting_date', e.target.value || null)}
        />
        <span className="muted" style={{ fontSize: 11 }}>
          Leave blank if unknown — never the capture/upload date.
        </span>
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Employment type
        <select value={value.employment_type ?? ''} disabled={disabled} onChange={(e) => set('employment_type', e.target.value || null)}>
          <option value="">Unspecified</option>
          {EMPLOYMENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {labelize(t)}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Seniority
        <select value={value.seniority_level ?? ''} disabled={disabled} onChange={(e) => set('seniority_level', e.target.value || null)}>
          <option value="">Unspecified</option>
          {SENIORITY_LEVELS.map((s) => (
            <option key={s} value={s}>
              {labelize(s)}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
        Remote type
        <select value={value.remote_type ?? ''} disabled={disabled} onChange={(e) => set('remote_type', e.target.value || null)}>
          <option value="">Unspecified</option>
          {REMOTE_TYPES.map((r) => (
            <option key={r} value={r}>
              {labelize(r)}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
