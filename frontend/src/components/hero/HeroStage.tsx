// Chooses between the static SVG illustration and the lazily loaded WebGL scene.
// The SVG paints first (no layout shift, good LCP); the 3D chunk is fetched when the browser is idle
// and only if WebGL is available, motion is allowed and the device is not flagged as slow.

import { Component, Suspense, lazy, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { HeroFallback } from './HeroFallback'
import { fallbackReason, markLowPerf, type FallbackReason, type HeroAsset } from './support'

const HeroScene = lazy(() => import('./HeroScene'))

export interface HeroStageProps {
  assets: HeroAsset[]
  selected: string[]
  weights?: Record<string, number> | null
  hover?: string | null
  compact?: boolean
  className?: string
  /** Caption under the illustration; pass null to hide (the parent then labels it). */
  caption?: string | null
}

type Mode = 'svg' | 'loading' | 'webgl'

const DEFAULT_CAPTION =
  'Conceptual illustration. The orbits are a visual motif, not a quantum circuit state or evidence of computation. Tiles are NIFTY 50 constituents; highlighted tiles are your current selection.'

const REASON_TEXT: Record<Exclude<FallbackReason, null>, string> = {
  'no-webgl': 'Static view: WebGL is not available.',
  'reduced-motion': 'Static view: reduced motion is on.',
  'low-performance': 'Static view: this device rendered the 3D scene too slowly.',
  'scene-error': 'Static view: the 3D scene failed to load.',
}

/** Catches a failed 3D chunk download or a WebGL/runtime error inside the scene; the stage then keeps the SVG. */
class SceneBoundary extends Component<{ onError: () => void; children: ReactNode }, { failed: boolean }> {
  override state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  override componentDidCatch() {
    this.props.onError()
  }
  override render() {
    return this.state.failed ? null : this.props.children
  }
}

function whenIdle(fn: () => void): () => void {
  const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void }
  if (w.requestIdleCallback) {
    const id = w.requestIdleCallback(fn, { timeout: 2500 })
    return () => w.cancelIdleCallback?.(id)
  }
  const id = window.setTimeout(fn, 600)
  return () => window.clearTimeout(id)
}

export function HeroStage({ assets, selected, weights, hover, compact, className = '', caption = DEFAULT_CAPTION }: HeroStageProps) {
  const [reason, setReason] = useState<FallbackReason>(() => (typeof window === 'undefined' ? 'no-webgl' : fallbackReason()))
  const [mode, setMode] = useState<Mode>('svg')
  const [visible, setVisible] = useState(true)
  const [pageVisible, setPageVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden')
  const ref = useRef<HTMLDivElement>(null)

  // React to reduced-motion changes while the page is open.
  useEffect(() => {
    let mq: MediaQueryList | null = null
    try {
      mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    } catch {
      return
    }
    const onChange = () => {
      const r = fallbackReason()
      setReason(r)
      if (r) setMode('svg')
    }
    mq.addEventListener?.('change', onChange)
    return () => mq?.removeEventListener?.('change', onChange)
  }, [])

  useEffect(() => {
    if (reason) return
    let cancelled = false
    const cancelIdle = whenIdle(() => {
      const go = () => {
        if (!cancelled) setMode((m) => (m === 'svg' ? 'loading' : m))
      }
      // Wait for fonts so the swap does not compete with text rendering for the LCP.
      if (document.fonts?.ready) document.fonts.ready.then(go, go)
      else go()
    })
    return () => {
      cancelled = true
      cancelIdle()
    }
  }, [reason])

  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { threshold: 0.05 })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  useEffect(() => {
    const on = () => setPageVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])

  const onLowPerf = useCallback(() => {
    markLowPerf()
    setReason('low-performance')
    setMode('svg')
  }, [])
  const onReady = useCallback(() => setMode('webgl'), [])
  const onSceneError = useCallback(() => {
    setReason('scene-error')
    setMode('svg')
  }, [])

  const showScene = !reason && (mode === 'loading' || mode === 'webgl')
  const running = visible && pageVisible

  return (
    <figure className={`relative m-0 flex flex-col ${className}`} data-testid="hero-stage" data-hero-mode={showScene ? mode : 'svg'} data-hero-reason={reason ?? ''} data-hero-running={showScene && running ? 'true' : 'false'}>
      <div ref={ref} className="relative min-h-0 w-full flex-1">
        <div className={`absolute inset-0 transition-opacity duration-700 ${mode === 'webgl' && showScene ? 'opacity-0' : 'opacity-100'}`}>
          <HeroFallback assets={assets} selected={selected} weights={weights} hover={hover} compact={compact} />
        </div>
        {showScene ? (
          <div className={`absolute inset-0 transition-opacity duration-700 ${mode === 'webgl' ? 'opacity-100' : 'opacity-0'}`}>
            <SceneBoundary onError={onSceneError}>
              <Suspense fallback={null}>
                <HeroScene assets={assets} selected={selected} weights={weights} hover={hover} running={running} compact={compact} onLowPerf={onLowPerf} onReady={onReady} />
              </Suspense>
            </SceneBoundary>
          </div>
        ) : null}
      </div>
      {caption !== null ? (
        <figcaption className="mt-2 shrink-0 text-xs leading-relaxed text-muted">
          {caption}
          {reason ? <span className="ml-1 text-faint">{REASON_TEXT[reason]}</span> : null}
        </figcaption>
      ) : null}
    </figure>
  )
}
