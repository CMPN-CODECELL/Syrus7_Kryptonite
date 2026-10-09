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
      ? `Split ${budget} across ${n} stocks in equal lots.`
      : `Pick ${k ? `${k} of ` : ''}${n} stocks for ${budget}.`,
  )

  const q = solver(r, 'qaoa')
  const qp = q?.portfolio
  if (!q || q.status === 'skipped') {
    out.push('QAOA did not run. Only the classical methods answered.')
    return out
  }
  if (!qp) {
    out.push(
      q.status === 'no_feasible_sample'
        ? 'QAOA found no portfolio that follows every rule. The classical methods still answered.'
        : 'QAOA returned no portfolio. The Comparison tab says why.',
    )
    return out
  }
  const picks = [...qp.holdings].filter((h) => h.weight > 0).sort((a, b) => b.weight - a.weight).map((h) => h.symbol)
  out.push(`QAOA picked ${list(picks)}.`)

  const g = q.gap
  if (g) {
    const who = SOLVER_LABEL[g.reference_solver] ?? g.reference_solver
    const same = Math.abs(g.absolute) <= 1e-9 || (g.relative !== null && Math.abs(g.relative) < 5e-5)
    if (same)
      out.push(
        g.reference === 'exact_optimum'
          ? 'The best possible portfolio, confirmed by exact search.'
          : `The same as the best classical answer (${who}).`,
      )
    else
      out.push(
        g.relative !== null
          ? `${formatPct(g.relative, 1)} off the ${g.reference === 'exact_optimum' ? 'best possible' : 'best known'} answer (${who}).`
          : `A little off the ${g.reference === 'exact_optimum' ? 'best possible' : 'best known'} answer (${who}).`,
      )
  }

  const t = qp.test
  if (t && !t.insufficient && typeof t.total_return === 'number')
    out.push(
      `${t.total_return >= 0 ? 'Gained' : 'Lost'} ${formatPct(Math.abs(t.total_return), 1)} on later, unseen prices (${formatDate(t.start)} to ${formatDate(t.end)}).`,
    )
  return out
}
