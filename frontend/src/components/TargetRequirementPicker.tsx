import { useEffect, useState } from 'react'
import { api, type Concept, type TargetDraft, type TargetRequirementMapping } from '../lib/api'

export default function TargetRequirementPicker({ skill, onChange }: {
  skill: TargetDraft['skills'][number]; onChange: (skill: TargetDraft['skills'][number]) => void
}) {
  const [mapping, setMapping] = useState<TargetRequirementMapping | null>(null)
  const [query, setQuery] = useState('')
  const [options, setOptions] = useState<Concept[]>([])
  const [error, setError] = useState('')
  const [searchError, setSearchError] = useState('')
  const [retry, setRetry] = useState(0)
  const { name, concept_id, mapping_reviewed } = skill
  useEffect(() => {
    let active = true
    setMapping(null); setError('')
    const timer = setTimeout(() => {
      api.resolveTargetRequirements([{ name, concept_id, mapping_reviewed }]).then(rows => { if (active) setMapping(rows[0]) })
        .catch(e => { if (active) setError(String(e)) })
    }, 200)
    return () => { active = false; clearTimeout(timer) }
  }, [name, concept_id, mapping_reviewed, retry])
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
    {error && <><p role="alert">{error}</p><button type="button" onClick={() => setRetry(n => n + 1)}>Retry mapping check</button></>}
    <label>Search vocabulary for {skill.name || 'this requirement'}<input value={query} onChange={e => setQuery(e.target.value)} /></label>
    {searchError && <p role="alert">Vocabulary search failed: {searchError}. Change the search to retry.</p>}
    {options.slice(0, 30).map(c => <button type="button" key={c.id} onClick={() => {
      onChange({ ...skill, concept_id: c.id, mapping_reviewed: true }); setQuery('')
    }}>Map to {c.canonical_name}</button>)}
    <button type="button" onClick={() => onChange({ ...skill, concept_id: null, mapping_reviewed: true })}>Leave unmapped</button>
  </div>
}
