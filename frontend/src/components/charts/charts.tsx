import { useMemo } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  Scatter,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ExperimentCell, FinalBatch, FrontierPoint, IterationPoint, NoiseComparison, SolverName, SolverResult } from '../../api/contract.gen'
import { DASH, SOLVER_LABEL, formatNum, formatPct, sectorShort } from '../../lib/format'
import { Table, Tooltip } from '../ui'
import { ChartFrame, LegendItem } from './ChartFrame'
import { FACTOR_LABEL, METRIC_LABEL, levelOf, toConvergenceRows, type ConvergenceRow, type ExpMetric } from './chartData'

const C = {
  cobalt: '#1f48d6',
  cobaltSoft: '#bccbfa',
  teal: '#0e8f8b',
  ink: '#0b1220',
  muted: '#525d6e',
  faint: '#8a94a6',
  line: '#e4e8ef',
  lineStrong: '#cbd3df',
  amber: '#9a5b00',
}

const AXIS = { stroke: C.lineStrong, tickLine: false, fontSize: 11 } as const
const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`

const SOLVER_STYLE: Record<SolverName, { color: string; shape: 'diamond' | 'star' | 'triangle' | 'square' | 'cross' }> = {
  qaoa: { color: C.cobalt, shape: 'diamond' },
  exact: { color: C.ink, shape: 'star' },
  relaxation_rounding: { color: C.muted, shape: 'triangle' },
  simulated_annealing: { color: '#3d4a5f', shape: 'square' },
  greedy: { color: C.faint, shape: 'cross' },
}

function TooltipBox({ title, rows }: { title?: string; rows: Array<[string, string]> }) {
  return (
    <div className="rounded-md border border-line bg-canvas px-3 py-2 text-xs shadow-float">
      {title ? <p className="mb-1 font-semibold text-ink">{title}</p> : null}
      <dl className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="text-right font-medium text-ink tabular">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/* ------------------------------------------------------------------ frontier */

export function FrontierChart({ points, solvers }: { points: FrontierPoint[]; solvers: SolverResult[] }) {
  const relax = useMemo(
    () => points.filter((p) => p.kind === 'continuous_relaxation').map((p) => ({ x: p.volatility, y: p.expected_return })).sort((a, b) => a.x - b.x),
    [points],
  )
  const discrete = useMemo(() => points.filter((p) => p.kind === 'discrete_feasible').map((p) => ({ x: p.volatility, y: p.expected_return, label: p.label })), [points])
  const solverPts = useMemo(() => {
    const fromFrontier = points.filter((p) => p.kind === 'solver')
    const pts = fromFrontier.length
      ? fromFrontier.map((p) => ({ x: p.volatility, y: p.expected_return, name: SOLVER_LABEL[p.label ?? ''] ?? p.label ?? 'Solver', solver: (p.label ?? '') as SolverName }))
      : solvers
          .filter((s) => s.portfolio)
          .map((s) => ({ x: s.portfolio!.volatility, y: s.portfolio!.expected_return, name: SOLVER_LABEL[s.solver] ?? s.solver, solver: s.solver }))
    // Solvers that found the same portfolio share one label, so the names do not print on top of each other.
    const at = (p: { x: number; y: number }) => `${p.x.toFixed(6)}:${p.y.toFixed(6)}`
    return pts.map((p, i) => {
      const same = pts.filter((o) => at(o) === at(p))
      const first = pts.findIndex((o) => at(o) === at(p)) === i
      const short = same.map((o) => SOLVER_SHORT[o.solver] ?? o.name)
      return { ...p, same: same.map((o) => o.name), tag: first ? (short.length > 2 ? `${short[0]} + ${short.length - 1} others` : short.join(' = ')) : '' }
    })
  }, [points, solvers])

  const q = solverPts.find((s) => s.solver === 'qaoa')
  const summary = points.length
    ? `${discrete.length} feasible discrete portfolios and ${relax.length} points on the continuous relaxation, plotted as annualised expected return against annualised volatility. ` +
      (q ? `The QAOA portfolio sits at ${pct1(q.y)} return and ${pct1(q.x)} volatility${q.same.length > 1 ? `, the same portfolio as ${q.same.filter((n) => n !== q.name).join(', ')}` : ''}. ` : '') +
      'The relaxation is a bound that ignores the integer constraints, so no feasible portfolio can sit above it.'
    : 'No frontier was computed for this run.'

  const rows = [
    ...relax.map((p, i) => ({ k: `r${i}`, kind: 'Continuous relaxation (bound)', label: DASH, ...p })),
    ...discrete.map((p, i) => ({ k: `d${i}`, kind: 'Discrete feasible', label: p.label ?? DASH, x: p.x, y: p.y })),
    ...solverPts.map((p, i) => ({ k: `s${i}`, kind: 'Solver result', label: p.name, x: p.x, y: p.y })),
  ]

  return (
    <ChartFrame
      testId="chart-frontier"
      title="Risk–return frontier"
      summary={summary}
      height={320}
      aside={
        <Tooltip
          label="How the relaxation line differs from the feasible points"
          content="The dashed line is the continuous relaxation: weights may take any value, so it ignores the exact number of holdings, lot sizes and sector caps. It is an upper bound for comparison, not a portfolio you can hold. Dots are portfolios that satisfy every constraint."
        />
      }
      legend={
        <>
          <LegendItem swatch={<line x1="1" y1="6" x2="17" y2="6" stroke={C.muted} strokeWidth="1.6" strokeDasharray="4 3" />}>
            Continuous relaxation (bound, not achievable)
          </LegendItem>
          <LegendItem swatch={<circle cx="9" cy="6" r="3.2" fill={C.lineStrong} stroke={C.muted} strokeWidth="0.8" />}>Discrete feasible portfolios</LegendItem>
          {solverPts.map((s) => (
            <LegendItem key={s.name} swatch={<SolverGlyph solver={s.solver} />}>
              {s.name}
            </LegendItem>
          ))}
        </>
      }
      table={
        <Table
          dense
          caption="Frontier points"
          captionHidden
          rows={rows}
          rowKey={(r) => r.k}
          columns={[
            { key: 'kind', header: 'Series', cell: (r) => r.kind, rowHeader: true },
            { key: 'label', header: 'Label', cell: (r) => r.label },
            { key: 'ret', header: 'Expected return (annual)', align: 'right', cell: (r) => formatPct(r.y) },
            { key: 'vol', header: 'Volatility (annual)', align: 'right', cell: (r) => formatPct(r.x) },
          ]}
        />
      }
    >
      <ComposedChart responsive accessibilityLayer={false} style={{ width: '100%', height: '100%' }} margin={{ top: 8, right: 24, bottom: 22, left: 4 }}>
        <CartesianGrid stroke={C.line} />
        <XAxis
          type="number"
          dataKey="x"
          domain={['auto', 'auto']}
          tickFormatter={pct1}
          {...AXIS}
          label={{ value: 'Volatility (annualised)', position: 'insideBottom', offset: -12, fill: C.muted, fontSize: 11 }}
        />
        <YAxis type="number" dataKey="y" domain={['auto', 'auto']} tickFormatter={pct1} width={52} {...AXIS} />
        <RTooltip
          cursor={false}
          content={({ active, payload }) => {
            const p = active && payload?.[0]?.payload
            if (!p) return null
            return <TooltipBox title={(p as { name?: string; label?: string }).name ?? (p as { label?: string }).label ?? 'Portfolio'} rows={[['Return', formatPct((p as { y: number }).y)], ['Volatility', formatPct((p as { x: number }).x)]]} />
          }}
        />
        <Line data={relax} dataKey="y" type="monotone" stroke={C.muted} strokeWidth={1.6} strokeDasharray="5 4" dot={false} isAnimationActive={false} name="Continuous relaxation" />
        <Scatter data={discrete} dataKey="y" fill={C.lineStrong} stroke={C.muted} strokeWidth={0.6} isAnimationActive={false} name="Discrete feasible" />
        {solverPts.map((s) => (
          <Scatter
            key={s.name}
            data={[s]}
            dataKey="y"
            fill={SOLVER_STYLE[s.solver]?.color ?? C.ink}
            shape={SOLVER_STYLE[s.solver]?.shape ?? 'circle'}
            isAnimationActive={false}
            name={s.name}
          >
            <LabelList dataKey="tag" position="right" fontSize={11} fill={C.ink} offset={8} />
          </Scatter>
        ))}
      </ComposedChart>
    </ChartFrame>
  )
}

const SOLVER_SHORT: Partial<Record<SolverName, string>> = { qaoa: 'QAOA', exact: 'Exact', relaxation_rounding: 'Relax + round', simulated_annealing: 'Annealing', greedy: 'Greedy' }

function SolverGlyph({ solver }: { solver: SolverName }) {
  const s = SOLVER_STYLE[solver] ?? { color: C.ink, shape: 'circle' }
  switch (s.shape) {
    case 'diamond':
      return <path d="M9 1.5L13.5 6 9 10.5 4.5 6z" fill={s.color} />
    case 'star':
      return <path d="M9 1l1.5 3.3 3.5.4-2.6 2.4.7 3.5L9 8.9 5.9 10.6l.7-3.5L4 4.7l3.5-.4z" fill={s.color} />
    case 'triangle':
      return <path d="M9 1.5l4.5 9h-9z" fill={s.color} />
    case 'square':
      return <rect x="5" y="2" width="8" height="8" fill={s.color} />
    case 'cross':
      return <path d="M5.5 2.5l7 7M12.5 2.5l-7 7" stroke={s.color} strokeWidth="2" />
    default:
      return <circle cx="9" cy="6" r="4" fill={s.color} />
  }
}

/* --------------------------------------------------------------- convergence */



export function ConvergenceChart({ points, budget, live = false }: { points: IterationPoint[]; budget?: number | null; live?: boolean }) {
  const rows = useMemo(() => toConvergenceRows(points), [points])
  const prep = rows.filter((r) => r.phase === 'warm_start_preparation').length
  const opt = rows.length - prep
  const last = rows.at(-1)
  const bests = rows.filter((r) => r.best !== null)
  const firstFeasible = bests[0]
  const summary = rows.length
    ? `${opt} measured optimisation evaluation${opt === 1 ? '' : 's'}${budget ? ` of a ${budget}-evaluation budget` : ''}${prep ? `, after ${prep} warm-start preparation evaluations` : ''}. ` +
      `Latest raw energy ${formatNum(last?.energy, 4)}. ` +
      (firstFeasible
        ? `A feasible sample first appeared at evaluation ${firstFeasible.i}; best feasible objective so far ${formatNum(bests.at(-1)?.best, 5)} (lower is better).`
        : 'No feasible sample has been seen yet.') +
      (live ? ' Only measured points are shown; nothing is extrapolated.' : '')
    : live
      ? 'Waiting for the first measured evaluation. Points appear as the optimiser reports them.'
      : 'This run recorded no optimiser iterations.'

  const table = (
    <Table
      dense
      caption="Optimiser evaluations"
      captionHidden
      rows={rows}
      rowKey={(r) => `${r.phase}:${r.evaluation}`}
      empty="No evaluations yet."
      columns={[
        { key: 'i', header: '#', align: 'right', cell: (r) => r.i, rowHeader: true },
        { key: 'phase', header: 'Phase', cell: (r) => (r.phase === 'optimisation' ? 'Optimisation' : 'Warm-start prep') },
        { key: 'e', header: 'Raw energy', align: 'right', cell: (r) => formatNum(r.energy, 5) },
        { key: 'b', header: 'Best feasible objective', align: 'right', cell: (r) => formatNum(r.best, 5) },
        { key: 'f', header: 'Feasible share', align: 'right', cell: (r) => formatPct(r.feasible, 1) },
        { key: 't', header: 'Elapsed', align: 'right', cell: (r) => `${r.elapsed.toFixed(2)} s` },
      ]}
    />
  )

  return (
    <ChartFrame
      testId="chart-convergence"
      title="Convergence"
      summary={summary}
      height={300}
      legend={
        <>
          <LegendItem swatch={<line x1="1" y1="6" x2="17" y2="6" stroke={C.cobalt} strokeWidth="1.8" />}>Raw energy (QUBO expectation, incl. penalties)</LegendItem>
          {prep ? <LegendItem swatch={<line x1="1" y1="6" x2="17" y2="6" stroke={C.faint} strokeWidth="1.8" strokeDasharray="2 2" />}>Warm-start preparation</LegendItem> : null}
          <LegendItem swatch={<path d="M1 9h6V4h10" fill="none" stroke={C.teal} strokeWidth="1.8" />}>Best feasible objective so far</LegendItem>
        </>
      }
      table={table}
    >
      {rows.length ? (
        <div className="grid h-full grid-rows-2 gap-1">
          <LineChart responsive accessibilityLayer={false} data={rows} syncId="conv" style={{ width: '100%', height: '100%' }} margin={{ top: 6, right: 16, bottom: 0, left: 4 }}>
            <CartesianGrid stroke={C.line} />
            <XAxis dataKey="i" type="number" domain={[1, Math.max(rows.length, budget ? budget + prep : 1)]} hide />
            <YAxis domain={['auto', 'auto']} width={58} tickFormatter={(v: number) => formatNum(v, 3)} {...AXIS} />
            <RTooltip content={({ active, payload }) => (active && payload?.[0] ? <ConvTip r={payload[0].payload as ConvergenceRow} /> : null)} />
            {prep ? <Line dataKey="prepEnergy" stroke={C.faint} strokeDasharray="2 2" dot={false} isAnimationActive={false} connectNulls={false} /> : null}
            <Line dataKey="optEnergy" stroke={C.cobalt} strokeWidth={1.6} dot={rows.length < 30 ? { r: 2 } : false} isAnimationActive={false} connectNulls={false} />
          </LineChart>
          <LineChart responsive accessibilityLayer={false} data={rows} syncId="conv" style={{ width: '100%', height: '100%' }} margin={{ top: 6, right: 16, bottom: 4, left: 4 }}>
            <CartesianGrid stroke={C.line} />
            <XAxis dataKey="i" type="number" domain={[1, Math.max(rows.length, budget ? budget + prep : 1)]} allowDecimals={false} {...AXIS} />
            <YAxis domain={['auto', 'auto']} width={58} tickFormatter={(v: number) => formatNum(v, 3)} {...AXIS} />
            <RTooltip content={({ active, payload }) => (active && payload?.[0] ? <ConvTip r={payload[0].payload as ConvergenceRow} /> : null)} />
            <Line dataKey="best" type="stepAfter" stroke={C.teal} strokeWidth={1.8} dot={false} isAnimationActive={false} connectNulls={false} />
          </LineChart>
        </div>
      ) : (
        <div className="grid grid-cols-1 h-full place-items-center rounded-md border border-dashed border-line-strong bg-mist text-sm text-muted">
          {live ? 'Waiting for the first evaluation…' : 'No iterations recorded.'}
        </div>
      )}
    </ChartFrame>
  )
}

function ConvTip({ r }: { r: ConvergenceRow }) {
  return (
    <TooltipBox
      title={`Evaluation ${r.i}${r.phase === 'warm_start_preparation' ? ' (warm-start prep)' : ''}`}
      rows={[
        ['Raw energy', formatNum(r.energy, 5)],
        ['Best feasible', formatNum(r.best, 5)],
        ['Feasible share', formatPct(r.feasible, 1)],
      ]}
    />
  )
}

/* ----------------------------------------------------------------- histogram */

export function HistogramChart({ batch }: { batch: FinalBatch }) {
  const rows = useMemo(
    () =>
      batch.objective_histogram.map((b, i) => {
        const p = b.probability ?? 0
        const f = Math.min(p, b.feasible_probability ?? 0)
        return { i, start: b.bin_start, end: b.bin_end, label: formatNum(((b.bin_start ?? 0) + (b.bin_end ?? 0)) / 2, 3), feasible: f, infeasible: Math.max(0, p - f), p }
      }),
    [batch.objective_histogram],
  )
  const summary = `${batch.shots.toLocaleString('en-IN')} final shots produced ${batch.distinct_bitstrings.toLocaleString('en-IN')} distinct bitstrings; ${formatPct(batch.feasible_probability, 1)} of the probability mass satisfied every constraint. Bars show how the sampled objective values are distributed, split into feasible and infeasible samples.`
  return (
    <ChartFrame
      testId="chart-histogram"
      title="Final-batch sample distribution"
      summary={summary}
      height={260}
      legend={
        <>
          <LegendItem swatch={<rect x="2" y="1" width="14" height="10" fill={C.teal} />}>Feasible</LegendItem>
          <LegendItem swatch={<HatchSwatch />}>Infeasible (violates a constraint)</LegendItem>
        </>
      }
      table={
        <Table
          dense
          caption="Objective histogram"
          captionHidden
          rows={rows}
          rowKey={(r) => String(r.i)}
          empty="The histogram is empty."
          columns={[
            { key: 'bin', header: 'Objective bin', cell: (r) => `${formatNum(r.start, 4)} to ${formatNum(r.end, 4)}`, rowHeader: true },
            { key: 'p', header: 'Probability', align: 'right', cell: (r) => formatPct(r.p, 2) },
            { key: 'f', header: 'Feasible', align: 'right', cell: (r) => formatPct(r.feasible, 2) },
            { key: 'n', header: 'Infeasible', align: 'right', cell: (r) => formatPct(r.infeasible, 2) },
          ]}
        />
      }
    >
      <BarChart responsive accessibilityLayer={false} data={rows} style={{ width: '100%', height: '100%' }} margin={{ top: 6, right: 12, bottom: 22, left: 4 }} barCategoryGap={1}>
        <defs>
          <pattern id="qf-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="#eef2f7" />
            <line x1="0" y1="0" x2="0" y2="6" stroke={C.faint} strokeWidth="2" />
          </pattern>
        </defs>
        <CartesianGrid stroke={C.line} vertical={false} />
        <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" minTickGap={18} label={{ value: 'Objective value (bin centre, lower is better)', position: 'insideBottom', offset: -12, fill: C.muted, fontSize: 11 }} />
        <YAxis tickFormatter={(v: number) => `${Math.round(v * 100)}%`} width={44} {...AXIS} />
        <RTooltip
          cursor={{ fill: 'rgb(31 72 214 / 0.06)' }}
          content={({ active, payload }) => {
            const r = active && (payload?.[0]?.payload as (typeof rows)[number] | undefined)
            return r ? <TooltipBox title={`${formatNum(r.start, 4)} to ${formatNum(r.end, 4)}`} rows={[['Feasible', formatPct(r.feasible, 2)], ['Infeasible', formatPct(r.infeasible, 2)]]} /> : null
          }}
        />
        <Bar dataKey="feasible" stackId="a" fill={C.teal} isAnimationActive={false} />
        <Bar dataKey="infeasible" stackId="a" fill="url(#qf-hatch)" stroke={C.faint} strokeWidth={0.5} isAnimationActive={false} />
      </BarChart>
    </ChartFrame>
  )
}

function HatchSwatch() {
  return (
    <>
      <rect x="2" y="1" width="14" height="10" fill="#eef2f7" stroke={C.faint} strokeWidth="0.8" />
      <path d="M2 9l6-8M7 11l9-10M12 11l4-5" stroke={C.faint} strokeWidth="1.2" />
    </>
  )
}

/* -------------------------------------------------------------------- sector */

export function SectorChart({ weights, title = 'Sector allocation', caps }: { weights: Record<string, number>; title?: string; caps?: Record<string, number> | null }) {
  const rows = useMemo(
    () =>
      Object.entries(weights)
        .filter(([, w]) => w > 1e-9)
        .sort((a, b) => b[1] - a[1])
        .map(([sector, w]) => ({ sector, short: sectorShort(sector), w, cap: caps?.[sector] ?? null })),
    [weights, caps],
  )
  const top = rows[0]
  // Round the axis up to a whole 10 % (20 % above one half) so every tick is a round number.
  const axisMax = Math.min(1, Math.max(0.1, Math.ceil((top?.w ?? 0) * 11.5) / 10))
  const step = axisMax > 0.5 ? 0.2 : 0.1
  const ticks = Array.from({ length: Math.floor(axisMax / step + 1e-9) + 1 }, (_, i) => Math.round(i * step * 10) / 10)
  const summary = rows.length
    ? `${rows.length} sector${rows.length === 1 ? '' : 's'} held. Largest: ${top.sector} at ${formatPct(top.w, 1)} of the budget.`
    : 'No sector exposure: the portfolio is empty or entirely cash.'
  return (
    <ChartFrame
      testId="chart-sector"
      title={title}
      summary={summary}
      height={Math.max(120, rows.length * 34 + 30)}
      table={
        <Table
          dense
          caption="Sector weights"
          captionHidden
          rows={rows}
          rowKey={(r) => r.sector}
          empty="No holdings."
          columns={[
            { key: 's', header: 'Sector', cell: (r) => r.sector, rowHeader: true },
            { key: 'w', header: 'Weight', align: 'right', cell: (r) => formatPct(r.w, 2) },
          ]}
        />
      }
    >
      <BarChart responsive accessibilityLayer={false} layout="vertical" data={rows} style={{ width: '100%', height: '100%' }} margin={{ top: 0, right: 48, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={C.line} horizontal={false} />
        <XAxis type="number" domain={[0, axisMax]} ticks={ticks} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} {...AXIS} />
        <YAxis type="category" dataKey="short" width={96} {...AXIS} />
        <Bar dataKey="w" fill={C.teal} radius={[0, 3, 3, 0]} isAnimationActive={false} barSize={18}>
          <LabelList dataKey="w" position="right" formatter={(v: unknown) => formatPct(Number(v), 1)} fontSize={11} fill={C.ink} />
        </Bar>
      </BarChart>
    </ChartFrame>
  )
}

/* ---------------------------------------------------------------- experiment */




export function ExperimentFactorChart({ factor, cells, metric }: { factor: string; cells: ExperimentCell[]; metric: ExpMetric }) {
  const data = useMemo(() => {
    const groups = new Map<string, ExperimentCell[]>()
    for (const c of cells) {
      const k = levelOf(c)
      groups.set(k, [...(groups.get(k) ?? []), c])
    }
    return [...groups.entries()].map(([level, cs]) => {
      const vals = cs.map((c) => c[metric]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
      const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null
      const sd = vals.length > 1 && mean !== null ? Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / (vals.length - 1)) : null
      return { level, mean, sd, n: vals.length, seeds: cs.length, min: vals.length ? Math.min(...vals) : null, max: vals.length ? Math.max(...vals) : null, failed: cs.filter((c) => c.status !== 'ok').length }
    })
  }, [cells, metric])
  const points = useMemo(() => cells.map((c) => ({ level: levelOf(c), v: c[metric], seed: c.seed })).filter((p) => typeof p.v === 'number'), [cells, metric])
  const fmt = (v: number | null) => (v === null ? DASH : metric === 'feasible_probability' ? formatPct(v, 1) : metric === 'runtime_s' ? `${v.toFixed(2)} s` : formatNum(v, 4))
  const bestRow = data.filter((d) => d.mean !== null).sort((a, b) => (metric === 'best_objective' || metric === 'runtime_s' ? a.mean! - b.mean! : b.mean! - a.mean!))[0]
  const summary = data.length
    ? `${cells.length} cells across ${data.length} levels of ${(FACTOR_LABEL[factor] ?? factor).toLowerCase()}. ` +
      (bestRow ? `Best mean ${METRIC_LABEL[metric].split(' (')[0].toLowerCase()}: ${bestRow.level} (${fmt(bestRow.mean)}, n=${bestRow.n}). ` : '') +
      'Bars are means over seeds; dots are individual seeds.'
    : 'No cells for this factor.'
  return (
    <ChartFrame
      testId={`chart-exp-${factor}`}
      title={FACTOR_LABEL[factor] ?? factor}
      summary={summary}
      height={240}
      legend={
        <>
          <LegendItem swatch={<rect x="2" y="1" width="14" height="10" fill={C.cobaltSoft} stroke={C.cobalt} strokeWidth="0.8" />}>Mean over seeds</LegendItem>
          <LegendItem swatch={<circle cx="9" cy="6" r="3" fill={C.ink} />}>Single seed</LegendItem>
        </>
      }
      table={
        <Table
          dense
          caption={`${FACTOR_LABEL[factor] ?? factor}: ${METRIC_LABEL[metric]}`}
          captionHidden
          rows={data}
          rowKey={(r) => r.level}
          columns={[
            { key: 'l', header: 'Level', cell: (r) => r.level, rowHeader: true },
            { key: 'm', header: 'Mean', align: 'right', cell: (r) => fmt(r.mean) },
            { key: 'sd', header: 'Std dev', align: 'right', cell: (r) => (r.sd === null ? DASH : fmt(r.sd)) },
            { key: 'r', header: 'Range', align: 'right', cell: (r) => (r.min === null ? DASH : `${fmt(r.min)} to ${fmt(r.max)}`) },
            { key: 'n', header: 'Seeds', align: 'right', cell: (r) => `${r.n}/${r.seeds}` },
            { key: 'f', header: 'Not ok', align: 'right', cell: (r) => r.failed },
          ]}
        />
      }
    >
      <ComposedChart responsive accessibilityLayer={false} data={data} style={{ width: '100%', height: '100%' }} margin={{ top: 6, right: 12, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={C.line} vertical={false} />
        <XAxis dataKey="level" type="category" allowDuplicatedCategory={false} {...AXIS} />
        <YAxis domain={['auto', 'auto']} width={58} tickFormatter={(v: number) => fmt(v)} {...AXIS} />
        {metric === 'approximation_ratio' ? <ReferenceLine y={1} stroke={C.muted} strokeDasharray="3 3" /> : null}
        <RTooltip
          cursor={{ fill: 'rgb(31 72 214 / 0.05)' }}
          content={({ active, payload }) => {
            const r = active && (payload?.[0]?.payload as (typeof data)[number] | undefined)
            return r && 'mean' in r ? <TooltipBox title={r.level} rows={[['Mean', fmt(r.mean)], ['Std dev', r.sd === null ? DASH : fmt(r.sd)], ['Seeds', `${r.n}`]]} /> : null
          }}
        />
        <Bar dataKey="mean" fill={C.cobaltSoft} stroke={C.cobalt} strokeWidth={0.8} isAnimationActive={false} barSize={36} />
        <Scatter data={points} dataKey="v" fill={C.ink} isAnimationActive={false} />
      </ComposedChart>
    </ChartFrame>
  )
}

/* --------------------------------------------------------------------- noise */

export function NoiseChart({ noise }: { noise: NoiseComparison }) {
  const rows = useMemo(() => {
    const seeds = [...new Set([...noise.ideal.map((t) => t.seed), ...noise.noisy.map((t) => t.seed)])].sort((a, b) => a - b)
    return seeds.map((seed) => {
      const i = noise.ideal.find((t) => t.seed === seed)
      const n = noise.noisy.find((t) => t.seed === seed)
      return { seed: `seed ${seed}`, ideal: i?.feasible_probability ?? null, noisy: n?.feasible_probability ?? null, iObj: i?.best_objective ?? null, nObj: n?.best_objective ?? null, iRt: i?.runtime_s ?? null, nRt: n?.runtime_s ?? null }
    })
  }, [noise])
  const mean = (xs: Array<number | null>) => {
    const v = xs.filter((x): x is number => x !== null)
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null
  }
  const mi = mean(rows.map((r) => r.ideal))
  const mn = mean(rows.map((r) => r.noisy))
  const summary = `Same trained parameters sampled ${rows.length} time${rows.length === 1 ? '' : 's'} on the ideal simulator and under the ${noise.backend} noise model (${noise.shots.toLocaleString('en-IN')} shots each). Mean feasible probability: ${formatPct(mi, 1)} ideal versus ${formatPct(mn, 1)} noisy.`
  return (
    <ChartFrame
      testId="chart-noise"
      title="Ideal versus noisy sampling"
      summary={summary}
      height={240}
      legend={
        <>
          <LegendItem swatch={<rect x="2" y="1" width="14" height="10" fill={C.cobalt} />}>Ideal simulator</LegendItem>
          <LegendItem swatch={<rect x="2" y="1" width="14" height="10" fill="url(#qf-hatch-n)" stroke={C.amber} strokeWidth="0.8" />}>Noisy ({noise.backend})</LegendItem>
        </>
      }
      table={
        <Table
          dense
          caption="Noise comparison per seed"
          captionHidden
          rows={rows}
          rowKey={(r) => r.seed}
          columns={[
            { key: 's', header: 'Seed', cell: (r) => r.seed, rowHeader: true },
            { key: 'i', header: 'Feasible (ideal)', align: 'right', cell: (r) => formatPct(r.ideal, 1) },
            { key: 'n', header: 'Feasible (noisy)', align: 'right', cell: (r) => formatPct(r.noisy, 1) },
            { key: 'io', header: 'Best objective (ideal)', align: 'right', cell: (r) => formatNum(r.iObj, 5) },
            { key: 'no', header: 'Best objective (noisy)', align: 'right', cell: (r) => formatNum(r.nObj, 5) },
            { key: 'rt', header: 'Runtime ideal / noisy', align: 'right', cell: (r) => `${r.iRt?.toFixed(2) ?? DASH} / ${r.nRt?.toFixed(2) ?? DASH} s` },
          ]}
        />
      }
    >
      <BarChart responsive accessibilityLayer={false} data={rows} style={{ width: '100%', height: '100%' }} margin={{ top: 6, right: 12, bottom: 4, left: 4 }}>
        <defs>
          <pattern id="qf-hatch-n" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="#fff7e8" />
            <line x1="0" y1="0" x2="0" y2="5" stroke={C.amber} strokeWidth="1.6" />
          </pattern>
        </defs>
        <CartesianGrid stroke={C.line} vertical={false} />
        <XAxis dataKey="seed" {...AXIS} />
        <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} width={44} {...AXIS} />
        <RTooltip
          cursor={{ fill: 'rgb(31 72 214 / 0.05)' }}
          content={({ active, payload }) => {
            const r = active && (payload?.[0]?.payload as (typeof rows)[number] | undefined)
            return r ? <TooltipBox title={r.seed} rows={[['Feasible (ideal)', formatPct(r.ideal, 1)], ['Feasible (noisy)', formatPct(r.noisy, 1)]]} /> : null
          }}
        />
        <Bar dataKey="ideal" fill={C.cobalt} isAnimationActive={false} />
        <Bar dataKey="noisy" fill="url(#qf-hatch-n)" stroke={C.amber} strokeWidth={0.8} isAnimationActive={false} />
      </BarChart>
    </ChartFrame>
  )
}

