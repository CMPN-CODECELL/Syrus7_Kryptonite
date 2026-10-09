// Pure data helpers for the charts (kept apart from components so Fast Refresh and unit tests can use them).
import type { ExperimentCell, IterationPoint } from '../../api/contract.gen'

export interface ConvergenceRow {
  i: number
  phase: IterationPoint['phase']
  evaluation: number
  energy: number
  prepEnergy: number | null
  optEnergy: number | null
  best: number | null
  feasible: number
  elapsed: number
}

export function toConvergenceRows(points: IterationPoint[]): ConvergenceRow[] {
  const sorted = [...points].sort((a, b) => (a.phase === b.phase ? a.evaluation - b.evaluation : a.phase === 'warm_start_preparation' ? -1 : 1))
  return sorted.map((p, i) => ({
    i: i + 1,
    phase: p.phase,
    evaluation: p.evaluation,
    energy: p.energy,
    prepEnergy: p.phase === 'warm_start_preparation' ? p.energy : null,
    optEnergy: p.phase === 'optimisation' ? p.energy : null,
    best: p.best_feasible_objective,
    feasible: p.feasible_fraction,
    elapsed: p.elapsed_s,
  }))
}

export const INIT_LABEL: Record<string, string> = { random: 'Random (seeded)', warm_interp: 'Warm start (interpolated)' }

export const FACTOR_LABEL: Record<string, string> = {
  depth: 'Circuit depth p',
  optimizer: 'Classical optimiser',
  init_strategy: 'Parameter initialisation',
  universe_size: 'Encoded universe size',
  n_assets: 'Encoded universe size',
  seed: 'Seed',
  base: 'Base configuration',
}

export function levelOf(c: ExperimentCell): string {
  switch (c.factor) {
    case 'depth':
      return `p=${c.depth}`
    case 'optimizer':
      return c.optimizer
    case 'init_strategy':
    case 'init':
      return c.init_strategy === 'warm_interp' ? 'Warm (interp)' : 'Random'
    case 'universe_size':
    case 'n_assets':
      return `${c.n_assets} assets`
    default:
      return `p=${c.depth} · ${c.optimizer} · ${c.init_strategy} · ${c.n_assets}`
  }
}

export type ExpMetric = 'approximation_ratio' | 'best_objective' | 'feasible_probability' | 'runtime_s'
export const METRIC_LABEL: Record<ExpMetric, string> = {
  approximation_ratio: 'Approximation ratio (1 = exact optimum)',
  best_objective: 'Best objective (lower is better)',
  feasible_probability: 'Feasible probability',
  runtime_s: 'Runtime (s)',
}
