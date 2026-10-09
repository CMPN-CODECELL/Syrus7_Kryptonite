import { useEffect, useMemo } from 'react'
import { Link } from 'react-router'
import type { RunResult } from '../api/contract.gen'
import { isExperimentResult } from '../api/client'
import { useAssets, useExample, usePresets, useRunResult } from '../api/hooks'
import { HeroStage } from '../components/hero/HeroStage'
import { Badge, ButtonLink, Card, CardHeader, EmptyState, ErrorState, IconArrowRight, IconClock, IconLayers, IconSpark, LoadingBlock, Metric, StatusChip } from '../components/ui'
import { DASH, SOLVER_LABEL, formatDateTime, formatInrCompact, formatNum, formatPct } from '../lib/format'
import { bestClassical, solver, weightsOf } from '../lib/result'
import { useLab } from '../state/lab'

const STEPS = [
  {
    title: 'Choose the universe',
    body: 'Pick from the 50 NIFTY constituents with their sectors and data coverage, then set risk appetite, holdings count, sector caps, lots and costs.',
  },
  {
    title: 'Check it fits',
    body: 'A live preflight shows decision and slack bits, total qubits, circuit depth, shots and evaluation budget, and explains any conflict before you run.',
  },
  {
    title: 'Run QAOA and the baselines',
    body: 'A QAOA circuit is optimised on a simulator while exact, relaxation, annealing and greedy solvers answer the same constrained problem.',
  },
  {
    title: 'Compare, then export',
    body: 'Holdings in rupees, costs, feasibility residuals, the frontier, held-out test returns and runtimes, with HTML, JSON and CSV downloads.',
  },
]

export default function Overview() {
  const assets = useAssets()
  const presets = usePresets()
  const { config, seed, hoverSymbol } = useLab()
  const example = useExample()
  const exampleId = example.data?.run_id ?? undefined
  const exResult = useRunResult(exampleId, !!exampleId)

  useEffect(() => {
    if (presets.data) seed(presets.data.default_config)
  }, [presets.data, seed])

  const heroAssets = useMemo(() => (assets.data?.assets ?? []).map((a) => ({ symbol: a.symbol, sector: a.sector })), [assets.data])
  const selected = config?.symbols ?? []

  return (
    <>
      <section className="relative overflow-hidden border-b border-line bg-gradient-to-b from-mist to-canvas">
        <div className="grid-paper pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_bottom,black,transparent_85%)]" aria-hidden="true" />
        <div className="relative mx-auto grid grid-cols-1 max-w-[1320px] items-center gap-8 px-4 pt-10 pb-12 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-6 lg:pt-12 lg:pb-14">
          <div className="max-w-xl">
            <p className="eyebrow">NIFTY 50 · QAOA versus classical solvers</p>
            <h1 className="mt-3 font-display text-[2.35rem] leading-[1.06] font-semibold tracking-tight text-ink sm:text-5xl lg:text-[3.5rem]">
              Build a portfolio. Examine every trade-off.
            </h1>
            <p className="mt-5 text-[1.05rem] leading-relaxed text-ink-2">
              Select a constrained portfolio from the NIFTY 50: a fixed number of holdings, sector caps, lot sizes and trading costs. QuantFolio encodes the
              problem for a QAOA circuit on a simulator and checks every answer against exact and classical baselines, with held-out test results in plain
              view.
            </p>
            <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
              <ButtonLink to="/lab" size="lg" iconRight={<IconArrowRight />} data-testid="cta-optimiser">
                Open the optimiser
              </ButtonLink>
              {exampleId ? (
                <ButtonLink to={`/runs/${exampleId}`} size="lg" variant="secondary" data-testid="cta-example">
                  View a completed example
                </ButtonLink>
              ) : (
                <span className="inline-flex min-h-12 items-center text-sm text-muted">{example.isPending ? 'Looking for a recorded example…' : 'No recorded example on this server yet.'}</span>
              )}
            </div>
            <p className="mt-4 text-xs text-muted">Research demonstration — not investment advice. Simulated circuits; no live trading.</p>
          </div>
          <HeroStage className="aspect-[4/3] w-full" assets={heroAssets} selected={selected} hover={hoverSymbol} />
        </div>
      </section>

      <section className="mx-auto max-w-[1320px] px-4 pt-14 sm:px-6" aria-labelledby="how">
        <p className="eyebrow">Workflow</p>
        <h2 id="how" className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          From a universe to an auditable answer
        </h2>
        <ol className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <li key={s.title} className="rounded-lg border border-line bg-canvas p-5 shadow-card">
              <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-cobalt-50 text-sm font-semibold text-cobalt-800 tabular">{i + 1}</span>
              <h3 className="mt-3 text-base font-semibold text-ink">{s.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="mx-auto max-w-[1320px] px-4 pt-14 sm:px-6" aria-labelledby="example-h">
        <ExampleSummary
          loading={example.isPending || (!!exampleId && exResult.isPending)}
          error={example.error ?? exResult.error}
          onRetry={() => {
            void example.refetch()
            if (exampleId) void exResult.refetch()
          }}
          runId={exampleId}
          label={example.data?.label ?? null}
          recordedAt={example.data?.recorded_at ?? null}
          detail={example.data?.detail ?? null}
          result={exResult.data && !isExperimentResult(exResult.data) ? exResult.data : null}
        />
      </section>

      <section className="mx-auto max-w-[1320px] px-4 pt-14 sm:px-6" aria-labelledby="ws-h">
        <h2 id="ws-h" className="font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          Workspace
        </h2>
        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
          <EntryCard to="/lab" icon={<IconSpark size={18} />} title="Portfolio Lab" body="Build one constrained portfolio, preflight it and run QAOA with every baseline." />
          <EntryCard to="/experiments" icon={<IconLayers size={18} />} title="Experiment Lab" body="Sweep depth, optimiser, initialisation, universe size and seeds with a visible budget." />
          <EntryCard to="/history" icon={<IconClock size={18} />} title="History and reports" body="Reopen completed, failed, cancelled and timed-out runs; download their reports." />
        </div>
      </section>
    </>
  )
}

function EntryCard({ to, icon, title, body }: { to: string; icon: React.ReactNode; title: string; body: string }) {
  return (
    <Link to={to} className="group flex min-h-11 flex-col rounded-lg border border-line bg-canvas p-5 shadow-card transition-colors hover:border-cobalt-200 hover:bg-cobalt-50/40">
      <span className="flex items-center gap-2 text-cobalt">
        {icon}
        <span className="text-base font-semibold text-ink">{title}</span>
        <IconArrowRight className="ml-auto text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-cobalt" />
      </span>
      <span className="mt-2 text-sm leading-relaxed text-muted">{body}</span>
    </Link>
  )
}

function ExampleSummary({
  loading,
  error,
  onRetry,
  runId,
  label,
  recordedAt,
  detail,
  result,
}: {
  loading: boolean
  error: unknown
  onRetry: () => void
  runId: string | undefined
  label: string | null
  recordedAt: string | null
  detail: string | null
  result: RunResult | null
}) {
  const heading = (
    <CardHeader
      id="example-h"
      eyebrow="Recorded example"
      title={label ?? 'A completed run, replayed from disk'}
      description={
        recordedAt
          ? `Recorded ${formatDateTime(recordedAt)} on this server and served as stored. It is a real run of the pipeline, not live market advice.`
          : 'A completed run stored on this server, shown exactly as recorded.'
      }
      aside={runId ? <Badge tone="cobalt">Recorded run</Badge> : null}
    />
  )
  if (loading)
    return (
      <Card>
        {heading}
        <LoadingBlock label="Loading the recorded example" rows={3} />
      </Card>
    )
  if (error)
    return (
      <Card>
        {heading}
        <ErrorState error={error} title="The recorded example could not be loaded" onRetry={onRetry} />
      </Card>
    )
  if (!runId || !result)
    return (
      <Card>
        {heading}
        <EmptyState title="No recorded example yet" action={<ButtonLink to="/lab">Run the Quick preset</ButtonLink>}>
          {detail ?? 'Once a run has been recorded as the example it appears here with its real numbers.'}
        </EmptyState>
      </Card>
    )

  const q = solver(result, 'qaoa')
  const ref = bestClassical(result)
  const exact = solver(result, 'exact')
  const qp = q?.portfolio
  const w = weightsOf(q)
  return (
    <Card data-testid="example-summary">
      {heading}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3 lg:grid-cols-6">
        <Metric label="Assets encoded" value={result.encoded_symbols.length} />
        <Metric label="Qubits" value={result.qaoa?.circuit.n_qubits ?? result.qubo.n_variables} hint={`depth p=${result.qaoa?.depth ?? DASH}`} />
        <Metric label="QAOA outcome" value={q ? <StatusChip status={q.status} /> : DASH} />
        <Metric label="QAOA objective" value={formatNum(qp?.objective, 5)} hint="lower is better" />
        <Metric
          label={exact?.status === 'ok' ? 'Gap to exact optimum' : 'Gap to best known'}
          value={q?.gap ? (q.gap.relative !== null ? formatPct(q.gap.relative, 2) : formatNum(q.gap.absolute, 5)) : DASH}
          hint={q?.gap ? `vs ${SOLVER_LABEL[q.gap.reference_solver] ?? q.gap.reference_solver}` : ref ? `best classical: ${SOLVER_LABEL[ref.solver]}` : undefined}
        />
        <Metric
          label="Held-out test return"
          value={formatPct(qp?.test?.total_return, 2, { sign: true })}
          tone={(qp?.test?.total_return ?? 0) < 0 ? 'loss' : undefined}
          hint={qp?.test ? `${qp.test.start ?? ''} to ${qp.test.end ?? ''}` : undefined}
        />
      </dl>
      {w ? (
        <div className="mt-6">
          <p className="text-sm font-medium text-ink-2">QAOA portfolio ({formatInrCompact(result.config.budget_inr)} budget)</p>
          <ul className="mt-2 flex flex-wrap gap-2" aria-label="QAOA holdings">
            {Object.entries(w)
              .sort((a, b) => b[1] - a[1])
              .map(([s, v]) => (
                <li key={s}>
                  <Badge tone="teal">
                    <span className="font-semibold">{s}</span> {formatPct(v, 1)}
                  </Badge>
                </li>
              ))}
          </ul>
        </div>
      ) : null}
      <p className="mt-5 text-xs leading-relaxed text-muted">{result.dataset.survivorship_note}</p>
      <div className="mt-5">
        <ButtonLink to={`/runs/${runId}`} variant="secondary" iconRight={<IconArrowRight />}>
          Open the full result
        </ButtonLink>
      </div>
    </Card>
  )
}
