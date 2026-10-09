import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { SCHEMA_VERSION, type Preset, type RunConfig } from '../api/contract.gen'
import { readJson, writeJson } from '../lib/storage'

// The Lab's working config is a complete RunConfig, seeded from the server's default example
// config (GET /api/presets -> default_config) so the UI never invents its own defaults.

const KEY = 'qf.lab.v1'

interface Persisted {
  schema: string
  config: RunConfig
}

interface LabCtx {
  config: RunConfig | null
  /** Initialise from server defaults unless a session draft already exists. */
  seed: (defaults: RunConfig) => void
  update: (fn: (draft: RunConfig) => void) => void
  replace: (config: RunConfig) => void
  applyPreset: (p: Preset) => void
  toggleSymbol: (symbol: string) => void
  setSymbols: (symbols: string[]) => void
}

const Ctx = createContext<LabCtx | null>(null)

export function LabProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<RunConfig | null>(() => {
    const p = readJson<Persisted>(KEY, 'session')
    return p && p.schema === SCHEMA_VERSION ? p.config : null
  })


  useEffect(() => {
    if (config) writeJson(KEY, { schema: SCHEMA_VERSION, config } satisfies Persisted, 'session')
  }, [config])

  const seed = useCallback((defaults: RunConfig) => setConfig((c) => c ?? structuredClone(defaults)), [])
  const replace = useCallback((c: RunConfig) => setConfig(structuredClone(c)), [])
  const update = useCallback((fn: (draft: RunConfig) => void) => {
    setConfig((c) => {
      if (!c) return c
      const next = structuredClone(c)
      fn(next)
      return next
    })
  }, [])
  const applyPreset = useCallback(
    (p: Preset) =>
      update((d) => {
        d.preset = p.name
        d.qaoa = structuredClone(p.qaoa)
        d.baselines = structuredClone(p.baselines)
        d.timeout_seconds = p.timeout_seconds
      }),
    [update],
  )
  const toggleSymbol = useCallback(
    (s: string) =>
      update((d) => {
        d.symbols = d.symbols.includes(s) ? d.symbols.filter((x) => x !== s) : [...d.symbols, s]
      }),
    [update],
  )
  const setSymbols = useCallback((symbols: string[]) => update((d) => void (d.symbols = [...new Set(symbols)])), [update])

  const value = useMemo<LabCtx>(
    () => ({ config, seed, update, replace, applyPreset, toggleSymbol, setSymbols }),
    [config, seed, update, replace, applyPreset, toggleSymbol, setSymbols],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useLab(): LabCtx {
  const c = useContext(Ctx)
  if (!c) throw new Error('useLab must be used inside <LabProvider>')
  return c
}

/** Marks the run settings as custom when the user edits them by hand. */
// eslint-disable-next-line react-refresh/only-export-components
export function markCustom(d: RunConfig): void {
  d.preset = 'custom'
}
