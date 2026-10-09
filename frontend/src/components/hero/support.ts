// Decides whether the 3D hero may run. Every "no" falls back to the static SVG illustration.

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

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** Coarse static check for very constrained devices; runtime frame timing refines it. */
export function looksLowEnd(): boolean {
  const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } }
  if (nav.connection?.saveData) return true
  if (typeof nav.hardwareConcurrency === 'number' && nav.hardwareConcurrency > 0 && nav.hardwareConcurrency <= 2) return true
  if (typeof nav.deviceMemory === 'number' && nav.deviceMemory > 0 && nav.deviceMemory <= 2) return true
  return false
}

const LOW_PERF_KEY = 'qf.hero.lowperf'

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

export function fallbackReason(): FallbackReason {
  if (prefersReducedMotion()) return 'reduced-motion'
  if (!hasWebGL()) return 'no-webgl'
  if (looksLowEnd() || lowPerfMarked()) return 'low-performance'
  return null
}

/** Three tilted orbits of the 3D scene. */
export const ORBITS = [
  { radius: 2.35, tiltX: 0.36, tiltZ: 0.1, speed: 0.05 },
  { radius: 2.95, tiltX: -0.2, tiltZ: -0.14, speed: -0.035 },
  { radius: 3.5, tiltX: 0.12, tiltZ: 0.05, speed: 0.025 },
] as const

export interface HeroAsset {
  symbol: string
  sector: string
}

/** At most this many tiles dock around the core; with more selected assets the last slot reads "+N". */
export const DOCK_MAX = 10
/** Unselected assets drawn on the orbits. Fewer, larger tiles keep every symbol legible. */
export const ORBIT_MAX = 15

export interface HeroPick {
  /** Selected symbols that dock around the core, in selection order. */
  docked: string[]
  /** Selected symbols not drawn (summarised as "+N"). */
  more: number
  /** Unselected symbols drawn on the orbits, in universe order. */
  orbit: string[]
}

/** Which assets the hero draws. The hovered asset is always among them, so hovering a list row stays visible. */
export function pickHeroAssets(assets: HeroAsset[], selected: string[], hover?: string | null): HeroPick {
  const known = new Set(assets.map((a) => a.symbol))
  const sel = selected.filter((s) => known.has(s))
  const selSet = new Set(sel)
  let docked = sel
  if (sel.length > DOCK_MAX) {
    docked = sel.slice(0, DOCK_MAX - 1)
    if (hover && selSet.has(hover) && !docked.includes(hover)) docked[docked.length - 1] = hover
  }
  const orbit = assets
    .filter((a) => !selSet.has(a.symbol))
    .slice(0, ORBIT_MAX)
    .map((a) => a.symbol)
  if (hover && known.has(hover) && !selSet.has(hover) && !orbit.includes(hover)) {
    if (orbit.length === ORBIT_MAX) orbit[ORBIT_MAX - 1] = hover
    else orbit.push(hover)
  }
  return { docked, more: sel.length - docked.length, orbit }
}

/**
 * 3D docking slot for the k-th of n docked tiles: an upright ellipse facing the camera, just in front of
 * the core, so neighbouring billboards never stack on top of each other.
 */
export function dockSlot(k: number, n: number): [number, number, number] {
  const a = -Math.PI / 2 + (k / Math.max(1, n)) * Math.PI * 2
  return [Math.cos(a) * 1.95, -Math.sin(a) * 1.2, 0.35]
}

/** Geometry of the static (SVG) hero, in its 800 x 560 viewBox. */
export const FLAT = {
  cx: 400,
  cy: 286,
  tileW: 80,
  tileH: 32,
  dock: { rx: 200, ry: 125 },
  orbit: { rx: 325, ry: 200 },
} as const

/**
 * Fixed slot of the static hero: docked tiles sit on an inner ellipse round the core, the others on an
 * outer one. Slots are evenly spaced and the orbit always has ORBIT_MAX of them, so tiles keep their
 * place as the selection changes and no two tiles overlap (checked in support.test.ts).
 */
export function flatSlot(ring: 'dock' | 'orbit', k: number, n: number): { x: number; y: number } {
  const e = FLAT[ring]
  const slots = ring === 'orbit' ? ORBIT_MAX : Math.max(1, n)
  const a = -Math.PI / 2 + ((ring === 'orbit' ? k + 0.5 : k) / slots) * Math.PI * 2
  return { x: FLAT.cx + Math.cos(a) * e.rx, y: FLAT.cy + Math.sin(a) * e.ry }
}
