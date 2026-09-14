import { useEffect, useState } from 'react'
import { api, type Concept, type TargetDraft, type TargetRequirementMapping } from '../lib/api'

export default function TargetRequirementPicker({ skill, onChange, mapping, error, onRetry }: {
  skill: TargetDraft['skills'][number]; onChange: (skill: TargetDraft['skills'][number]) => void
  mapping: TargetRequirementMapping | null; error: string; onRetry: () => void
}) {
  const [query, setQuery] = useState('')
  const [options, setOptions] = useState<Concept[]>([])
  const [searchError, setSearchError] = useState('')
  useEffect(() => {
    let active = true
    setOptions([]); setSearchError('')
    if (!query.trim()) return
    const timer = setTimeout(() => {
      api.listConcepts({ q: query, status: 'active' }).then(rows => { if (active) setOptions(rows) })
        .catch(e => { if (active) setSearchError(String(e)) })
    }, 200)
    return () => { active = false; clearTimeout(timer) }
  }, [query])
  return <div className="form-stack">
    <p role="status">{error ? 'Mapping could not be checked — needs review.' : !mapping ? 'Checking vocabulary mapping…' :
      mapping.mapping_status === 'mapped' ? `Mapped → ${mapping.canonical_name}` : 'Unmapped — excluded from analysis; needs review.'}</p>
    {error && <><p role="alert">{error}</p><button type="button" onClick={onRetry}>Retry mapping check</button></>}
    <label>Search vocabulary for {skill.name || 'this requirement'}<input value={query} onChange={e => setQuery(e.target.value)} /></label>
    {searchError && <p role="alert">Vocabulary search failed: {searchError}. Change the search to retry.</p>}
    {options.slice(0, 30).map(c => <button type="button" key={c.id} onClick={() => {
      onChange({ ...skill, concept_id: c.id, mapping_reviewed: true }); setQuery('')
    }}>Map to {c.canonical_name}</button>)}
    <button type="button" onClick={() => onChange({ ...skill, concept_id: null, mapping_reviewed: true })}>Leave unmapped</button>
  </div>
}
