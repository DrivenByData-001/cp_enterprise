import { useEffect, useState } from 'react'
import { api, type PlanningAssumption } from '../../lib/api'
import { formatMoney } from '../../lib/money'

/** The contract-annualisation assumption, editable where it is actually used.
 *
 * A day rate is never annualised on its own, so without this control a user
 * whose evidence is contract work simply cannot compare it against an annual
 * salary anywhere in the product — the backend supported the assumption but
 * nothing could set it. Setting it here shows the arithmetic and refreshes
 * Pathways; clearing it immediately withdraws every derived equivalent. */
export function PlanningAssumptionEditor({
  dayRate,
  currency,
  onChanged,
}: {
  dayRate?: number | null
  currency?: string | null
  onChanged: () => void
}) {
  const [assumption, setAssumption] = useState<PlanningAssumption | null>(null)
  const [days, setDays] = useState('')
  const [note, setNote] = useState('')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getPlanningAssumptions()
      .then((current) => {
        setAssumption(current)
        setDays(current.contract_billable_days_per_year?.toString() ?? '')
        setNote(current.note ?? '')
      })
      .catch((e) => setError(String(e)))
  }, [])

  const save = async (value: number | null) => {
    setBusy(true)
    setError(null)
    try {
      const saved = await api.savePlanningAssumptions({
        contract_billable_days_per_year: value,
        note: value === null ? null : note || null,
      })
      setAssumption(saved)
      setDays(saved.contract_billable_days_per_year?.toString() ?? '')
      onChanged()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const parsed = Number(days)
  const valid = days.trim() !== '' && Number.isFinite(parsed) && parsed > 0 && parsed <= 366
  const preview =
    valid && dayRate
      ? `${formatMoney(dayRate, currency ?? '')} × ${parsed} days = ${formatMoney(dayRate * parsed, currency ?? '')}`
      : null

  return (
    <div>
      <div className="muted" style={{ fontSize: 12 }}>
        Contract comparison assumption
      </div>
      <div className="secondary" style={{ fontSize: 13 }}>
        {assumption?.contract_billable_days_per_year
          ? `${assumption.contract_billable_days_per_year} billable days/year`
          : 'Not set — day rates are not annualised'}
      </div>
      <button type="button" onClick={() => setOpen((prior) => !prior)} style={{ marginTop: 6 }}>
        {open ? 'Close' : assumption?.contract_billable_days_per_year ? 'Change' : 'Set assumption'}
      </button>

      {open && (
        <div style={{ marginTop: 8 }}>
          <label style={{ display: 'block' }}>
            <span className="muted" style={{ fontSize: 12 }}>
              Billable days per year
            </span>
            <input
              type="number"
              min={1}
              max={366}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              style={{ display: 'block', marginTop: 4, width: 120 }}
            />
          </label>
          <label style={{ display: 'block', marginTop: 6 }}>
            <span className="muted" style={{ fontSize: 12 }}>
              Note (optional)
            </span>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              style={{ display: 'block', marginTop: 4, width: 240 }}
            />
          </label>

          {preview && (
            <p className="secondary" style={{ fontSize: 13, margin: '8px 0 0' }}>
              {preview}
              <br />
              <span className="muted" style={{ fontSize: 12 }}>
                This is a planning equivalent, not salary.
              </span>
            </p>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button type="button" onClick={() => save(parsed)} disabled={busy || !valid}>
              {busy ? 'Saving…' : 'Save'}
            </button>
            {assumption?.contract_billable_days_per_year !== null &&
              assumption?.contract_billable_days_per_year !== undefined && (
                <button type="button" onClick={() => save(null)} disabled={busy}>
                  Clear
                </button>
              )}
          </div>
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
