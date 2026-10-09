// The landing illustration: one Bloch sphere that measures each stock of a run in turn. Measuring a qubit gives
// 1 (buy, the arrow drops to the bottom pole) or 0 (skip, it rises to the top). The 3D scene loads after the page is
// idle; until then, and whenever 3D is unavailable or motion is reduced, a still drawing of the same sphere stands in.

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { formatPct } from '../../lib/format'
import { useMediaQuery } from '../../lib/media'
import { IconArrowRight, IconRefresh } from '../ui'
import { AXIS_END, CAMERA, IDLE, START_TURN, blochDir, latitude, meridian, outlineRadius, project, turnY, type Vec3 } from './bloch'
import type { BlochScene, QubitPose } from './blochScene'
import { deviceFallback, markLowPerf, type FallbackReason } from './support'

export interface QubitStock {
  symbol: string
  sector?: string
  /** Portfolio weight when bought; 0 or absent means the qubit read 0. */
  weight?: number | null
}

export interface QuantumSphereProps {
  stocks: readonly QubitStock[]
  /** False before anything has run: the qubit stays in superposition and the list shows "Not run yet". */
  measured: boolean
  /** Total qubits in the circuit (stocks plus helper qubits for the rules). */
  qubits: number
  source: 'example' | 'selection'
  footer?: ReactNode
  caption?: ReactNode
  className?: string
}

/** Rows shown before the list folds the rest into "+N more". */
const MAX_ROWS = 10

/** Timings of one measurement (ms): the wait before the first, the spin, and the pause on the result. */
const TIMING = { start: 500, measure: 560, hold: 520, last: 1100 } as const

const COLOR: Record<QubitPose, string> = {
  idle: 'var(--color-cobalt)',
  measure: 'var(--color-cobalt)',
  '1': 'var(--color-teal-600)',
  '0': 'var(--color-faint)',
}

const bitOf = (s: QubitStock): '0' | '1' => ((s.weight ?? 0) > 0 ? '1' : '0')

// The still drawing, computed once: lines split into the runs in front of and behind the sphere.
const VIEW = 240
const CAM = project([0, 0, 0], VIEW)
const ELEV = (CAMERA.elevation * Math.PI) / 180
/** A point on the unit sphere faces the camera when its dot product with the camera position exceeds 1. */
const facing = (p: Vec3) => CAMERA.distance * (p[1] * Math.sin(ELEV) + p[2] * Math.cos(ELEV)) > 1

function runs(points: Vec3[]): { front: string; back: string } {
  let front = ''
  let back = ''
  let prev: boolean | null = null
  for (const p of points) {
    const f = facing(p)
    const { x, y } = project(p, VIEW)
    const cmd = f === prev ? 'L' : 'M'
    const seg = `${cmd}${x.toFixed(1)} ${y.toFixed(1)}`
    if (f) front += seg
    else back += seg
    prev = f
  }
  return { front, back }
}

const STILL = (() => {
  const grid = [
    ...[Math.PI / 6, Math.PI / 3, (2 * Math.PI) / 3, (5 * Math.PI) / 6].map((t) => latitude(t, START_TURN)),
    ...[0, 1, 2, 3, 4, 5].map((k) => meridian((k * Math.PI) / 6, START_TURN)),
  ].map(runs)
  const equatorPts = latitude(Math.PI / 2, START_TURN)
  const equator = runs(equatorPts)
  const disc = equatorPts.map((p) => project(p, VIEW)).map(({ x, y }, i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join('') + 'Z'
  return {
    grid,
    equator,
    disc,
    r: outlineRadius() * VIEW,
    north: project([0, 1, 0], VIEW),
    south: project([0, -1, 0], VIEW),
    top: project([0, AXIS_END, 0], VIEW),
    bottom: project([0, -AXIS_END, 0], VIEW),
  }
})()

/** Where the pole labels sit, as fractions of the box, so they line up with both the still and the 3D scene. */
const LABEL = { top: STILL.top.y / VIEW, bottom: STILL.bottom.y / VIEW }

function SphereStill({ pose, className = '' }: { pose: QubitPose; className?: string }) {
  const id = useId().replace(/:/g, '')
  const theta = pose === '0' ? 0 : pose === '1' ? Math.PI : IDLE.theta
  const tip = project(turnY(blochDir(theta, IDLE.phi), START_TURN), VIEW)
  const color = COLOR[pose]
  return (
    <svg viewBox={`0 0 ${VIEW} ${VIEW}`} className={className} aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id={`${id}g`} cx="38%" cy="32%" r="75%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.7" stopColor="var(--color-cobalt-50)" />
          <stop offset="1" stopColor="var(--color-cobalt-100)" />
        </radialGradient>
      </defs>
      <circle cx={CAM.x} cy={CAM.y} r={STILL.r} fill={`url(#${id}g)`} stroke="var(--color-cobalt)" strokeOpacity="0.35" strokeWidth="1.2" />
      <path d={STILL.disc} fill="var(--color-cobalt)" fillOpacity="0.05" />
      <g fill="none" stroke="var(--color-cobalt)" strokeWidth="0.9">
        {STILL.grid.map((g, i) => (
          <g key={i}>
            <path d={g.back} strokeOpacity="0.07" />
            <path d={g.front} strokeOpacity="0.2" />
          </g>
        ))}
        <path d={STILL.equator.back} strokeOpacity="0.16" strokeDasharray="3 3" />
        <path d={STILL.equator.front} strokeOpacity="0.5" strokeWidth="1.2" />
      </g>
      <line x1={STILL.top.x} y1={STILL.top.y} x2={STILL.bottom.x} y2={STILL.bottom.y} stroke="var(--color-muted)" strokeOpacity="0.55" strokeWidth="1" />
      <circle cx={STILL.north.x} cy={STILL.north.y} r="3.4" fill="var(--color-faint)" />
      <circle cx={STILL.south.x} cy={STILL.south.y} r="3.4" fill="var(--color-teal-600)" />
      <line x1={CAM.x} y1={CAM.y} x2={tip.x} y2={tip.y} stroke={color} strokeWidth="3" strokeLinecap="round" />
      <circle cx={tip.x} cy={tip.y} r="5" fill={color} />
      <circle cx={CAM.x} cy={CAM.y} r="3.6" fill="var(--color-ink-2)" />
    </svg>
  )
}

type RowState = 'waiting' | 'measure' | '0' | '1' | 'unrun'

function Pill({ state, weight }: { state: RowState; weight?: number | null }) {
  const tone =
    state === '1'
      ? 'bg-teal-50 text-teal ring-teal-100'
      : state === 'measure'
        ? 'bg-cobalt-50 text-cobalt-800 ring-cobalt-200'
        : 'bg-mist-2 text-muted ring-line'
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset ${tone}`}>
      {state === '1' ? (
        <>
          <span className="font-semibold">1</span> Buy{weight ? ` · ${formatPct(weight, 0)}` : ''}
        </>
      ) : state === '0' ? (
        <>
          <span className="font-semibold">0</span> Skip
        </>
      ) : state === 'measure' ? (
        'Measuring'
      ) : state === 'waiting' ? (
        <span className="font-semibold">?</span>
      ) : (
        'Not run yet'
      )}
    </span>
  )
}

export function QuantumSphere({ stocks, measured, qubits, source, footer, caption, className = '' }: QuantumSphereProps) {
  const reduce = useMediaQuery('(prefers-reduced-motion: reduce)')
  const animated = measured && !reduce
  const count = stocks.length

  // Whether the 3D scene may run here, and if it ran, whether it is on screen yet.
  const [device] = useState(deviceFallback)
  const [failure, setFailure] = useState<FallbackReason>(null)
  const [ready, setReady] = useState(false)
  const reason: FallbackReason = reduce ? 'reduced-motion' : (device ?? failure)
  const mode = reason ? 'svg' : ready ? 'webgl' : 'loading'

  // The timeline: k = 2i while stock i is measured, 2i + 1 once it has collapsed, 2n when all are done.
  const [play, setPlay] = useState(0)
  const [step, setStep] = useState({ play: 0, k: -1 })
  const [late, setLate] = useState(false)
  const started = mode !== 'loading' || late
  const k = !measured ? -1 : !animated ? 2 * count : step.play === play ? step.k : -1
  const cur = k >= 0 && k < 2 * count ? Math.floor(k / 2) : -1
  const pose: QubitPose = cur < 0 ? 'idle' : k % 2 === 0 ? 'measure' : bitOf(stocks[cur])

  useEffect(() => {
    if (mode !== 'loading') return
    const t = window.setTimeout(() => setLate(true), 3000)
    return () => window.clearTimeout(t)
  }, [mode])

  useEffect(() => {
    if (!animated || !started) return
    let at = -1
    let t = 0
    const next = () => {
      at += 1
      setStep({ play, k: at })
      if (at >= 2 * count) return
      t = window.setTimeout(next, at % 2 === 0 ? TIMING.measure : at === 2 * count - 1 ? TIMING.last : TIMING.hold)
    }
    t = window.setTimeout(next, TIMING.start)
    return () => window.clearTimeout(t)
  }, [animated, started, play, count])

  // Load the 3D scene once the page is idle. Any failure falls back to the still for good.
  const hostRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<BlochScene | null>(null)
  const poseRef = useRef<QubitPose>(pose)
  useEffect(() => {
    poseRef.current = pose
    sceneRef.current?.setPose(pose)
  }, [pose])

  useEffect(() => {
    if (reason) return
    const host = hostRef.current
    if (!host) return
    let cancelled = false
    let scene: BlochScene | null = null
    const load = () => {
      import('./blochScene')
        .then(({ createBlochScene }) => {
          if (cancelled) return
          scene = createBlochScene({
            host,
            pose: poseRef.current,
            onReady: () => {
              if (!cancelled) setReady(true)
            },
            onSlow: () => {
              markLowPerf()
              if (!cancelled) setFailure('low-performance')
            },
            onError: () => {
              if (!cancelled) setFailure('scene-error')
            },
          })
          sceneRef.current = scene
        })
        .catch(() => {
          if (!cancelled) setFailure('scene-error')
        })
    }
    const idle = 'requestIdleCallback' in window
    const handle = idle ? window.requestIdleCallback(load, { timeout: 1200 }) : window.setTimeout(load, 300)
    return () => {
      cancelled = true
      if (idle) window.cancelIdleCallback(handle)
      else window.clearTimeout(handle)
      scene?.dispose()
      sceneRef.current = null
    }
  }, [reason])

  const picked = stocks.filter((s) => bitOf(s) === '1').length
  const flow = [
    { n: count, label: count === 1 ? 'stock' : 'stocks', tone: 'border-line bg-canvas text-ink' },
    { n: qubits, label: qubits === 1 ? 'qubit' : 'qubits', tone: 'border-cobalt-200 bg-cobalt-50 text-cobalt-800' },
    { n: measured ? picked : '?', label: 'picked', tone: 'border-teal-100 bg-teal-50 text-teal' },
  ]
  const shown = stocks.slice(0, MAX_ROWS)
  const done = k >= 2 * count

  const readout = !measured ? (
    <>Superposition · not measured yet</>
  ) : cur >= 0 && pose === 'measure' ? (
    <>
      Measuring <span className="font-semibold text-ink">{stocks[cur].symbol}</span>
    </>
  ) : cur >= 0 ? (
    <>
      <span className="font-semibold text-ink">{stocks[cur].symbol}</span> = <span className="font-semibold">{pose}</span> ·{' '}
      <span className={pose === '1' ? 'font-medium text-teal' : ''}>{pose === '1' ? 'buy' : 'skip'}</span>
    </>
  ) : done ? (
    <>
      <span className="font-semibold text-teal tabular">{picked}</span> of {count} measured as buy
    </>
  ) : (
    <>Ready to measure</>
  )

  return (
    <figure
      data-testid="hero-stage"
      data-hero-mode={mode}
      data-hero-reason={reason ?? undefined}
      data-hero-animated={animated ? 'true' : 'false'}
      data-hero-source={source}
      className={`relative isolate m-0 min-w-0 ${className}`}
    >
      <div className="pointer-events-none absolute -inset-4 -z-10" aria-hidden="true">
        <div className="absolute top-0 right-6 h-44 w-44 rounded-full bg-cobalt/15 blur-3xl" />
        <div className="absolute bottom-8 left-2 h-36 w-36 rounded-full bg-teal/15 blur-3xl" />
      </div>
      <div className="overflow-hidden rounded-xl border border-line bg-canvas/95 shadow-float">
        <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <p className="eyebrow">{source === 'example' ? 'A real quantum pick' : 'Your selection'}</p>
            <ol className="mt-2 flex flex-wrap items-center gap-1.5 text-sm" aria-label="From stocks to picks">
              {flow.map((f, i) => (
                <li key={f.label} className="flex items-center gap-1.5">
                  {i > 0 ? <IconArrowRight size={14} className="text-faint" /> : null}
                  <span className={`inline-flex items-baseline gap-1 rounded-md border px-2 py-0.5 ${f.tone}`}>
                    <span className="font-semibold tabular">{f.n}</span>
                    {f.label}
                  </span>
                </li>
              ))}
            </ol>
          </div>
          {animated ? (
            <button
              type="button"
              onClick={() => setPlay((p) => p + 1)}
              className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium text-cobalt hover:bg-cobalt-50 pointer-fine:min-h-9"
              data-testid="hero-replay"
            >
              <IconRefresh size={15} />
              Replay
            </button>
          ) : null}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <div className="flex flex-col items-center border-b border-line bg-[radial-gradient(circle_at_50%_42%,var(--color-cobalt-50),transparent_70%)] px-4 pt-3 pb-4 sm:border-r sm:border-b-0">
            <div className="relative aspect-square w-full max-w-[16.5rem]">
              <SphereStill pose={pose} className={`absolute inset-0 h-full w-full transition-opacity duration-500 ${mode === 'webgl' ? 'opacity-0' : 'opacity-100'}`} />
              {reason ? null : (
                <div ref={hostRef} className={`absolute inset-0 transition-opacity duration-500 ${mode === 'webgl' ? 'opacity-100' : 'opacity-0'}`} data-testid="hero-canvas" />
              )}
              <span
                className="pointer-events-none absolute left-[calc(50%+10px)] -translate-y-1/2 text-xs whitespace-nowrap text-muted"
                style={{ top: `${LABEL.top * 100}%` }}
                aria-hidden="true"
              >
                <span className="font-semibold">|0⟩</span> skip
              </span>
              <span
                className="pointer-events-none absolute left-[calc(50%+10px)] -translate-y-1/2 text-xs font-medium whitespace-nowrap text-teal"
                style={{ top: `${LABEL.bottom * 100}%` }}
                aria-hidden="true"
              >
                <span className="font-semibold">|1⟩</span> buy
              </span>
            </div>
            <p className="mt-1 min-h-6 text-center text-sm text-muted" aria-hidden="true" data-testid="hero-readout">
              {readout}
            </p>
          </div>

          <ul className="grid content-start gap-1.5 p-3 max-sm:grid-cols-2 sm:p-4" aria-label="Each stock is a qubit">
            {shown.map((s, i) => {
              const bit = bitOf(s)
              const state: RowState = !measured ? 'unrun' : k >= 2 * i + 1 ? bit : k === 2 * i ? 'measure' : 'waiting'
              return (
                <li
                  key={s.symbol}
                  data-bit={state === '0' || state === '1' ? state : '?'}
                  data-symbol={s.symbol}
                  title={s.sector}
                  className={`flex min-w-0 items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 transition-colors duration-300 ${
                    state === '1' ? 'border-teal-100 bg-teal-50/60' : state === 'measure' ? 'border-cobalt-200 bg-cobalt-50/60' : 'border-line bg-canvas'
                  }`}
                >
                  <span className="truncate text-sm font-semibold text-ink">{s.symbol}</span>
                  <Pill state={state} weight={s.weight} />
                  <span className="sr-only">{state === '1' ? 'measured 1: bought' : state === '0' ? 'measured 0: skipped' : 'not measured yet'}</span>
                </li>
              )
            })}
            {stocks.length > MAX_ROWS ? <li className="px-2.5 py-1 text-xs text-muted">+{stocks.length - MAX_ROWS} more</li> : null}
          </ul>
        </div>

        {footer ? <div className="border-t border-line bg-mist">{footer}</div> : null}
      </div>
      {caption ? <figcaption className="mt-3 px-1 text-xs leading-relaxed text-muted">{caption}</figcaption> : null}
    </figure>
  )
}
