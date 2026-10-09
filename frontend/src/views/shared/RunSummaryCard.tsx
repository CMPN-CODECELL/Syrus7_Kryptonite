// A finished run in brief: plain-language sentences, six key numbers and the QAOA holdings.
// Used for the recorded example on the landing page and for the latest result on the dashboard.

import type { ReactNode } from 'react'
import type { RunResult } from '../../api/contract.gen'
import { Badge, ButtonLink, Card, CardHeader, EmptyState, ErrorState, IconArrowRight, InSimpleWords, LoadingBlock, Metric, StatusChip } from '../../components/ui'
import { DASH, SOLVER_LABEL, formatDate, formatInrCompact, formatNum, formatPct } from '../../lib/format'
import { GLOSSARY } from '../../lib/glossary'
import { plainRunSummary } from '../../lib/plain'
import { bestClassical, solver, weightsOf } from '../../lib/result'

export interface RunSummaryCardProps {
  headingId: string
  eyebrow: string
  title: string
  description?: ReactNode
  badge?: ReactNode
  loading: boolean
  error: unknown
  onRetry: () => void
  runId: string | undefined
  result: RunResult | null
  /** Shown when there is no run to summarise. */
  emptyTitle: string
  emptyBody: ReactNode
  /** Three metrics per row instead of six, for a narrower column. */
  dense?: boolean
  testId?: string
}

export function RunSummaryCard({
  headingId,
  eyebrow,
  title,
  description,
  badge,
  loading,
  error,
  onRetry,
  runId,
  result,
  emptyTitle,
  emptyBody,
  dense = false,
  testId,
}: RunSummaryCardProps) {
  const heading = <CardHeader id={headingId} eyebrow={eyebrow} title={title} description={description} aside={runId ? badge : null} />
  if (loading)
    return (
      <Card>
        {heading}
        <LoadingBlock label="Loading the result" rows={3} />
      </Card>
    )
  if (error)
    return (
      <Card>
        {heading}
        <ErrorState error={error} title="The result could not be loaded" onRetry={onRetry} />
      </Card>
    )
  if (!runId || !result)
    return (
      <Card>
        {heading}
        <EmptyState title={emptyTitle} action={<ButtonLink to="/lab">Run the Quick preset</ButtonLink>}>
          {emptyBody}
        </EmptyState>
      </Card>
    )

  const q = solver(result, 'qaoa')
  const ref = bestClassical(result)
  const exact = solver(result, 'exact')
  const qp = q?.portfolio
  const w = weightsOf(q)
  return (
    <Card data-testid={testId}>
      {heading}
      <InSimpleWords className="mb-5">
        <ul className="space-y-1">
          {plainRunSummary(result).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </InSimpleWords>
      <dl className={`grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3 ${dense ? '' : 'xl:grid-cols-6'}`}>
        <Metric label="Stocks considered" value={result.encoded_symbols.length} help="The stocks this problem was built from. Each one is a qubit in the circuit." />
        <Metric label="Qubits" value={result.qaoa?.circuit.n_qubits ?? result.qubo.n_variables} hint={`depth p=${result.qaoa?.depth ?? DASH}`} help={GLOSSARY.qubits} />
        <Metric label="QAOA outcome" value={q ? <StatusChip status={q.status} /> : DASH} help="Whether the circuit produced a portfolio that follows every rule." />
        <Metric label="QAOA score" value={formatNum(qp?.objective, 5)} hint="lower is better" help={GLOSSARY.objective} />
        <Metric
          help={GLOSSARY.gap}
          label={exact?.status === 'ok' ? 'Gap to exact optimum' : 'Gap to best known'}
          value={q?.gap ? (q.gap.relative !== null ? formatPct(q.gap.relative, 2) : formatNum(q.gap.absolute, 5)) : DASH}
          hint={q?.gap ? `vs ${SOLVER_LABEL[q.gap.reference_solver] ?? q.gap.reference_solver}` : ref ? `best classical: ${SOLVER_LABEL[ref.solver]}` : undefined}
        />
        <Metric
          label="Return on later prices"
          help={GLOSSARY.testReturn}
          value={formatPct(qp?.test?.total_return, 2, { sign: true })}
          tone={(qp?.test?.total_return ?? 0) < 0 ? 'loss' : undefined}
          hint={qp?.test ? `${formatDate(qp.test.start)} to ${formatDate(qp.test.end)}` : undefined}
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
