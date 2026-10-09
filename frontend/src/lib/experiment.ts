// Experiment Lab helpers (kept in sync with backend jobs/impl.py experiment_cell_count).

export type Design = 'one_factor_at_a_time' | 'full_factorial'

/** Same rule as the server's experiment_cell_count (upper bound on QAOA runs). */
export function cellCount(s: { depths: number[]; optimizers: string[]; init_strategies: string[]; universe_sizes: number[]; seeds: number[]; design: Design }): number {
  const seeds = Math.max(1, s.seeds.length)
  if (s.design === 'full_factorial')
    return Math.max(1, s.depths.length) * Math.max(1, s.optimizers.length) * Math.max(1, s.init_strategies.length) * Math.max(1, s.universe_sizes.length) * seeds
  return Math.max(1, s.depths.length + s.optimizers.length + s.init_strategies.length + s.universe_sizes.length) * seeds
}

export function parseIntList(text: string): { values: number[]; bad: string[] } {
  const parts = text
    .split(/[\s,]+/)
    .map((p) => p.trim())
    .filter(Boolean)
  const values: number[] = []
  const bad: string[] = []
  for (const p of parts) {
    const n = Number(p)
    if (Number.isInteger(n) && n >= 0) {
      if (!values.includes(n)) values.push(n)
    }
    else bad.push(p)
  }
  return { values, bad }
}
