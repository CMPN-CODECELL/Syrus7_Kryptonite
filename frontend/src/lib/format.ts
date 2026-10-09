// Number/currency/date formatting. Indian conventions: ₹ with lakh/crore digit grouping.

const inrFull = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
const inrPaise = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2, maximumFractionDigits: 2 })
const groupedInt = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

export const DASH = '—'

const isNum = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

/** ₹10,00,000 (lakh/crore grouping). */
export function formatInr(v: number | null | undefined, opts: { paise?: boolean } = {}): string {
  if (!isNum(v)) return DASH
  return (opts.paise ? inrPaise : inrFull).format(v)
}

/** Compact Indian units: ₹12.5 L, ₹1.20 Cr, ₹8,400. */
export function formatInrCompact(v: number | null | undefined): string {
  if (!isNum(v)) return DASH
  const sign = v < 0 ? '−' : ''
  const a = Math.abs(v)
  if (a >= 1e7) return `${sign}₹${trim(a / 1e7, 2)} Cr`
  if (a >= 1e5) return `${sign}₹${trim(a / 1e5, 2)} L`
  return `${sign}₹${groupedInt.format(a)}`
}

function trim(v: number, digits: number): string {
  return v.toFixed(digits).replace(/\.?0+$/, '')
}

export function formatInt(v: number | null | undefined): string {
  return isNum(v) ? groupedInt.format(v) : DASH
}

/** Decimal fraction -> percent. 0.1234 -> "12.34%". */
export function formatPct(v: number | null | undefined, digits = 2, opts: { sign?: boolean } = {}): string {
  if (!isNum(v)) return DASH
  const pct = v * 100
  const s = Math.abs(pct).toFixed(digits)
  const sign = pct < 0 && Number(s) !== 0 ? '−' : opts.sign && pct > 0 ? '+' : ''
  return `${sign}${s}%`
}

/** Plain decimal with sign-aware minus. */
export function formatNum(v: number | null | undefined, digits = 4): string {
  if (!isNum(v)) return DASH
  const s = Math.abs(v).toFixed(digits)
  return `${v < 0 && Number(s) !== 0 ? '−' : ''}${s}`
}

/** At most three significant figures, without trailing zeros: 2 -> "2", 1.5625 -> "1.56", 512.4 -> "512". */
function sig3(v: number): string {
  if (v >= 1000) return v.toPrecision(3)
  return String(Number(v.toPrecision(3)))
}

const pad2 = (n: number) => String(n).padStart(2, '0')
const YEAR_S = 365.25 * 86400

/** Seconds -> "840 ms", "12.4 s", "3 min 05 s", "2 h 05 min", "3 d 4 h", "2.5 years". */
export function formatDuration(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return DASH
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`
  if (seconds < 3599.5) {
    const t = Math.round(seconds)
    return `${Math.floor(t / 60)} min ${pad2(t % 60)} s`
  }
  if (seconds < 86400 - 30) {
    const t = Math.round(seconds / 60)
    return `${Math.floor(t / 60)} h ${pad2(t % 60)} min`
  }
  if (seconds < YEAR_S) {
    const t = Math.round(seconds / 3600)
    return `${Math.floor(t / 24)} d ${t % 24} h`
  }
  const years = seconds / YEAR_S
  return years < 100 ? `${sig3(years)} years` : 'over 100 years'
}

const MB_UNITS = ['MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']

/** Megabytes (binary) -> "512 KB", "12.4 MB", "2 GB", "16.8 PB": the largest unit, at most three significant figures. */
export function formatBytesMb(mb: number | null | undefined): string {
  if (!isNum(mb)) return DASH
  if (mb < 1) return `${Math.max(1, Math.round(mb * 1024))} KB`
  let v = mb
  let u = 0
  while (v >= 1024 && u < MB_UNITS.length - 1) {
    v /= 1024
    u += 1
  }
  return `${sig3(v)} ${MB_UNITS[u]}`
}

/** A memory estimate against the server's limit: "512 MB", "1.56 GB, over the 1,500 MB limit", or "far above the 1,500 MB limit". */
export function formatMemoryEstimate(mb: number | null | undefined, limitMb?: number | null): string {
  if (!isNum(mb)) return DASH
  if (!isNum(limitMb) || limitMb <= 0 || mb <= limitMb) return formatBytesMb(mb)
  const limit = `${formatInt(limitMb)} MB limit`
  return mb > limitMb * 100 ? `far above the ${limit}` : `${formatBytesMb(mb)}, over the ${limit}`
}

const dateFmt = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
const dateTimeFmt = new Intl.DateTimeFormat('en-IN', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** "2025-10-01" or ISO datetime -> "01 Oct 2025". */
export function formatDate(v: string | null | undefined): string {
  if (!v) return DASH
  const d = /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(`${v}T00:00:00`) : new Date(v)
  return Number.isNaN(d.getTime()) ? v : dateFmt.format(d)
}

export function formatDateTime(v: string | null | undefined): string {
  if (!v) return DASH
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : dateTimeFmt.format(d)
}

/** Relative time for history lists: "4 min ago". */
export function formatRelative(v: string | null | undefined, now: number = Date.now()): string {
  if (!v) return DASH
  const t = new Date(v).getTime()
  if (Number.isNaN(t)) return v
  const s = Math.round((now - t) / 1000)
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return formatDate(v)
}

/** The unique part of a server id: "run-20261008-f29dc129" -> "f29dc129". Ids without such a suffix are returned whole. */
export function shortId(id: string | null | undefined): string {
  if (!id) return DASH
  const tail = id.slice(id.lastIndexOf('-') + 1)
  return tail.length >= 6 && tail.length < id.length ? tail : id
}

/** Human labels for contract enums. */
export const SOLVER_LABEL: Record<string, string> = {
  qaoa: 'QAOA (sampled)',
  exact: 'Exact enumeration',
  relaxation_rounding: 'Relaxation + rounding',
  simulated_annealing: 'Simulated annealing',
  greedy: 'Greedy',
}

export const STAGE_LABEL: Record<string, string> = {
  queued: 'Queued',
  loading_data: 'Loading data',
  estimating: 'Estimating risk model',
  screening: 'Screening universe',
  building_qubo: 'Building QUBO',
  transpiling: 'Transpiling circuit',
  optimising: 'Optimising parameters',
  final_sampling: 'Final sampling batch',
  baselines: 'Classical baselines',
  frontier: 'Efficient frontier',
  noise: 'Noise comparison',
  experiment: 'Experiment cells',
  reporting: 'Writing report',
  done: 'Done',
}

export const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
  timed_out: 'Timed out',
  ok: 'Feasible',
  infeasible: 'Proven infeasible',
  no_feasible_sample: 'No feasible sample',
  no_feasible_found: 'None found',
  unsupported: 'Unsupported',
  time_limited: 'Time-limited',
  skipped: 'Skipped',
}

export function sectorShort(sector: string): string {
  const map: Record<string, string> = {
    'Financial Services': 'Financials',
    'Information Technology': 'IT',
    'Oil Gas & Consumable Fuels': 'Oil & Gas',
    'Automobile and Auto Components': 'Auto',
    'Fast Moving Consumer Goods': 'FMCG',
    'Construction Materials': 'Cement',
    'Metals & Mining': 'Metals',
    'Consumer Durables': 'Durables',
    'Consumer Services': 'Cons. Services',
    'Capital Goods': 'Cap. Goods',
    Telecommunication: 'Telecom',
  }
  return map[sector] ?? sector
}
