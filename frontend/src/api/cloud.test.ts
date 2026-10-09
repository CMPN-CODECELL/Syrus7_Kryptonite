// The cloud build's wiring: the same modules with the build flag switched on (lib/cloud.ts).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cloudErrorMessage } from '../lib/cloud'
import { CLOUD_LIVE_DEPS } from '../lib/live'
import { ApiError, request } from './client'
import type { JobStatus } from './contract.gen'
import { RunStream } from './events'

vi.mock('../lib/cloud', async (original) => ({ ...(await original<typeof import('../lib/cloud')>()), CLOUD: true }))

const json = (status: number, body: unknown, headers: Record<string, string> = {}) => new Response(JSON.stringify(body), { status, headers })

afterEach(() => {
  vi.restoreAllMocks()
})

describe('api client in the cloud build', () => {
  it('sends no Authorization header', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(200, { ok: true }))
    await request('/health')
    expect(fetch).toHaveBeenCalledTimes(1)
    const headers = (fetch.mock.calls[0][1] as RequestInit | undefined)?.headers as Record<string, string> | undefined
    expect(headers?.Authorization).toBeUndefined()
  })

  it('words quota, pause and cold-start refusals plainly', async () => {
    const fail = (res: Response) => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(res)
      return request('/runs', { method: 'POST', body: {} }).then(() => null, (e: unknown) => e as ApiError)
    }
    const quota = await fail(json(429, { error: { code: 'daily_quota_exhausted', message: 'quota', details: { scope: 'global', limit: 20, used: 20 } } }))
    expect(quota).toMatchObject({ status: 429, code: 'daily_quota_exhausted' })
    expect(quota?.message).toMatch(/used all 20 live runs for today/)
    expect((await fail(json(503, { error: { code: 'compute_paused', message: 'paused', details: { reason: null } } }, { 'retry-after': '300' })))?.message).toMatch(/paused by the operator/)
    expect((await fail(new Response('<html>503</html>', { status: 503 })))?.message).toMatch(/starting after being idle/)
    // Anything the copy table does not know keeps the server's message.
    expect((await fail(json(422, { error: { code: 'limit_exceeded', message: 'At most 12 qubits on the public demo.' } })))?.message).toBe('At most 12 qubits on the public demo.')
  })
})

describe('cloud error wording', () => {
  it('words each known refusal and keeps the server message for the rest', () => {
    const at = '2026-10-10T00:00:00Z'
    expect(cloudErrorMessage(429, 'daily_quota_exhausted', { limit: 20, resets_at: at }, null)).toMatch(/^The public demo has used all 20 live runs for today\. The count resets at .+ \(midnight UTC\)\./)
    expect(cloudErrorMessage(429, 'user_quota_exhausted', { limit: 3 }, null)).toBe('You have reached your own limit of 3 for today. The count resets at midnight UTC.')
    expect(cloudErrorMessage(409, 'user_active_limit', null, null)).toMatch(/already have a run in progress/)
    expect(cloudErrorMessage(429, 'queue_full', null, 30)).toBe('The job queue is full. Try again in about 30 s.')
    expect(cloudErrorMessage(503, 'compute_paused', { reason: 'budget' }, 300)).toMatch(/^New runs are paused by the operator \(budget\)\./)
    expect(cloudErrorMessage(429, 'http_429', null, 10)).toBe('Too many requests. Try again in about 10 s.')
    expect(cloudErrorMessage(403, 'http_403', null, null)).toBe('This is not available on the public demo.')
    expect(cloudErrorMessage(504, 'http_504', null, null)).toMatch(/starting after being idle/)
    // Unknown codes, including any other 429 such as the per-visitor cap, keep the server's own message.
    expect(cloudErrorMessage(429, 'rate_limited', null, null)).toBeNull()
    expect(cloudErrorMessage(401, 'http_401', null, null)).toBeNull()
  })
})

describe('run progress in the cloud build', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const running = { run_id: 'r1', kind: 'run', status: 'running', progress: { stage: 'optimising' } } as unknown as JobStatus
  const event = (id: number, type: string, data: Record<string, unknown>) => ({ id, run_id: 'r1', type, data, stage: null, ts: '2026-10-09T00:00:00Z' })

  it('polls instead of opening an event stream, slows while nothing changes and stops at the end', async () => {
    const ES = vi.fn()
    vi.stubGlobal('EventSource', ES)
    const fetchSnapshot = vi.fn().mockResolvedValue([])
    const s = new RunStream('r1', { fetchStatus: vi.fn().mockResolvedValue(running), fetchSnapshot, notifyMs: 0, pollMs: 100, maxPollMs: 200 })
    await s.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ES).not.toHaveBeenCalled()
    expect(s.getState()).toMatchObject({ transport: 'polling', connection: 'open' })
    expect(fetchSnapshot).toHaveBeenCalledTimes(1)
    // Quiet polls wait 100, 150 and then 200 ms.
    for (const [wait, calls] of [[100, 2], [150, 3], [199, 3], [1, 4], [200, 5]]) {
      await vi.advanceTimersByTimeAsync(wait)
      expect(fetchSnapshot).toHaveBeenCalledTimes(calls)
    }
    // News brings the quick interval back.
    fetchSnapshot.mockResolvedValueOnce([event(1, 'log', { message: 'transpiled' })])
    await vi.advanceTimersByTimeAsync(200)
    expect(fetchSnapshot).toHaveBeenCalledTimes(6)
    await vi.advanceTimersByTimeAsync(100)
    expect(fetchSnapshot).toHaveBeenCalledTimes(7)
    // A terminal status ends polling.
    fetchSnapshot.mockResolvedValueOnce([event(2, 'status', { run_id: 'r1', kind: 'run', status: 'cancelled' })])
    await vi.advanceTimersByTimeAsync(1_000)
    expect(s.getState().status?.status).toBe('cancelled')
    expect(s.getState().connection).toBe('closed')
    const calls = fetchSnapshot.mock.calls.length
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchSnapshot).toHaveBeenCalledTimes(calls)
    s.stop()
    vi.unstubAllGlobals()
  })
})

describe('live prices in the cloud build', () => {
  it('poll every 5 s and never open a stream', () => {
    expect(CLOUD_LIVE_DEPS).toEqual({ EventSource: null, pollMs: 5_000 })
  })
})
