import { useMemo } from 'react'
import { isExperimentResult } from '../api/client'
import { useAssets, useCapabilities, useDataStatus, useExample, usePresets, useRunResult } from '../api/hooks'
import { QuantumSphere, type QubitStock } from '../components/hero/QuantumSphere'
import { Badge, ButtonLink, IconArrowRight, IconCheck, IconShield } from '../components/ui'
import { DASH, SOLVER_LABEL, formatDate, formatDateTime, formatInt, formatPct } from '../lib/format'
import { guardrailFamilies, liveLimits } from '../lib/guardrails'
import { solver, weightsOf } from '../lib/result'
import { FAMILY_ICON } from './shared/guardIcons'
import { RunSummaryCard } from './shared/RunSummaryCard'

const STEPS = [
  {
    title: 'Pick stocks and rules',
    body: 'Choose some of the 50 NIFTY stocks, then say how many to hold, how much risk is fine, your budget and how much to allow per sector.',
  },
  {
    title: 'Check it fits',
    body: 'Before anything runs, a live check shows how many qubits the problem needs and explains any rule that cannot be met.',
  },
  {
    title: 'Quantum and classical solve it',
    body: 'A QAOA quantum circuit (on a simulator) and four classical methods solve exactly the same problem, so the comparison is fair.',
  },
  {
    title: 'Read the answer',
    body: 'See what each method bought, in rupees, how close QAOA came to the best answer and how the picks did on later prices. Download a report.',
  },
]

function HeroStat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'gain' | 'loss' }) {
  return (
    <div className="min-w-0 px-3 py-3 sm:px-4">
      <dt className="text-[0.7rem] leading-tight font-medium text-muted sm:text-xs">{label}</dt>
      <dd className={`mt-1 text-base font-semibold tabular sm:text-lg ${tone === 'loss' ? 'text-danger' : tone === 'gain' ? 'text-teal' : 'text-ink'}`}>{value}</dd>
      {hint ? <dd className="mt-0.5 truncate text-[0.7rem] text-muted sm:text-xs">{hint}</dd> : null}
    </div>
  )
}

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
      return {
        source: 'example' as const,
        measured: true,
        stocks: exRun.encoded_symbols.map((s) => stock(s, w[s] ?? 0)),
        qubits: exRun.qaoa?.circuit.n_qubits ?? exRun.qubo.n_variables,
        footer: (
          <dl className="grid grid-cols-3 divide-x divide-line">
            <HeroStat
              label={exact?.status === 'ok' ? 'Gap to the best possible' : 'Gap to the best known'}
              value={g ? (g.relative !== null ? formatPct(g.relative, 2) : DASH) : DASH}
              hint={same ? 'a perfect match' : g ? `vs ${SOLVER_LABEL[g.reference_solver] ?? g.reference_solver}` : undefined}
              tone={same ? 'gain' : undefined}
            />
            <HeroStat
              label="Return on later prices"
              value={formatPct(ret, 1, { sign: true })}
              hint={t ? `to ${formatDate(t.end)}` : undefined}
              tone={ret === null ? undefined : ret < 0 ? 'loss' : 'gain'}
            />
            <HeroStat label="Ran on" value="Qiskit Aer" hint={`depth-${exRun.config.qaoa.depth} QAOA simulator`} />
          </dl>
        ),
        caption: `A real run stored on this server${example.data?.recorded_at ? ` (${formatDateTime(example.data.recorded_at)})` : ''}, not a mock-up. Each stock is a qubit: measuring it gives 1 (buy) or 0 (skip).`,
      }
    }
    const symbols = presets.data?.default_config.symbols ?? []
    return {
      source: 'selection' as const,
      measured: false,
      stocks: symbols.map((s) => stock(s)),
      qubits: symbols.length,
      footer: <p className="px-4 py-3 text-sm text-muted sm:px-5">No run recorded yet. Open the dashboard and run the Quick preset to see which qubits read 1.</p>,
      caption: 'Each stock is a qubit. Measuring it gives 1 (buy) or 0 (skip).',
    }
  }, [assets.data, presets.data, exRun, example.data])

  const L = liveLimits(caps.data)
  const families = guardrailFamilies(L)
  const active = data.data?.active

  return (
    <>
      <section className="relative overflow-hidden border-b border-line bg-gradient-to-b from-mist to-canvas">
        <div className="grid-paper pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_bottom,black,transparent_85%)]" aria-hidden="true" />
        <div className="relative mx-auto grid max-w-[1320px] grid-cols-1 items-center gap-10 px-4 pt-10 pb-14 sm:px-6 lg:pt-14 lg:pb-16 xl:grid-cols-2 xl:gap-14">
          <div className="max-w-xl">
            <Badge tone="cobalt" icon={<span className="h-1.5 w-1.5 rounded-full bg-cobalt" aria-hidden="true" />}>
              NIFTY 50 · Quantum versus classical
            </Badge>
            <h1 className="mt-4 text-[2.35rem] leading-[1.06] font-bold tracking-[-0.03em] text-ink sm:text-5xl lg:text-[3.4rem]">
              Quantum stock picks you can check.
            </h1>
            <p className="mt-5 text-[1.05rem] leading-relaxed text-ink-2">
              Choose a few of India&apos;s 50 largest stocks and set simple rules. QuantFolio turns the choice into a quantum circuit, measures which stocks to
              buy, then checks the answer against proven classical methods and against later prices it never saw.
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
              {['Real NSE closing prices', 'Runs on your own computer', 'Not investment advice'].map((t) => (
                <li key={t} className="flex items-center gap-1.5">
                  <IconCheck size={15} className="text-teal" />
                  {t}
                </li>
              ))}
            </ul>
          </div>
          <QuantumSphere
            className="w-full xl:max-w-[35rem] xl:justify-self-end"
            source={hero.source}
            measured={hero.measured}
            stocks={hero.stocks}
            qubits={hero.qubits}
            footer={hero.footer}
            caption={hero.caption}
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
            <div key={s.l} className={`px-4 py-6 sm:px-6 ${i % 2 ? 'border-l border-line' : ''} ${i > 1 ? 'border-t border-line md:border-t-0' : ''} ${i === 2 ? 'md:border-l' : ''}`}>
              <dt className="text-sm text-muted">{s.l}</dt>
              <dd className="mt-1 text-3xl font-semibold tracking-tight text-ink tabular">{s.v}</dd>
              <dd className="mt-1 text-xs text-muted">{s.h}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="mx-auto max-w-[1320px] px-4 pt-16 sm:px-6" aria-labelledby="how">
        <p className="eyebrow">How it works</p>
        <h2 id="how" className="mt-2 scroll-mt-24 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          Four steps, no maths needed
        </h2>
        <ol className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {STEPS.map((s, i) => (
            <li key={s.title} className="rounded-xl border border-line bg-canvas p-5 shadow-card">
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-cobalt text-sm font-semibold text-white tabular">{i + 1}</span>
              <h3 className="mt-3 text-base font-semibold text-ink">{s.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section id="example" className="mx-auto max-w-[1320px] scroll-mt-24 px-4 pt-16 sm:px-6" aria-labelledby="example-h">
        <RunSummaryCard
          headingId="example-h"
          eyebrow="Recorded example"
          title={example.data?.label ?? 'A completed run, replayed from disk'}
          description={
            example.data?.recorded_at
              ? `Recorded ${formatDateTime(example.data.recorded_at)} on this server and shown as stored. It is a real run of the pipeline, not live market advice.`
              : 'A completed run stored on this server, shown exactly as recorded.'
          }
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
          emptyBody={example.data?.detail ?? 'Once a run has been recorded as the example it appears here with its real numbers.'}
          testId="example-summary"
        />
      </section>

      <section id="guardrails" className="mx-auto max-w-[1320px] scroll-mt-24 px-4 pt-16 sm:px-6" aria-labelledby="guardrails-h">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl">
            <p className="eyebrow">Guardrails</p>
            <h2 id="guardrails-h" className="mt-2 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              Built-in safety checks
            </h2>
            <p className="mt-2 text-muted">Automatic checks stop runs that cannot work, keep the quantum-versus-classical comparison fair and protect your data.</p>
          </div>
          <ButtonLink to="/guardrails" variant="secondary" iconRight={<IconArrowRight />}>
            See every guardrail
          </ButtonLink>
        </div>
        <ul className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {families.map((f) => {
            const Icon = FAMILY_ICON[f.id] ?? IconShield
            return (
              <li key={f.id} className="flex gap-4 rounded-xl border border-line bg-canvas p-5 shadow-card">
                <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-cobalt-50 text-cobalt">
                  <Icon size={20} />
                </span>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-ink">{f.title}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{f.short}</p>
                </div>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="mx-auto max-w-[1320px] px-4 pt-16 sm:px-6">
        <div className="relative overflow-hidden rounded-xl bg-ink px-6 py-10 text-white sm:px-10">
          <div className="pointer-events-none absolute -top-16 -right-10 h-56 w-56 rounded-full bg-cobalt/40 blur-3xl" aria-hidden="true" />
          <div className="pointer-events-none absolute -bottom-20 left-10 h-48 w-48 rounded-full bg-teal/30 blur-3xl" aria-hidden="true" />
          <div className="relative flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="max-w-xl">
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Ready to try it?</h2>
              <p className="mt-2 text-white/75">Open the dashboard, keep the Quick preset and press Run. The checks guide you the whole way.</p>
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
