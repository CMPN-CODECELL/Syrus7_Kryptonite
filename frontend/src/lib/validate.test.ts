import { describe, expect, it } from 'vitest'
import type { RunConfig } from '../api/contract.gen'
import base from '../test/fixtures/default-config.json'
import { validateRunConfig } from './validate'

// Test fixture: the API's default configuration with K set (not market data).
const cfg = (patch: (c: RunConfig) => void = () => {}): RunConfig => {
  const c = structuredClone(base) as unknown as RunConfig
  patch(c)
  return c
}

describe('validateRunConfig', () => {
  it('accepts the default configuration with K set', () => {
    expect(validateRunConfig(cfg())).toEqual({})
  })

  it('requires K in selection mode and forbids it in lots mode', () => {
    expect(validateRunConfig(cfg((c) => (c.constraints.cardinality = null)))['constraints.cardinality']).toMatch(/how many assets/)
    const lots = validateRunConfig(cfg((c) => (c.mode = 'lots')))
    expect(lots['constraints.cardinality']).toMatch(/not used in lots mode/)
    expect(lots['constraints.lots']).toMatch(/lot settings/)
  })

  it('rejects K larger than the selected universe', () => {
    expect(validateRunConfig(cfg((c) => (c.constraints.cardinality = 9)))['constraints.cardinality']).toMatch(/Cannot hold 9 of 6/)
  })

  it('rejects duplicate and malformed symbols', () => {
    expect(validateRunConfig(cfg((c) => (c.symbols = ['INFY', 'INFY', 'TCS'])))['symbols']).toMatch(/once/)
    expect(validateRunConfig(cfg((c) => (c.symbols = ['INFY', 'TCS', 'bad sym'])))['symbols']).toMatch(/not a valid NSE symbol/)
  })

  it('requires dates in train → validation → test order', () => {
    const e = validateRunConfig(cfg((c) => (c.dates.validation_start = '2023-06-01')))
    expect(e['dates.validation_start']).toMatch(/after train end/)
    expect(e.dates).toMatch(/in order/)
  })

  it('checks numeric bounds generated from the contract', () => {
    expect(validateRunConfig(cfg((c) => (c.qaoa.depth = 0)))['qaoa.depth']).toMatch(/Depth p must be/)
    expect(validateRunConfig(cfg((c) => (c.qaoa.shots = 1.5)))['qaoa.shots']).toMatch(/whole number/)
    expect(validateRunConfig(cfg((c) => (c.budget_inr = Number.NaN)))['budget_inr']).toMatch(/must be a number/)
  })

  it('checks sector caps by basis', () => {
    const e = validateRunConfig(cfg((c) => (c.constraints.sector_caps = { basis: 'weight', default_max: 1.5, per_sector: {} } as RunConfig['constraints']['sector_caps'])))
    expect(e['constraints.sector_caps.default_max']).toMatch(/fraction/)
  })
})
