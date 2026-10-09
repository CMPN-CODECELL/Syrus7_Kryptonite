// Plain-language sentences for first-time readers: what was asked, what QAOA picked, how it compares and how it did later.

import type { RunResult } from '../api/contract.gen'
import { SOLVER_LABEL, formatDate, formatInrCompact, formatPct } from './format'
import { solver } from './result'

const list = (xs: string[]) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)

/** A short story of one run, one sentence per item. */
export function plainRunSummary(r: RunResult): string[] {
  const out: string[] = []
  const n = r.encoded_symbols.length
  const k = r.config.constraints.cardinality
  const budget = formatInrCompact(r.config.budget_inr)
  out.push(
    r.config.mode === 'lots'
      ? `The task: split ${budget} across ${n} stocks in equal lots, balancing risk, return and trading costs.`
      : `The task: choose ${k ? `${k} of ` : ''}${n} stocks for ${budget}, balancing risk, return and trading costs.`,
  )

  const q = solver(r, 'qaoa')
  const qp = q?.portfolio
  if (!q || q.status === 'skipped') {
    out.push('The quantum circuit (QAOA) was not run this time; only the classical methods answered.')
    return out
  }
  if (!qp) {
    out.push(
      q.status === 'no_feasible_sample'
        ? 'The quantum circuit (QAOA) did not produce any portfolio that follows every rule, so it has no answer this time. The classical methods still solved the same problem.'
        : 'The quantum circuit (QAOA) did not produce a portfolio this time. The Comparison tab says why.',
    )
    return out
  }
  const picks = [...qp.holdings].filter((h) => h.weight > 0).sort((a, b) => b.weight - a.weight).map((h) => h.symbol)
  out.push(`The quantum circuit (QAOA) picked ${list(picks)}.`)

  const g = q.gap
  if (g) {
    const who = SOLVER_LABEL[g.reference_solver] ?? g.reference_solver
    const same = Math.abs(g.absolute) <= 1e-9 || (g.relative !== null && Math.abs(g.relative) < 5e-5)
    if (same)
      out.push(
        g.reference === 'exact_optimum'
          ? 'That is exactly the best possible portfolio: exact search tried every valid option and found the same one.'
          : `That matches the best answer the classical methods found (${who}).`,
      )
    else
      out.push(
        g.relative !== null
          ? `Its score is ${formatPct(g.relative, 1)} worse than the ${g.reference === 'exact_optimum' ? 'best possible' : 'best known'} answer (from ${who}). 0 % would mean a perfect match.`
          : `Its score is a little worse than the ${g.reference === 'exact_optimum' ? 'best possible' : 'best known'} answer (from ${who}).`,
      )
  }

  const t = qp.test
  if (t && !t.insufficient && typeof t.total_return === 'number')
    out.push(
      `On later prices it never saw (${formatDate(t.start)} to ${formatDate(t.end)}), this portfolio ${t.total_return >= 0 ? 'gained' : 'lost'} ${formatPct(Math.abs(t.total_return), 1)}. Past results do not predict future ones.`,
    )
  return out
}
