// Scrolling strip of live NSE prices shown above the page header. Display only: it never feeds a run.

import { memo } from 'react'
import { useLiveQuotes, type LiveEntry, type LiveState } from '../../lib/live'
import { formatDayMonth, formatQuotePct, formatQuotePrice, quoteDirection } from './quote'

interface Pill {
  label: string
  tone: 'live' | 'delayed' | 'idle'
}

function pillFor(s: LiveState): Pill {
  if (s.connection === 'offline') return { label: 'Offline', tone: 'idle' }
  if (!s.loaded) return s.connection === 'error' ? { label: 'Offline', tone: 'idle' } : { label: 'Connecting', tone: 'idle' }
  switch (s.status) {
    case 'live':
      return { label: 'Live', tone: 'live' }
    case 'delayed':
      return { label: 'Delayed', tone: 'delayed' }
    case 'closed':
      return { label: 'Market closed', tone: 'idle' }
    case 'connecting':
      return { label: 'Connecting', tone: 'idle' }
    default: {
      const day = formatDayMonth(s.asOf)
      return { label: day ? `Last close ${day}` : 'Last close', tone: 'idle' }
    }
  }
}

const DOT: Record<Pill['tone'], string> = {
  live: 'bg-teal-600 animate-pulse-dot',
  delayed: 'bg-amber-200',
  idle: 'bg-white/45',
}

const TickerItem = memo(function TickerItem({ entry }: { entry: LiveEntry }) {
  const q = entry.quote
  const dir = quoteDirection(q.change_pct)
  return (
    <li className="flex shrink-0 items-center gap-2 px-4 text-[13px] leading-none whitespace-nowrap">
      <span className="font-semibold tracking-wide text-white">{q.label}</span>
      <span
        key={entry.tick}
        className={`tabular rounded px-1 py-0.5 text-white/90 ${entry.tick > 0 && entry.move ? (entry.move === 'up' ? 'qf-tick-flash-up' : 'qf-tick-flash-down') : ''}`}
      >
        {formatQuotePrice(q.price)}
      </span>
      {q.change_pct !== null && (
        <span className={`tabular inline-flex items-center gap-1 text-xs font-medium ${dir === 'up' ? 'qf-tick-up' : dir === 'down' ? 'qf-tick-down' : 'text-white/60'}`}>
          {dir !== 'flat' && <span aria-hidden="true">{dir === 'up' ? '▲' : '▼'}</span>}
          {dir !== 'flat' && <span className="sr-only">{dir === 'up' ? 'up' : 'down'}</span>}
          {formatQuotePct(q.change_pct)}
        </span>
      )}
    </li>
  )
})

function Items({ entries }: { entries: readonly LiveEntry[] }) {
  return entries.map((e) => <TickerItem key={e.quote.symbol} entry={e} />)
}

export function MarketTicker() {
  const s = useLiveQuotes()
  const pill = pillFor(s)
  const entries = s.entries.filter((e) => e.quote.price !== null)
  const loading = !s.loaded && s.connection !== 'error' && s.connection !== 'offline'
  // About 4 s per item keeps the scroll readable however many symbols there are.
  const duration = `${Math.max(30, entries.length * 4)}s`

  return (
    <section
      aria-label="Live NSE prices"
      data-testid="market-ticker"
      data-status={s.status ?? (loading ? 'loading' : 'unavailable')}
      className="qf-ticker relative flex h-9 items-stretch overflow-hidden bg-ink text-white"
    >
      <p
        className="relative z-10 flex shrink-0 items-center gap-2 border-r border-white/10 bg-ink px-3 text-xs font-semibold whitespace-nowrap sm:px-4"
        title={s.note ?? undefined}
        data-testid="market-ticker-status"
      >
        <span aria-hidden="true" className={`size-2 rounded-full ${DOT[pill.tone]}`} />
        {pill.label}
        {s.note && <span className="sr-only">. {s.note}</span>}
      </p>
      {entries.length > 0 ? (
        <div role="marquee" aria-label="NSE prices" tabIndex={0} className="qf-ticker-viewport relative min-w-0 flex-1 overflow-hidden outline-none">
          <div className="qf-ticker-track flex h-full w-max items-center" style={{ animationDuration: duration }}>
            <ul className="flex shrink-0 items-center">
              <Items entries={entries} />
            </ul>
            <ul aria-hidden="true" className="qf-ticker-copy flex shrink-0 items-center">
              <Items entries={entries} />
            </ul>
          </div>
        </div>
      ) : loading ? (
        <div aria-hidden="true" className="flex min-w-0 flex-1 items-center gap-6 overflow-hidden px-4">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <span key={i} className="h-3 w-28 shrink-0 rounded bg-white/10" />
          ))}
        </div>
      ) : (
        <p className="min-w-0 flex-1 truncate px-4 text-xs leading-9 text-white/70">
          {s.status === 'off' ? 'Live prices are off.' : 'Prices are not available right now.'}
        </p>
      )}
    </section>
  )
}
