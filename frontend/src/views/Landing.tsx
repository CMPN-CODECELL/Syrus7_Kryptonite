import { useMemo } from 'react'
import { isExperimentResult } from '../api/client'
import { useAssets, useCapabilities, useDataStatus, useExample, usePresets, useRunResult } from '../api/hooks'
import { QubitCoins, type QubitStock } from '../components/hero/QubitCoins'
import { MarketGlance } from '../components/market/MarketGlance'
import { useLiveCandles } from '../components/market/useLiveCandles'
import { Badge, ButtonLink, IconArrowRight, IconCheck, IconCoin, IconGrid, IconHelp, IconRefresh, IconScale, IconShield } from '../components/ui'
import { CLOUD } from '../lib/cloud'
import { SOLVER_LABEL, formatDate, formatDateTime, formatInt, formatPct } from '../lib/format'
import { guardrailFamilies, liveLimits } from '../lib/guardrails'
import { solver, weightsOf } from '../lib/result'
import { FAMILY_ICON } from './shared/guardIcons'
import { RunSummaryCard } from './shared/RunSummaryCard'

const STEPS = [
  { title: 'Pick stocks and rules', body: 'Choose stocks, a budget and how much risk is fine.' },
  { title: 'Check it fits', body: 'See the qubits it needs before anything runs.' },
  { title: 'Quantum and classical solve it', body: 'QAOA and four classical methods, same problem.' },
  { title: 'Read the answer', body: 'What to buy, in rupees, and how it did later.' },
]

// The same idea for a ten-year-old: picking a team with fixed pocket money. One plain sentence each, no jargon.
const SIMPLE = [
  { icon: IconGrid, title: 'Pick players', body: 'Choose a few big Indian companies from a list of 50, like players for a team.' },
  { icon: IconScale, title: 'Fixed pocket money', body: 'The money is fixed, so only some can join, say 4 out of 8.' },
  { icon: IconCoin, title: 'A coin each', body: 'Each company gets a special coin called a qubit: heads means buy, tails means skip.' },
  { icon: IconRefresh, title: 'Toss and learn', body: 'The computer tosses the coins many times and learns to land on teams that grew well before, without wild ups and downs.' },
  { icon: IconCheck, title: 'Check the answer', body: 'Ordinary methods solve the same puzzle, so you can see if the coins found a good team.' },
]

export default function Landing() {
  const assets = useAssets()
  const presets = usePresets()
  const caps = useCapabilities()
  const data = useDataStatus()
  const example = useExample()
  const exampleId = example.data?.run_id ?? undefined
  const exResult = useRunResult(exampleId, !!exampleId)
  const exRun = exResult.data && !isExperimentResult(exResult.data) ? exResult.data : null

  const hero = useMemo(() => {
    const info = new Map((assets.data?.assets ?? []).map((a) => [a.symbol, a]))
    const stock = (s: string, weight?: number): QubitStock => ({ symbol: s, sector: info.get(s)?.sector, weight })
    const q = exRun ? solver(exRun, 'qaoa') : undefined
    const w = weightsOf(q)
    // The recorded example when it has a QAOA answer; otherwise the default selection, not yet measured.
    if (exRun && q && w) {
      const exact = solver(exRun, 'exact')
      const g = q.gap
      const same = !!g && (Math.abs(g.absolute) <= 1e-9 || (g.relative !== null && Math.abs(g.relative) < 5e-5))
      const t = q.portfolio?.test
      const ret = t && !t.insufficient ? t.total_return : null
      const ref = g ? (SOLVER_LABEL[g.reference_solver] ?? g.reference_solver) : 'classical'
      const recorded = example.data?.recorded_at
      return {
        source: 'example' as const,
        measured: true,
        stocks: exRun.encoded_symbols.map((s) => stock(s, w[s] ?? 0)),
        subtitle: `${exRun.encoded_symbols.length} NIFTY 50 stocks · Qiskit Aer`,
        stat:
          g && g.relative !== null
            ? { value: formatPct(g.relative, 2), label: exact?.status === 'ok' ? 'gap to best answer' : 'gap to best known', good: same }
            : undefined,
        verdict: same ? 'The same pick as the best classical method.' : g?.relative != null ? `Within ${formatPct(g.relative, 1)} of ${ref}.` : undefined,
        caption: (
          <>
            <span>A real run stored on this server{recorded ? `, ${formatDateTime(recorded)}` : ''}.</span>
            {ret !== null ? (
              <span>
                Later return <span className={`font-semibold tabular ${ret < 0 ? 'text-danger' : 'text-teal'}`}>{formatPct(ret, 1, { sign: true })}</span>
              </span>
            ) : null}
          </>
        ),
      }
    }
    const symbols = presets.data?.default_config.symbols ?? []
    return {
      source: 'selection' as const,
      measured: false,
      stocks: symbols.map((s) => stock(s)),
      subtitle: symbols.length ? `${symbols.length} NIFTY 50 stocks` : undefined,
      stat: undefined,
      verdict: undefined,
      caption: undefined,
    }
  }, [assets.data, presets.data, exRun, example.data])

  const L = liveLimits(caps.data)
  const families = guardrailFamilies(L)
  const active = data.data?.active
  // Today's NIFTY 50 candles on the hero card; nothing at all when there are no real candles to show.
  const market = useLiveCandles('^NSEI', '15m', 'NIFTY 50')

  return (
    <>
      <section className="relative overflow-hidden border-b border-line bg-gradient-to-b from-mist to-canvas">
        <div className="relative mx-auto grid max-w-[1320px] grid-cols-1 items-center gap-10 px-4 pt-10 pb-14 sm:px-6 lg:pt-14 lg:pb-16 xl:grid-cols-2 xl:gap-14">
          <div className="max-w-xl">
            <Badge tone="cobalt" icon={<span className="h-1.5 w-1.5 rounded-full bg-cobalt" aria-hidden="true" />}>
              NIFTY 50 · Quantum versus classical
            </Badge>
            <h1 className="mt-4 text-[2.35rem] leading-[1.06] font-bold tracking-[-0.03em] text-ink sm:text-5xl lg:text-[3.4rem]">
              Quantum stock picks you can check.
            </h1>
            <p className="mt-5 max-w-lg text-[1.1rem] leading-relaxed text-ink-2">
              Pick NIFTY 50 stocks. A quantum circuit chooses what to buy. Classical methods check it.
            </p>
            <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
              <ButtonLink to="/dashboard" size="lg" iconRight={<IconArrowRight />} data-testid="cta-dashboard">
                Open dashboard
              </ButtonLink>
              {exampleId ? (
                <ButtonLink to={`/runs/${exampleId}`} size="lg" variant="secondary" data-testid="cta-example">
                  See a finished example
                </ButtonLink>
              ) : null}
            </div>
            <ul className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted">
              {['Real NSE closing prices', CLOUD ? 'Runs on a simulator' : 'Runs on your own computer', 'Not investment advice'].map((t) => (
                <li key={t} className="flex items-center gap-1.5">
                  <IconCheck size={15} className="text-teal" />
                  {t}
                </li>
              ))}
            </ul>
          </div>
          <QubitCoins
            className="w-full max-w-xl xl:max-w-[34rem] xl:justify-self-end"
            source={hero.source}
            measured={hero.measured}
            stocks={hero.stocks}
            title="Quantum pick"
            subtitle={hero.subtitle}
            stat={hero.stat}
            verdict={hero.verdict}
            caption={hero.caption}
            market={market.state === 'none' ? undefined : <MarketGlance market={market} interval="15m" height={208} maxBody={17} />}
          />
        </div>
      </section>

      <section className="border-b border-line bg-canvas" aria-label="At a glance">
        <dl className="mx-auto grid max-w-[1320px] grid-cols-2 md:grid-cols-4">
          {[
            { v: active ? formatInt(active.n_symbols) : '50', l: 'NIFTY 50 stocks', h: active ? `Prices to ${formatDate(active.last_date)}` : 'Daily NSE closes' },
            { v: L.qubits, l: 'Qubits on the simulator', h: `${L.noiseQubits} with hardware noise` },
            { v: '5', l: 'Methods compared', h: 'QAOA and 4 classical solvers' },
            { v: String(families.length), l: 'Kinds of guardrails', h: 'Checked on every run' },
          ].map((s, i) => (
            <div key={s.l} className={`px-4 py-4 sm:px-6 sm:py-5 ${i % 2 ? 'border-l border-line' : ''} ${i > 1 ? 'border-t border-line md:border-t-0' : ''} ${i === 2 ? 'md:border-l' : ''}`}>
              <dt className="text-sm text-muted">{s.l}</dt>
              <dd className="mt-0.5 text-2xl font-semibold tracking-tight text-ink tabular sm:text-3xl">{s.v}</dd>
              <dd className="mt-0.5 text-xs text-muted">{s.h}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="mx-auto max-w-[1320px] px-4 pt-10 sm:px-6 sm:pt-12" aria-labelledby="how">
        <p className="eyebrow">How it works</p>
        <h2 id="how" className="mt-1 scroll-mt-24 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          Four steps, no maths needed
        </h2>
        <ol className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-4">
          {STEPS.map((s, i) => (
            <li key={s.title} className="rounded-xl border border-line bg-canvas p-4 shadow-card">
              {/* The step number sits beside its title rather than on a line of its own. */}
              <div className="flex items-center gap-2.5">
                <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-cobalt text-sm font-semibold text-white tabular">{i + 1}</span>
                <h3 className="text-base font-semibold text-ink">{s.title}</h3>
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{s.body}</p>
            </li>
          ))}
        </ol>
        {/* The same steps once more, in words a child can follow. */}
        <div className="mt-4 rounded-xl border border-cobalt-100 bg-cobalt-50/50 p-4 sm:p-5" data-testid="kid-explainer">
          <p className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-cobalt-800 uppercase">
            <IconHelp size={14} />
            Explained simply: picking a team with pocket money
          </p>
          <ol className="mt-3 grid grid-cols-1 gap-x-5 gap-y-3.5 sm:grid-cols-2 lg:grid-cols-5">
            {SIMPLE.map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex gap-3 sm:last:col-span-2 lg:flex-col lg:gap-2 lg:last:col-span-1">
                <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-canvas text-cobalt shadow-card">
                  <Icon size={16} />
                </span>
                {/* Title and sentence run on in one paragraph on a phone, and stack in a column on a wide screen. */}
                <div className="min-w-0 text-sm leading-snug">
                  <h3 className="inline font-semibold text-ink lg:block">
                    {title}
                    <span className="lg:hidden">.</span>
                  </h3>{' '}
                  <p className="inline text-ink-2 lg:mt-0.5 lg:block">{body}</p>
                </div>
              </li>
            ))}
          </ol>
          <p className="mt-3.5 text-xs leading-relaxed text-muted">
            The coins are pretend: a normal computer acts out what a quantum one would do (a simulator). It uses real past prices, and it is
            a learning tool, not advice on what to buy.
          </p>
        </div>
      </section>

      <section id="example" className="mx-auto max-w-[1320px] scroll-mt-24 px-4 pt-10 sm:px-6 sm:pt-12" aria-labelledby="example-h">
        <RunSummaryCard
          headingId="example-h"
          eyebrow="Recorded example"
          title={example.data?.label ?? 'A completed run, replayed from disk'}
          description={example.data?.recorded_at ? `Recorded ${formatDateTime(example.data.recorded_at)}. A real run, not advice.` : 'A real run, shown as recorded.'}
          badge={<Badge tone="cobalt">Recorded run</Badge>}
          loading={example.isPending || (!!exampleId && exResult.isPending)}
          error={example.error ?? exResult.error}
          onRetry={() => {
            void example.refetch()
            if (exampleId) void exResult.refetch()
          }}
          runId={exampleId}
          result={exRun}
          emptyTitle="No recorded example yet"
          emptyBody={example.data?.detail ?? 'Run the Quick preset to record one.'}
          testId="example-summary"
        />
      </section>

      <section id="guardrails" className="mx-auto max-w-[1320px] scroll-mt-24 px-4 pt-10 sm:px-6 sm:pt-12" aria-labelledby="guardrails-h">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl">
            <p className="eyebrow">Guardrails</p>
            <h2 id="guardrails-h" className="mt-1 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              Built-in safety checks
            </h2>
            <p className="mt-1 text-muted">Checked on every run.</p>
          </div>
          <ButtonLink to="/guardrails" variant="secondary" iconRight={<IconArrowRight />}>
            See every guardrail
          </ButtonLink>
        </div>
        <ul className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
          {families.map((f) => {
            const Icon = FAMILY_ICON[f.id] ?? IconShield
            return (
              <li key={f.id} className="flex gap-3 rounded-xl border border-line bg-canvas p-4 shadow-card">
                <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-cobalt-50 text-cobalt">
                  <Icon size={17} />
                </span>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-ink">{f.title}</h3>
                  <p className="mt-0.5 text-sm leading-relaxed text-muted">{f.short}</p>
                </div>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="mx-auto max-w-[1320px] px-4 pt-10 sm:px-6 sm:pt-12">
        <div className="relative overflow-hidden rounded-xl bg-ink px-5 py-7 text-white sm:px-10 sm:py-8">
          <div className="pointer-events-none absolute -top-16 -right-10 h-56 w-56 rounded-full bg-cobalt/40 blur-3xl" aria-hidden="true" />
          <div className="pointer-events-none absolute -bottom-20 left-10 h-48 w-48 rounded-full bg-teal/30 blur-3xl" aria-hidden="true" />
          <div className="relative flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
            <div className="max-w-xl">
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Ready to try it?</h2>
              <p className="mt-2 text-white/75">Keep the Quick preset and press Run.</p>
            </div>
            <ButtonLink to="/dashboard" size="lg" variant="secondary" iconRight={<IconArrowRight />} className="shrink-0">
              Open dashboard
            </ButtonLink>
          </div>
        </div>
      </section>
    </>
  )
}
