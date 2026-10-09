// The landing page's picture of one quantum pick. Each stock is a qubit, drawn as a coin: the coins spin while the
// circuit searches every buy/skip mix at once, then land on 1 (buy) or 0 (skip). Plain CSS 3D, no WebGL.
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { formatInt, formatPct } from '../../lib/format'
import { useMediaQuery } from '../../lib/media'
import { IconCheck, IconGrid, IconRefresh, IconSpark } from '../ui'

export interface QubitStock {
  symbol: string
  sector?: string
  /** The weight QAOA gave the stock; 0 or missing once measured means it was skipped. */
  weight?: number
}

type Phase = 'pick' | 'spin' | 'done'
type CoinState = 'idle' | 'spin' | 'buy' | 'skip'

const STEPS: Array<{ phase: Phase; label: string }> = [
  { phase: 'pick', label: 'Stocks in' },
  { phase: 'spin', label: 'Quantum search' },
  { phase: 'done', label: 'Result' },
]
const ORDER: Phase[] = ['pick', 'spin', 'done']

const MAX_COINS = 8
/** With a chart above them, wide screens set the coins in one row so the card stays about as tall as before. */
const ONE_ROW: Record<number, string> = { 5: 'lg:grid-cols-5', 6: 'lg:grid-cols-6', 7: 'lg:grid-cols-7', 8: 'lg:grid-cols-8' }
/** When the coins start spinning, when the first one lands, and the gap between landings (ms). */
const TIMING = { spin: 1400, land: 3800, stagger: 180 }

/** How many coins have landed: -1 while the stocks come in, 0 while all spin, n when every coin is down. */
function useLanded(n: number, animate: boolean, measured: boolean): number {
  const [landed, setLanded] = useState(animate ? -1 : measured ? n : 0)
  useEffect(() => {
    if (!animate) return
    const timers = [window.setTimeout(() => setLanded(0), TIMING.spin)]
    if (measured) for (let i = 1; i <= n; i++) timers.push(window.setTimeout(() => setLanded(i), TIMING.land + (i - 1) * TIMING.stagger))
    return () => timers.forEach((t) => window.clearTimeout(t))
  }, [animate, measured, n])
  return landed
}

function Coin({ state, measured, row }: { state: CoinState; measured: boolean; row: boolean }) {
  const front = state === 'idle' || !measured ? '?' : '1'
  const back = measured ? '0' : '?'
  const frontTone = state === 'buy' ? 'bg-linear-to-br from-teal-600 to-teal text-white' : state === 'spin' ? 'bg-linear-to-br from-cobalt to-cobalt-800 text-white' : 'bg-mist-2 text-muted'
  const backTone = state === 'skip' ? 'bg-mist-2 text-muted' : 'bg-linear-to-br from-cobalt-50 to-cobalt-200 text-cobalt-800'
  const face = 'qf-coin-face absolute inset-0 flex items-center justify-center rounded-full text-[0.95rem] font-bold tabular sm:text-base'
  return (
    <span className={`h-10 w-10 shrink-0 [perspective:240px] sm:h-12 sm:w-12 ${row ? 'lg:h-10 lg:w-10' : ''}`} aria-hidden="true">
      <span className="qf-coin relative block h-full w-full" data-state={state} data-measured={measured ? undefined : 'false'}>
        <span className={`${face} ${frontTone}`} data-flat={state === 'idle' ? '' : undefined}>
          {front}
        </span>
        <span className={`qf-coin-back ${face} ${backTone}`} data-flat={state === 'skip' ? '' : undefined}>
          {back}
        </span>
      </span>
    </span>
  )
}

interface StoryProps {
  stocks: QubitStock[]
  measured: boolean
  animate: boolean
  /** One line for the last step, e.g. how the pick compares with the best classical answer. */
  verdict?: string
  /** One row of coins on wide screens (the card has a chart above). */
  row: boolean
  onReplay?: () => void
}

function Story({ stocks, measured, animate, verdict, row, onReplay }: StoryProps) {
  const shown = stocks.slice(0, MAX_COINS)
  const n = shown.length
  const landed = useLanded(n, animate, measured)
  const phase: Phase = landed < 0 ? 'pick' : measured && landed >= n ? 'done' : 'spin'
  const at = ORDER.indexOf(phase)
  const bought = stocks.filter((s) => (s.weight ?? 0) > 0)

  const note =
    phase === 'pick'
      ? { title: `${stocks.length} stocks go in`, body: 'Each one is a qubit, drawn as a coin.' }
      : phase === 'spin'
        ? measured
          ? { title: 'Quantum search', body: `All coins spin at once, weighing ${formatInt(2 ** stocks.length)} buy or skip mixes.` }
          : { title: 'Not measured yet', body: 'Run the Quick preset to land these coins.' }
        : { title: `Buy ${bought.length}, skip ${stocks.length - bought.length}`, body: verdict }
  // The current step's bar fills while it lasts: the stocks come in, then the coins spin until the last one lands.
  const fillMs = !animate ? 0 : phase === 'pick' ? TIMING.spin : phase === 'spin' && measured ? TIMING.land + (n - 1) * TIMING.stagger - TIMING.spin : 0

  return (
    <>
      <ol className="mx-4 mt-4 grid grid-cols-3 gap-1.5 sm:mx-5" aria-label="Steps">
        {STEPS.map((s, i) => {
          const state = i < at || phase === 'done' ? 'done' : i === at ? 'current' : 'todo'
          const fill = state === 'done' ? 1 : state === 'todo' ? 0 : fillMs > 0 ? undefined : 0.5
          return (
            <li key={s.phase} aria-current={state === 'current' ? 'step' : undefined} className="h-1 overflow-hidden rounded-full bg-mist-2">
              <span
                className={`qf-coin-step block h-full rounded-full ${phase === 'done' ? 'bg-teal' : 'bg-cobalt'}`}
                data-run={fill === undefined ? '' : undefined}
                style={{ transform: fill === undefined ? undefined : `scaleX(${fill})`, '--qf-step-ms': `${fillMs}ms` } as CSSProperties}
              />
              <span className="sr-only">
                {s.label}
                {state === 'done' ? ', done' : ''}
              </span>
            </li>
          )
        })}
      </ol>

      <ul
        className={`grid min-h-[9.5rem] grid-cols-4 content-start gap-x-1 gap-y-4 px-3 pt-5 pb-5 sm:gap-x-3 sm:px-5 ${
          row ? `lg:min-h-0 lg:gap-x-1 lg:px-3 lg:pt-4 lg:pb-3 ${ONE_ROW[n] ?? ''}` : ''
        } ${animate ? 'qf-coins-live' : ''}`}
        aria-label="Each stock is a qubit"
      >
        {shown.map((s, i) => {
          const bit = (s.weight ?? 0) > 0 ? 'buy' : 'skip'
          const state: CoinState = landed < 0 ? 'idle' : measured && i < landed ? bit : 'spin'
          return (
            <li
              key={s.symbol}
              data-symbol={s.symbol}
              data-bit={state === 'buy' ? '1' : state === 'skip' ? '0' : '?'}
              title={s.sector}
              className="animate-fade-up flex min-w-0 flex-col items-center text-center"
              style={animate ? { animationDelay: `${i * 70}ms` } : { animation: 'none' }}
            >
              <Coin state={state} measured={measured} row={row} />
              <span
                className={`mt-2 text-[0.68rem] font-semibold whitespace-nowrap transition-colors duration-500 sm:text-xs ${row ? 'lg:max-w-full lg:truncate lg:text-[0.625rem] lg:tracking-tight' : ''} ${
                  state === 'skip' ? 'text-muted' : 'text-ink'
                }`}
              >
                {s.symbol}
              </span>
              <span className={`mt-0.5 h-4 text-[0.68rem] leading-4 font-medium tabular sm:text-xs ${state === 'buy' ? 'text-teal' : 'text-muted'}`} aria-hidden="true">
                {state === 'buy' ? formatPct(s.weight ?? 0, 0) : state === 'skip' ? 'Skip' : ''}
              </span>
              <span className="sr-only">{state === 'buy' ? `measured 1: buy, ${formatPct(s.weight ?? 0, 0)}` : state === 'skip' ? 'measured 0: skip' : 'not measured yet'}</span>
            </li>
          )
        })}
        {stocks.length > MAX_COINS ? <li className={`col-span-4 text-center text-xs text-muted ${row ? 'lg:col-span-full' : ''}`}>+{stocks.length - MAX_COINS} more</li> : null}
      </ul>

      <div
        className={`mx-2.5 mb-2.5 flex min-h-[4.25rem] items-center gap-3 rounded-lg py-2.5 pr-1.5 pl-3 transition-colors duration-500 sm:mx-3 sm:mb-3 sm:pl-4 ${
          phase === 'done' ? 'bg-teal-50' : 'bg-cobalt-50'
        }`}
      >
        <span
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors duration-500 ${phase === 'done' ? 'bg-teal text-white' : 'bg-cobalt-100 text-cobalt'}`}
          aria-hidden="true"
        >
          {phase === 'done' ? <IconCheck size={16} /> : phase === 'spin' ? <IconSpark size={16} /> : <IconGrid size={15} />}
        </span>
        <p className="min-w-0 flex-1" aria-live="polite" data-testid="hero-caption" data-phase={phase}>
          <span className="block text-sm font-semibold text-ink">{note.title}</span>
          {note.body ? <span className="mt-0.5 block text-[0.8125rem] leading-snug text-ink-2">{note.body}</span> : null}
        </p>
        {onReplay ? (
          <button
            type="button"
            onClick={onReplay}
            className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-md px-2 text-sm font-medium text-cobalt hover:bg-white/70 pointer-fine:min-h-9"
            data-testid="hero-replay"
          >
            <IconRefresh size={15} />
            <span className="sr-only sm:not-sr-only">Replay</span>
          </button>
        ) : null}
      </div>
    </>
  )
}

interface QubitCoinsProps {
  stocks: QubitStock[]
  measured: boolean
  source: 'example' | 'selection'
  /** The card's header: a short name, a line under it, and one headline figure on the right. */
  title: string
  subtitle?: string
  stat?: { value: string; label: string; good?: boolean }
  verdict?: string
  caption?: ReactNode
  /** Shown at the top of the card, above a hairline: today's market chart. Leave it out and the card is unchanged. */
  market?: ReactNode
  className?: string
}

export function QubitCoins({ stocks, measured, source, title, subtitle, stat, verdict, caption, market, className = '' }: QubitCoinsProps) {
  const reduce = useMediaQuery('(prefers-reduced-motion: reduce)')
  const animate = !reduce
  // Replay remounts the story, which restarts its timeline from the first step.
  const [play, setPlay] = useState(0)

  return (
    <figure data-testid="hero-stage" data-hero-source={source} data-hero-animated={animate ? 'true' : 'false'} className={`relative isolate m-0 min-w-0 ${className}`}>
      <div className="pointer-events-none absolute -inset-6 -z-10" aria-hidden="true">
        <div className="absolute top-2 right-4 h-40 w-40 rounded-full bg-cobalt/12 blur-3xl" />
        <div className="absolute bottom-6 left-0 h-32 w-32 rounded-full bg-teal/12 blur-3xl" />
      </div>
      <div className="overflow-hidden rounded-xl border border-line bg-canvas shadow-float">
        {market ? <div className="border-b border-line px-4 pt-4 pb-3 sm:px-5">{market}</div> : null}
        <div className={`flex items-start justify-between gap-4 px-4 pt-4 sm:px-5 ${market ? '' : 'sm:pt-5'}`}>
          <div className="min-w-0">
            <p className="text-base font-semibold tracking-tight text-ink">{title}</p>
            {subtitle ? <p className="mt-0.5 text-xs text-muted">{subtitle}</p> : null}
          </div>
          {stat ? (
            <div className="shrink-0 text-right">
              <p className={`text-[1.6rem] leading-none font-semibold tracking-tight tabular ${stat.good ? 'text-teal' : 'text-ink'}`}>{stat.value}</p>
              <p className="mt-1.5 text-xs text-muted">{stat.label}</p>
            </div>
          ) : null}
        </div>
        <Story
          key={`${play}:${stocks.map((s) => s.symbol).join(',')}:${measured}`}
          stocks={stocks}
          measured={measured}
          animate={animate}
          verdict={verdict}
          row={!!market}
          onReplay={animate && measured ? () => setPlay((p) => p + 1) : undefined}
        />
      </div>
      {caption ? <figcaption className="mt-3 flex flex-wrap justify-between gap-x-4 gap-y-1 px-1 text-xs leading-relaxed text-muted">{caption}</figcaption> : null}
    </figure>
  )
}
