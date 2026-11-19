// Fixed locale: the per-message label reads "1.2k in / 340 out" everywhere.
const compactFormatter = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
const exactFormatter = new Intl.NumberFormat('en-US')

const DEFAULT_CURRENCY = 'USD'
// Below this a sub-unit estimate would round to zero, which must never read as free.
const SMALLEST_SHOWN_AMOUNT = 0.0001

export type TokenCountStyle = 'compact' | 'exact'

export const formatTokenCount = (value: number, style: TokenCountStyle = 'compact'): string =>
  style === 'exact' ? exactFormatter.format(value) : compactFormatter.format(value).replace('K', 'k')

const formatMoney = (amount: number, currency: string, fractionDigits: number): string => {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: fractionDigits,
    }).format(amount)
  } catch {
    // Not an ISO 4217 code: still show the amount, labelled with what the server sent.
    return `${amount.toFixed(fractionDigits)} ${currency}`
  }
}

// Sub-unit amounts keep four decimals ($0.0123); larger ones read as money ($1.23).
export const formatEstimatedCost = (amount: number, currency: string | null = DEFAULT_CURRENCY): string => {
  const code = currency ?? DEFAULT_CURRENCY
  const fractionDigits = Math.abs(amount) < 1 ? 4 : 2

  return amount > 0 && amount < SMALLEST_SHOWN_AMOUNT
    ? `<${formatMoney(SMALLEST_SHOWN_AMOUNT, code, fractionDigits)}`
    : formatMoney(amount, code, fractionDigits)
}
