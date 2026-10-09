// The first page inside the app: what data is loaded, what has run, the latest answer and the market at a glance.

import type { ComponentType, ReactNode } from 'react'
import { Link } from 'react-router'
import { isExperimentResult } from '../api/client'
import type { AssetInfo } from '../api/contract.gen'
import { useAssets, useCapabilities, useDataStatus, useExample, useRunResult, useRuns } from '../api/hooks'
import {
  Badge,
  ButtonLink,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  IconArrowRight,
  IconCheck,
  IconClock,
  IconCpu,
  IconDatabase,
  IconLayers,
  IconShield,
  IconSpark,
  LoadingBlock,
  Sparkline,
  StatusChip,
} from '../components/ui'
import { DASH, formatDate, formatDateTime, formatInr, formatInt, formatPct, formatRelative } from '../lib/format'
import { guardrailFamilies, liveLimits } from '../lib/guardrails'
import { FAMILY_ICON } from './shared/guardIcons'
import { RunSummaryCard } from './shared/RunSummaryCard'

type IconType = ComponentType<{ size?: number; className?: string }>

function Kpi({
  label,
  icon: Icon,
  value,
  hint,
  to,
  testId,
  className = '',
}: {
  label: string
  icon: IconType
  value: ReactNode
  hint?: ReactNode
  to?: string
  testId?: string
  className?: string
}) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted">{label}</p>
        <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-cobalt-50 text-cobalt">
          <Icon size={17} />
        </span>
      </div>
      <p className="mt-1 truncate text-2xl font-semibold tracking-tight text-ink tabular">{value}</p>
      {hint ? <p className="mt-1 truncate text-xs text-muted">{hint}</p> : null}
    </>
  )
  const cls = `block min-w-0 rounded-lg border border-line bg-canvas p-4 shadow-card ${className}`
  return to ? (
    <Link to={to} className={`${cls} transition-colors hover:border-cobalt-200 hover:bg-cobalt-50/30`} data-testid={testId}>
      {body}
    </Link>
  ) : (
    <div className={cls} data-testid={testId}>
      {body}
    </div>
  )
}

function Mover({ a }: { a: AssetInfo }) {
  const up = (a.change_1y ?? 0) >= 0
  return (
    <li className="flex items-center gap-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-ink">{a.symbol}</p>
        <p className="truncate text-xs text-muted" title={a.name}>
          {a.name}
        </p>
      </div>
      {a.trend.length > 1 ? <Sparkline values={a.trend} width={64} height={22} className="hidden shrink-0 sm:block" /> : null}
      <div className="w-24 shrink-0 text-right">
        <p className="text-sm font-medium text-ink tabular">{formatInr(a.last_price_inr, { paise: true })}</p>
        <p className={`text-xs font-medium tabular ${up ? 'text-teal' : 'text-danger'}`}>{formatPct(a.change_1y, 1, { sign: true })}</p>
      </div>
    </li>
  )
}

export default function Dashboard() {
  const assets = useAssets()
  const caps = useCapabilities()
  const data = useDataStatus()
  const example = useExample()
  const recent = useRuns({ limit: 6 })
  const succeeded = useRuns({ status: 'succeeded', limit: 1 })
  const latestOk = useRuns({ status: 'succeeded', kind: 'run', limit: 1 })

  // The latest finished run of your own; the recorded example until there is one.
  const ownId = latestOk.data?.items[0]?.run_id
  const exampleId = example.data?.run_id ?? undefined
  const shownId = latestOk.isPending ? undefined : (ownId ?? exampleId)
  const shown = useRunResult(shownId, !!shownId)
  const shownRun = shown.data && !isExperimentResult(shown.data) ? shown.data : null
  const isOwn = !!ownId && shownId === ownId && !latestOk.data?.items[0]?.is_example

  const active = data.data?.active
  const L = liveLimits(caps.data)
  const families = guardrailFamilies(L)
  const last = recent.data?.items[0]

  const priced = (assets.data?.assets ?? []).filter((a) => a.data_ok && a.change_1y !== null)
  const byChange = [...priced].sort((a, b) => (b.change_1y ?? 0) - (a.change_1y ?? 0))
  const gainers = byChange.slice(0, 5)
  const losers = byChange.slice(-5).reverse()
  const risers = priced.filter((a) => (a.change_1y ?? 0) > 0).length

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">Dashboard</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted sm:text-base">Your market data, runs and safety checks in one place. Start a new portfolio whenever you are ready.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ButtonLink to="/experiments" variant="secondary" icon={<IconLayers size={16} />}>
            Run an experiment
          </ButtonLink>
          <ButtonLink to="/lab" icon={<IconSpark size={16} />} data-testid="cta-optimiser">
            New optimisation
          </ButtonLink>
        </div>
      </div>

      <section aria-label="Key numbers" className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi
          label="Market data"
          icon={IconDatabase}
          value={active ? `${formatInt(active.n_symbols)} stocks` : DASH}
          hint={active ? (active.stale ? 'Prices are out of date' : `NSE closes to ${formatDate(active.last_date)}`) : 'Loading…'}
          testId="kpi-data"
        />
        <Kpi
          label="Your runs"
          icon={IconClock}
          value={recent.data ? formatInt(recent.data.total) : DASH}
          hint={succeeded.data ? `${formatInt(succeeded.data.total)} finished successfully` : 'Loading…'}
          to="/history"
          testId="kpi-runs"
        />
        <Kpi
          label="Latest run"
          icon={IconSpark}
          value={last ? `${last.total_qubits ?? last.n_symbols} qubits` : 'None yet'}
          hint={last ? `${last.n_symbols} stocks${last.depth ? ` · depth ${last.depth}` : ''} · ${formatRelative(last.created_at)}` : 'Run the Quick preset to start'}
          to={last ? `/runs/${last.run_id}` : '/lab'}
          testId="kpi-latest"
        />
        <Kpi label="Quantum simulator" icon={IconCpu} value="Qiskit Aer" hint={`Up to ${L.qubits} qubits (${L.noiseQubits} with noise)`} testId="kpi-simulator" />
        <Kpi
          label="Guardrails"
          icon={IconShield}
          value="All on"
          hint={`${families.length} kinds, checked every run`}
          to="/guardrails"
          testId="kpi-guardrails"
          className="sm:max-xl:col-span-2"
        />
      </section>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="min-w-0 xl:col-span-2 [&>*]:h-full">
          <RunSummaryCard
            headingId="latest-h"
            eyebrow={isOwn ? 'Latest result' : 'Recorded example'}
            title={isOwn ? (latestOk.data?.items[0]?.label ?? 'Your latest finished run') : (example.data?.label ?? 'A completed run')}
            description={
              isOwn
                ? `Finished ${formatDateTime(latestOk.data?.items[0]?.finished_at ?? latestOk.data?.items[0]?.created_at)}.`
                : 'You have not finished a run yet, so here is one recorded on this server.'
            }
            badge={isOwn ? <StatusChip status="succeeded" label="Finished" /> : <Badge tone="cobalt">Recorded run</Badge>}
            loading={latestOk.isPending || (!ownId && example.isPending) || (!!shownId && shown.isPending)}
            error={latestOk.error ?? shown.error}
            onRetry={() => {
              void latestOk.refetch()
              if (shownId) void shown.refetch()
            }}
            runId={shownId}
            result={shownRun}
            emptyTitle="No finished runs yet"
            emptyBody="Open the Portfolio Lab, keep the Quick preset and press Run. The result appears here."
            dense
            testId="latest-summary"
          />
        </div>

        <Card aria-labelledby="recent-h">
          <CardHeader
            id="recent-h"
            title="Recent runs"
            aside={
              <Link to="/history" className="inline-flex min-h-11 items-center gap-1 text-sm font-medium text-cobalt hover:underline pointer-fine:min-h-8">
                View all <IconArrowRight size={14} />
              </Link>
            }
          />
          {recent.isPending ? (
            <LoadingBlock label="Loading runs" rows={4} />
          ) : recent.isError ? (
            <ErrorState error={recent.error} title="Could not load your runs" onRetry={() => void recent.refetch()} />
          ) : !recent.data?.items.length ? (
            <EmptyState title="No runs yet" action={<ButtonLink to="/lab">Start a run</ButtonLink>}>
              Runs you start appear here.
            </EmptyState>
          ) : (
            <ul className="-mx-2 divide-y divide-line" data-testid="recent-runs">
              {recent.data.items.map((r) => (
                <li key={r.run_id}>
                  <Link to={`/runs/${r.run_id}`} className="flex items-center gap-3 rounded-md px-2 py-2.5 hover:bg-mist">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-ink">
                        {r.label ?? (r.kind === 'experiment' ? 'Experiment' : `${r.n_symbols} stocks${r.total_qubits ? ` · ${r.total_qubits} qubits` : ''}`)}
                      </p>
                      <p className="truncate text-xs text-muted">
                        {[r.depth ? `depth ${r.depth}` : null, r.optimizer, r.cached_from ? 'cached replay' : null, formatRelative(r.created_at)].filter(Boolean).join(' · ')}
                      </p>
                    </div>
                    <StatusChip status={r.status} className="shrink-0" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="xl:col-span-2" aria-labelledby="market-h">
          <CardHeader
            id="market-h"
            title="Market snapshot"
            description={
              priced.length
                ? `${risers} of ${priced.length} NIFTY 50 stocks are up over the last year. Prices are NSE closing prices${active ? ` to ${formatDate(active.last_date)}` : ''}.`
                : undefined
            }
            aside={
              <ButtonLink to="/lab" variant="secondary" size="sm">
                Pick stocks
              </ButtonLink>
            }
          />
          {assets.isPending ? (
            <LoadingBlock label="Loading prices" rows={4} />
          ) : assets.isError ? (
            <ErrorState error={assets.error} title="Could not load prices" onRetry={() => void assets.refetch()} />
          ) : (
            <div className="grid grid-cols-1 gap-x-8 gap-y-2 md:grid-cols-2" data-testid="market-snapshot">
              <div className="min-w-0">
                <h3 className="text-xs font-semibold tracking-wide text-teal uppercase">Up the most in a year</h3>
                <ul className="divide-y divide-line">
                  {gainers.map((a) => (
                    <Mover key={a.symbol} a={a} />
                  ))}
                </ul>
              </div>
              <div className="min-w-0">
                <h3 className="text-xs font-semibold tracking-wide text-danger uppercase">Down the most in a year</h3>
                <ul className="divide-y divide-line">
                  {losers.map((a) => (
                    <Mover key={a.symbol} a={a} />
                  ))}
                </ul>
              </div>
            </div>
          )}
        </Card>

        <Card aria-labelledby="guard-h">
          <CardHeader
            id="guard-h"
            title="Guardrails"
            description="Checks that run on their own, every time."
            aside={
              <Badge tone="teal" icon={<IconCheck size={13} />}>
                All on
              </Badge>
            }
          />
          <ul className="space-y-1">
            {families.map((f) => {
              const Icon = FAMILY_ICON[f.id] ?? IconShield
              return (
                <li key={f.id}>
                  <Link to={`/guardrails#${f.id}`} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-mist">
                    <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-mist-2 text-ink-2">
                      <Icon size={15} />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{f.title}</span>
                    <span className="text-xs text-muted tabular">{f.items.length}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
          <ButtonLink to="/guardrails" variant="secondary" size="sm" className="mt-4 w-full" iconRight={<IconArrowRight size={14} />}>
            See every guardrail
          </ButtonLink>
        </Card>
      </div>
    </div>
  )
}
