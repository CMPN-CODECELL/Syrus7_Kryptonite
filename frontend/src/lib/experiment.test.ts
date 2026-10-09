import { describe, expect, it } from 'vitest'
import { cellCount, parseIntList } from './experiment'

const sweep = { depths: [1, 2, 3], optimizers: ['COBYLA'], init_strategies: ['random', 'warm_interp'], universe_sizes: [] as number[], seeds: [1, 2] }

describe('experiment cell count (mirrors backend experiment_cell_count)', () => {
  it('sums factor levels for one-factor-at-a-time', () => {
    expect(cellCount({ ...sweep, design: 'one_factor_at_a_time' })).toBe((3 + 1 + 2 + 0) * 2)
  })
  it('multiplies factor levels for full factorial, treating empty lists as one level', () => {
    expect(cellCount({ ...sweep, design: 'full_factorial' })).toBe(3 * 1 * 2 * 1 * 2)
  })
  it('never returns zero', () => {
    expect(cellCount({ depths: [], optimizers: [], init_strategies: [], universe_sizes: [], seeds: [], design: 'one_factor_at_a_time' })).toBe(1)
  })
})

describe('parseIntList', () => {
  it('accepts commas and whitespace and removes duplicates', () => {
    expect(parseIntList(' 4, 6 8,,6 ')).toEqual({ values: [4, 6, 8], bad: [] })
  })
  it('reports entries that are not whole numbers', () => {
    expect(parseIntList('1, 2.5, x, -3')).toEqual({ values: [1], bad: ['2.5', 'x', '-3'] })
  })
  it('returns nothing for an empty string', () => {
    expect(parseIntList('')).toEqual({ values: [], bad: [] })
  })
})
