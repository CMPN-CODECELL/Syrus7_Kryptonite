// A candlestick chart drawn as plain SVG: a green bar when the price rose over the interval, a red one when it
// fell. Display only: it never feeds a run. It draws exactly the bars it is given and nothing when there are none.

import { useEffect, useMemo, useState, type KeyboardEvent, type PointerEvent } from 'react'
import type { Candle, CandleInterval } from '../../api/contract.gen'
import { axisPrice, candleLayout, candleTime, cleanCandles, daySummary, timeTicks } from '../../lib/candles'
import { formatQuotePrice } from './quote'

const UP = 'var(--color-teal-600)'
const DOWN = 'var(--color-danger)'

/** The element's width in CSS pixels, kept current as it resizes; 0 until it has been measured. */
function useWidth(el: HTMLElement | null, enabled: boolean): number {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!enabled || !el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0)
      setWidth((p) => (p === w ? p : w))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [el, enabled])
  return width
}

export interface CandleChartProps {
  candles: readonly Candle[]
  interval: CandleInterval
  prevClose?: number | null
  /** What is charted, e.g. "NIFTY 50". Starts the spoken summary. */
  label: string
  height?: number
  /** Smaller labels and fewer of them, for a narrow card. */
  compact?: boolean
  /** A fixed width in pixels. Leave it out and the chart fills its container. */
  width?: number
  /** Widest candle body in pixels; bodies still shrink to fit when there are many bars. */
  maxBody?: number
  className?: string
}

export function CandleChart({ candles, interval, prevClose = null, label, height = 260, compact = false, width: fixedWidth, maxBody, className = '' }: CandleChartProps) {
  const [el, setEl] = useState<HTMLDivElement | null>(null)
  const measured = useWidth(el, fixedWidth === undefined)
  const width = fixedWidth ?? measured
  const [active, setActive] = useState<number | null>(null)

  const bars = useMemo(() => cleanCandles(candles), [candles])
  const day = useMemo(() => daySummary(bars), [bars])
  const font = compact ? 10 : 11
  const gutter = day ? Math.ceil(axisPrice(day.high).length * font * 0.6) + 14 : 0
  const layout = useMemo(
    () => (width > 0 ? candleLayout(bars, prevClose, { width, height, top: 8, right: gutter, bottom: 20, left: 2, maxBody }) : null),
    [bars, prevClose, width, height, gutter, maxBody],
  )
  const ticks = useMemo(() => timeTicks(bars, interval, compact ? 4 : width >= 720 ? 8 : 5), [bars, interval, compact, width])

  if (!day) return null
  const n = bars.length
  const at = active !== null && active < n ? active : null
  const summary =
    `${label}: opened at ${formatQuotePrice(day.open)}, high ${formatQuotePrice(day.high)}, low ${formatQuotePrice(day.low)}, last ${formatQuotePrice(day.last)}.` +
    (prevClose ? ` Previous close ${formatQuotePrice(prevClose)}.` : '')

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!layout) return
    const i = Math.floor((e.clientX - e.currentTarget.getBoundingClientRect().left - 2) / layout.band)
    setActive(i >= 0 && i < n ? i : null)
  }
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const from = at ?? n - 1
    const next = e.key === 'ArrowLeft' ? from - 1 : e.key === 'ArrowRight' ? from + 1 : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : null
    if (e.key === 'Escape') setActive(null)
    if (next === null) return
    e.preventDefault()
    setActive(Math.max(0, Math.min(n - 1, next)))
  }

  const plotBottom = height - 20
  const lastUp = day.last >= (prevClose ?? day.open)
  const lastY = layout ? Math.max(8 + font / 2, Math.min(plotBottom - font / 2, layout.y(day.last))) : 0
  // The previous close, the low and the high are labelled where they sit, unless another label is already there.
  const edges: Array<{ v: number; y: number }> = []
  if (layout) {
    const taken = [lastY]
    for (const v of [layout.prevCloseY !== null ? prevClose : null, day.low, day.high]) {
      if (v === null) continue
      const y = layout.y(v)
      if (taken.some((t) => Math.abs(t - y) < font + 5)) continue
      taken.push(y)
      edges.push({ v, y })
    }
  }
  const bodyW = layout?.bodyW ?? 1
  const shown = at !== null ? bars[at] : null

  return (
    <div
      ref={setEl}
      role="group"
      aria-label={`${label} price chart. Arrow keys read each bar.`}
      tabIndex={0}
      onPointerMove={onMove}
      onPointerDown={onMove}
      onPointerLeave={(e) => e.pointerType === 'mouse' && setActive(null)}
      onFocus={() => setActive((a) => a ?? n - 1)}
      onBlur={() => setActive(null)}
      onKeyDown={onKey}
      className={`relative w-full min-w-0 touch-pan-y rounded-md outline-offset-2 select-none ${className}`}
      style={{ height }}
      data-testid="candle-chart"
      data-candles={n}
    >
      {layout ? (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary} focusable="false" className="block tabular" style={{ fontSize: font }}>
          {at !== null ? <rect x={2 + layout.band * at} y={4} width={layout.band} height={plotBottom - 4} rx={2} fill="var(--color-mist-2)" /> : null}
          {layout.prevCloseY !== null ? (
            <line x1={2} x2={width - gutter} y1={Math.round(layout.prevCloseY) + 0.5} y2={Math.round(layout.prevCloseY) + 0.5} stroke="var(--color-line-strong)" strokeDasharray="3 4" />
          ) : null}
          {layout.bars.map((b, i) => {
            const x = Math.floor(b.x) + 0.5
            const tone = b.up ? UP : DOWN
            return (
              <g key={bars[i].t} data-up={b.up ? '1' : '0'}>
                <line x1={x} x2={x} y1={b.highY} y2={b.lowY} stroke={tone} strokeWidth={1} />
                <rect x={x - bodyW / 2} y={b.bodyY} width={bodyW} height={b.bodyH} fill={tone} />
              </g>
            )
          })}
          <g aria-hidden="true">
            {edges.map((p) => (
              <text key={p.v} x={width - gutter + 8} y={p.y} dominantBaseline="central" fill="var(--color-faint)">
                {axisPrice(p.v)}
              </text>
            ))}
            <rect x={width - gutter + 4} y={lastY - font / 2 - 3} width={gutter - 4} height={font + 6} rx={3} fill={lastUp ? UP : DOWN} />
            <text x={width - gutter + 8} y={lastY} dominantBaseline="central" fill="#fff" fontWeight={600}>
              {axisPrice(day.last)}
            </text>
            {ticks.map((t) => (
              <text key={t.index} x={Math.max(16, layout.bars[t.index].x)} y={height - 5} textAnchor="middle" fill="var(--color-faint)">
                {t.label}
              </text>
            ))}
          </g>
        </svg>
      ) : null}
      <p
        aria-live="polite"
        className={`pointer-events-none absolute top-0 flex max-w-[calc(100%-0.5rem)] flex-wrap gap-x-2.5 rounded-md border border-line bg-canvas/95 px-2 py-1 text-xs leading-snug text-ink-2 shadow-card tabular ${
          shown ? '' : 'hidden'
        } ${at !== null && at < n / 2 ? 'right-1' : 'left-1'}`}
        data-testid="candle-readout"
      >
        {shown ? (
          <>
            <span className="font-semibold text-ink">{candleTime(shown.t, interval)}</span>
            <span>Open {formatQuotePrice(shown.o)}</span>
            <span>High {formatQuotePrice(shown.h)}</span>
            <span>Low {formatQuotePrice(shown.l)}</span>
            <span className={`font-semibold ${shown.c >= shown.o ? 'text-teal' : 'text-danger'}`}>Close {formatQuotePrice(shown.c)}</span>
          </>
        ) : null}
      </p>
    </div>
  )
}
