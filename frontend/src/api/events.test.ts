import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { JobStatus, RunEvent } from './contract.gen'
import { RunStream, parseSseData } from './events'

// Minimal EventSource double: records instances so tests can push frames into them.
class FakeES {
  static all: FakeES[] = []
  url: string
  onopen: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
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
  open() {
    this.onopen?.(new Event('open'))
  }
  emit(type: string, data: unknown, id?: number) {
    const e = new MessageEvent(type, { data: JSON.stringify(data), lastEventId: id ? String(id) : '' })
    for (const fn of this.handlers.get(type) ?? []) fn(e)
  }
  fail() {
    for (const fn of this.handlers.get('error') ?? []) fn(new Event('error'))
  }
}

const ES = FakeES as unknown as typeof EventSource
const job = (status: JobStatus['status']): JobStatus => ({ run_id: 'r1', kind: 'run', status }) as unknown as JobStatus
const point = (evaluation: number, phase = 'optimisation') => ({ phase, evaluation, energy: -evaluation, best_feasible_objective: null, feasible_fraction: 0.5, elapsed_s: evaluation })
const ev = (id: number, type: RunEvent['type'], data: Record<string, unknown>) => ({ id, run_id: 'r1', type, data, stage: null, ts: '2026-10-09T00:00:00Z' })
const statusData = (s: JobStatus['status']) => job(s) as unknown as Record<string, unknown>

describe('RunStream', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeES.all = []
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('streams over SSE, deduplicates by id and point, and resumes after the last id', async () => {
    const s = new RunStream('r1', { EventSource: ES, fetchStatus: vi.fn().mockResolvedValue(job('running')), fetchSnapshot: vi.fn().mockResolvedValue([]), notifyMs: 0 })
    await s.start()
    const es = FakeES.all[0]
    expect(es.url).toBe('/api/runs/r1/events?after=0')
    es.open()
    es.emit('iteration', ev(1, 'iteration', { points: [point(1), point(2)] }), 1)
    es.emit('iteration', ev(1, 'iteration', { points: [point(1), point(2)] }), 1) // same id again
    es.emit('iteration', ev(2, 'iteration', { points: [point(2), point(3)] }), 2) // overlapping point
    expect(s.getState().iterations.map((p) => p.evaluation)).toEqual([1, 2, 3])
    expect(s.getState().lastEventId).toBe(2)

    es.fail()
    expect(s.getState().connection).toBe('reconnecting')
    await vi.advanceTimersByTimeAsync(2_100)
    expect(FakeES.all).toHaveLength(2)
    expect(FakeES.all[1].url).toBe('/api/runs/r1/events?after=2')
    s.stop()
  })

  it('closes on a terminal status event', async () => {
    const s = new RunStream('r1', { EventSource: ES, fetchStatus: vi.fn().mockResolvedValue(job('running')), fetchSnapshot: vi.fn().mockResolvedValue([]), notifyMs: 0 })
    await s.start()
    const es = FakeES.all[0]
    es.open()
    es.emit('status', ev(5, 'status', statusData('succeeded')), 5)
    expect(s.getState().status?.status).toBe('succeeded')
    expect(s.getState().connection).toBe('closed')
    expect(es.closed).toBe(true)
    s.stop()
  })

  it('falls back to polling the snapshot after repeated SSE failures', async () => {
    const fetchSnapshot = vi
      .fn()
      .mockResolvedValueOnce([ev(1, 'stage', { stage: 'qaoa' })])
      .mockResolvedValue([ev(2, 'status', statusData('succeeded'))])
    const s = new RunStream('r1', { EventSource: ES, fetchStatus: vi.fn().mockResolvedValue(job('running')), fetchSnapshot, notifyMs: 0, maxSseFailures: 2, pollMs: 50 })
    await s.start()
    FakeES.all[0].fail()
    await vi.advanceTimersByTimeAsync(2_100) // backoff 2 s after the first failure
    expect(FakeES.all).toHaveLength(2)
    FakeES.all[1].fail()
    await vi.advanceTimersByTimeAsync(4_100) // then 4 s
    expect(FakeES.all).toHaveLength(2) // no third SSE attempt
    await vi.advanceTimersByTimeAsync(200)
    expect(fetchSnapshot).toHaveBeenCalledWith('r1', 0, expect.anything())
    expect(fetchSnapshot).toHaveBeenCalledWith('r1', 1, expect.anything())
    expect(s.getState().stages.map((x) => x.stage)).toContain('qaoa')
    expect(s.getState().status?.status).toBe('succeeded')
    expect(s.getState().connection).toBe('closed')
    s.stop()
  })

  it('loads the recorded history once for a run that already finished', async () => {
    const fetchSnapshot = vi.fn().mockResolvedValue([ev(1, 'iteration', { points: [point(1)] })])
    const s = new RunStream('r1', { EventSource: ES, fetchStatus: vi.fn().mockResolvedValue(job('failed')), fetchSnapshot, notifyMs: 0 })
    await s.start()
    expect(FakeES.all).toHaveLength(0)
    expect(s.getState().iterations).toHaveLength(1)
    expect(s.getState().connection).toBe('closed')
    s.stop()
  })

  it('goes quiet while offline and reconnects from the last id when back online', async () => {
    const s = new RunStream('r1', { EventSource: ES, fetchStatus: vi.fn().mockResolvedValue(job('running')), fetchSnapshot: vi.fn().mockResolvedValue([]), notifyMs: 0 })
    await s.start()
    FakeES.all[0].open()
    FakeES.all[0].emit('log', ev(4, 'log', { message: 'hello' }), 4)
    window.dispatchEvent(new Event('offline'))
    expect(s.getState().connection).toBe('offline')
    expect(FakeES.all[0].closed).toBe(true)
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(10)
    expect(FakeES.all.at(-1)?.url).toBe('/api/runs/r1/events?after=4')
    expect(s.getState().logs).toEqual(['hello'])
    s.stop()
  })
})

describe('parseSseData', () => {
  it('reads a full RunEvent body and ignores heartbeats and junk', () => {
    expect(parseSseData('stage', JSON.stringify(ev(3, 'stage', { stage: 'qaoa' })))).toMatchObject({ id: 3, type: 'stage', data: { stage: 'qaoa' } })
    expect(parseSseData('heartbeat', JSON.stringify({ ts: 'x' }))).toBeNull()
    expect(parseSseData('stage', 'not json')).toBeNull()
  })
  it('accepts a bare payload named by the SSE event field, taking the id from the header', () => {
    expect(parseSseData('log', JSON.stringify({ message: 'hi' }), '7')).toEqual({ type: 'log', data: { message: 'hi' }, id: 7 })
  })
})
