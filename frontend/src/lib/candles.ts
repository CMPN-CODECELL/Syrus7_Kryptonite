// Scale maths for the candle chart (display only). Pure functions: no DOM, no network.

import type { Candle, CandleInterval } from '../api/contract.gen'

export interface ChartBox {
  width: number
  height: number
  /** Space kept free around the bars: price labels sit on the right, time labels along the bottom. */
  top: number
  right: number
  bottom: number
  left: number
  /** Widest candle body in pixels (default 11). */
  maxBody?: number
}

export interface CandleBar {
  /** Centre of the bar. */
  x: number
  /** Top and height of the body (open to close), never thinner than 1 px. */
  bodyY: number
  bodyH: number
  /** The wick runs from the high to the low. */
  highY: number
  lowY: number
  up: boolean
}

export interface CandleLayout {
  bars: CandleBar[]
  bodyW: number
  /** Width of one bar's slot; divide a pointer offset by it to find the bar under the pointer. */
  band: number
  low: number
  high: number
  y: (price: number) => number
  /** Where the previous close sits, or null when it is missing or too far away to draw. */
  prevCloseY: number | null
}

const ok = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** Bars with usable numbers only; the server never sends a broken bar, this just keeps the maths safe. */
export function cleanCandles(candles: readonly Candle[] | null | undefined): Candle[] {
  return (candles ?? []).filter((c) => ok(c.o) && ok(c.h) && ok(c.l) && ok(c.c) && c.h >= c.l)
}

/** The last bar follows the live price: its close moves to `price`, and its high or low stretch to include it. */
export function followLive(candles: readonly Candle[], price: number | null | undefined): Candle[] {
  if (!candles.length || !ok(price) || price <= 0) return [...candles]
  const last = candles[candles.length - 1]
  if (last.c === price) return [...candles]
  return [...candles.slice(0, -1), { ...last, c: price, h: Math.max(last.h, price), l: Math.min(last.l, price) }]
}

export interface DaySummary {
  open: number
  high: number
  low: number
  last: number
}

export function daySummary(candles: readonly Candle[]): DaySummary | null {
  if (!candles.length) return null
  return {
    open: candles[0].o,
    high: Math.max(...candles.map((c) => c.h)),
    low: Math.min(...candles.map((c) => c.l)),
    last: candles[candles.length - 1].c,
  }
}

/** True when the previous close sits near the day's range; a far-off one is left out, as it would squash the bars flat. */
export function showsPrevClose(candles: readonly Candle[], prevClose: number | null | undefined): prevClose is number {
  const day = daySummary(candles)
  if (!day || !ok(prevClose) || prevClose <= 0) return false
  const reach = Math.max((day.high - day.low) * 0.6, day.last * 0.002)
  return prevClose >= day.low - reach && prevClose <= day.high + reach
}

/** Fewer bars than this still get this many slots, so an early session does not draw as a few giant bars. */
export const MIN_SLOTS = 24

export function candleLayout(candles: readonly Candle[], prevClose: number | null | undefined, box: ChartBox): CandleLayout | null {
  const day = daySummary(candles)
  const plotW = box.width - box.left - box.right
  const plotH = box.height - box.top - box.bottom
  if (!day || plotW <= 0 || plotH <= 0) return null
  let low = day.low
  let high = day.high
  const showPrev = showsPrevClose(candles, prevClose)
  if (showPrev) {
    low = Math.min(low, prevClose)
    high = Math.max(high, prevClose)
  }
  const pad = (high - low) * 0.06 || Math.max(day.last * 0.001, 0.01)
  low -= pad
  high += pad
  const y = (price: number) => box.top + ((high - price) / (high - low)) * plotH
  const band = plotW / Math.max(candles.length, MIN_SLOTS)
  // A whole, odd number of pixels, so a body sits evenly on its one-pixel wick and its edges stay sharp.
  const fit = Math.min(box.maxBody ?? 11, Math.floor(band - 1))
  const bodyW = Math.max(1, fit % 2 ? fit : fit - 1)
  const bars = candles.map((c, i): CandleBar => {
    const top = y(Math.max(c.o, c.c))
    return {
      x: box.left + band * (i + 0.5),
      bodyY: top,
      bodyH: Math.max(1, y(Math.min(c.o, c.c)) - top),
      highY: y(c.h),
      lowY: y(c.l),
      up: c.c >= c.o,
    }
  })
  return { bars, bodyW, band, low, high, y, prevCloseY: showPrev ? y(prevClose as number) : null }
}

const IST = 'Asia/Kolkata'
const clock = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: IST })
// en-US month names are always three letters ("Sep"); other English locales vary between browsers.
const dayOnly = new Intl.DateTimeFormat('en-US', { day: 'numeric', timeZone: IST })
const monthOnly = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: IST })

/** A bar's start in exchange time: "09:15" for intraday bars, "9 Oct" for daily ones. */
export function candleTime(t: string, interval: CandleInterval): string {
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return ''
  return interval === '1d' ? `${dayOnly.format(d)} ${monthOnly.format(d)}` : clock.format(d)
}

export interface TimeTick {
  index: number
  label: string
}

/** A few evenly placed time labels: whole hours for intraday bars, month starts for daily ones. */
export function timeTicks(candles: readonly Candle[], interval: CandleInterval, max: number): TimeTick[] {
  const marks: TimeTick[] = []
  let lastMonth = ''
  candles.forEach((c, index) => {
    const d = new Date(c.t)
    if (Number.isNaN(d.getTime())) return
    if (interval === '1d') {
      const month = monthOnly.format(d)
      if (lastMonth && month !== lastMonth) marks.push({ index, label: month })
      lastMonth = month
    } else {
      const label = clock.format(d)
      if (label.endsWith(':00')) marks.push({ index, label })
    }
  })
  if (marks.length <= max || max < 1) return max < 1 ? [] : marks
  const step = Math.ceil(marks.length / max)
  return marks.filter((_, i) => i % step === 0)
}

const whole = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
const paise = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Axis price: index levels lose their decimals ("22,548"), share prices keep them ("1,170.70"). */
export function axisPrice(v: number): string {
  return Math.abs(v) >= 10_000 ? whole.format(v) : paise.format(v)
}
