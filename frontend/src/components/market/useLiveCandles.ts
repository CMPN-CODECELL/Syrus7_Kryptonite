// Chart bars for one symbol, with the last bar following the live price (display only).

import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api } from '../../api/client'
import type { Candle, CandleInterval } from '../../api/contract.gen'
import { cleanCandles, followLive } from '../../lib/candles'
import { useLivePrice } from '../../lib/live'
import { quoteDirection, type QuoteDirection } from './quote'

export interface LiveCandles {
  /** `none` covers every reason there is nothing to draw: live prices off, Yahoo unreachable, or a failed request. */
  state: 'loading' | 'ready' | 'none'
  /** True when live prices are switched off on the server, so there is nothing to pick or retry. */
  off: boolean
  label: string
  candles: Candle[]
  prevClose: number | null
  price: number | null
  changePct: number | null
  direction: QuoteDirection
  /** "Live", "Delayed" or "Last session". */
  when: string
  trading: boolean
}

export function useLiveCandles(symbol: string, interval: CandleInterval, fallbackLabel = symbol): LiveCandles {
  const q = useQuery({
    queryKey: ['market-candles', symbol, interval] as const,
    queryFn: ({ signal }) => api.marketCandles(symbol, interval, signal),
    staleTime: 30_000,
    retry: 1,
    // About once a minute while the market trades; rarely once it has closed; never when live prices are off.
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'off' ? false : s === 'closed' ? 5 * 60_000 : 60_000
    },
  })
  const live = useLivePrice(symbol)
  const data = q.data
  const trading = data?.status === 'live' || data?.status === 'delayed'
  const livePrice = trading ? (live?.quote.price ?? null) : null

  const candles = useMemo(() => followLive(cleanCandles(data?.candles), livePrice), [data?.candles, livePrice])
  const last = candles.length ? candles[candles.length - 1].c : null
  const prevClose = data?.prev_close ?? null
  const changePct = trading && live?.quote.change_pct != null ? live.quote.change_pct : last !== null && prevClose ? ((last - prevClose) / prevClose) * 100 : null

  return {
    state: q.isPending ? 'loading' : candles.length ? 'ready' : 'none',
    off: data?.status === 'off',
    label: data?.label ?? fallbackLabel,
    candles,
    prevClose,
    price: last,
    changePct,
    direction: quoteDirection(changePct),
    when: data?.status === 'live' ? 'Live' : data?.status === 'delayed' ? 'Delayed' : 'Last session',
    trading,
  }
}
