import { useState } from 'react'
import { Segmented } from '../../components/ui'
import { formatInt } from '../../lib/format'

type Depth = '1' | '2' | '3'

const MAX_WIRES = 6 // wires drawn before the rest collapse into a "+N more" row
const ROW = 26 // vertical spacing between qubit wires
const TOP = 36 // room above the wires for the two block-label lines
const LABEL_W = 30
const H_X = 40 // left edge of the Hadamard column
const BOX = 20 // single-qubit gate box size
const COST_W = 84
const MIXER_W = 66
const BLOCK_GAP = 6
const LAYER_GAP = 10
const LAYER_W = COST_W + BLOCK_GAP + MIXER_W + LAYER_GAP
const LAYERS_X = H_X + BOX + 14

// Representative RZZ couplings for the drawn wires, grouped into columns so no two lines in a column overlap. The
// real circuit couples many more pairs; the per-layer total is stated under the diagram.
const COUPLING_COLUMNS: Array<Array<[number, number]>> = [
  [[0, 1], [2, 3], [4, 5]],
  [[1, 2], [3, 4]],
  [[0, 2], [3, 5]],
]

/** Optimised angle per parameter name (e.g. `gamma[0]`), read from the run's parameter order. */
function angleMap(order: Array<string>, values: Array<number>): Map<string, number> {
  const m = new Map<string, number>()
  order.forEach((name, i) => {
    if (Number.isFinite(values[i])) m.set(name, values[i])
  })
  return m
}

/** Gates of one kind per layer, only when the transpiled total divides evenly across the run's layers. */
function perLayer(counts: Record<string, number>, gate: string, depth: number): number | null {
  const total = counts[gate]
  if (!total || depth < 1 || total % depth !== 0) return null
  return total / depth
}

function GateBox({ x, y, label, tone }: { x: number; y: number; label: string; tone: 'plain' | 'cost' | 'mixer' }) {
  const fill = tone === 'cost' ? 'fill-cobalt-100 stroke-cobalt-200' : tone === 'mixer' ? 'fill-teal-100 stroke-teal-600/40' : 'fill-mist-2 stroke-line-strong'
  return (
    <g>
      <rect x={x} y={y - BOX / 2} width={BOX} height={BOX} rx={4} className={fill} />
      <text x={x + BOX / 2} y={y + 3.5} textAnchor="middle" className="fill-ink-2 text-[9.5px] font-semibold">
        {label}
      </text>
    </g>
  )
}

function Measure({ x, y }: { x: number; y: number }) {
  return (
    <g>
      <rect x={x} y={y - BOX / 2} width={BOX} height={BOX} rx={4} className="fill-canvas stroke-line-strong" />
      <path d={`M${x + 4} ${y + 4} A6 6 0 0 1 ${x + BOX - 4} ${y + 4}`} fill="none" className="stroke-ink-2" strokeWidth={1.2} />
      <line x1={x + BOX / 2} y1={y + 4} x2={x + BOX - 5} y2={y - 6} className="stroke-ink-2" strokeWidth={1.2} />
    </g>
  )
}

/**
 * QAOA circuit structure as a compact SVG: H on every wire, then p rounds of cost U(γ) and mixer U(β), then measurement.
 * The toggle redraws it for p = 1–3 so the effect of depth is visible; layers the run actually used carry its
 * optimised angles, the others are drawn dashed with symbolic angles.
 */
export function QaoaCircuitDiagram({
  qubits,
  runDepth,
  parameterOrder,
  optimalParameters,
  gateCounts,
}: {
  qubits: number
  runDepth: number
  parameterOrder: Array<string>
  optimalParameters: Array<number>
  gateCounts: Record<string, number>
}) {
  const runP = Math.min(3, Math.max(1, Math.round(runDepth)))
  const [p, setP] = useState<Depth>(String(runP) as Depth)
  const layers = Number(p)
  const angles = angleMap(parameterOrder, optimalParameters)

  const shown = qubits <= MAX_WIRES + 1 ? qubits : MAX_WIRES
  const hidden = qubits - shown
  const wireY = (i: number) => TOP + 14 + i * ROW
  const wiresBottom = wireY(Math.max(0, shown - 1)) + 14
  const height = wiresBottom + (hidden > 0 ? 22 : 4)
  const measureX = LAYERS_X + layers * LAYER_W + 4
  const width = measureX + BOX + 14
  const wires = Array.from({ length: shown }, (_, i) => i)

  const rzz = perLayer(gateCounts, 'rzz', runDepth)
  const rz = perLayer(gateCounts, 'rz', runDepth)
  const rx = perLayer(gateCounts, 'rx', runDepth)
  const label = `QAOA circuit with p = ${layers} layer${layers === 1 ? '' : 's'} on ${qubits} qubits: a Hadamard on every qubit, then ${layers} round${
    layers === 1 ? '' : 's'
  } of a cost block U(γ) and a mixer block U(β), then measurement.${layers > runDepth ? ` This run used p = ${runDepth}.` : ''}`

  return (
    <figure className="m-0 min-w-0" data-testid="qaoa-circuit">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="text-sm font-medium text-ink-2" aria-hidden="true">
          Depth p
        </span>
        <div className="min-w-0 flex-1 sm:max-w-72">
          <Segmented
            name="qaoa-depth"
            legend="Circuit depth p (layers) to draw"
            hideLegend
            size="sm"
            value={p}
            onChange={setP}
            options={(['1', '2', '3'] as const).map((d) => ({
              value: d,
              label: (
                <span>
                  {d}
                  {Number(d) === runDepth ? (
                    <>
                      {' '}
                      <span className="text-xs font-normal text-faint">this run</span>
                    </>
                  ) : null}
                </span>
              ),
            }))}
          />
        </div>
      </div>
      {/* Grows a little on wide cards, shrinks a little on narrow ones, then scrolls inside the card rather than the page. */}
      <div className="-mx-1 overflow-x-auto px-1 pb-1" tabIndex={0} role="region" aria-label="QAOA circuit diagram, scrolls sideways">
        <svg
          role="img"
          aria-label={label}
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          style={{ width: '100%', maxWidth: Math.round(width * 1.2), minWidth: Math.round(width * 0.8), height: 'auto' }}
          className="block"
        >
          {/* wires and their labels */}
          {wires.map((i) => (
            <g key={i}>
              <text x={LABEL_W - 6} y={wireY(i) + 3.5} textAnchor="end" className="fill-muted text-[10px] tabular">
                q{i}
              </text>
              <line x1={LABEL_W} x2={measureX + BOX / 2} y1={wireY(i)} y2={wireY(i)} className="stroke-line-strong" strokeWidth={1} />
            </g>
          ))}

          <text x={H_X + BOX / 2} y={12} textAnchor="middle" className="fill-muted text-[10px] font-semibold">
            Start
          </text>
          {wires.map((i) => (
            <GateBox key={i} x={H_X} y={wireY(i)} label="H" tone="plain" />
          ))}

          {Array.from({ length: layers }, (_, l) => {
            const x0 = LAYERS_X + l * LAYER_W
            const mx = x0 + COST_W + BLOCK_GAP
            const real = l < runDepth
            const gamma = real ? angles.get(`gamma[${l}]`) : undefined
            const beta = real ? angles.get(`beta[${l}]`) : undefined
            const blockTop = TOP
            const blockH = wiresBottom - TOP
            const dash = real ? undefined : '4 3'
            const rzX = x0 + COST_W - BOX - 8
            return (
              <g key={l} data-layer={l + 1}>
                <g data-block="cost">
                  <rect x={x0} y={blockTop} width={COST_W} height={blockH} rx={8} className="fill-cobalt-50 stroke-cobalt-200" strokeDasharray={dash} />
                  <text x={x0 + COST_W / 2} y={12} textAnchor="middle" className="fill-cobalt-800 text-[10px] font-semibold">
                    Cost U(γ)
                  </text>
                  <text x={x0 + COST_W / 2} y={26} textAnchor="middle" className={`text-[10px] tabular ${gamma === undefined ? 'fill-faint italic' : 'fill-ink-2'}`}>
                    {gamma === undefined ? `γ[${l}]` : `γ[${l}] ${gamma.toFixed(3)}`}
                  </text>
                  {COUPLING_COLUMNS.map((col, c) =>
                    col
                      .filter(([a, b]) => a < shown && b < shown)
                      .map(([a, b]) => {
                        const cx = x0 + 12 + c * 13
                        return (
                          <g key={`${a}-${b}`}>
                            <line x1={cx} x2={cx} y1={wireY(a)} y2={wireY(b)} className="stroke-cobalt" strokeWidth={1.4} />
                            <circle cx={cx} cy={wireY(a)} r={3} className="fill-cobalt" />
                            <circle cx={cx} cy={wireY(b)} r={3} className="fill-cobalt" />
                          </g>
                        )
                      }),
                  )}
                  {wires.map((i) => (
                    <GateBox key={i} x={rzX} y={wireY(i)} label="RZ" tone="cost" />
                  ))}
                </g>
                <g data-block="mixer">
                  <rect x={mx} y={blockTop} width={MIXER_W} height={blockH} rx={8} className="fill-teal-50 stroke-teal-100" strokeDasharray={dash} />
                  <text x={mx + MIXER_W / 2} y={12} textAnchor="middle" className="fill-teal text-[10px] font-semibold">
                    Mixer U(β)
                  </text>
                  <text x={mx + MIXER_W / 2} y={26} textAnchor="middle" className={`text-[10px] tabular ${beta === undefined ? 'fill-faint italic' : 'fill-ink-2'}`}>
                    {beta === undefined ? `β[${l}]` : `β[${l}] ${beta.toFixed(3)}`}
                  </text>
                  {wires.map((i) => (
                    <GateBox key={i} x={mx + (MIXER_W - BOX) / 2} y={wireY(i)} label="RX" tone="mixer" />
                  ))}
                </g>
              </g>
            )
          })}

          <text x={measureX + BOX / 2} y={12} textAnchor="middle" className="fill-muted text-[10px] font-semibold">
            Read
          </text>
          {wires.map((i) => (
            <Measure key={i} x={measureX} y={wireY(i)} />
          ))}

          {hidden > 0 ? (
            <text x={LABEL_W - 6} y={wiresBottom + 14} className="fill-faint text-[10px]">
              ⋮ +{hidden} more qubit{hidden === 1 ? '' : 's'} with the same gates
            </text>
          ) : null}
        </svg>
      </div>
      <figcaption className="mt-2 space-y-1 text-xs text-muted">
        <span className="block">Each layer scores the portfolio (cost) then mixes choices (mixer). More layers p can find better picks but take longer.</span>
        <span className="block text-faint">
          {rzz !== null && rz !== null && rx !== null
            ? `Each cost block holds ${formatInt(rzz)} RZZ and ${formatInt(rz)} RZ gates (a few couplings drawn); each mixer holds ${formatInt(rx)} RX. `
            : 'A few of the RZZ couplings are drawn. '}
          {layers > runDepth ? `Dashed layers were not run (this run used p = ${runDepth}).` : runDepth > 3 ? `This run used p = ${runDepth}; up to 3 layers are drawn.` : ''}
        </span>
      </figcaption>
    </figure>
  )
}
