// Decides whether the 3D sphere may run. Every "no" keeps the still SVG drawing of the same sphere.

let webglCache: boolean | null = null

export function hasWebGL(): boolean {
  if (webglCache !== null) return webglCache
  try {
    const c = document.createElement('canvas')
    const gl = c.getContext('webgl2') ?? c.getContext('webgl')
    webglCache = !!gl
    // Release the probe context immediately.
    ;(gl as WebGLRenderingContext | null)?.getExtension('WEBGL_lose_context')?.loseContext()
  } catch {
    webglCache = false
  }
  return webglCache
}

/** Coarse static check for very constrained devices; frame timing in the scene refines it. */
export function looksLowEnd(): boolean {
  const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } }
  if (nav.connection?.saveData) return true
  if (typeof nav.hardwareConcurrency === 'number' && nav.hardwareConcurrency > 0 && nav.hardwareConcurrency <= 2) return true
  if (typeof nav.deviceMemory === 'number' && nav.deviceMemory > 0 && nav.deviceMemory <= 2) return true
  return false
}

const LOW_PERF_KEY = 'qf.hero.lowperf'

/** Remembers for this browser session that the scene ran too slowly, so it is not tried again on every visit. */
export function markLowPerf(): void {
  try {
    sessionStorage.setItem(LOW_PERF_KEY, '1')
  } catch {
    /* ignore */
  }
}

function lowPerfMarked(): boolean {
  try {
    return sessionStorage.getItem(LOW_PERF_KEY) === '1'
  } catch {
    return false
  }
}

export type FallbackReason = 'no-webgl' | 'reduced-motion' | 'low-performance' | 'scene-error' | null

/** Why the 3D scene should not start on this device, apart from reduced motion (a live media query). */
export function deviceFallback(): FallbackReason {
  if (!hasWebGL()) return 'no-webgl'
  if (looksLowEnd() || lowPerfMarked()) return 'low-performance'
  return null
}
