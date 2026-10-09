// Live NSE prices for the ticker and price cells. Display only: nothing here ever feeds a run.
//
// One shared store per tab, started by the first subscriber and stopped shortly after the last one leaves.
//  - Server-Sent Events from /api/market/stream: a `snapshot` frame replaces every quote, a `delta` frame merges
//    the changed ones. Heartbeats only keep the watchdog quiet.
//  - After a few failed connections in a row it polls /api/market/live every second instead.
//  - It pauses while the tab is hidden or the browser is offline, and resumes when that ends.
//  - When the server says live prices are off it closes the stream and stays closed.

import { useSyncExternalStore } from 'react'
import { api } from '../api/client'
import type { LiveQuote, LiveQuotesResponse, LiveStatus } from '../api/contract.gen'
import { CLOUD } from './cloud'

export type LiveConnection = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'polling' | 'paused' | 'offline' | 'error' | 'closed'
export type LiveMove = 'up' | 'down' | null

/** One symbol: its latest quote, the direction of its last price move, and a counter that changes on every move. */
export interface LiveEntry {
  quote: LiveQuote
  move: LiveMove
  tick: number
}

export interface LiveState {
  /** True once any frame or poll has answered. */
  loaded: boolean
  status: LiveStatus | null
  source: string | null
  asOf: string | null
  updatedAt: string | null
  marketOpen: boolean
  note: string | null
  /** Indices first, then stocks, in server order. */
  entries: readonly LiveEntry[]
  bySymbol: ReadonlyMap<string, LiveEntry>
  connection: LiveConnection
}

export const LIVE_SOURCE_STORED = 'Stored dataset'

const EMPTY: LiveState = {
  loaded: false,
  status: null,
  source: null,
  asOf: null,
  updatedAt: null,
  marketOpen: false,
  note: null,
  entries: [],
  bySymbol: new Map(),
  connection: 'idle',
}

interface PageLike {
  readonly hidden: boolean
  addEventListener(type: 'visibilitychange', fn: () => void): void
  removeEventListener(type: 'visibilitychange', fn: () => void): void
}

interface NetLike {
  readonly onLine: boolean
  addEventListener(type: 'online' | 'offline', fn: () => void): void
  removeEventListener(type: 'online' | 'offline', fn: () => void): void
}

export interface LiveDeps {
  EventSource?: typeof EventSource | null
  fetchLive?: (signal?: AbortSignal) => Promise<LiveQuotesResponse>
  url?: string
  pollMs?: number
  watchdogMs?: number
  maxSseFailures?: number
  /** How long the stream stays up after the last subscriber leaves (route changes remount the ticker). */
  lingerMs?: number
  page?: PageLike | null
  net?: NetLike | null
}

function browserPage(): PageLike | null {
  if (typeof document === 'undefined') return null
  return {
    get hidden() {
      return document.hidden
    },
    addEventListener: (t, fn) => document.addEventListener(t, fn),
    removeEventListener: (t, fn) => document.removeEventListener(t, fn),
  }
}

function browserNet(): NetLike | null {
  if (typeof window === 'undefined') return null
  return {
    get onLine() {
      return typeof navigator === 'undefined' || navigator.onLine !== false
    },
    addEventListener: (t, fn) => window.addEventListener(t, fn),
    removeEventListener: (t, fn) => window.removeEventListener(t, fn),
  }
}

/** Parses one stream frame. Returns null for anything that is not a well-formed snapshot or delta body. */
export function parseLiveFrame(data: unknown): LiveQuotesResponse | null {
  if (typeof data !== 'string' || !data) return null
  let o: unknown
  try {
    o = JSON.parse(data)
  } catch {
    return null
  }
  if (!o || typeof o !== 'object') return null
  const r = o as Partial<LiveQuotesResponse>
  if (typeof r.status !== 'string' || !Array.isArray(r.quotes)) return null
  const quotes = r.quotes.filter((q): q is LiveQuote => !!q && typeof q === 'object' && typeof (q as LiveQuote).symbol === 'string')
  return { ...(r as LiveQuotesResponse), quotes }
}

export class LiveStore {
  private state: LiveState = EMPTY
  private listeners = new Set<() => void>()
  private readonly deps: Required<Omit<LiveDeps, 'EventSource' | 'page' | 'net'>> & Pick<LiveDeps, 'EventSource' | 'page' | 'net'>
  private running = false
  private es: EventSource | null = null
  private sseFailures = 0
  private pollFailures = 0
  private polling = false
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private watchdog: ReturnType<typeof setTimeout> | null = null
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  private lingerTimer: ReturnType<typeof setTimeout> | null = null
  private pollAbort: AbortController | null = null
  private generation = 0

  constructor(deps: LiveDeps = {}) {
    this.deps = {
      EventSource: deps.EventSource === undefined ? (typeof EventSource === 'undefined' ? null : EventSource) : deps.EventSource,
      fetchLive: deps.fetchLive ?? api.marketLive,
      url: deps.url ?? '/api/market/stream',
      pollMs: deps.pollMs ?? 1_000,
      watchdogMs: deps.watchdogMs ?? 25_000,
      maxSseFailures: deps.maxSseFailures ?? 3,
      lingerMs: deps.lingerMs ?? 5_000,
      page: deps.page === undefined ? browserPage() : deps.page,
      net: deps.net === undefined ? browserNet() : deps.net,
    }
  }

  getState = (): LiveState => this.state

  getServerState = (): LiveState => EMPTY

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    if (this.lingerTimer) {
      clearTimeout(this.lingerTimer)
      this.lingerTimer = null
    }
    if (!this.running) this.start()
    return () => {
      this.listeners.delete(fn)
      if (this.listeners.size === 0 && this.running && !this.lingerTimer) {
        this.lingerTimer = setTimeout(() => {
          this.lingerTimer = null
          if (this.listeners.size === 0) this.stop()
        }, this.deps.lingerMs)
      }
    }
  }

  /** Starts listening (normally done by the first subscriber). */
  start(): void {
    if (this.running) return
    this.running = true
    this.sseFailures = 0
    this.deps.page?.addEventListener('visibilitychange', this.onVisibility)
    this.deps.net?.addEventListener('online', this.onOnline)
    this.deps.net?.addEventListener('offline', this.onOffline)
    this.resume()
  }

  /** Stops every connection, timer and listener. The last prices stay in the state. */
  stop(): void {
    if (!this.running) return
    this.running = false
    this.deps.page?.removeEventListener('visibilitychange', this.onVisibility)
    this.deps.net?.removeEventListener('online', this.onOnline)
    this.deps.net?.removeEventListener('offline', this.onOffline)
    this.halt()
    this.set({ connection: 'idle' })
  }

  private onVisibility = () => {
    if (!this.running) return
    if (this.deps.page?.hidden) {
      this.halt()
      this.set({ connection: 'paused' })
    } else this.resume()
  }

  private onOnline = () => {
    if (this.running) this.resume()
  }

  private onOffline = () => {
    if (!this.running) return
    this.halt()
    this.set({ connection: 'offline' })
  }

  private resume(): void {
    this.halt()
    if (this.deps.net && !this.deps.net.onLine) return this.set({ connection: 'offline' })
    if (this.deps.page?.hidden) return this.set({ connection: 'paused' })
    if (this.state.status === 'off' && this.state.connection === 'closed') return
    this.sseFailures = 0
    this.connect()
  }

  /** Tears down the stream, polling and timers without touching `running`. */
  private halt(): void {
    this.generation++
    this.teardownStream()
    this.polling = false
    this.pollAbort?.abort()
    this.pollAbort = null
    for (const t of [this.retryTimer, this.pollTimer]) if (t) clearTimeout(t)
    this.retryTimer = null
    this.pollTimer = null
  }

  private teardownStream(): void {
    if (this.watchdog) clearTimeout(this.watchdog)
    this.watchdog = null
    const es = this.es
    this.es = null
    if (es) {
      es.onopen = null
      es.onmessage = null
      es.onerror = null
      es.close()
    }
  }

  private connect(): void {
    const ES = this.deps.EventSource
    if (!ES || this.sseFailures >= this.deps.maxSseFailures) return this.startPolling()
    const gen = this.generation
    let es: EventSource
    try {
      es = new ES(this.deps.url)
    } catch {
      return this.startPolling()
    }
    this.es = es
    this.set({ connection: this.state.loaded ? 'reconnecting' : 'connecting' })
    this.armWatchdog()
    const onFrame = (full: boolean) => (e: Event) => {
      if (gen !== this.generation) return
      this.armWatchdog()
      const body = parseLiveFrame((e as MessageEvent).data)
      if (!body) return
      this.sseFailures = 0
      this.apply(body, full, 'open')
      if (body.status === 'off') {
        this.halt()
        this.set({ connection: 'closed' })
      }
    }
    es.addEventListener('snapshot', onFrame(true))
    es.addEventListener('delta', onFrame(false))
    es.addEventListener('heartbeat', () => {
      if (gen === this.generation) this.armWatchdog()
    })
    es.addEventListener('error', (e) => {
      if (gen !== this.generation) return
      if (e instanceof MessageEvent && typeof e.data === 'string' && e.data) return // a server event named "error"
      this.streamFailed()
    })
  }

  private armWatchdog(): void {
    if (this.watchdog) clearTimeout(this.watchdog)
    this.watchdog = setTimeout(() => {
      this.watchdog = null
      if (this.es) this.streamFailed()
    }, this.deps.watchdogMs)
  }

  private streamFailed(): void {
    this.teardownStream()
    this.sseFailures++
    if (this.sseFailures >= this.deps.maxSseFailures) return this.startPolling()
    this.set({ connection: this.state.loaded ? 'reconnecting' : 'connecting' })
    const delay = Math.min(15_000, 1_000 * 2 ** Math.min(4, this.sseFailures - 1))
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      if (this.running) this.connect()
    }, delay)
  }

  private startPolling(): void {
    this.teardownStream()
    if (this.polling) return
    this.polling = true
    this.pollFailures = 0
    if (!this.state.loaded) this.set({ connection: 'connecting' })
    void this.poll(this.generation)
  }

  private async poll(gen: number): Promise<void> {
    if (gen !== this.generation || !this.polling) return
    const ctrl = new AbortController()
    this.pollAbort = ctrl
    let delay = this.deps.pollMs
    try {
      const body = await this.deps.fetchLive(ctrl.signal)
      if (gen !== this.generation) return
      this.pollFailures = 0
      this.apply(body, true, 'polling')
      if (body.status === 'off') {
        this.halt()
        this.set({ connection: 'closed' })
        return
      }
    } catch {
      if (gen !== this.generation) return
      this.pollFailures++
      this.set({ connection: 'error' })
      delay = Math.min(15_000, this.deps.pollMs * 2 ** Math.min(4, this.pollFailures))
    } finally {
      if (this.pollAbort === ctrl) this.pollAbort = null
    }
    if (gen !== this.generation || !this.polling) return
    this.pollTimer = setTimeout(() => {
      this.pollTimer = null
      void this.poll(gen)
    }, delay)
  }

  /** Applies a full snapshot (replace) or a delta (merge by symbol). */
  apply(body: LiveQuotesResponse, full: boolean, connection: LiveConnection = this.state.connection): void {
    const prev = this.state.bySymbol
    const next = new Map<string, LiveEntry>()
    let order: string[]
    if (full) order = body.quotes.map((q) => q.symbol)
    else {
      order = this.state.entries.map((e) => e.quote.symbol)
      for (const [k, v] of prev) next.set(k, v)
      for (const q of body.quotes) if (!prev.has(q.symbol)) order.push(q.symbol)
    }
    for (const q of body.quotes) next.set(q.symbol, merge(prev.get(q.symbol), q))
    const entries: LiveEntry[] = []
    const seen = new Set<string>()
    for (const s of order) {
      const e = next.get(s)
      if (e && !seen.has(s)) {
        seen.add(s)
        entries.push(e)
      }
    }
    this.set({
      loaded: true,
      status: body.status,
      source: body.source,
      asOf: body.as_of,
      updatedAt: body.updated_at,
      marketOpen: body.market_open,
      note: body.note,
      entries,
      bySymbol: new Map(entries.map((e) => [e.quote.symbol, e])),
      connection,
    })
  }

  private set(patch: Partial<LiveState>): void {
    const cur = this.state
    let changed = false
    for (const k of Object.keys(patch) as Array<keyof LiveState>) {
      if (cur[k] !== patch[k]) {
        changed = true
        break
      }
    }
    if (!changed) return
    this.state = { ...cur, ...patch }
    for (const fn of [...this.listeners]) fn()
  }
}

function merge(prev: LiveEntry | undefined, q: LiveQuote): LiveEntry {
  if (!prev) return { quote: q, move: null, tick: 0 }
  const a = prev.quote.price
  const b = q.price
  if (a === b && prev.quote.change_pct === q.change_pct && prev.quote.at === q.at) return prev
  if (typeof a === 'number' && typeof b === 'number' && a !== b) return { quote: q, move: b > a ? 'up' : 'down', tick: prev.tick + 1 }
  return { quote: q, move: prev.move, tick: prev.tick }
}

/** True when the prices come from the live feed rather than the stored dataset. */
export function hasLivePrices(s: LiveState): boolean {
  return s.loaded && s.source !== LIVE_SOURCE_STORED && s.status !== 'off' && s.status !== 'offline' && s.entries.length > 0
}

/**
 * The cloud build never opens the stream and polls every 5 s instead of every second. Behind Firebase Hosting a
 * stream is cut after 60 s, and while it is open it keeps a Cloud Run instance running (and billed) for every tab.
 */
export const CLOUD_LIVE_DEPS: LiveDeps = { EventSource: null, pollMs: 5_000 }

/** The tab-wide store used by the hooks below. */
export const liveStore = new LiveStore(CLOUD ? CLOUD_LIVE_DEPS : {})

/** Every live quote plus the feed status. Subscribing starts the feed. */
export function useLiveQuotes(): LiveState {
  return useSyncExternalStore(liveStore.subscribe, liveStore.getState, liveStore.getServerState)
}

function livePriceOf(symbol: string | null | undefined): LiveEntry | undefined {
  const s = liveStore.getState()
  if (!symbol || !hasLivePrices(s)) return undefined
  const e = s.bySymbol.get(symbol)
  return e && e.quote.price !== null ? e : undefined
}

/**
 * One symbol's live entry, or undefined while the feed has no live price for it (off, offline, still loading).
 * Re-renders only when that symbol changes.
 */
export function useLivePrice(symbol: string | null | undefined): LiveEntry | undefined {
  return useSyncExternalStore(liveStore.subscribe, () => livePriceOf(symbol), () => undefined)
}

/** The feed status when it is serving live prices, else null. */
export function useLiveStatus(): LiveStatus | null {
  return useSyncExternalStore(
    liveStore.subscribe,
    () => {
      const s = liveStore.getState()
      return hasLivePrices(s) ? s.status : null
    },
    () => null,
  )
}
