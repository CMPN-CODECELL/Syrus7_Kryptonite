// Formatting for live quotes (display only).

import { DASH } from '../../lib/format'

const price2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const dayMonth = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short' })

const isNum = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

/** 22453.3 -> "22,453.30" (Indian grouping, two decimals, no currency sign). */
export function formatQuotePrice(v: number | null | undefined): string {
  return isNum(v) ? price2.format(v) : DASH
}

/** Day change in percent (1.25 means +1.25%) -> "1.25%" without a sign; direction is shown separately. */
export function formatQuotePct(pct: number | null | undefined): string {
  return isNum(pct) ? `${Math.abs(pct).toFixed(2)}%` : DASH
}

export type QuoteDirection = 'up' | 'down' | 'flat'

export function quoteDirection(pct: number | null | undefined): QuoteDirection {
  if (!isNum(pct) || Math.abs(pct) < 0.005) return 'flat'
  return pct > 0 ? 'up' : 'down'
}

/** "2026-10-08" -> "08 Oct". */
export function formatDayMonth(v: string | null | undefined): string {
  if (!v) return ''
  const d = /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(`${v}T00:00:00`) : new Date(v)
  return Number.isNaN(d.getTime()) ? v : dayMonth.format(d)
}
