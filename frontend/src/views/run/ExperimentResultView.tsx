import { useMemo, useState } from 'react'
import type { ExperimentCell, ExperimentResult } from '../../api/contract.gen'
import { ExperimentFactorChart, NoiseChart } from '../../components/charts/charts'
import { FACTOR_LABEL, INIT_LABEL, METRIC_LABEL, levelOf, type ExpMetric } from '../../components/charts/chartData'
import { Card, CardHeader, Notice, Segmented, StatusChip, Table } from '../../components/ui'
import { DASH, formatDateTime, formatInt, formatNum, formatPct } from '../../lib/format'
import { formatUnknown, humanKey } from '../../lib/result'
import { ConfigDump, DatasetCard, DictTable, Downloads, KV, Mono, SoftwareCard } from './shared'

export function ExperimentResultView({ result }: { result: ExperimentResult }) {
  const [metric, setMetric] = useState<ExpMetric>('approximation_ratio')
  const factors = useMemo(() => [...new Set(result.cells.map((c) => c.factor))], [result.cells])
  const cfg = result.config
  const failed = result.cells.filter((c) => c.error || c.status === 'failed').length
  const summaryCols = useMemo(() => [...new Set(result.summary.flatMap((r) => Object.keys(r)))], [result.summary])

  return (
    <div className="grid grid-cols-1 gap-5" data-testid="experiment-result">
      <Notice tone="warning" title="Research demonstration — not investment advice">
        Simulated QAOA circuits and historical estimates; no live trading.
      </Notice>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink">Controlled experiment</h2>
            <p className="mt-1 text-sm text-ink-2 tabular">
              {formatInt(result.cells.length)} cells · {cfg.design === 'full_factorial' ? 'full factorial' : 'one factor at a time'} · seeds {cfg.seeds.join(', ')}
              {failed ? ` · ${failed} failed` : ''}
            </p>
          </div>
          <Downloads runId={result.run_id} counts={false} />
        </div>
      </Card>

      {result.warnings.length ? (
        <Notice tone="warning" title={`${result.warnings.length} warning${result.warnings.length === 1 ? '' : 's'}`}>
          <ul className="list-disc space-y-0.5 pl-5">
            {result.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      ) : null}

      <Card>
        <CardHeader level={2} title="Selected configuration" description={result.selection_rule} />
        {result.selected_configuration ? (
          <DictTable dict={result.selected_configuration} caption="Selected configuration" />
        ) : (
          <p className="text-sm text-muted">No configuration met the selection rule.</p>
        )}
      </Card>

      <Card>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-base font-semibold text-ink">Effect of each factor</h2>
          <div className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
            {/* Visible label beside the control; the fieldset keeps "Metric" as its accessible legend. */}
            <span className="text-sm font-medium text-ink-2" aria-hidden="true">
              Metric
            </span>
            <Segmented
              name="exp-metric"
              legend="Metric"
              hideLegend
              size="sm"
              value={metric}
              onChange={setMetric}
              options={(Object.keys(METRIC_LABEL) as ExpMetric[]).map((m) => ({ value: m, label: METRIC_LABEL[m] }))}
            />
          </div>
        </div>
        {/* A lone factor's chart takes the full width; two or more share rows on wide screens. */}
        <div className={`mt-4 grid grid-cols-1 gap-6 ${factors.length > 1 ? 'xl:grid-cols-2' : ''}`}>
          {factors.map((f) => (
            <ExperimentFactorChart key={f} factor={f} cells={result.cells} metric={metric} />
          ))}
        </div>
      </Card>

      {result.summary.length ? (
        <Card>
          <CardHeader level={2} title="Summary by level" description="Aggregated over seeds by the server; spreads are shown where it reports them." />
          <Table
            caption="Experiment summary"
            captionHidden
            dense
            rows={result.summary}
            rowKey={(_r, i) => String(i)}
            columns={summaryCols.map((k, i) => ({
              key: k,
              header: humanKey(k),
              rowHeader: i === 0,
              align: typeof result.summary[0]?.[k] === 'number' ? ('right' as const) : undefined,
              cell: (r: Record<string, unknown>) => {
                const v = r[k]
                if (typeof v !== 'string') return formatUnknown(v)
                return (k === 'factor' ? FACTOR_LABEL[v] : INIT_LABEL[v]) ?? v
              },
            }))}
          />
        </Card>
      ) : null}

      <Card>
        <CardHeader level={2} title="Every cell" description="Each row is one QAOA run on the same data. Approximation ratio compares with the exact optimum when exact enumeration was feasible." />
        <CellTable cells={result.cells} />
      </Card>

      {result.noise_comparison ? (
        <Card>
          <NoiseChart noise={result.noise_comparison} />
        </Card>
      ) : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <DatasetCard dataset={result.dataset} />
        <SoftwareCard
          software={result.software}
          timings={result.timings_s}
          extra={[
            ['Experiment id', <Mono key="r">{result.run_id}</Mono>],
            ['Created', formatDateTime(result.created_at)],
            ['Implementation', `${result.implementation_version} · schema ${result.schema_version}`],
            ['Sweeps', `depths ${cfg.depths.join(', ') || DASH}; optimisers ${cfg.optimizers.join(', ') || DASH}; inits ${cfg.init_strategies.map((s) => INIT_LABEL[s] ?? s).join(', ') || DASH}; sizes ${cfg.universe_sizes.join(', ') || DASH}`],
          ]}
        />
      </div>
      <Card>
        <CardHeader level={2} title="Configuration" />
        <KV
          rows={[
            ['Base universe', cfg.base.symbols.join(', ')],
            ['Budget per cell', `${formatInt(cfg.base.qaoa.max_evaluations)} evaluations, ${formatInt(cfg.base.qaoa.shots)} shots`],
            ['Cell limit', formatInt(cfg.max_cells)],
          ]}
        />
        <div className="mt-2">
          <ConfigDump config={cfg} />
        </div>
      </Card>
    </div>
  )
}

function CellTable({ cells }: { cells: ExperimentCell[] }) {
  return (
    <Table
      caption="Experiment cells"
      captionHidden
      dense
      rows={cells}
      rowKey={(_c, i) => String(i)}
      columns={[
        { key: 'f', header: 'Factor', rowHeader: true, cell: (c) => `${FACTOR_LABEL[c.factor] ?? c.factor}: ${levelOf(c)}` },
        { key: 'p', header: 'p', align: 'right', cell: (c) => c.depth },
        { key: 'o', header: 'Optimiser', cell: (c) => c.optimizer },
        { key: 'i', header: 'Init', cell: (c) => (c.init_strategy === 'warm_interp' ? 'Warm (interp)' : c.init_strategy === 'random' ? 'Random' : c.init_strategy) },
        { key: 'n', header: 'Assets', align: 'right', cell: (c) => c.n_assets },
        { key: 'q', header: 'Qubits', align: 'right', cell: (c) => c.total_qubits },
        { key: 's', header: 'Seed', align: 'right', cell: (c) => c.seed },
        { key: 'st', header: 'Status', cell: (c) => <StatusChip status={c.status} /> },
        { key: 'ar', header: 'Approx. ratio', align: 'right', cell: (c) => formatNum(c.approximation_ratio, 3) },
        { key: 'bo', header: 'Best objective', align: 'right', cell: (c) => formatNum(c.best_objective, 5) },
        { key: 'eo', header: 'Exact', align: 'right', cell: (c) => formatNum(c.exact_objective, 5) },
        { key: 'fp', header: 'Feasible share', align: 'right', cell: (c) => formatPct(c.feasible_probability, 1) },
        { key: 'ev', header: 'Evaluations', align: 'right', cell: (c) => (c.preparation_evaluations ? `${c.evaluations} + ${c.preparation_evaluations}` : String(c.evaluations)) },
        { key: 'rt', header: 'Runtime', align: 'right', cell: (c) => `${c.runtime_s.toFixed(1)} s` },
        { key: 'vs', header: 'Val. Sharpe', align: 'right', cell: (c) => formatNum(c.validation_sharpe, 2) },
        { key: 'er', header: 'Error', cell: (c) => (c.error ? <span className="text-xs text-danger">{c.error}</span> : DASH) },
      ]}
    />
  )
}
