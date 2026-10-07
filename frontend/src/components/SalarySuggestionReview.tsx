import type { SalaryReview, SalarySuggestion } from '../lib/api'

export default function SalarySuggestionReview({ suggestion, value, onChange, disabled }: {
  suggestion: SalarySuggestion; value: SalaryReview | null;
  onChange: (value: SalaryReview | null) => void; disabled: boolean
}) {
  if (suggestion.error) return <p role="alert">Salary suggestion failed: {suggestion.error}. You can still save the factual details, or discard and retry.</p>
  if (!suggestion.review) return null
  const estimate = value?.estimate
  const edit = (fields: Partial<NonNullable<SalaryReview['estimate']>>) => {
    if (value && estimate) onChange({ ...value, estimate: { ...estimate, ...fields } })
  }
  return <section style={{ marginTop: 16 }}>
    <h4>Compensation review</h4>
    <p className="muted">{suggestion.reason}</p>
    <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}><input style={{ width: 'auto' }} type="checkbox" checked={!!value} disabled={disabled}
      onChange={e => onChange(e.target.checked ? suggestion.review : null)} /> Save compensation with these details</label>
    {(suggestion.stated_items ?? []).filter(i => !i.acceptable).map((item, i) =>
      <p key={i} role="status">Unverified stated figure (excluded from saving): {item.evidence_span}. {item.problems?.join('; ')}</p>)}
    {value?.stated_items.map((item, index) => <fieldset key={index} disabled={disabled} style={{ marginTop: 12 }}>
      <legend>Stated compensation · {item.component} · {item.pay_period}</legend>
      <blockquote>{item.evidence_span}</blockquote>
      <div className="form-grid">
        {(['amount_min', 'amount_max', 'bonus_pct'] as const).map(key => <label key={key}>
          {key === 'amount_min' ? 'Stated minimum' : key === 'amount_max' ? 'Stated maximum' : 'Bonus (%)'}
          <input type="number" value={item[key] ?? ''} onChange={e => onChange({ ...value,
            stated_items: value.stated_items.map((r, i) => i === index ? { ...r, [key]: e.target.value === '' ? null : Number(e.target.value) } : r) })} />
        </label>)}
        <label>Currency<input value={item.currency ?? ''} onChange={e => onChange({ ...value,
          stated_items: value.stated_items.map((r, i) => i === index ? { ...r, currency: e.target.value.toUpperCase() } : r) })} /></label>
      </div>
      <button type="button" onClick={() => onChange({ ...value, stated_items: value.stated_items.filter((_, i) => i !== index) })}>Exclude this figure</button>
    </fieldset>)}
    {estimate && <fieldset disabled={disabled} style={{ marginTop: 12 }}>
      <legend>AI-estimated salary range</legend>
      <p>Not stated by the employer. Model confidence: {estimate.confidence}.</p>
      <div className="form-grid">
        <label>Estimated minimum<input type="number" min="1" value={estimate.amount_min} onChange={e => edit({ amount_min: Number(e.target.value) })} /></label>
        <label>Estimated maximum<input type="number" min="1" value={estimate.amount_max} onChange={e => edit({ amount_max: Number(e.target.value) })} /></label>
        <label>Estimate currency<input maxLength={3} value={estimate.currency} onChange={e => edit({ currency: e.target.value.toUpperCase() })} /></label>
        <label>Pay period<select value={estimate.pay_period} onChange={e => edit({ pay_period: e.target.value as 'annual' | 'daily' })}>
          <option value="annual">Annual base salary</option><option value="daily">Contract day rate</option>
        </select></label>
        <label>Employment basis<select value={estimate.employment_basis} onChange={e => edit({ employment_basis: e.target.value as 'permanent' | 'contract' | 'unknown' })}>
          <option value="permanent">Permanent</option><option value="contract">Contract</option><option value="unknown">Unknown</option>
        </select></label>
      </div>
      <label style={{ display: 'block' }}>Reasoning<textarea style={{ width: '100%' }} value={estimate.rationale} onChange={e => edit({ rationale: e.target.value })} /></label>
      <label style={{ display: 'block' }}>Assumptions and limitations<textarea style={{ width: '100%' }} value={estimate.assumptions} onChange={e => edit({ assumptions: e.target.value })} /></label>
      {estimate.evidence_ids.length === 0 ? <p>No app salary evidence cited; this is a general-knowledge estimate.</p> :
        <details><summary>Supporting salary evidence ({estimate.evidence_ids.length})</summary>
          <ul>{suggestion.evidence?.filter(e => estimate.evidence_ids.includes(e.id)).map(e => <li key={e.id}>
            {e.title || e.raw_role_label || e.document_title} — {e.market || 'Market unspecified'}: {e.currency} {e.amount_min ?? e.amount_mid ?? '?'}–{e.amount_max ?? e.amount_mid ?? '?'} · {e.component} · {e.pay_period} · {e.observed_at || 'Date unknown'}
            {e.document_title && <span> · {e.document_title}</span>}
          </li>)}</ul>
        </details>}
    </fieldset>}
  </section>
}
