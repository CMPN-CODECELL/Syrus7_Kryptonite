// Client-side validation mirroring the server's pydantic bounds (BOUNDS is generated from contracts.py).
// The server's /preflight remains authoritative; this only gives instant field-level feedback and
// keeps obviously invalid configs from being submitted.

import { BOUNDS, type FieldBounds, type RunConfig } from '../api/contract.gen'

export type FieldErrors = Record<string, string>

function rangeMsg(b: FieldBounds, unit = ''): string {
  const lo = b.minimum ?? b.exclusiveMinimum
  const hi = b.maximum ?? b.exclusiveMaximum
  const loS = lo === undefined ? null : `${b.exclusiveMinimum !== undefined ? '>' : '≥'} ${lo}${unit}`
  const hiS = hi === undefined ? null : `${b.exclusiveMaximum !== undefined ? '<' : '≤'} ${hi}${unit}`
  return [loS, hiS].filter(Boolean).join(' and ')
}

export function checkNumber(v: number | null | undefined, b: FieldBounds, opts: { label: string; nullable?: boolean; unit?: string }): string | null {
  if (v === null || v === undefined) return opts.nullable ? null : `${opts.label} is required.`
  if (!Number.isFinite(v)) return `${opts.label} must be a number.`
  if (b.integer && !Number.isInteger(v)) return `${opts.label} must be a whole number.`
  const bad =
    (b.minimum !== undefined && v < b.minimum) ||
    (b.maximum !== undefined && v > b.maximum) ||
    (b.exclusiveMinimum !== undefined && v <= b.exclusiveMinimum) ||
    (b.exclusiveMaximum !== undefined && v >= b.exclusiveMaximum)
  return bad ? `${opts.label} must be ${rangeMsg(b, opts.unit)}.` : null
}

const SYMBOL_RE = /^[A-Za-z0-9&_-]{1,20}$/

export function validateRunConfig(c: RunConfig): FieldErrors {
  const e: FieldErrors = {}
  const set = (k: string, m: string | null) => {
    if (m && !e[k]) e[k] = m
  }
  const R = BOUNDS.RunConfig
  const n = c.symbols.length
  if (n < R.symbols.minItems) set('symbols', `Select at least ${R.symbols.minItems} assets.`)
  if (n > R.symbols.maxItems) set('symbols', `Select at most ${R.symbols.maxItems} assets.`)
  if (new Set(c.symbols).size !== n) set('symbols', 'Each asset can be selected once.')
  const bad = c.symbols.find((s) => !SYMBOL_RE.test(s))
  if (bad) set('symbols', `“${bad}” is not a valid NSE symbol.`)

  set('risk_aversion', checkNumber(c.risk_aversion, R.risk_aversion, { label: 'Risk aversion' }))
  set('budget_inr', checkNumber(c.budget_inr, R.budget_inr, { label: 'Budget' }))
  set('risk_free_rate', checkNumber(c.risk_free_rate, R.risk_free_rate, { label: 'Risk-free rate' }))
  set('timeout_seconds', checkNumber(c.timeout_seconds, R.timeout_seconds, { label: 'Timeout', unit: ' s' }))
  set('noise_trials', checkNumber(c.noise_trials, R.noise_trials, { label: 'Noise trials' }))

  // Dates: train_start < train_end < validation_start <= validation_end < test_start <= test_end
  const d = c.dates
  const order: Array<[keyof typeof d, keyof typeof d, boolean]> = [
    ['train_start', 'train_end', true],
    ['train_end', 'validation_start', true],
    ['validation_start', 'validation_end', false],
    ['validation_end', 'test_start', true],
    ['test_start', 'test_end', false],
  ]
  for (const k of Object.keys(d) as Array<keyof typeof d>) if (!/^\d{4}-\d{2}-\d{2}$/.test(d[k] ?? '')) set(`dates.${k}`, 'Enter a date.')
  for (const [a, b, strict] of order) {
    if (e[`dates.${a}`] || e[`dates.${b}`]) continue
    if (strict ? !(d[a] < d[b]) : !(d[a] <= d[b])) set(`dates.${b}`, `Must be ${strict ? 'after' : 'on or after'} ${a.replace('_', ' ')}.`)
  }
  if (Object.keys(e).some((k) => k.startsWith('dates.'))) set('dates', 'Dates must be in order: train, then validation, then test, with no overlap.')

  const k = c.constraints
  const C = BOUNDS.ConstraintsConfig
  if (c.mode === 'selection' && k.cardinality === null) set('constraints.cardinality', 'Choose how many assets to hold (K).')
  if (c.mode === 'lots' && k.cardinality !== null) set('constraints.cardinality', 'Cardinality is not used in lots mode.')
  if (c.mode === 'selection' && k.cardinality !== null) {
    set('constraints.cardinality', checkNumber(k.cardinality, C.cardinality, { label: 'Number of holdings' }))
    if (k.cardinality > n && n >= 2) set('constraints.cardinality', `Cannot hold ${k.cardinality} of ${n} selected assets.`)
  }
  set('constraints.target_return', checkNumber(k.target_return, C.target_return, { label: 'Target return', nullable: true }))
  if (c.mode === 'lots') {
    if (!k.lots) set('constraints.lots', 'Lots mode needs lot settings.')
    else {
      const L = BOUNDS.LotConfig
      set('constraints.lots.total_lots', checkNumber(k.lots.total_lots, L.total_lots, { label: 'Total lots' }))
      set('constraints.lots.max_lots_per_asset', checkNumber(k.lots.max_lots_per_asset, L.max_lots_per_asset, { label: 'Max lots per asset' }))
    }
  }
  if (k.sector_caps) {
    const sc = k.sector_caps
    const caps = [['default', sc.default_max] as const, ...Object.entries(sc.per_sector)]
    for (const [name, v] of caps) {
      if (v === null || v === undefined) continue
      const key = name === 'default' ? 'constraints.sector_caps.default_max' : `constraints.sector_caps.${name}`
      if (!Number.isFinite(v) || v < 0) set(key, 'Cap must be zero or more.')
      else if (sc.basis === 'count' && !Number.isInteger(v)) set(key, 'A count cap must be a whole number.')
      else if (sc.basis === 'weight' && v > 1) set(key, 'A weight cap is a fraction between 0 and 1.')
    }
  }
  const T = BOUNDS.TransactionCosts
  set('constraints.transaction_costs.proportional_bps', checkNumber(k.transaction_costs.proportional_bps, T.proportional_bps, { label: 'Proportional cost', unit: ' bps' }))
  set('constraints.transaction_costs.fixed_inr_per_trade', checkNumber(k.transaction_costs.fixed_inr_per_trade, T.fixed_inr_per_trade, { label: 'Fixed cost per trade' }))
  const sp = k.transaction_costs.starting_portfolio
  if (sp.type === 'holdings') {
    const ws = Object.values(sp.weights)
    if (ws.some((w) => !Number.isFinite(w) || w < 0)) set('constraints.transaction_costs.starting_portfolio', 'Starting weights must be zero or more.')
    else if (ws.reduce((a, b) => a + b, 0) > 1 + 1e-9) set('constraints.transaction_costs.starting_portfolio', 'Starting weights must add up to 100% or less (the rest is cash).')
  }

  const Q = BOUNDS.QaoaSettings
  const q = c.qaoa
  set('qaoa.depth', checkNumber(q.depth, Q.depth, { label: 'Depth p' }))
  set('qaoa.shots', checkNumber(q.shots, Q.shots, { label: 'Shots per evaluation' }))
  set('qaoa.final_shots', checkNumber(q.final_shots, Q.final_shots, { label: 'Final batch shots' }))
  set('qaoa.max_evaluations', checkNumber(q.max_evaluations, Q.max_evaluations, { label: 'Evaluation budget' }))
  set('qaoa.seed', checkNumber(q.seed, Q.seed, { label: 'Seed' }))
  set('qaoa.penalty_scale', checkNumber(q.penalty_scale, Q.penalty_scale, { label: 'Penalty scale' }))
  set('qaoa.cvar_alpha', checkNumber(q.cvar_alpha, Q.cvar_alpha, { label: 'CVaR α' }))
  if (q.noise === 'fake_backend' && !q.fake_backend) set('qaoa.fake_backend', 'Choose a fake backend for noisy simulation.')
  if (c.noise_comparison && !q.fake_backend) set('qaoa.fake_backend', 'Choose a fake backend for the noise comparison.')
  if (c.screening.rule !== 'none') {
    set('screening.keep', checkNumber(c.screening.keep, BOUNDS.ScreeningConfig.keep, { label: 'Assets kept after screening' }))
    if (c.screening.keep !== null && c.screening.keep > n) set('screening.keep', `Cannot keep ${c.screening.keep} of ${n} selected assets.`)
  }
  return e
}
