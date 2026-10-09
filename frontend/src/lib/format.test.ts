import { describe, expect, it } from 'vitest'
import { DASH, formatBytesMb, formatDuration, formatInr, formatMemoryEstimate, formatInrCompact, formatInt, formatNum, formatPct, formatRelative, shortId } from './format'

describe('format', () => {
  it('uses Indian digit grouping for rupees', () => {
    expect(formatInr(1_000_000)).toBe('₹10,00,000')
    expect(formatInt(12_345_678)).toBe('1,23,45,678')
    expect(formatInr(1234.5, { paise: true })).toBe('₹1,234.50')
  })

  it('compacts to lakh and crore', () => {
    expect(formatInrCompact(1_250_000)).toBe('₹12.5 L')
    expect(formatInrCompact(12_000_000)).toBe('₹1.2 Cr')
    expect(formatInrCompact(8_400)).toBe('₹8,400')
    expect(formatInrCompact(-300_000)).toBe('−₹3 L')
  })

  it('renders missing and non-finite values as a dash', () => {
    for (const f of [formatInr, formatInrCompact, formatInt, formatPct, formatNum, formatDuration, formatBytesMb]) {
      expect(f(null)).toBe(DASH)
      expect(f(undefined)).toBe(DASH)
      expect(f(Number.NaN)).toBe(DASH)
    }
  })

  it('formats percentages with a true minus sign and optional plus', () => {
    expect(formatPct(0.1234)).toBe('12.34%')
    expect(formatPct(-0.05, 1)).toBe('−5.0%')
    expect(formatPct(0.02, 1, { sign: true })).toBe('+2.0%')
    expect(formatPct(-0.00001, 2)).toBe('0.00%')
  })

  it('formats decimals and durations', () => {
    expect(formatNum(-0.123456, 3)).toBe('−0.123')
    expect(formatDuration(0.84)).toBe('840 ms')
    expect(formatDuration(5)).toBe('5.00 s')
    expect(formatDuration(12.44)).toBe('12.4 s')
    expect(formatDuration(185)).toBe('3 min 05 s')
    expect(formatDuration(3599.6)).toBe('1 h 00 min')
    expect(formatDuration(7500)).toBe('2 h 05 min')
    expect(formatDuration(90_000)).toBe('1 d 1 h')
    expect(formatDuration(2.5 * 365.25 * 86400)).toBe('2.5 years')
    expect(formatDuration(1e15)).toBe('over 100 years')
  })

  it('humanises memory with at most three significant figures', () => {
    expect(formatBytesMb(0.5)).toBe('512 KB')
    expect(formatBytesMb(12.44)).toBe('12.4 MB')
    expect(formatBytesMb(512.4)).toBe('512 MB')
    expect(formatBytesMb(2048)).toBe('2 GB')
    expect(formatBytesMb(1600)).toBe('1.56 GB')
    expect(formatBytesMb(3 * 1024 ** 2)).toBe('3 TB')
    // 16 bytes x 2^50 amplitudes: the figure that used to read "17592186.04 GB".
    expect(formatBytesMb(18_014_398_504)).toBe('16.8 PB')
    expect(formatBytesMb(1024 ** 6 * 5000)).toBe('5.00e+3 YB')
  })

  it('states a memory estimate against the limit', () => {
    expect(formatMemoryEstimate(512, 1500)).toBe('512 MB')
    expect(formatMemoryEstimate(1600, 1500)).toBe('1.56 GB, over the 1,500 MB limit')
    expect(formatMemoryEstimate(18_014_398_504, 1500)).toBe('far above the 1,500 MB limit')
    expect(formatMemoryEstimate(18_014_398_504, null)).toBe('16.8 PB')
    expect(formatMemoryEstimate(Number.NaN, 1500)).toBe(DASH)
  })

  it('gives relative times and short ids', () => {
    const now = Date.parse('2026-10-09T12:00:00Z')
    expect(formatRelative('2026-10-09T11:59:40Z', now)).toBe('just now')
    expect(formatRelative('2026-10-09T11:56:00Z', now)).toBe('4 min ago')
    expect(formatRelative('2026-10-09T09:00:00Z', now)).toBe('3 h ago')
    expect(formatRelative('not a date', now)).toBe('not a date')
    expect(shortId('run-20261008-f29dc129')).toBe('f29dc129')
    expect(shortId('exp-20261009-0a1b2c3d')).toBe('0a1b2c3d')
    expect(shortId('run-0123456789abcdef')).toBe('0123456789abcdef')
    expect(shortId('abc')).toBe('abc')
    expect(shortId('run-ab')).toBe('run-ab')
  })
})
