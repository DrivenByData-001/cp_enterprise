// Never more precise than the underlying evidence: always rounded to the
// nearest whole unit, never a decimal a source didn't state.
export function formatMoney(amount: number | null | undefined, currency: string): string {
  if (amount === null || amount === undefined) return '—'
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount)
  } catch {
    return `${Math.round(amount).toLocaleString()} ${currency}`
  }
}
