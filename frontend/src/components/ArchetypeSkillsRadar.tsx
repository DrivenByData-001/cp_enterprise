import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Legend, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip } from 'recharts'
import { api, type Archetype, type ArchetypeSkillProfile } from '../lib/api'

const LABELS = { evidenced: 'Evidenced', partial: 'Partially evidenced', user_asserted: 'User asserted', not_found: 'No evidence found', unconfigured: 'Assessment not configured' }

export default function ArchetypeSkillsRadar() {
  const [archetypes, setArchetypes] = useState<Archetype[]>([])
  const [selected, setSelected] = useState('')
  const [profile, setProfile] = useState<ArchetypeSkillProfile | null>(null)
  const [chosen, setChosen] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [chartWidth, setChartWidth] = useState(0)

  useEffect(() => {
    let active = true
    api.listArchetypes().then(rows => {
      if (!active) return
      setArchetypes(rows)
      setSelected(rows[0]?.id ?? '')
      if (!rows.length) setLoading(false)
    }).catch(e => { if (active) { setError(String(e)); setLoading(false) } })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!selected) return
    let active = true
    setProfile(null); setLoading(true); setError(''); setMessage(''); setEditing(false); setQuery('')
    api.getArchetypeSkillProfile(selected).then(data => {
      if (!active) return
      setProfile(data); setChosen(data.axes.map(axis => axis.id))
    }).catch(e => { if (active) setError(String(e)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [selected])

  const save = async () => {
    setSaving(true); setError(''); setMessage('')
    try {
      const data = await api.saveArchetypeSkillProfile(selected, chosen)
      setProfile(data); setChosen(data.axes.map(axis => axis.id)); setEditing(false)
      setMessage('Target skillset saved for this archetype.')
    } catch (e) { setError(String(e)) }
    finally { setSaving(false) }
  }
  const candidates = profile?.candidates.filter(skill => chosen.includes(skill.id) || skill.canonical_name.toLowerCase().includes(query.toLowerCase())) ?? []
  const chartReady = profile && profile.axes.length >= 3 && profile.axes.every(axis => axis.coverage !== null)

  return <section className="card" aria-labelledby="archetype-skills-heading" style={{ margin: '20px 0' }}>
    <h2 id="archetype-skills-heading">My skills vs a typical role</h2>
    <p>Compare your evidence with a focused target skillset for a role archetype. Choose three to eight skills to keep the spider chart readable.</p>
    <label>Typical role{' '}
      <select value={selected} disabled={saving} onChange={e => setSelected(e.target.value)}>
        {!archetypes.length && <option value="">No archetypes available</option>}
        {archetypes.map(role => <option key={role.id} value={role.id}>{role.canonical_name}</option>)}
      </select>
    </label>
    {loading && <p role="status">Loading skill comparison…</p>}
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {!loading && !archetypes.length && !error && <p>Create a role archetype in <Link to="/vocabulary">Vocabulary</Link> to start comparing.</p>}
    {profile && <>
      <p className="secondary">{profile.source === 'saved' ? 'Saved target skillset.' : 'Suggested target: the most frequently required skills in assigned postings; review and save to make this your target.'} Based on {profile.sample_role_count} assigned posting{profile.sample_role_count === 1 ? '' : 's'}, including historic roles.</p>
      {!!profile.unavailable_skill_ids.length && <p role="alert">Some saved skills are no longer active. Edit the target skillset to replace them.</p>}
      {chartReady ? <div aria-label="Spider chart comparing my evidence coverage with the role target" role="img" style={{ width: '100%', height: 390 }}>
        <ResponsiveContainer width="100%" height="100%" onResize={width => setChartWidth(width)}>
          <RadarChart data={profile.axes} outerRadius="75%" margin={{ top: 25, right: 35, bottom: 25, left: 35 }}>
            <PolarGrid />
            <PolarAngleAxis dataKey="canonical_name" tick={{ fontSize: 12 }} tickFormatter={(value: string) => chartWidth < 500 ? String(profile.axes.findIndex(axis => axis.canonical_name === value) + 1) : value.length > 25 ? `${value.slice(0, 23)}…` : value} />
            <PolarRadiusAxis domain={[0, 100]} ticks={[0, 25, 50, 100]} tick={{ fontSize: 11 }} />
            <Radar name="Role target" dataKey="target" stroke="#64748b" fill="#64748b" fillOpacity={0.08} strokeDasharray="5 4" isAnimationActive={false} />
            <Radar name="My evidence coverage" dataKey="coverage" stroke="#2563eb" fill="#2563eb" fillOpacity={0.3} isAnimationActive={false} />
            <Tooltip /><Legend />
          </RadarChart>
        </ResponsiveContainer>
      </div> : <p>{profile.axes.some(axis => axis.coverage === null) ? 'Configure the missing capability assessments to draw the chart.' : 'At least three skills are needed for a spider chart. Edit the target skillset to choose them.'}</p>}
      <p className="muted">Evidence scale: 100 = evidenced, 50 = partial, 25 = asserted, 0 = no evidence found. The role target is full evidence for each selected skill. This measures evidence coverage, not proficiency; no evidence found does not mean you lack the skill.</p>
      {!!profile.axes.length && <div style={{ overflowX: 'auto' }}><table className="archetype-skills-table">
        <caption>Skills coverage and role target{chartWidth < 500 ? ' — chart numbers match the skills below' : ''}</caption>
        <thead><tr><th>Skill</th><th>My evidence</th><th>Role target</th><th>Postings requiring it</th></tr></thead>
        <tbody>{profile.axes.map((axis, index) => <tr key={axis.id}><th scope="row">{index + 1}. {axis.canonical_name}</th><td>{LABELS[axis.status]}{axis.coverage !== null ? ` (${axis.coverage})` : ''}</td><td>Evidenced (100)</td><td>{axis.role_count} / {profile.sample_role_count}</td></tr>)}</tbody>
      </table></div>}
      <button disabled={saving} onClick={() => { setEditing(!editing); setChosen(profile.axes.map(axis => axis.id)); setQuery('') }}>{editing ? 'Cancel editing' : 'Edit target skillset'}</button>
      {editing && <fieldset disabled={saving} style={{ marginTop: 16 }}>
        <legend>Target skills ({chosen.length}/8 selected)</legend>
        <label>Find a skill <input value={query} onChange={e => setQuery(e.target.value)} /></label>
        <div style={{ maxHeight: 280, overflowY: 'auto', margin: '12px 0' }}>
          {candidates.map(skill => <label key={skill.id} style={{ display: 'block', margin: '8px 0' }}>
            <input type="checkbox" checked={chosen.includes(skill.id)} disabled={!chosen.includes(skill.id) && chosen.length >= 8}
              onChange={e => setChosen(e.target.checked ? [...chosen, skill.id] : chosen.filter(id => id !== skill.id))} />{' '}
            {skill.canonical_name} <span className="muted">({skill.type_code}; required in {skill.role_count} postings)</span>
          </label>)}
          {!candidates.length && <p>No matching skills.</p>}
        </div>
        <button className="primary" disabled={chosen.length < 3 || chosen.length > 8} onClick={save}>{saving ? 'Saving…' : 'Save target skillset'}</button>
      </fieldset>}
    </>}
  </section>
}
