// Storage can be unavailable (private mode, blocked site data). Every access is guarded.

function store(kind: 'local' | 'session'): Storage | null {
  try {
    return kind === 'local' ? window.localStorage : window.sessionStorage
  } catch {
    return null
  }
}

export function readJson<T>(key: string, kind: 'local' | 'session' = 'local'): T | null {
  try {
    const raw = store(kind)?.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

export function writeJson(key: string, value: unknown, kind: 'local' | 'session' = 'local'): void {
  try {
    store(kind)?.setItem(key, JSON.stringify(value))
  } catch {
    /* quota or disabled: non-essential */
  }
}

export function removeKey(key: string, kind: 'local' | 'session' = 'local'): void {
  try {
    store(kind)?.removeItem(key)
  } catch {
    /* ignore */
  }
}

// Submitted configs, kept locally so a failed/cancelled run can be retried with the exact inputs.
const RUN_CFG = 'qf.run-config.'
const RUN_CFG_INDEX = 'qf.run-config.index'

export function rememberRunConfig(runId: string, config: unknown): void {
  writeJson(RUN_CFG + runId, config)
  const idx = (readJson<string[]>(RUN_CFG_INDEX) ?? []).filter((x) => x !== runId)
  idx.push(runId)
  // keep the 40 most recent
  for (const old of idx.splice(0, Math.max(0, idx.length - 40))) removeKey(RUN_CFG + old)
  writeJson(RUN_CFG_INDEX, idx)
}

export function recallRunConfig<T>(runId: string): T | null {
  return readJson<T>(RUN_CFG + runId)
}
