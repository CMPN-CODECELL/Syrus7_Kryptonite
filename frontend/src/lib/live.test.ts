import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveQuote, LiveQuotesResponse } from '../api/contract.gen'
import { LiveStore, hasLivePrices, parseLiveFrame } from './live'

// Minimal EventSource double: records instances so tests can push frames into them.
class FakeES {
  static all: FakeES[] = []
  url: string
  onopen: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  closed = false
  private handlers = new Map<string, Array<(e: Event) => void>>()
  constructor(url: string) {
    this.url = url
    FakeES.all.push(this)
  }
  addEventListener(t: string, fn: (e: Event) => void) {
    this.handlers.set(t, [...(this.handlers.get(t) ?? []), fn])
  }
  close() {
    this.closed = true
  }
  emit(type: string, data: unknown) {
    const e = new MessageEvent(type, { data: typeof data === 'string' ? data : JSON.stringify(data) })
    for (const fn of this.handlers.get(type) ?? []) fn(e)
  }
  fail() {
    for (const fn of this.handlers.get('error') ?? []) fn(new Event('error'))
  }
}

class FakePage extends EventTarget {
  hidden = false
}

class FakeNet extends EventTarget {
  onLine = true
}

const ES = FakeES as unknown as typeof EventSource
const q = (symbol: string, price: number | null, extra: Partial<LiveQuote> = {}): LiveQuote => ({
  symbol,
  label: symbol.replace('.NS', ''),
  kind: symbol.startsWith('^') ? 'index' : 'stock',
  price,
  change: null,
  change_pct: 0.5,
  prev_close: null,
  at: '2026-10-09T08:30:00Z',
  delayed: false,
  ...extra,
})
const body = (quotes: LiveQuote[], extra: Partial<LiveQuotesResponse> = {}): LiveQuotesResponse => ({
  status: 'live',
  source: 'Yahoo Finance',
  updated_at: '2026-10-09T08:30:00Z',
  market_open: true,
  as_of: '2026-10-09',
  note: null,
  seq: 1,
  quotes,
  ...extra,
})

function makeStore(over: Partial<ConstructorParameters<typeof LiveStore>[0]> = {}) {
  const page = new FakePage()
  const net = new FakeNet()
  const fetchLive = vi.fn<(s?: AbortSignal) => Promise<LiveQuotesResponse>>().mockResolvedValue(body([q('^NSEI', 22000)]))
  const store = new LiveStore({ EventSource: ES, fetchLive, page, net, lingerMs: 100, ...over })
  return { store, page, net, fetchLive }
}

describe('LiveStore', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeES.all = []
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts on the first subscriber, applies a snapshot, then merges deltas in order', () => {
    const { store } = makeStore()
    const seen = vi.fn()
    const off = store.subscribe(seen)
    expect(FakeES.all).toHaveLength(1)
    expect(FakeES.all[0].url).toBe('/api/market/stream')
    expect(store.getState().connection).toBe('connecting')

    const es = FakeES.all[0]
    es.emit('snapshot', body([q('^NSEI', 22000), q('RELIANCE.NS', 2900)]))
    let s = store.getState()
    expect(s.loaded).toBe(true)
    expect(s.connection).toBe('open')
    expect(s.entries.map((e) => e.quote.symbol)).toEqual(['^NSEI', 'RELIANCE.NS'])
    expect(hasLivePrices(s)).toBe(true)
    const relBefore = s.bySymbol.get('RELIANCE.NS')

    es.emit('delta', body([q('^NSEI', 22010)], { seq: 2 }))
    s = store.getState()
    expect(s.bySymbol.get('^NSEI')?.quote.price).toBe(22010)
    expect(s.bySymbol.get('^NSEI')?.move).toBe('up')
    expect(s.bySymbol.get('^NSEI')?.tick).toBe(1)
    expect(s.bySymbol.get('RELIANCE.NS')).toBe(relBefore) // unchanged symbols keep their identity

    es.emit('delta', body([q('^NSEI', 21990), q('TCS.NS', 4100)], { seq: 3 }))
    s = store.getState()
    expect(s.bySymbol.get('^NSEI')?.move).toBe('down')
    expect(s.bySymbol.get('^NSEI')?.tick).toBe(2)
    expect(s.entries.map((e) => e.quote.symbol)).toEqual(['^NSEI', 'RELIANCE.NS', 'TCS.NS'])
    expect(seen).toHaveBeenCalled()

    off()
    vi.advanceTimersByTime(150)
    expect(es.closed).toBe(true)
    expect(store.getState().connection).toBe('idle')
    expect(store.getState().entries).toHaveLength(3) // last prices stay
  })

  it('ignores heartbeats and malformed frames', () => {
    const { store } = makeStore()
    store.subscribe(() => {})
    const es = FakeES.all[0]
    es.emit('snapshot', body([q('^NSEI', 22000)]))
    const before = store.getState()
    es.emit('heartbeat', { type: 'heartbeat', data: {} })
    es.emit('delta', 'not json')
    es.emit('delta', { nope: true })
    expect(store.getState()).toBe(before)
    store.stop()
  })

  it('closes for good when live prices are off', () => {
    const { store, net } = makeStore()
    store.subscribe(() => {})
    const es = FakeES.all[0]
    es.emit('snapshot', body([q('RELIANCE.NS', 2900)], { status: 'off', source: 'Stored dataset', updated_at: null }))
    expect(es.closed).toBe(true)
    expect(store.getState().connection).toBe('closed')
    expect(store.getState().status).toBe('off')
    expect(hasLivePrices(store.getState())).toBe(false)
    vi.advanceTimersByTime(60_000)
    net.dispatchEvent(new Event('online'))
    expect(FakeES.all).toHaveLength(1) // no reconnect
    store.stop()
  })

  it('reconnects with backoff, then falls back to polling every second', async () => {
    const { store, fetchLive } = makeStore()
    store.subscribe(() => {})
    FakeES.all[0].fail()
    expect(store.getState().connection).toBe('connecting')
    vi.advanceTimersByTime(1_000)
    expect(FakeES.all).toHaveLength(2)
    FakeES.all[1].fail()
    vi.advanceTimersByTime(2_000)
    expect(FakeES.all).toHaveLength(3)
    FakeES.all[2].fail() // third failure in a row: poll instead
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchLive).toHaveBeenCalledTimes(1)
    expect(store.getState().connection).toBe('polling')
    expect(store.getState().bySymbol.get('^NSEI')?.quote.price).toBe(22000)
    fetchLive.mockResolvedValue(body([q('^NSEI', 22050)], { seq: 2 }))
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fetchLive).toHaveBeenCalledTimes(2)
    expect(store.getState().bySymbol.get('^NSEI')?.move).toBe('up')
    expect(FakeES.all).toHaveLength(3)
    store.stop()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchLive).toHaveBeenCalledTimes(2)
  })

  it('reports an unreachable API while polling', async () => {
    const fetchLive = vi.fn<(s?: AbortSignal) => Promise<LiveQuotesResponse>>().mockRejectedValue(new Error('down'))
    const { store } = makeStore({ EventSource: null, fetchLive })
    store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    expect(store.getState().connection).toBe('error')
    expect(store.getState().loaded).toBe(false)
    store.stop()
  })

  it('reconnects when the watchdog hears nothing', () => {
    const { store } = makeStore({ watchdogMs: 5_000 })
    store.subscribe(() => {})
    FakeES.all[0].emit('snapshot', body([q('^NSEI', 22000)]))
    vi.advanceTimersByTime(4_000)
    FakeES.all[0].emit('heartbeat', {})
    vi.advanceTimersByTime(4_000)
    expect(FakeES.all[0].closed).toBe(false)
    vi.advanceTimersByTime(1_500)
    expect(FakeES.all[0].closed).toBe(true)
    expect(store.getState().connection).toBe('reconnecting')
    vi.advanceTimersByTime(1_000)
    expect(FakeES.all).toHaveLength(2)
    store.stop()
  })

  it('pauses while hidden or offline and resumes after', () => {
    const { store, page, net } = makeStore()
    store.subscribe(() => {})
    page.hidden = true
    page.dispatchEvent(new Event('visibilitychange'))
    expect(FakeES.all[0].closed).toBe(true)
    expect(store.getState().connection).toBe('paused')
    page.hidden = false
    page.dispatchEvent(new Event('visibilitychange'))
    expect(FakeES.all).toHaveLength(2)

    net.onLine = false
    net.dispatchEvent(new Event('offline'))
    expect(FakeES.all[1].closed).toBe(true)
    expect(store.getState().connection).toBe('offline')
    net.onLine = true
    net.dispatchEvent(new Event('online'))
    expect(FakeES.all).toHaveLength(3)
    store.stop()
  })

  it('keeps the stream through a quick unsubscribe and resubscribe', () => {
    const { store } = makeStore()
    const off = store.subscribe(() => {})
    off()
    vi.advanceTimersByTime(50)
    store.subscribe(() => {})
    vi.advanceTimersByTime(500)
    expect(FakeES.all).toHaveLength(1)
    expect(FakeES.all[0].closed).toBe(false)
    store.stop()
  })
})

describe('parseLiveFrame', () => {
  it('accepts a body and drops broken quotes', () => {
    const r = parseLiveFrame(JSON.stringify(body([q('^NSEI', 1), null as unknown as LiveQuote])))
    expect(r?.quotes).toHaveLength(1)
    expect(parseLiveFrame('')).toBeNull()
    expect(parseLiveFrame('{"status":1}')).toBeNull()
    expect(parseLiveFrame(42)).toBeNull()
  })
})
