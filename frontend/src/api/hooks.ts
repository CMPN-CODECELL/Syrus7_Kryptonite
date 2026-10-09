import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ExperimentConfigInput, JobStatus, RunConfigInput } from './contract.gen'
import { ApiError, api, isApiError, isTerminal, newIdempotencyKey } from './client'
import { RunStream, type RunStreamState } from './events'

export const qk = {
  health: ['health'] as const,
  assets: ['assets'] as const,
  presets: ['presets'] as const,
  capabilities: ['capabilities'] as const,
  dataStatus: ['data-status'] as const,
  example: ['example'] as const,
  ibm: ['ibm-status'] as const,
  runs: (q: object) => ['runs', q] as const,
  run: (id: string) => ['run', id] as const,
  result: (id: string) => ['result', id] as const,
  preflight: (key: string) => ['preflight', key] as const,
}

/** Do not retry client errors; retry network/5xx a couple of times. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status >= 400 && error.status < 500) return false
  return failureCount < 2
}

export function useHealth() {
  return useQuery({ queryKey: qk.health, queryFn: ({ signal }) => api.health(signal), refetchInterval: 20_000, retry: 0, staleTime: 10_000 })
}

export function useAssets() {
  return useQuery({ queryKey: qk.assets, queryFn: ({ signal }) => api.assets(signal), staleTime: 5 * 60_000 })
}

export function usePresets() {
  return useQuery({ queryKey: qk.presets, queryFn: ({ signal }) => api.presets(signal), staleTime: 10 * 60_000 })
}

export function useCapabilities() {
  return useQuery({ queryKey: qk.capabilities, queryFn: ({ signal }) => api.capabilities(signal), staleTime: 10 * 60_000 })
}

export function useDataStatus() {
  return useQuery({ queryKey: qk.dataStatus, queryFn: ({ signal }) => api.dataStatus(signal), staleTime: 60_000 })
}

export function useExample() {
  return useQuery({ queryKey: qk.example, queryFn: ({ signal }) => api.example(signal), staleTime: 60_000 })
}

export function useIbmStatus() {
  return useQuery({ queryKey: qk.ibm, queryFn: ({ signal }) => api.ibmStatus(signal), staleTime: 5 * 60_000 })
}

export function useRunStatus(id: string | undefined, opts: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: qk.run(id ?? ''),
    queryFn: ({ signal }) => api.run(id as string, signal),
    enabled: !!id,
    refetchInterval: (q) => (opts.poll && !isTerminal(q.state.data?.status) ? 3_000 : false),
  })
}

export function useRunResult(id: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: qk.result(id ?? ''),
    queryFn: ({ signal }) => api.result(id as string, signal),
    enabled: !!id && enabled,
    staleTime: Infinity, // results are immutable
  })
}

export interface RunsQuery {
  status?: string
  kind?: string
  limit?: number
  offset?: number
}

/** The server's stored copy of a run's submitted configuration (for retry / edit from any browser). */
export function useRunConfig(id: string | undefined, enabled: boolean) {
  return useQuery({ queryKey: ['run-config', id ?? ''], queryFn: ({ signal }) => api.runConfig(id!, signal), enabled: !!id && enabled, staleTime: Infinity, retry: 1 })
}

export function useRuns(q: RunsQuery) {
  return useQuery({
    queryKey: qk.runs(q),
    queryFn: ({ signal }) => api.listRuns(q, signal),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  })
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** Live preflight: debounced, cancels stale requests, keeps the last report on screen while refreshing. */
export function usePreflight(config: RunConfigInput | null, ms = 350) {
  const key = useMemo(() => (config ? JSON.stringify(config) : ''), [config])
  const debouncedKey = useDebounced(key, ms)
  const query = useQuery({
    queryKey: qk.preflight(debouncedKey),
    queryFn: ({ signal }) => api.preflight(JSON.parse(debouncedKey) as RunConfigInput, signal),
    enabled: !!debouncedKey,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: shouldRetry,
  })
  return { ...query, pending: key !== debouncedKey || query.isFetching }
}

/**
 * Submission with one Idempotency-Key per user intent: the key is created on the first click and reused
 * for automatic retries of that same intent, so double-clicks/retries can never start duplicate work.
 * Any change to the payload starts a new intent.
 */
export function useSubmitJob<TInput extends RunConfigInput | ExperimentConfigInput>(kind: 'run' | 'experiment') {
  const qc = useQueryClient()
  const intent = useRef<{ payload: string; key: string } | null>(null)
  const mutation = useMutation({
    mutationFn: async (vars: { config: TInput; forceFresh?: boolean }) => {
      const payload = JSON.stringify(vars)
      if (!intent.current || intent.current.payload !== payload) intent.current = { payload, key: newIdempotencyKey() }
      const key = intent.current.key
      const res =
        kind === 'run'
          ? await api.submitRun(vars.config as RunConfigInput, key, { forceFresh: vars.forceFresh })
          : await api.submitExperiment(vars.config as ExperimentConfigInput, key)
      return { job: res.data, replayed: res.status === 200, key }
    },
    retry: (count, err) => count < 2 && isApiError(err) && (err.isNetwork || err.status >= 500),
    retryDelay: (n) => 800 * (n + 1),
    onSuccess: ({ job }) => {
      qc.setQueryData<JobStatus>(qk.run(job.run_id), job)
      void qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
  return {
    ...mutation,
    /** Forget the current intent so the next click is a new submission (e.g. "Run again"). */
    resetIntent: () => {
      intent.current = null
      mutation.reset()
    },
  }
}

export function useCancelRun(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.cancel(id),
    onSuccess: (job) => {
      qc.setQueryData<JobStatus>(qk.run(job.run_id), job)
      void qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}

/** Subscribes to live SSE progress for a run; one RunStream per run id. */
export function useRunStream(runId: string | undefined): RunStreamState | null {
  // Constructing a RunStream has no side effects; the connection opens in start() and closes in stop().
  const stream = useMemo(() => (runId ? new RunStream(runId) : null), [runId])
  useEffect(() => {
    if (!stream) return
    void stream.start()
    return () => stream.stop()
  }, [stream])
  const subscribe = useMemo(() => (stream ? stream.subscribe : () => () => {}), [stream])
  const getSnapshot = useMemo(() => (stream ? stream.getState : () => null), [stream])
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

export function useOnline(): boolean {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('online', cb)
      window.addEventListener('offline', cb)
      return () => {
        window.removeEventListener('online', cb)
        window.removeEventListener('offline', cb)
      }
    },
    () => navigator.onLine,
    () => true,
  )
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return 'Something went wrong.'
}
