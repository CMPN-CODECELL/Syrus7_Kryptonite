// Typed HTTP client for the QuantFolio API (/api, proxied to the FastAPI backend).
// Every non-2xx body is `{"error": {code, message, details?, request_id?}}` (ErrorEnvelope);
// FastAPI's default `{detail}` shape is also understood so nothing surfaces as "[object Object]".

import type {
  AssetsResponse,
  Capabilities,
  DataStatusResponse,
  DatasetInfo,
  ExampleResponse,
  ExperimentConfig,
  ExperimentConfigInput,
  ExperimentResult,
  HealthResponse,
  IbmStatus,
  JobStatus,
  PreflightReport,
  PresetsResponse,
  ReadyResponse,
  RunConfig,
  RunConfigInput,
  RunEvent,
  RunResult,
  RunSummary,
} from './contract.gen'

export const API_BASE = '/api'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown
  readonly requestId: string | null
  readonly retryAfter: number | null

  constructor(init: { status: number; code: string; message: string; details?: unknown; requestId?: string | null; retryAfter?: number | null }) {
    super(init.message)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.details = init.details ?? null
    this.requestId = init.requestId ?? null
    this.retryAfter = init.retryAfter ?? null
  }

  /** Network failure (no HTTP response at all). */
  get isNetwork(): boolean {
    return this.status === 0
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

/** Field-level messages from a 422 body, keyed by dotted location. */
export function fieldErrors(e: unknown): Array<{ loc: string; msg: string }> {
  if (!isApiError(e) || !Array.isArray(e.details)) return []
  return e.details.flatMap((d) => {
    if (d && typeof d === 'object' && 'msg' in d) {
      const loc = Array.isArray((d as { loc?: unknown }).loc) ? ((d as { loc: unknown[] }).loc.filter((p) => p !== 'body').join('.')) : ''
      return [{ loc, msg: String((d as { msg: unknown }).msg) }]
    }
    return []
  })
}

async function parseError(res: Response): Promise<ApiError> {
  const retry = res.headers.get('retry-after')
  const retryAfter = retry && /^\d+$/.test(retry) ? Number(retry) : null
  const requestId = res.headers.get('x-request-id')
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    /* non-JSON body (e.g. proxy error page) */
  }
  if (body && typeof body === 'object') {
    const env = (body as { error?: unknown }).error
    if (env && typeof env === 'object' && 'message' in env) {
      const e = env as { code?: string; message: string; details?: unknown; request_id?: string | null }
      return new ApiError({ status: res.status, code: e.code ?? `http_${res.status}`, message: e.message, details: e.details, requestId: e.request_id ?? requestId, retryAfter })
    }
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return new ApiError({ status: res.status, code: `http_${res.status}`, message: detail, requestId, retryAfter })
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: string } | undefined
      return new ApiError({ status: res.status, code: 'validation_error', message: first?.msg ?? 'The request was not valid.', details: detail, requestId, retryAfter })
    }
  }
  const fallback =
    res.status === 502 || res.status === 503 || res.status === 504
      ? 'The QuantFolio API is not reachable. Start the backend and try again.'
      : `Request failed (HTTP ${res.status}).`
  return new ApiError({ status: res.status, code: `http_${res.status}`, message: fallback, requestId, retryAfter })
}

export interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
  query?: Record<string, string | number | boolean | null | undefined>
}

export interface ApiResponse<T> {
  data: T
  status: number
  headers: Headers
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  const q = qs.toString()
  return `${API_BASE}${path}${q ? `?${q}` : ''}`
}

export async function requestFull<T>(path: string, opts: RequestOptions = {}): Promise<ApiResponse<T>> {
  const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers }
  let body: BodyInit | undefined
  if (opts.body instanceof FormData) body = opts.body
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  let res: Response
  try {
    res = await fetch(buildUrl(path, opts.query), { method: opts.method ?? 'GET', headers, body, signal: opts.signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError({
      status: 0,
      code: 'network_error',
      message: typeof navigator !== 'undefined' && navigator.onLine === false ? 'You are offline. Reconnect and try again.' : 'Could not reach the QuantFolio API.',
    })
  }
  if (!res.ok) throw await parseError(res)
  const text = await res.text()
  return { data: (text ? JSON.parse(text) : null) as T, status: res.status, headers: res.headers }
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return (await requestFull<T>(path, opts)).data
}

/** One key per user intent (click). Reuse it for retries of the same intent so the server dedupes. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

const enc = encodeURIComponent

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>('/health', { signal }),
  ready: (signal?: AbortSignal) => request<ReadyResponse>('/ready', { signal }),
  assets: (signal?: AbortSignal) => request<AssetsResponse>('/assets', { signal }),
  dataStatus: (signal?: AbortSignal) => request<DataStatusResponse>('/data/status', { signal }),
  uploadData: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<DatasetInfo>('/data/upload', { method: 'POST', body: fd })
  },
  presets: (signal?: AbortSignal) => request<PresetsResponse>('/presets', { signal }),
  capabilities: (signal?: AbortSignal) => request<Capabilities>('/capabilities', { signal }),
  preflight: (config: RunConfigInput, signal?: AbortSignal) => request<PreflightReport>('/preflight', { method: 'POST', body: config, signal }),
  submitRun: (config: RunConfigInput, idempotencyKey: string, opts: { forceFresh?: boolean } = {}) =>
    requestFull<JobStatus>('/runs', {
      method: 'POST',
      body: config,
      headers: { 'Idempotency-Key': idempotencyKey },
      query: { force_fresh: opts.forceFresh ? 'true' : undefined },
    }),
  submitExperiment: (config: ExperimentConfigInput, idempotencyKey: string) =>
    requestFull<JobStatus>('/experiments', { method: 'POST', body: config, headers: { 'Idempotency-Key': idempotencyKey } }),
  listRuns: async (q: { status?: string; kind?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal) => {
    const r = await requestFull<RunSummary[]>('/runs', { query: q, signal })
    const total = Number(r.headers.get('x-total-count'))
    return { items: r.data, total: Number.isFinite(total) && total > 0 ? total : r.data.length }
  },
  run: (id: string, signal?: AbortSignal) => request<JobStatus>(`/runs/${enc(id)}`, { signal }),
  result: (id: string, signal?: AbortSignal) => request<RunResult | ExperimentResult>(`/runs/${enc(id)}/result`, { signal }),
  cancel: (id: string) => request<JobStatus>(`/runs/${enc(id)}/cancel`, { method: 'POST' }),
  /** The submitted configuration as stored by the server (RunConfig, or ExperimentConfig for experiments). */
  runConfig: (id: string, signal?: AbortSignal) => request<RunConfig | ExperimentConfig>(`/runs/${enc(id)}/config`, { signal }),
  eventsSnapshot: (id: string, after: number, signal?: AbortSignal) => request<RunEvent[]>(`/runs/${enc(id)}/events/snapshot`, { query: { after }, signal }),
  example: (signal?: AbortSignal) => request<ExampleResponse>('/example', { signal }),
  ibmStatus: (signal?: AbortSignal) => request<IbmStatus>('/ibm/status', { signal }),
}

export const downloadUrl = {
  html: (id: string) => `${API_BASE}/runs/${enc(id)}/report.html`,
  json: (id: string) => `${API_BASE}/runs/${enc(id)}/report.json`,
  csv: (id: string) => `${API_BASE}/runs/${enc(id)}/report.csv`,
  counts: (id: string) => `${API_BASE}/runs/${enc(id)}/counts.csv`,
}

export function isExperimentResult(r: RunResult | ExperimentResult): r is ExperimentResult {
  return Array.isArray((r as ExperimentResult).cells)
}

export const TERMINAL_STATES = new Set(['succeeded', 'failed', 'cancelled', 'timed_out'])
export const isTerminal = (s: string | null | undefined) => !!s && TERMINAL_STATES.has(s)
