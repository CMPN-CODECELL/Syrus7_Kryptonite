import { describe, expect, it } from 'vitest'
import type { Candle } from '../api/contract.gen'
import { MIN_SLOTS, axisPrice, candleLayout, candleTime, cleanCandles, daySummary, followLive, showsPrevClose, timeTicks } from './candles'

const bar = (i: number, o: number, h: number, l: number, c: number, stepMin = 5): Candle => ({
  t: new Date(Date.UTC(2026, 9, 9, 3, 45 + i * stepMin)).toISOString(), // 09:15 IST onwards
  o,
  h,
  l,
  c,
})
const DAY = [bar(0, 100, 104, 99, 103), bar(1, 103, 105, 101, 102), bar(2, 102, 106, 100, 105)]
const BOX = { width: 400, height: 200, top: 10, right: 50, bottom: 20, left: 0 }

describe('cleanCandles', () => {
  it('keeps real bars and drops broken ones', () => {
    const broken = [{ ...DAY[0], c: Number.NaN }, { ...DAY[1], h: 90 }, DAY[2]]
    expect(cleanCandles(broken)).toEqual([DAY[2]])
    expect(cleanCandles(null)).toEqual([])
  })
})

describe('followLive', () => {
  it('moves the last close to the live price and stretches the high or low to include it', () => {
    expect(followLive(DAY, 108).at(-1)).toMatchObject({ o: 102, h: 108, l: 100, c: 108 })
    expect(followLive(DAY, 98).at(-1)).toMatchObject({ o: 102, h: 106, l: 98, c: 98 })
    expect(followLive(DAY, 104).at(-1)).toMatchObject({ h: 106, l: 100, c: 104 })
  })
  it('leaves earlier bars alone, never adds a bar, and ignores a missing price', () => {
    const out = followLive(DAY, 108)
    expect(out).toHaveLength(3)
    expect(out.slice(0, 2)).toEqual(DAY.slice(0, 2))
    expect(DAY[2].c).toBe(105) // the input is not changed
    for (const p of [null, undefined, Number.NaN, 0]) expect(followLive(DAY, p)).toEqual(DAY)
    expect(followLive([], 100)).toEqual([])
  })
})

describe('daySummary', () => {
  it('is the first open, the extremes and the last close', () => {
    expect(daySummary(DAY)).toEqual({ open: 100, high: 106, low: 99, last: 105 })
    expect(daySummary([])).toBeNull()
  })
})

describe('candleLayout', () => {
  it('puts higher prices nearer the top and keeps every bar inside the plot', () => {
    const L = candleLayout(DAY, 101, BOX)!
    expect(L.y(106)).toBeLessThan(L.y(99))
    expect(L.bars.map((b) => b.up)).toEqual([true, false, true])
    for (const b of L.bars) {
      expect(b.highY).toBeGreaterThanOrEqual(BOX.top)
      expect(b.lowY).toBeLessThanOrEqual(BOX.height - BOX.bottom)
      expect(b.highY).toBeLessThanOrEqual(b.bodyY)
      expect(b.bodyY + b.bodyH).toBeLessThanOrEqual(b.lowY + 1e-9)
      expect(b.x).toBeLessThan(BOX.width - BOX.right)
    }
    expect(L.bars[0].bodyY).toBeCloseTo(L.y(103))
    expect(L.bars[0].bodyH).toBeCloseTo(L.y(100) - L.y(103))
  })
  it('gives a flat bar a visible body and a few bars a sensible width', () => {
    const L = candleLayout([bar(0, 100, 101, 99, 100)], null, BOX)!
    expect(L.bars[0].bodyH).toBe(1)
    expect(L.band).toBeCloseTo((BOX.width - BOX.right) / MIN_SLOTS)
    expect(L.bodyW).toBe(11) // capped, odd
    expect(candleLayout(Array.from({ length: 70 }, (_, i) => bar(i, 100, 101, 99, 100)), null, BOX)!.bodyW).toBe(3)
    expect(candleLayout(Array.from({ length: 200 }, (_, i) => bar(i, 100, 101, 99, 100)), null, BOX)!.bodyW).toBe(1)
    expect(L.prevCloseY).toBeNull()
  })
  it('lets a wider body cap through, still odd and still inside its slot', () => {
    const wide = { ...BOX, width: 800 } // 750 px over 24 slots: about 31 px a slot
    expect(candleLayout(DAY, null, { ...wide, maxBody: 17 })!.bodyW).toBe(17)
    expect(candleLayout(DAY, null, { ...wide, maxBody: 16 })!.bodyW).toBe(15)
    expect(candleLayout(DAY, null, wide)!.bodyW).toBe(11)
    // A narrow chart still shrinks the body to fit, whatever the cap.
    expect(candleLayout(DAY, null, { ...BOX, maxBody: 17 })!.bodyW).toBe(13)
  })
  it('draws the previous close only when it is near the range', () => {
    const near = candleLayout(DAY, 97, BOX)!
    expect(near.prevCloseY).toBeCloseTo(near.y(97))
    expect(near.low).toBeLessThan(97)
    expect(showsPrevClose(DAY, 97)).toBe(true)
    expect(candleLayout(DAY, 60, BOX)!.prevCloseY).toBeNull()
    expect(showsPrevClose(DAY, 60)).toBe(false)
    expect(showsPrevClose(DAY, null)).toBe(false)
  })
  it('handles a single flat price and an empty or zero-size chart', () => {
    const flat = candleLayout([bar(0, 50, 50, 50, 50)], null, BOX)!
    expect(Number.isFinite(flat.y(50))).toBe(true)
    expect(candleLayout([], 100, BOX)).toBeNull()
    expect(candleLayout(DAY, 100, { ...BOX, width: 40 })).toBeNull()
  })
})

describe('time labels', () => {
  it('formats in exchange time whatever the viewer time zone', () => {
    expect(candleTime(DAY[0].t, '5m')).toBe('09:15')
    expect(candleTime('2026-10-09T00:00:00+05:30', '1d')).toBe('9 Oct')
    expect(candleTime('nope', '5m')).toBe('')
  })
  it('marks whole hours for intraday bars and thins them to the limit', () => {
    const session = Array.from({ length: 75 }, (_, i) => bar(i, 100, 101, 99, 100)) // 09:15 to 15:25
    expect(timeTicks(session, '5m', 8).map((t) => t.label)).toEqual(['10:00', '11:00', '12:00', '13:00', '14:00', '15:00'])
    expect(timeTicks(session, '5m', 3).map((t) => t.label)).toEqual(['10:00', '12:00', '14:00'])
    expect(timeTicks(session, '5m', 8)[0].index).toBe(9)
    expect(timeTicks(session, '5m', 0)).toEqual([])
  })
  it('marks month starts for daily bars', () => {
    const days = ['2026-08-28', '2026-08-31', '2026-09-01', '2026-09-30', '2026-10-01'].map((d) => ({ ...DAY[0], t: `${d}T00:00:00+05:30` }))
    expect(timeTicks(days, '1d', 4)).toEqual([
      { index: 2, label: 'Sep' },
      { index: 4, label: 'Oct' },
    ])
  })
})

describe('axisPrice', () => {
  it('drops decimals for index levels and keeps them for share prices', () => {
    expect(axisPrice(22548.3)).toBe('22,548')
    expect(axisPrice(1170.7)).toBe('1,170.70')
    expect(axisPrice(123456.7)).toBe('1,23,457')
  })
})
