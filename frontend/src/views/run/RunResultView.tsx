import { useMemo, useState } from 'react'
import type { Holding, Residual, RunResult, SampleRow, SolverName, SolverResult } from '../../api/contract.gen'
import { ConvergenceChart, FrontierChart, HistogramChart, NoiseChart, SectorChart } from '../../components/charts/charts'
import { Badge, Card, CardHeader, Disclosure, EmptyState, IconAlert, IconCheck, IconX, InSimpleWords, Metric, Notice, Segmented, StatusChip, Table, Tabs } from '../../components/ui'
import type { Column } from '../../components/ui/Table'
import { DASH, SOLVER_LABEL, STATUS_LABEL, formatDate, formatDateTime, formatInr, formatInt, formatNum, formatPct } from '../../lib/format'
import { GLOSSARY } from '../../lib/glossary'
import { plainRunSummary } from '../../lib/plain'
import { bestClassical, decodeSelection, formatUnknown, gapReference, gapText, humanKey, numericEntries, solver, solverRuntime, sortedSolvers } from '../../lib/result'
import { ConfigDump, DatasetCard, DictTable, Downloads, KV, Literal, PeriodBlock, SoftwareCard } from './shared'

type TabId = 'summary' | 'compare' | 'qaoa' | 'provenance'

function secs(v: number | null): string {
  if (v === null) return DASH
  return v < 1 ? `${(v * 1000).toFixed(0)} ms` : `${v.toFixed(2)} s`
}

export function RunResultView({ result }: { result: RunResult }) {
  const [tab, setTab] = useState<TabId>('summary')
  const q = solver(result, 'qaoa')
  const ref = bestClassical(result)
  const ordered = useMemo(() => sortedSolvers(result), [result])

  return (
    <div className="grid grid-cols-1 gap-5" data-testid="run-result">
      <Notice tone="warning" title="Research demonstration — not investment advice">
        Simulated QAOA circuits and historical estimates; no live trading.
      </Notice>
      <Headline result={result} q={q} reference={ref} />
      {result.warnings.length ? (
        <Notice tone="warning" title={`${result.warnings.length} warning${result.warnings.length === 1 ? '' : 's'} from this run`}>
          <ul className="list-disc space-y-0.5 pl-5">
            {result.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-ink">Downloads</h2>
          <Downloads runId={result.run_id} counts={!!result.qaoa} />
        </div>
      </Card>
      <Tabs
        label="Result sections"
        value={tab}
        onChange={setTab}
        items={[
          { id: 'summary', label: 'Portfolio' },
          { id: 'compare', label: 'Comparison', badge: ordered.length },
          { id: 'qaoa', label: 'QAOA details' },
          { id: 'provenance', label: 'Data & provenance' },
        ]}
      >
        {(active, panel) => (
          <div {...panel} className="mt-5 focus-visible:outline-none">
            {active === 'summary' ? <SummaryTab result={result} /> : null}
            {active === 'compare' ? <CompareTab result={result} solvers={ordered} /> : null}
            {active === 'qaoa' ? <QaoaTab result={result} /> : null}
            {active === 'provenance' ? <ProvenanceTab result={result} /> : null}
          </div>
        )}
      </Tabs>
    </div>
  )
}

/* -------------------------------------------------------------------- headline */

function Headline({ result, q, reference }: { result: RunResult; q: SolverResult | undefined; reference: SolverResult | undefined }) {
  const qp = q?.portfolio
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold text-ink">QAOA outcome</h2>
        {q ? <StatusChip status={q.status} /> : <Badge>QAOA not run</Badge>}
      </div>
      {q?.status_detail ? <p className="mt-1 text-sm text-ink-2">{q.status_detail}</p> : null}
      <InSimpleWords className="mt-4">
        <ul className="space-y-1" data-testid="plain-summary">
          {plainRunSummary(result).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </InSimpleWords>
      <dl className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
        <Metric label="QAOA score" value={qp ? formatNum(qp.objective, 5) : DASH} hint="Lower is better: q·risk − return + costs" help={GLOSSARY.objective} />
        <Metric
          label="Gap to reference"
          help={GLOSSARY.gap}
          value={gapText(q?.gap ?? null)}
          hint={q?.gap ? gapReference(q.gap) : !reference ? 'No feasible classical reference' : qp ? 'This run did not report a gap' : 'No gap: QAOA had no feasible answer'}
        />
        <Metric
          label="Best classical score"
          value={reference?.portfolio ? formatNum(reference.portfolio.objective, 5) : DASH}
          hint={reference ? SOLVER_LABEL[reference.solver] : undefined}
          help={GLOSSARY.baselines}
        />
        <Metric
          label="Feasible share of final batch"
          help="Of the final batch of measurements, the share that gave a portfolio following every rule. Higher means the circuit learned the rules well."
          value={result.qaoa ? formatPct(result.qaoa.final_batch.feasible_probability, 1) : DASH}
          hint={result.qaoa ? `${formatInt(result.qaoa.final_batch.shots)} shots` : undefined}
        />
      </dl>
      {q?.status === 'no_feasible_sample' ? (
        <Notice tone="warning" className="mt-4" title="No feasible sample in the final batch">
          Every sampled bitstring broke at least one constraint, so QAOA has no portfolio to report. The classical baselines below still solved the same problem. Try a higher
          penalty scale, more evaluations or a smaller universe.
        </Notice>
      ) : null}
      {q?.status === 'infeasible' || result.solvers.every((s) => s.status === 'infeasible' || s.status === 'skipped' || s.status === 'unsupported') ? (
        <Notice tone="danger" className="mt-4" title="The constraints cannot all be met">
          {result.solvers.find((s) => s.status === 'infeasible')?.status_detail ?? 'No portfolio satisfies every constraint for these settings.'}
        </Notice>
      ) : null}
    </Card>
  )
}

/* --------------------------------------------------------------------- summary */

function SummaryTab({ result }: { result: RunResult }) {
  const withPortfolio = sortedSolvers(result).filter((s) => s.portfolio)
  const initial: SolverName = withPortfolio.find((s) => s.solver === 'qaoa')?.solver ?? withPortfolio[0]?.solver ?? 'qaoa'
  const [pick, setPick] = useState<SolverName>(initial)
  const s = solver(result, pick)
  if (!withPortfolio.length)
    return (
      <EmptyState title="No solver produced a feasible portfolio" icon={<IconAlert />}>
        See the Comparison tab for each solver's status and the reason it stopped.
      </EmptyState>
    )
  const p = s?.portfolio
  const caps = result.config.constraints.sector_caps
  const capMap = caps && caps.basis === 'weight' && p ? Object.fromEntries(Object.keys(p.sector_weights).map((sec) => [sec, caps.per_sector[sec] ?? caps.default_max ?? 1])) : null

  return (
    <div className="grid grid-cols-1 gap-5">
      {withPortfolio.length > 1 ? (
        <Segmented
          name="solver-pick"
          legend="Show the portfolio from"
          value={pick}
          onChange={setPick}
          options={withPortfolio.map((x) => ({ value: x.solver, label: SOLVER_LABEL[x.solver] ?? x.solver }))}
        />
      ) : null}
      {p && s ? (
        <>
          <Card>
            <CardHeader level={2} title={`${SOLVER_LABEL[s.solver]} portfolio`} aside={<StatusChip status={s.status} />} description={s.status_detail ?? undefined} />
            <dl className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6" data-testid="portfolio-metrics">
              <Metric
                label="Expected return"
                value={formatPct(p.expected_return, 2, { sign: true })}
                unit="p.a., gross"
                tone={p.expected_return < 0 ? 'loss' : undefined}
                help={GLOSSARY.expectedReturn}
              />
              <Metric
                label="Net of costs"
                value={formatPct(p.net_expected_return, 2, { sign: true })}
                unit="p.a."
                tone={p.net_expected_return < 0 ? 'loss' : undefined}
                help="Expected return after subtracting trading costs."
              />
              <Metric label="Volatility" value={formatPct(p.volatility, 2)} unit="p.a." help={GLOSSARY.volatility} />
              <Metric label="Sharpe estimate" value={formatNum(p.sharpe_estimate, 2)} hint={`Risk-free ${formatPct(result.config.risk_free_rate, 2)}`} help={GLOSSARY.sharpe} />
              <Metric label="Score" value={formatNum(p.objective, 5)} hint={`q = ${formatNum(result.config.risk_aversion, 3)}`} help={GLOSSARY.objective} />
              <Metric label="Transaction costs" value={formatInr(p.cost_inr)} hint={`${formatPct(p.cost_fraction, 3)} of budget`} help={GLOSSARY.costs} />
            </dl>
            <p className="mt-3 text-xs text-muted">
              Training-window estimates on {formatInr(result.config.budget_inr)}; cash left uninvested {formatInr(p.cash_inr)} ({formatPct(p.cash_weight, 2)}).
            </p>
          </Card>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <Card>
              <CardHeader level={2} title="Holdings" />
              <HoldingsTable holdings={p.holdings} lots={result.config.mode === 'lots'} priceDate={result.config.dates.train_end} />
            </Card>
            <Card>
              <SectorChart weights={p.sector_weights} caps={capMap} />
            </Card>
          </div>

          <Card>
            <CardHeader
              level={2}
              title="Constraint check"
              aside={
                p.feasibility.feasible ? (
                  <Badge tone="teal" icon={<IconCheck size={13} />}>
                    All satisfied
                  </Badge>
                ) : (
                  <Badge tone="danger" icon={<IconX size={13} />}>
                    Violated
                  </Badge>
                )
              }
              description="Each constraint re-checked on the decoded portfolio, independently of the QUBO penalties."
            />
            <ResidualTable rows={p.feasibility.residuals} />
          </Card>

          <Card>
            <CardHeader
              level={2}
              title="Out-of-sample check"
              description="The risk model was fitted on the training window only. Validation selects settings; the test window is evaluated once, last."
            />
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
              <PeriodBlock title="Validation window" m={p.validation} help={GLOSSARY.validationReturn} />
              <PeriodBlock title="Held-out test window" m={p.test} help={GLOSSARY.testReturn} />
            </div>
            <p className="mt-4 border-t border-line pt-3 text-xs leading-relaxed text-muted">{result.dataset.survivorship_note}</p>
          </Card>
        </>
      ) : null}

      {result.explanations.length ? (
        <Card>
          <CardHeader level={2} title="What drove this result" />
          <ul className="grid grid-cols-1 gap-2 text-sm leading-relaxed text-ink-2" data-testid="explanations">
            {result.explanations.map((e) => (
              <li key={e} className="flex gap-2">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cobalt" aria-hidden="true" />
                <span>{e}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  )
}

function HoldingsTable({ holdings, lots, priceDate }: { holdings: Holding[]; lots: boolean; priceDate: string }) {
  const rows = [...holdings].sort((a, b) => b.weight - a.weight)
  const cols: Array<Column<Holding>> = [
    { key: 'sym', header: 'Symbol', rowHeader: true, cell: (h) => <span className="font-medium">{h.symbol}</span> },
    { key: 'sector', header: 'Sector', cell: (h) => <span className="text-ink-2">{h.sector}</span> },
    { key: 'w', header: 'Weight', align: 'right', cell: (h) => formatPct(h.weight, 2) },
    { key: 'amt', header: 'Amount', align: 'right', cell: (h) => formatInr(h.amount_inr) },
    ...(lots ? [{ key: 'lots', header: 'Lots', align: 'right' as const, cell: (h: Holding) => formatInt(h.lots) }] : []),
    { key: 'px', header: `Price on ${formatDate(priceDate)}`, align: 'right', cell: (h) => formatInr(h.price_inr, { paise: true }) },
    { key: 'sh', header: 'Shares*', align: 'right', cell: (h) => formatInt(h.indicative_shares) },
  ]
  // The note on shares sits under the table rather than in a header tooltip: a tooltip inside the scroll region is
  // clipped by it, and its touch target alone made the table wider than its card.
  return (
    <>
      <Table
        caption="Holdings by weight"
        captionHidden
        columns={cols}
        rows={rows}
        rowKey={(h) => h.symbol}
        dense
        footer={
          <tr className="border-t border-line-strong bg-mist font-medium">
            <th scope="row" className="px-2.5 py-1.5 text-left">
              Total
            </th>
            <td />
            <td className="px-2.5 py-1.5 text-right">{formatPct(rows.reduce((a, h) => a + h.weight, 0), 2)}</td>
            <td className="px-2.5 py-1.5 text-right">{formatInr(rows.reduce((a, h) => a + h.amount_inr, 0))}</td>
            <td colSpan={lots ? 3 : 2} />
          </tr>
        }
      />
      <p className="mt-2 text-xs leading-relaxed text-muted">
        * Indicative shares: amount divided by the NSE closing price on the last training day ({formatDate(priceDate)}), rounded down to whole shares.
        Prices move after that day and exchange lot rules are not modelled.
      </p>
    </>
  )
}

/** "sector_cap:Financial Services" -> "Sector cap: Financial Services". */
const constraintLabel = (c: string) => {
  const i = c.indexOf(':')
  return i < 0 ? humanKey(c) : `${humanKey(c.slice(0, i))}: ${c.slice(i + 1)}`
}

/** Counts read as counts; other residuals keep four decimals. */
const residualNum = (v: number) => (Number.isInteger(v) ? formatInt(v) : formatNum(v, 4))

function ResidualTable({ rows }: { rows: Residual[] }) {
  return (
    <Table
      caption="Constraint residuals"
      captionHidden
      rows={rows}
      rowKey={(r) => r.constraint}
      dense
      empty="No constraints were active."
      columns={[
        { key: 'c', header: 'Constraint', rowHeader: true, className: 'min-w-36 whitespace-normal!', cell: (r) => constraintLabel(r.constraint) },
        {
          key: 's',
          header: 'Status',
          cell: (r) =>
            r.satisfied ? (
              <span className="inline-flex items-center gap-1 text-teal">
                <IconCheck size={14} /> Satisfied
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 font-medium text-danger">
                <IconX size={14} /> Violated
              </span>
            ),
        },
        { key: 'v', header: 'Value', align: 'right', cell: (r) => residualNum(r.value) },
        { key: 'l', header: 'Limit', align: 'right', cell: (r) => (r.limit === null ? DASH : residualNum(r.limit)) },
        { key: 'e', header: 'Meaning', className: 'min-w-52', cell: (r) => <span className="text-xs text-ink-2">{r.explanation}</span> },
      ]}
    />
  )
}

/* ------------------------------------------------------------------ comparison */

function CompareTab({ result, solvers }: { result: RunResult; solvers: SolverResult[] }) {
  const ref = solvers.find((s) => s.gap)?.gap
  return (
    <div className="grid grid-cols-1 gap-5">
      <Card>
        <CardHeader
          level={2}
          title="Same problem, every solver"
          description={
            ref
              ? `Gaps are objective differences ${gapReference(ref)}, normalised by ${ref.normalisation}. Lower objective is better.`
              : 'No feasible reference was available, so gaps are not reported.'
          }
        />
        <Table
          caption="Solver comparison"
          captionHidden
          rows={solvers}
          rowKey={(s) => s.solver}
          rowClassName={(s) => (s.solver === 'qaoa' ? 'bg-cobalt-50/50' : '')}
          columns={[
            { key: 'n', header: 'Solver', rowHeader: true, cell: (s) => <span className="font-medium whitespace-nowrap">{SOLVER_LABEL[s.solver]}</span> },
            { key: 'st', header: 'Status', cell: (s) => <StatusChip status={s.status} /> },
            { key: 'obj', header: 'Objective', align: 'right', cell: (s) => formatNum(s.portfolio?.objective, 5) },
            { key: 'gap', header: 'Gap', align: 'right', cell: (s) => <span title={gapReference(s.gap)}>{gapText(s.gap)}</span> },
            { key: 'ret', header: 'Return p.a.', align: 'right', cell: (s) => formatPct(s.portfolio?.expected_return, 2) },
            { key: 'vol', header: 'Volatility', align: 'right', cell: (s) => formatPct(s.portfolio?.volatility, 2) },
            { key: 'net', header: 'Net return', align: 'right', cell: (s) => formatPct(s.portfolio?.net_expected_return, 2) },
            { key: 'cost', header: 'Costs', align: 'right', cell: (s) => (s.portfolio ? formatInr(s.portfolio.cost_inr) : DASH) },
            {
              key: 'test',
              header: 'Test return',
              align: 'right',
              cell: (s) => (s.portfolio?.test && !s.portfolio.test.insufficient ? formatPct(s.portfolio.test.total_return, 2, { sign: true }) : DASH),
            },
            {
              key: 'h',
              header: 'Holdings',
              cell: (s) => (
                <span className="text-xs text-ink-2">
                  {s.portfolio
                    ? s.portfolio.holdings
                        .filter((h) => h.weight > 0)
                        .map((h) => h.symbol)
                        .join(', ')
                    : DASH}
                </span>
              ),
            },
            { key: 'rt', header: 'Runtime', align: 'right', cell: (s) => secs(solverRuntime(s)) },
          ]}
        />
        <ul className="mt-3 grid grid-cols-1 gap-1 text-xs text-muted">
          {solvers
            .filter((s) => s.status_detail || s.notes.length)
            .map((s) => (
              <li key={s.solver}>
                <span className="font-medium text-ink-2">{SOLVER_LABEL[s.solver]}:</span> {[STATUS_LABEL[s.status], s.status_detail, ...s.notes].filter(Boolean).join(' · ')}
              </li>
            ))}
        </ul>
      </Card>
      <Card>
        <FrontierChart points={result.frontier} solvers={solvers} />
      </Card>
      <Card>
        <CardHeader level={2} title="Work done by each solver" description="Counts reported by the solvers themselves, so the comparison is not just wall-clock time." />
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {solvers.map((s) => (
            <div key={s.solver} className="rounded-md border border-line p-3">
              <h3 className="text-sm font-semibold text-ink">{SOLVER_LABEL[s.solver]}</h3>
              {Object.keys(s.work).length || numericEntries(s.runtime_s).length ? (
                <DictTable
                  dict={{ ...s.work, ...Object.fromEntries(numericEntries(s.runtime_s).map(([k, v]) => [`runtime_${k}`, `${v.toFixed(3)} s`])) }}
                  caption={`${s.solver} work`}
                />
              ) : (
                <p className="mt-1 text-sm text-muted">{[STATUS_LABEL[s.status], s.status_detail].filter(Boolean).join(' · ')}; no work reported.</p>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------ QAOA */

function QaoaTab({ result }: { result: RunResult }) {
  const qa = result.qaoa
  if (!qa)
    return (
      <EmptyState title="QAOA did not run for this result" icon={<IconAlert />}>
        The comparison tab shows why.
      </EmptyState>
    )
  const fb = qa.final_batch
  const symbols = result.encoded_symbols
  return (
    <div className="grid grid-cols-1 gap-5">
      <Card>
        <ConvergenceChart points={qa.iterations} budget={qa.evaluation_budget} />
        <p className="mt-3 text-xs text-muted tabular">
          {formatInt(qa.evaluations_used)} of {formatInt(qa.evaluation_budget)} evaluations used
          {qa.preparation_evaluations ? `, plus ${formatInt(qa.preparation_evaluations)} warm-start preparation evaluations` : ''}. {qa.init_description}
        </p>
      </Card>
      <Card>
        <HistogramChart batch={fb} />
      </Card>
      <Card>
        <CardHeader
          level={2}
          title="Most frequent samples"
          description={`Decision bit i (left to right) is asset i of the encoded universe: ${symbols.join(', ')}. Slack bits are not shown. Selection policy: ${fb.selection_policy}.`}
        />
        <Table
          caption="Top sampled bitstrings"
          captionHidden
          rows={fb.top_samples}
          rowKey={(r, i) => `${r.bitstring}-${i}`}
          dense
          rowClassName={(r) => (r.bitstring === fb.best_bitstring ? 'bg-teal-50' : '')}
          columns={[
            { key: 'rank', header: '#', cell: (_r, i) => i + 1 },
            {
              key: 'bits',
              header: 'Decision bits',
              rowHeader: true,
              cell: (r: SampleRow) => (
                <span className="inline-flex items-center gap-1.5">
                  <Literal>{r.decision_bits}</Literal>
                  {r.bitstring === fb.best_bitstring ? <Badge tone="teal">chosen</Badge> : null}
                </span>
              ),
            },
            { key: 'dec', header: 'Assets held', cell: (r) => <span className="text-xs">{decodeSelection(r.decision_bits, symbols).join(', ') || DASH}</span> },
            {
              key: 'f',
              header: 'Feasible',
              cell: (r) =>
                r.feasible ? (
                  <span className="inline-flex items-center gap-1 text-teal">
                    <IconCheck size={14} /> yes
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-muted">
                    <IconX size={14} /> no
                  </span>
                ),
            },
            { key: 'o', header: 'Objective', align: 'right', cell: (r) => formatNum(r.objective, 5) },
            { key: 'e', header: 'QUBO energy', align: 'right', cell: (r) => formatNum(r.qubo_energy, 4) },
            { key: 'c', header: 'Count', align: 'right', cell: (r) => formatInt(r.count) },
            { key: 'p', header: 'Probability', align: 'right', cell: (r) => formatPct(r.probability, 2) },
          ]}
        />
        <p className="mt-2 text-xs text-muted">
          {formatInt(fb.distinct_bitstrings)} distinct bitstrings in {formatInt(fb.shots)} shots · sampling seed {fb.seed} · counts SHA-256 <Literal>{fb.counts_sha256}</Literal>
        </p>
      </Card>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader level={2} title="Circuit" description="After transpilation for the simulator's basis gates." />
          <KV
            rows={[
              ['Qubits', qa.circuit.n_qubits],
              ['Depth p (layers)', qa.depth],
              ['Transpiled depth', formatInt(qa.circuit.depth)],
              ['Gate count', formatInt(qa.circuit.size)],
              ['Two-qubit gates', formatInt(qa.circuit.two_qubit_gates)],
              ['Gates by type', Object.entries(qa.circuit.gate_counts).map(([g, n]) => `${g}: ${n}`).join(', ')],
              ['Basis', qa.circuit.basis_gates.join(', ')],
              ['Transpile time', secs(qa.circuit.transpile_seconds)],
              ['Optimiser', `${qa.optimizer} · ${qa.estimator === 'cvar' ? `CVaR α = ${qa.cvar_alpha}` : 'mean energy'}`],
              ['Simulator', `${qa.primitive} · ${qa.simulator_method}${qa.noise_model ? ` · ${qa.noise_model}` : ' · ideal'}`],
              ['Seeds', Object.entries(qa.seeds).map(([k, v]) => `${k} ${v}`).join(', ')],
            ]}
          />
          <Disclosure summary="Optimiser settings" className="mt-2">
            <DictTable dict={qa.optimizer_settings} caption="Optimiser settings" />
          </Disclosure>
        </Card>
        <Card>
          <CardHeader level={2} title="Parameters" description="Angles for each layer, before and after optimisation (radians)." />
          <Table
            caption="QAOA parameters"
            captionHidden
            dense
            rows={qa.parameter_order.map((name, i) => ({ name, init: qa.initial_parameters[i], opt: qa.optimal_parameters[i] }))}
            rowKey={(r) => r.name}
            columns={[
              { key: 'n', header: 'Parameter', rowHeader: true, cell: (r) => <Literal>{r.name}</Literal> },
              { key: 'i', header: 'Initial', align: 'right', cell: (r) => formatNum(r.init, 4) },
              { key: 'o', header: 'Optimised', align: 'right', cell: (r) => formatNum(r.opt, 4) },
            ]}
          />
        </Card>
      </div>

      <Card>
        <CardHeader level={2} title="QUBO encoding" description={result.qubo.objective_definition} />
        <dl className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Metric label="Decision bits" value={result.qubo.n_decision} help="Qubits that stand for stocks (or lots): 1 means buy, 0 means skip." />
          <Metric label="Slack bits" value={result.qubo.n_slack} help={GLOSSARY.slackQubits} />
          <Metric label="Linear terms" value={formatInt(result.qubo.n_linear_terms)} help="Parts of the score that depend on one qubit alone, such as a stock's own expected return." />
          <Metric
            label="Quadratic terms"
            value={formatInt(result.qubo.n_quadratic_terms)}
            help="Parts of the score that depend on a pair of qubits, such as how two stocks move together. Each one becomes a two-qubit gate."
          />
        </dl>
        <div className="mt-4">
          <Table
            caption="Penalty weights"
            dense
            rows={result.qubo.penalties}
            rowKey={(p) => p.constraint}
            empty="No penalty terms: the problem is unconstrained apart from the encoding."
            columns={[
              { key: 'c', header: 'Constraint', rowHeader: true, cell: (p) => humanKey(p.constraint) },
              { key: 'w', header: 'Weight λ', align: 'right', cell: (p) => formatNum(p.weight, 4) },
              { key: 'd', header: 'How it was derived', cell: (p) => <span className="text-xs text-ink-2">{p.derivation}</span> },
            ]}
          />
        </div>
        <Disclosure summary={`Variables (${result.qubo.n_variables})`} className="mt-2">
          <Table
            caption="QUBO variables"
            captionHidden
            dense
            rows={result.qubo.variables}
            rowKey={(v) => String(v.index)}
            columns={[
              { key: 'i', header: 'Bit', cell: (v) => v.index },
              { key: 'n', header: 'Name', rowHeader: true, cell: (v) => <Literal>{v.name}</Literal> },
              { key: 'k', header: 'Kind', cell: (v) => v.kind.replace('_', ' ') },
              { key: 'a', header: 'Asset or constraint', cell: (v) => v.asset ?? (v.constraint ? humanKey(v.constraint) : DASH) },
              { key: 'c', header: 'Coefficient', align: 'right', cell: (v) => formatNum(v.coefficient, 3) },
            ]}
          />
        </Disclosure>
        <p className="mt-2 text-xs text-muted tabular">
          Scale {formatNum(result.qubo.scale, 4)} · offset {formatNum(result.qubo.offset, 4)} · Ising offset {formatNum(result.qubo.ising_offset, 4)} · largest |coefficient|{' '}
          {formatNum(result.qubo.max_abs_coefficient, 4)}
        </p>
      </Card>

      {result.noise_comparison ? (
        <Card>
          <NoiseChart noise={result.noise_comparison} />
        </Card>
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------------ provenance */

function ProvenanceTab({ result }: { result: RunResult }) {
  const e = result.estimation
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <DatasetCard dataset={result.dataset} />
      <Card>
        <CardHeader level={2} title="Risk model" />
        <KV
          rows={[
            ['Training window', `${e.train.start} to ${e.train.end} (${formatInt(e.train.n_obs)} days)`],
            ['Validation window', `${e.validation.start} to ${e.validation.end} (${formatInt(e.validation.n_obs)} days)`],
            ['Test window', `${e.test.start} to ${e.test.end} (${formatInt(e.test.n_obs)} days)`],
            ['Returns', `${e.return_type}, annualised × ${e.annualisation_days}`],
            ['Covariance', `${e.covariance_method}${e.shrinkage_intensity !== null ? `, shrinkage ${formatNum(e.shrinkage_intensity, 3)}` : ''}`],
            ['PSD check', `${e.psd_corrected ? 'Corrected' : 'Not needed'} (min eigenvalue ${formatNum(e.min_eigenvalue_before, 6)} → ${formatNum(e.min_eigenvalue_after, 6)})`],
          ]}
        />
        {e.notes.length ? (
          <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-muted">
            {e.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
        {Object.keys(result.screening).length ? (
          <>
            <h3 className="mt-5 text-sm font-semibold text-ink">Screening</h3>
            <DictTable dict={result.screening} caption="Screening" />
          </>
        ) : null}
      </Card>
      <SoftwareCard
        software={result.software}
        timings={result.timings_s}
        extra={[
          ['Run id', <Literal key="r">{result.run_id}</Literal>],
          ['Created', formatDateTime(result.created_at)],
          ['Config hash', <Literal key="h">{result.config_hash}</Literal>],
          ['Cache key', <Literal key="c">{result.cache_key}</Literal>],
          ['Implementation', `${result.implementation_version} · schema ${result.schema_version}`],
          ['Encoded universe', result.encoded_symbols.join(', ')],
        ]}
      />
      <Card>
        <CardHeader level={2} title="Configuration" description="Exactly what was submitted, after server defaults were applied." />
        <KV
          rows={[
            ['Preset', result.config.preset],
            ['Mode', result.config.mode === 'lots' ? 'Lots (bounded weights)' : 'Selection (equal weight 1/K)'],
            ['Budget', formatInr(result.config.budget_inr)],
            ['Risk aversion q', formatUnknown(result.config.risk_aversion)],
            ['QAOA', `p = ${result.config.qaoa.depth}, ${result.config.qaoa.optimizer}, ${result.config.qaoa.init_strategy}, seed ${result.config.qaoa.seed}`],
            ['Timeout', `${result.config.timeout_seconds} s`],
          ]}
        />
        <div className="mt-2">
          <ConfigDump config={result.config} />
        </div>
      </Card>
    </div>
  )
}
