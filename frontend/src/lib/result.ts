import type { GapInfo, RunResult, SolverName, SolverResult } from '../api/contract.gen'
import { DASH, SOLVER_LABEL, formatNum, formatPct } from './format'

export const SOLVER_ORDER: SolverName[] = ['qaoa', 'exact', 'relaxation_rounding', 'simulated_annealing', 'greedy']

export function solver(r: RunResult, name: SolverName): SolverResult | undefined {
  return r.solvers.find((s) => s.solver === name)
}

export function sortedSolvers(r: RunResult): SolverResult[] {
  return [...r.solvers].sort((a, b) => SOLVER_ORDER.indexOf(a.solver) - SOLVER_ORDER.indexOf(b.solver))
}

/** The feasible classical solver with the lowest objective (the comparison reference when exact is skipped). */
export function bestClassical(r: RunResult): SolverResult | undefined {
  return r.solvers
    .filter((s) => s.solver !== 'qaoa' && s.status === 'ok' && s.portfolio)
    .sort((a, b) => a.portfolio!.objective - b.portfolio!.objective)[0]
}

/** Weights by symbol for a solver's portfolio (used to drive the hero's allocation bars). */
export function weightsOf(s: SolverResult | undefined): Record<string, number> | null {
  if (!s?.portfolio) return null
  return Object.fromEntries(s.portfolio.holdings.filter((h) => h.weight > 0).map((h) => [h.symbol, h.weight]))
}

/** Decision bit i (left to right in `decision_bits`) is asset i of `encoded_symbols`. */
export function decodeSelection(decisionBits: string, symbols: string[]): string[] {
  const out: string[] = []
  for (let i = 0; i < Math.min(decisionBits.length, symbols.length); i++) if (decisionBits[i] === '1') out.push(symbols[i])
  return out
}

/** Free-form numeric dicts (timings, runtime breakdowns) as sorted rows. */
export function numericEntries(d: Record<string, unknown> | null | undefined): Array<[string, number]> {
  if (!d) return []
  return Object.entries(d)
    .filter((e): e is [string, number] => typeof e[1] === 'number' && Number.isFinite(e[1]))
    .sort((a, b) => b[1] - a[1])
}

export function humanKey(k: string): string {
  const s = k.replace(/_s$/, '').replace(/_/g, ' ').replace(/\b(qaoa|qubo|psd)\b/gi, (m) => m.toUpperCase())
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function formatUnknown(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(4)
  if (typeof v === 'string') return v
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v === 'object' && Object.keys(v).length === 0) return 'none'
  return JSON.stringify(v)
}

/** Sum of a solver's runtime breakdown; prefers an explicit total when the backend sends one. */
export function solverRuntime(s: SolverResult): number | null {
  const rt = s.runtime_s
  if (typeof rt.total === 'number') return rt.total
  const v = numericEntries(rt)
  return v.length ? v.reduce((a, [, x]) => a + x, 0) : null
}

export function gapText(g: GapInfo | null): string {
  if (!g) return DASH
  const rel = g.relative !== null ? ` (${formatPct(g.relative, 2)})` : ''
  return `${formatNum(g.absolute, 5)}${rel}`
}

export function gapReference(g: GapInfo | null): string {
  if (!g) return ''
  const who = SOLVER_LABEL[g.reference_solver] ?? g.reference_solver
  return g.reference === 'exact_optimum' ? `vs exact optimum (${who})` : `vs best known (${who}; exact not run)`
}
