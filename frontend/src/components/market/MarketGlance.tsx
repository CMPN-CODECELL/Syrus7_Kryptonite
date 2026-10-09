// Today's market in a few lines: a name, the live price with the day's change, and the candle chart under them.
// Used on the landing hero card. Display only: it never feeds a run.

import type { CandleInterval } from '../../api/contract.gen'
import { Skeleton } from '../ui'
import { CandleChart } from './CandleChart'
import { formatQuotePct, formatQuotePrice } from './quote'
import type { LiveCandles } from './useLiveCandles'

const TONE = { up: 'text-teal', down: 'text-danger', flat: 'text-muted' }
const ARROW = { up: '▲ ', down: '▼ ', flat: '' }

/** "NIFTY 50 · Live" on the left, the price and the day's change on the right. */
export function MarketPrice({ market, className = '' }: { market: LiveCandles; className?: string }) {
  return (
    <div className={`flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 ${className}`}>
      <p className="flex min-w-0 items-center gap-2 text-sm font-semibold text-ink">
        <span className="truncate">{market.label}</span>
        <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-muted">
          <span className={`h-1.5 w-1.5 rounded-full ${market.trading ? 'bg-teal-600 animate-pulse-dot' : 'bg-line-strong'}`} aria-hidden="true" />
          {market.when}
        </span>
      </p>
      <p className="flex shrink-0 items-baseline gap-2 tabular" data-testid="market-price">
        <span className="text-base font-semibold tracking-tight text-ink">{formatQuotePrice(market.price)}</span>
        <span className={`text-xs font-semibold ${TONE[market.direction]}`}>
          {ARROW[market.direction]}
          {formatQuotePct(market.changePct)}
          <span className="sr-only">{market.direction === 'down' ? ' down' : market.direction === 'up' ? ' up' : ''} on the previous close</span>
        </span>
      </p>
    </div>
  )
}

interface MarketGlanceProps {
  market: LiveCandles
  interval: CandleInterval
  /** Height of the chart alone, in pixels. */
  height?: number
  /** Widest candle body in pixels, passed to the chart. */
  maxBody?: number
  className?: string
}

/** Nothing at all when there are no real candles to draw, so the card around it looks as it does without a chart. */
export function MarketGlance({ market, interval, height = 148, maxBody, className = '' }: MarketGlanceProps) {
  if (market.state === 'none') return null
  return (
    <section aria-label={`${market.label} today`} className={className} data-testid="market-glance" data-state={market.state}>
      {market.state === 'loading' ? (
        <div style={{ height: height + 32 }}>
          <Skeleton className="h-full w-full" label="Loading the price chart" />
        </div>
      ) : (
        <>
          <MarketPrice market={market} />
          <CandleChart candles={market.candles} interval={interval} prevClose={market.prevClose} label={market.label} height={height} maxBody={maxBody} compact className="mt-2" />
        </>
      )}
    </section>
  )
}
