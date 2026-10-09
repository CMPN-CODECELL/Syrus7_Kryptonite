import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, fieldErrors, requestFull } from './client'

const respond = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers }))

const fail = (p: Promise<unknown>) => p.then(() => null, (e: unknown) => e as ApiError)

afterEach(() => {
  vi.restoreAllMocks()
})

describe('api client errors', () => {
  it('parses the error envelope with request id and Retry-After', async () => {
    respond(429, { error: { code: 'queue_full', message: 'The queue is full.', request_id: 'req-1' } }, { 'retry-after': '5' })
    const e = await fail(requestFull('/runs'))
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({ status: 429, code: 'queue_full', message: 'The queue is full.', requestId: 'req-1', retryAfter: 5 })
  })

  it('understands FastAPI validation details and exposes field errors', async () => {
    respond(422, { detail: [{ loc: ['body', 'constraints', 'cardinality'], msg: 'Input should be greater than 0', type: 'greater_than' }] })
    const e = await fail(requestFull('/preflight', { method: 'POST', body: {} }))
    expect(e?.code).toBe('validation_error')
    expect(fieldErrors(e)).toEqual([{ loc: 'constraints.cardinality', msg: 'Input should be greater than 0' }])
  })

  it('explains a proxy 502 without a JSON body', async () => {
    respond(502, '<html>bad gateway</html>')
    expect((await fail(requestFull('/health')))?.message).toMatch(/not reachable/)
  })

  it('turns a network failure into status 0', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    const e = await fail(requestFull('/health'))
    expect(e?.isNetwork).toBe(true)
    expect(e?.code).toBe('network_error')
  })
})

describe('api requests', () => {
  it('sends the idempotency key and force_fresh flag and returns the HTTP status', async () => {
    const spy = respond(200, { run_id: 'r1', status: 'succeeded' })
    const r = await api.submitRun({} as never, 'key-1', { forceFresh: true })
    const [url, init] = spy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/runs?force_fresh=true')
    expect((init.headers as Record<string, string>)['Idempotency-Key']).toBe('key-1')
    expect(r.status).toBe(200)
  })

  it('reads the total from X-Total-Count when listing runs', async () => {
    respond(200, [{ run_id: 'a' }], { 'x-total-count': '41' })
    expect(await api.listRuns({ limit: 20, offset: 20, status: '' })).toEqual({ items: [{ run_id: 'a' }], total: 41 })
  })

  it('fetches the stored config of a run', async () => {
    const spy = respond(200, { symbols: ['INFY'] })
    expect(await api.runConfig('exp-1')).toEqual({ symbols: ['INFY'] })
    expect(spy.mock.calls[0][0]).toBe('/api/runs/exp-1/config')
  })
})
