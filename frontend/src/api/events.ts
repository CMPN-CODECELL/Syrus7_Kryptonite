// Live run progress over Server-Sent Events, with resume and a polling fallback.
//
//  - Connects to /api/runs/{id}/events?after={lastEventId}; the browser also sends
//    Last-Event-ID on its own automatic reconnects, so no event is replayed twice.
//  - A watchdog reconnects (with backoff) if nothing, not even a heartbeat, arrives for 25 s.
//  - After repeated SSE failures it falls back to polling /events/snapshot?after= every 2 s.
//  - Goes quiet while the browser is offline and resumes from the last id when it is back.
//  - Closes on a terminal `status` event. Listener notifications are coalesced.
//  - Cloud build: polling only, as the Hosting proxy ends any request after 60 s. Polls start 2 s apart and slow to
//    10 s while nothing changes, as each one is a billed request.

import type { IterationPoint, JobError, JobStatus, RunEvent, Stage } from './contract.gen'
import { EVENT_TYPE_VALUES } from './contract.gen'
import { CLOUD } from '../lib/cloud'
import { api, isTerminal } from './client'

export type Transport = 'sse' | 'polling' | 'none'
export type Connection = 'connecting' | 'open' | 'reconnecting' | 'offline' | 'closed'

export interface StageEntry {
  stage: Stage
  message: string | null
  ts: string
}

export interface RunStreamState {
  runId: string
  status: JobStatus | null
  stages: StageEntry[]
  iterations: IterationPoint[]
  logs: string[]
  error: JobError | null
  resultReady: boolean
  lastEventId: number
  transport: Transport
  connection: Connection
  reconnects: number
}

type Listener = (s: RunStreamState) => void

export interface RunStreamDeps {
  EventSource?: typeof EventSource
  fetchStatus?: (id: string, signal?: AbortSignal) => Promise<JobStatus>
  fetchSnapshot?: (id: string, after: number, signal?: AbortSignal) => Promise<RunEvent[]>
  /** ms without any event before the SSE connection is considered dead. */
  watchdogMs?: number
  pollMs?: number
  /** Polling slows from `pollMs` to this while nothing new arrives. Equal to `pollMs` (no slowing) outside the cloud build. */
  maxPollMs?: number
  notifyMs?: number
  /** SSE failures (without a single event) before switching to polling. */
  maxSseFailures?: number
}

const SSE_TYPES = EVENT_TYPE_VALUES.filter((t) => t !== 'heartbeat')

export function pointKey(p: Pick<IterationPoint, 'phase' | 'evaluation'>): string {
  return `${p.phase}:${p.evaluation}`
}

function isIterationPoint(v: unknown): v is IterationPoint {
  return !!v && typeof v === 'object' && typeof (v as IterationPoint).evaluation === 'number' && typeof (v as IterationPoint).energy === 'number'
}

export class RunStream {
  private state: RunStreamState
  private listeners = new Set<Listener>()
  private deps: Required<RunStreamDeps>
  private es: EventSource | null = null
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  private watchdog: ReturnType<typeof setTimeout> | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private notifyTimer: ReturnType<typeof setTimeout> | null = null
  private abort = new AbortController()
  private seenPoints = new Set<string>()
  private sseFailures = 0
  private gotEventThisConnection = false
  private stopped = false

  constructor(runId: string, deps: RunStreamDeps = {}) {
    this.deps = {
      EventSource: deps.EventSource ?? (!CLOUD && typeof EventSource !== 'undefined' ? EventSource : (undefined as unknown as typeof EventSource)),
      fetchStatus: deps.fetchStatus ?? api.run,
      fetchSnapshot: deps.fetchSnapshot ?? api.eventsSnapshot,
      watchdogMs: deps.watchdogMs ?? 25_000,
      pollMs: deps.pollMs ?? 2_000,
      maxPollMs: deps.maxPollMs ?? (CLOUD ? 10_000 : (deps.pollMs ?? 2_000)),
      notifyMs: deps.notifyMs ?? 120,
      maxSseFailures: deps.maxSseFailures ?? 3,
    }
    this.state = {
      runId,
      status: null,
      stages: [],
      iterations: [],
      logs: [],
      error: null,
      resultReady: false,
      lastEventId: 0,
      transport: 'none',
      connection: 'connecting',
      reconnects: 0,
    }
  }

  getState = (): RunStreamState => this.state

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  async start(): Promise<void> {
    // Restartable (React StrictMode runs effects twice in development).
    this.stopped = false
    if (this.abort.signal.aborted) this.abort = new AbortController()
    if (typeof window !== 'undefined') {
      window.addEventListener('online', this.onOnline)
      window.addEventListener('offline', this.onOffline)
    }
    try {
      const status = await this.deps.fetchStatus(this.state.runId, this.abort.signal)
      if (this.stopped) return
      this.applyStatus(status)
      if (isTerminal(status.status)) {
        // Finished already: load the recorded event history once (stages + measured iterations).
        await this.pollOnce()
        this.close('closed')
        return
      }
    } catch (e) {
      if (this.stopped) return
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        this.patch({ connection: navigator.onLine === false ? 'offline' : 'reconnecting' })
        this.scheduleRetry()
        return
      }
    }
    this.connect()
  }

  stop(): void {
    this.stopped = true
    this.abort.abort()
    this.teardownTransport()
    if (this.notifyTimer) clearTimeout(this.notifyTimer)
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', this.onOnline)
      window.removeEventListener('offline', this.onOffline)
    }
    this.listeners.clear()
  }

  /** Feed a parsed event (exposed for tests). */
  ingest(ev: Pick<RunEvent, 'type' | 'data'> & { id?: number | null; stage?: Stage | null; ts?: string }): void {
    if (typeof ev.id === 'number' && ev.id > 0) {
      if (ev.id <= this.state.lastEventId) return // duplicate after a resume
      this.state = { ...this.state, lastEventId: ev.id }
    }
    const data = (ev.data ?? {}) as Record<string, unknown>
    switch (ev.type) {
      case 'status': {
        if (data && typeof data === 'object' && 'status' in data) this.applyStatus(data as unknown as JobStatus)
        break
      }
      case 'stage': {
        const stage = (data.stage as Stage | undefined) ?? ev.stage ?? null
        if (stage) {
          const last = this.state.stages[this.state.stages.length - 1]
          const message = typeof data.message === 'string' ? data.message : null
          if (!last || last.stage !== stage || last.message !== message) {
            this.state = { ...this.state, stages: [...this.state.stages, { stage, message, ts: ev.ts ?? new Date().toISOString() }] }
          }
        }
        break
      }
      case 'iteration': {
        const pts: IterationPoint[] = Array.isArray(data.points) ? (data.points as unknown[]).filter(isIterationPoint) : isIterationPoint(data) ? [data] : []
        const fresh = pts.filter((p) => {
          const k = pointKey(p)
          if (this.seenPoints.has(k)) return false
          this.seenPoints.add(k)
          return true
        })
        if (fresh.length) this.state = { ...this.state, iterations: [...this.state.iterations, ...fresh] }
        break
      }
      case 'log': {
        const msg = typeof data.message === 'string' ? data.message : null
        if (msg) this.state = { ...this.state, logs: [...this.state.logs.slice(-199), msg] }
        break
      }
      case 'result':
        this.state = { ...this.state, resultReady: true }
        break
      case 'error':
        if (typeof data.message === 'string') this.state = { ...this.state, error: { code: String(data.code ?? 'error'), message: data.message } }
        break
      default:
        break
    }
    this.scheduleNotify()
  }

  // ---- internals -----------------------------------------------------------

  private applyStatus(s: JobStatus): void {
    const stages = this.state.stages
    const stage = s.progress?.stage
    const nextStages =
      stage && (!stages.length || stages[stages.length - 1].stage !== stage)
        ? [...stages, { stage, message: s.progress?.message ?? null, ts: new Date().toISOString() }]
        : stages
    this.state = {
      ...this.state,
      status: s,
      stages: nextStages,
      error: s.error ?? this.state.error,
      resultReady: this.state.resultReady || s.status === 'succeeded',
    }
    this.scheduleNotify()
    if (isTerminal(s.status) && this.state.transport !== 'none') this.close('closed')
  }

  private connect(): void {
    if (this.stopped) return
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      this.patch({ connection: 'offline' })
      return
    }
    if (!this.deps.EventSource || this.sseFailures >= this.deps.maxSseFailures) {
      this.startPolling()
      return
    }
    this.teardownTransport()
    this.gotEventThisConnection = false
    const url = `/api/runs/${encodeURIComponent(this.state.runId)}/events?after=${this.state.lastEventId}`
    const es = new this.deps.EventSource(url)
    this.es = es
    this.patch({ transport: 'sse', connection: this.state.reconnects ? 'reconnecting' : 'connecting' })
    es.onopen = () => {
      this.patch({ connection: 'open' })
      this.armWatchdog()
    }
    const handler = (e: Event) => this.onSseMessage(e)
    es.onmessage = handler
    for (const t of SSE_TYPES) if (t !== 'error') es.addEventListener(t, handler)
    es.addEventListener('heartbeat', () => this.onAnyEvent())
    // 'error' is both the server's JobError event (a MessageEvent with data) and the connection-error event.
    es.addEventListener('error', (e) => {
      if (e instanceof MessageEvent && typeof e.data === 'string' && e.data) {
        this.onSseMessage(e)
        return
      }
      this.onSseFailure()
    })
  }

  private onAnyEvent(): void {
    this.gotEventThisConnection = true
    this.sseFailures = 0
    if (this.state.connection !== 'open') this.patch({ connection: 'open' })
    this.armWatchdog()
  }

  private onSseMessage(e: Event): void {
    this.onAnyEvent()
    const me = e as MessageEvent<string>
    const parsed = parseSseData(me.type === 'message' ? null : me.type, me.data, me.lastEventId)
    if (parsed) this.ingest(parsed)
  }

  private onSseFailure(): void {
    if (this.stopped) return
    if (!this.gotEventThisConnection) this.sseFailures += 1
    this.teardownTransport()
    if (isTerminal(this.state.status?.status)) return
    this.patch({ connection: navigator.onLine === false ? 'offline' : 'reconnecting', reconnects: this.state.reconnects + 1 })
    this.scheduleRetry()
  }

  private scheduleRetry(): void {
    if (this.stopped || this.retryTimer) return
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return // 'online' resumes
    const delay = Math.min(15_000, 1000 * 2 ** Math.min(4, this.state.reconnects))
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.refreshThenConnect()
    }, delay)
  }

  private async refreshThenConnect(): Promise<void> {
    try {
      const s = await this.deps.fetchStatus(this.state.runId, this.abort.signal)
      this.applyStatus(s)
      if (isTerminal(s.status)) {
        await this.pollOnce()
        this.close('closed')
        return
      }
    } catch {
      if (this.stopped) return
      this.patch({ reconnects: this.state.reconnects + 1, connection: navigator.onLine === false ? 'offline' : 'reconnecting' })
      this.scheduleRetry()
      return
    }
    this.connect()
  }

  private armWatchdog(): void {
    if (this.watchdog) clearTimeout(this.watchdog)
    this.watchdog = setTimeout(() => {
      this.watchdog = null
      this.onSseFailure()
    }, this.deps.watchdogMs)
  }

  private startPolling(): void {
    this.teardownTransport()
    this.patch({ transport: 'polling', connection: 'open' })
    let delay = this.deps.pollMs
    let seen = ''
    const tick = async () => {
      this.pollTimer = null
      if (this.stopped) return
      try {
        await this.pollOnce()
        if (this.state.connection !== 'open') this.patch({ connection: 'open' })
        if (!isTerminal(this.state.status?.status)) {
          // Snapshot carries status events too, but refresh the job record as the source of truth.
          const s = await this.deps.fetchStatus(this.state.runId, this.abort.signal)
          this.applyStatus(s)
        }
      } catch {
        if (this.stopped) return
        this.patch({ connection: navigator.onLine === false ? 'offline' : 'reconnecting' })
      }
      // Anything new (an event, a status or a stage) polls quickly again; a quiet poll waits longer, up to maxPollMs.
      const now = `${this.state.status?.status}:${this.state.status?.progress?.stage}:${this.state.lastEventId}`
      delay = now === seen ? Math.min(this.deps.maxPollMs, delay * 1.5) : this.deps.pollMs
      seen = now
      if (!this.stopped && !isTerminal(this.state.status?.status)) this.pollTimer = setTimeout(tick, delay)
      else this.close('closed')
    }
    void tick()
  }

  private async pollOnce(): Promise<void> {
    const events = await this.deps.fetchSnapshot(this.state.runId, this.state.lastEventId, this.abort.signal)
    for (const ev of events) this.ingest(ev)
  }

  private onOnline = () => {
    if (this.stopped || this.state.connection === 'closed') return
    if (this.retryTimer) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    this.patch({ connection: 'reconnecting' })
    void this.refreshThenConnect()
  }

  private onOffline = () => {
    if (this.stopped || this.state.connection === 'closed') return
    this.teardownTransport()
    this.patch({ connection: 'offline' })
  }

  private teardownTransport(): void {
    if (this.es) {
      this.es.onopen = null
      this.es.onmessage = null
      this.es.close()
      this.es = null
    }
    if (this.watchdog) clearTimeout(this.watchdog)
    if (this.pollTimer) clearTimeout(this.pollTimer)
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.watchdog = this.pollTimer = this.retryTimer = null
  }

  private close(connection: Connection): void {
    this.teardownTransport()
    this.patch({ connection })
  }

  private patch(p: Partial<RunStreamState>): void {
    this.state = { ...this.state, ...p }
    this.scheduleNotify()
  }

  private scheduleNotify(): void {
    if (this.notifyTimer || this.stopped) return
    this.notifyTimer = setTimeout(() => {
      this.notifyTimer = null
      for (const l of this.listeners) l(this.state)
    }, this.deps.notifyMs)
  }
}

/**
 * Accepts either a full RunEvent JSON in `data` or a bare payload named by the SSE `event:` field.
 * Returns null for unparseable or heartbeat frames.
 */
export function parseSseData(
  eventName: string | null,
  raw: string,
  lastEventId?: string,
): (Pick<RunEvent, 'type' | 'data'> & { id: number | null; stage?: Stage | null; ts?: string }) | null {
  if (!raw) return null
  let obj: unknown
  try {
    obj = JSON.parse(raw)
  } catch {
    return null
  }
  if (!obj || typeof obj !== 'object') return null
  const idFromHeader = lastEventId && /^\d+$/.test(lastEventId) ? Number(lastEventId) : null
  const o = obj as Record<string, unknown>
  if (typeof o.type === 'string' && 'data' in o && (EVENT_TYPE_VALUES as readonly string[]).includes(o.type)) {
    if (o.type === 'heartbeat') return null
    return {
      type: o.type as RunEvent['type'],
      data: (o.data ?? {}) as Record<string, unknown>,
      id: typeof o.id === 'number' ? o.id : idFromHeader,
      stage: (o.stage as Stage | null | undefined) ?? null,
      ts: typeof o.ts === 'string' ? o.ts : undefined,
    }
  }
  if (!eventName || !(EVENT_TYPE_VALUES as readonly string[]).includes(eventName) || eventName === 'heartbeat') return null
  return { type: eventName as RunEvent['type'], data: o, id: idFromHeader }
}
