import type { TargetDraft } from '../lib/api'
import TargetRequirementPicker from './TargetRequirementPicker'

export default function TargetDraftEditor({ value, onChange, disabled = false }: {
  value: TargetDraft; onChange: (value: TargetDraft) => void; disabled?: boolean
}) {
  const target = value.target
  const update = (patch: Partial<TargetDraft['target']>) => onChange({ ...value, target: { ...target, ...patch } })
  return <fieldset disabled={disabled} className="form-stack">
    <legend>Review and edit your target</legend>
    <div className="form-grid">
      <label>Target title<input value={target.title} maxLength={300} onChange={e => update({ title: e.target.value })} /></label>
      <label>Organisation<input value={target.organisation ?? ''} onChange={e => update({ organisation: e.target.value })} /></label>
      <label>Role type<select value={target.is_imagined ? 'imagined' : 'real'} onChange={e => update({ is_imagined: e.target.value === 'imagined' })}>
        <option value="real">Real role</option><option value="imagined">Imagined role</option>
      </select></label>
      <label>Career track<input value={target.career_track ?? ''} onChange={e => update({ career_track: e.target.value })} /></label>
      <label>Seniority<input value={target.seniority_level ?? ''} onChange={e => update({ seniority_level: e.target.value })} /></label>
    </div>
    <label>Description<textarea rows={4} value={target.description ?? ''} onChange={e => update({ description: e.target.value })} /></label>
    <label>Summary<textarea rows={3} value={target.summary ?? ''} onChange={e => update({ summary: e.target.value })} /></label>
    <label>Typical tasks (one per line)<textarea rows={4} value={target.typical_tasks.join('\n')} onChange={e => update({ typical_tasks: e.target.value.split('\n') })} /></label>
    <h3>Skills and examples</h3>
    {target.skill_decomposition.map((skill, index) => <div className="form-grid" key={index}>
      <label>Skill {index + 1}<input value={skill.skill} onChange={e => update({ skill_decomposition: target.skill_decomposition.map((s, i) => i === index ? { ...s, skill: e.target.value } : s) })} /></label>
      <label>Examples (one per line)<textarea value={skill.examples.join('\n')} onChange={e => update({ skill_decomposition: target.skill_decomposition.map((s, i) => i === index ? { ...s, examples: e.target.value.split('\n') } : s) })} /></label>
      <button type="button" onClick={() => update({ skill_decomposition: target.skill_decomposition.filter((_, i) => i !== index) })}>Remove skill {index + 1}</button>
    </div>)}
    <button type="button" onClick={() => update({ skill_decomposition: [...target.skill_decomposition, { skill: '', examples: [] }] })}>Add skill</button>
    <h3>Subjects to study</h3>
    {target.technical_subjects.map((subject, index) => <div key={index} className="form-stack card">
      <label>Subject {index + 1}<input value={subject.subject} onChange={e => update({ technical_subjects: target.technical_subjects.map((s, i) => i === index ? { ...s, subject: e.target.value } : s) })} /></label>
      <label>Why study this?<textarea value={subject.why ?? ''} onChange={e => update({ technical_subjects: target.technical_subjects.map((s, i) => i === index ? { ...s, why: e.target.value } : s) })} /></label>
      <label>Resources (one per line)<textarea value={subject.resources.join('\n')} onChange={e => update({ technical_subjects: target.technical_subjects.map((s, i) => i === index ? { ...s, resources: e.target.value.split('\n') } : s) })} /></label>
      <button type="button" onClick={() => update({ technical_subjects: target.technical_subjects.filter((_, i) => i !== index) })}>Remove subject {index + 1}</button>
    </div>)}
    <button type="button" onClick={() => update({ technical_subjects: [...target.technical_subjects, { subject: '', why: '', resources: [] }] })}>Add subject</button>
    <label>Grounding and assumptions<textarea value={target.grounding_note ?? ''} onChange={e => update({ grounding_note: e.target.value })} /></label>
    <label>Feasibility notes<textarea value={target.feasibility_note ?? ''} onChange={e => update({ feasibility_note: e.target.value })} /></label>
    <label>Feasibility assessment<select value={target.is_plausible == null ? 'unknown' : String(target.is_plausible)} onChange={e => update({ is_plausible: e.target.value === 'unknown' ? null : e.target.value === 'true' })}>
      <option value="unknown">Not assessed</option><option value="true">Plausible</option><option value="false">Implausible</option>
    </select></label>
    <h3>Target requirements and vocabulary mapping</h3>
    <p className="secondary">Review these separately from descriptive examples. Search and select a vocabulary concept to confirm different wording. Unmapped requirements can be saved, but prevent a complete target assessment.</p>
    {value.skills.map((skill, index) => <div className="form-grid" key={index}>
      <label>Requirement {index + 1}<input value={skill.name} onChange={e => onChange({ ...value, skills: value.skills.map((s, i) => i === index ? { ...s, name: e.target.value, concept_id: null, mapping_reviewed: false } : s) })} /></label>
      <TargetRequirementPicker skill={skill} onChange={updated => onChange({ ...value, skills: value.skills.map((s, i) => i === index ? updated : s) })} />
      <label>Priority<select value={skill.requirement_type ?? ''} onChange={e => onChange({ ...value, skills: value.skills.map((s, i) => i === index ? { ...s, requirement_type: e.target.value || null } : s) })}>
        <option value="">Unspecified</option><option value="required">Required</option><option value="preferred">Preferred</option><option value="contextual">Contextual</option>
      </select></label>
      <button type="button" onClick={() => onChange({ ...value, skills: value.skills.filter((_, i) => i !== index) })}>Remove requirement {index + 1}</button>
    </div>)}
    <button type="button" onClick={() => onChange({ ...value, skills: [...value.skills, { name: '', requirement_type: 'required' }] })}>Add requirement</button>
  </fieldset>
}
